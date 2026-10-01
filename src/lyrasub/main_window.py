"""Main PySide6 workbench for the LyraSub baseline release."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .anchor_mapping import entries_from_text_list, entry_risk
from .constants import (
    ABOUT_EMAIL,
    ABOUT_PRODUCER,
    APP_DISPLAY_NAME,
    DEFAULT_MAX_CJK_CHARS,
    DEFAULT_MAX_LATIN_CHARS,
    VERSION,
)
from .llm_client import LLMResponseError, OpenAICompatibleClient
from .llm_config import LLMProfile, ProfileStore, SecretStore, parse_imported_profile_json
from .model_manager import MODEL_PRESETS, MODEL_SOURCES, ModelManager, ModelSource, SourceProbe
from .models import (
    AnchorState,
    BilingualOrder,
    LanguageMode,
    PreviewAudioSource,
    ProcessingDevice,
    SourceAudio,
    SubtitleEntry,
    SubtitleLayoutMode,
    SubtitleOutputMode,
    SubtitleProject,
)
from .project_io import (
    ProjectFormatError,
    autosave_path,
    load_project,
    remove_autosave,
    save_autosave,
    save_project,
)
from .prompt_templates import PromptTemplates, PromptTemplateStore
from .segment_merge import (
    GlobalMergeEntry,
    MergeProject,
    SegmentPlacementMode,
    apply_continuous_origins,
    build_global_entries,
    load_merge_project,
    parse_srt_text,
    parse_srt_time,
    render_merged_srt,
    save_merge_project,
    segment_from_lyrasub,
    segment_from_srt,
    validate_merge_project,
)
from .segmentation import segment_locally
from .srt_export import export_srt, format_srt_time, subtitle_output_text, validate_entries
from .tasks import MainThreadTaskBridge, TaskCancelledError, TaskWorker
from .text_normalization import to_simplified_chinese
from .transcription import LocalTranscriber, TranscriptionResult
from .translation import (
    TranslationRecord,
    chunk_translation_records,
    is_retryable_translation_response_error,
)


class TextEdit(QPlainTextEdit):
    """A single place for a pleasant professional multiline editor baseline."""

    def __init__(self, placeholder: str = "", readonly: bool = False) -> None:
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setReadOnly(readonly)
        self.setMinimumHeight(230)
        self.setTabChangesFocus(False)


class LargeTextEditorDialog(QDialog):
    """Edit a text field in a distraction-free, large-font window."""

    def __init__(self, parent: QWidget, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("大字号编辑清洗文本")
        self.resize(1080, 760)
        layout = QVBoxLayout(self)
        note = QLabel("在此处编辑后点击“保存到清洗文本”。不会直接改变原始语音锚点。")
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.editor = QPlainTextEdit(text)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        # The main workbench stylesheet uses 13 px.  Apply a widget-local pixel
        # style so this editor remains visibly at least 1.5× larger under Windows
        # DPI scaling and cannot be overridden by the global stylesheet.
        self.font_size_px = 20
        self._apply_font_size()
        layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        smaller = buttons.addButton("缩小字号", QDialogButtonBox.ButtonRole.ActionRole)
        larger = buttons.addButton("放大字号", QDialogButtonBox.ButtonRole.ActionRole)
        smaller.clicked.connect(lambda: self._change_font_size(-2))
        larger.clicked.connect(lambda: self._change_font_size(2))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_font_size(self) -> None:
        self.editor.setStyleSheet(
            f"QPlainTextEdit {{ font-size: {self.font_size_px}px; line-height: 1.55; }}"
        )

    def _change_font_size(self, delta: int) -> None:
        self.font_size_px = max(16, min(36, self.font_size_px + delta))
        self._apply_font_size()

    def edited_text(self) -> str:
        return self.editor.toPlainText()


class VideoPreviewDialog(QDialog):
    """Resizable review window sharing the main review player's clock and media."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_DISPLAY_NAME} · 视频审片预览")
        self.setModal(False)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.resize(1100, 760)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        self.video_widget = QVideoWidget(self)
        self.video_widget.setMinimumSize(640, 360)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.video_widget, 1)
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("subtitlePreviewText")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.set_subtitle_font_size(28)
        layout.addWidget(self.subtitle_label)
        transport = QHBoxLayout()
        self.play_button = QPushButton("播放")
        self.stop_button = QPushButton("停止")
        self.position_label = QLabel("00:00:00.000")
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.duration_label = QLabel("00:00:00.000")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(16, 64)
        self.font_size_spin.setValue(28)
        self.font_size_spin.setSuffix(" px")
        transport.addWidget(self.play_button)
        transport.addWidget(self.stop_button)
        transport.addWidget(self.position_label)
        transport.addWidget(self.timeline, 1)
        transport.addWidget(self.duration_label)
        transport.addWidget(QLabel("音量"))
        transport.addWidget(self.volume_slider)
        transport.addWidget(QLabel("字幕"))
        transport.addWidget(self.font_size_spin)
        layout.addLayout(transport)
        self.status_label = QLabel("视频画面、播放控制和字幕时间轴与校对页同步。")
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def set_subtitle_font_size(self, pixel_size: int) -> None:
        safe_height = max(86, int(pixel_size * 3.15) + 20)
        self.subtitle_label.setMinimumHeight(safe_height)
        self.subtitle_label.setStyleSheet(
            f"font-size: {pixel_size}px; font-weight: 600; line-height: 1.32; "
            "padding: 8px 20px; background: rgba(0, 0, 0, 168); border-radius: 4px;"
        )


class AutosaveSettingsDialog(QDialog):
    """Configure local recovery snapshots without changing project data."""

    def __init__(self, parent: QWidget, seconds: int, location: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("自动保存设置")
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        note = QLabel(
            "自动保存仅在工程已保存到磁盘且存在未保存改动时写入恢复快照。设为 0 秒可关闭自动保存。"
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.interval = QSpinBox()
        self.interval.setRange(0, 3600)
        self.interval.setSpecialValueText("关闭")
        self.interval.setSuffix(" 秒")
        self.interval.setValue(max(0, seconds))
        path_label = QLabel(location)
        path_label.setObjectName("filePath")
        path_label.setWordWrap(True)
        form.addRow("保存间隔", self.interval)
        form.addRow("恢复文件位置", path_label)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class DownloadDiagnosticDialog(QDialog):
    """Show a selectable diagnostic with a one-click clipboard copy action."""

    def __init__(
        self,
        parent: QWidget,
        diagnostic: str,
        title: str = "模型下载诊断",
        prompt_text: str = "任务未完成。请点击“复制诊断”，然后把完整内容发送给开发者排查。",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 460)
        layout = QVBoxLayout(self)
        prompt = QLabel(prompt_text)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlainText(diagnostic)
        self.detail.selectAll()
        layout.addWidget(self.detail, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton("复制诊断", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(self.copy_diagnostic)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def copy_diagnostic(self) -> None:
        QApplication.clipboard().setText(self.detail.toPlainText())


class JsonImportDialog(QDialog):
    """Accept JSON pasted from an API provider or loaded from a local file."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入模型服务 JSON")
        self.resize(700, 460)
        layout = QVBoxLayout(self)
        note = QLabel(
            "可粘贴服务商提供的 OpenAI 兼容 JSON，或从本地选择 JSON 文件。导入仅填充表单；"
            "请在确认参数后点击“保存配置”，API 密钥才会被加密写入本机。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            '{\n  "base_url": "https://api.example.com/v1",\n  "model": "gpt-4o-mini",\n  "api_key": "..."\n}'
        )
        layout.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        browse = buttons.addButton("选择 JSON 文件", QDialogButtonBox.ButtonRole.ActionRole)
        apply_button = buttons.addButton("导入到表单", QDialogButtonBox.ButtonRole.AcceptRole)
        browse.clicked.connect(self._choose_file)
        apply_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_file(self) -> None:
        settings = QSettings("Lyra Studio", "LyraSub")
        stored = str(settings.value("files/last_directory", "")).strip()
        initial_directory = stored if stored and Path(stored).is_dir() else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型服务 JSON", initial_directory, "JSON 文件 (*.json)"
        )
        if not path:
            return
        settings.setValue("files/last_directory", str(Path(path).parent))
        try:
            self.editor.setPlainText(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, f"无法读取 JSON 文件：{exc}")

    def raw_json(self) -> str:
        return self.editor.toPlainText().strip()


class PromptTemplateDialog(QDialog):
    """Advanced editor for the built-in cleanup and semantic segmentation prompts."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("LLM 提示词模板（高级）")
        self.resize(900, 760)
        self.store = PromptTemplateStore()
        templates = self.store.load()
        layout = QVBoxLayout(self)
        note = QLabel(
            "这里的模板会用于所有项目的 LLM 清洗和语义切分。切分模板中的 "
            "{max_cjk_chars} 与 {max_latin_chars} 会在请求时自动替换为当前项目上限。"
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        cleanup_group = QGroupBox("文本清洗系统提示词")
        cleanup_layout = QVBoxLayout(cleanup_group)
        self.cleanup_editor = QPlainTextEdit(templates.cleanup_system)
        self.cleanup_editor.setMinimumHeight(150)
        cleanup_layout.addWidget(self.cleanup_editor)
        cleanup_reset = QPushButton("恢复清洗默认模板")
        cleanup_reset.clicked.connect(self._reset_cleanup)
        cleanup_layout.addWidget(cleanup_reset, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(cleanup_group)
        translation_group = QGroupBox("批量翻译系统提示词")
        translation_layout = QVBoxLayout(translation_group)
        self.translation_editor = QPlainTextEdit(templates.translation_system)
        self.translation_editor.setMinimumHeight(130)
        translation_layout.addWidget(self.translation_editor)
        translation_reset = QPushButton("恢复翻译默认模板")
        translation_reset.clicked.connect(self._reset_translation)
        translation_layout.addWidget(translation_reset, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(translation_group)
        segmentation_group = QGroupBox("语义切分系统提示词")
        segmentation_layout = QVBoxLayout(segmentation_group)
        self.segmentation_editor = QPlainTextEdit(templates.segmentation_system)
        self.segmentation_editor.setMinimumHeight(260)
        segmentation_layout.addWidget(self.segmentation_editor)
        segmentation_reset = QPushButton("恢复切分默认模板")
        segmentation_reset.clicked.connect(self._reset_segmentation)
        segmentation_layout.addWidget(segmentation_reset, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(segmentation_group, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        reset_all = buttons.addButton("全部恢复默认", QDialogButtonBox.ButtonRole.ResetRole)
        reset_all.clicked.connect(self._reset_all)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reset_cleanup(self) -> None:
        self.cleanup_editor.setPlainText(PromptTemplateStore.defaults().cleanup_system)

    def _reset_segmentation(self) -> None:
        self.segmentation_editor.setPlainText(PromptTemplateStore.defaults().segmentation_system)

    def _reset_translation(self) -> None:
        self.translation_editor.setPlainText(PromptTemplateStore.defaults().translation_system)

    def _reset_all(self) -> None:
        defaults = PromptTemplateStore.defaults()
        self.cleanup_editor.setPlainText(defaults.cleanup_system)
        self.segmentation_editor.setPlainText(defaults.segmentation_system)
        self.translation_editor.setPlainText(defaults.translation_system)

    def accept(self) -> None:
        cleanup = self.cleanup_editor.toPlainText().strip()
        segmentation = self.segmentation_editor.toPlainText().strip()
        translation = self.translation_editor.toPlainText().strip()
        if not cleanup or not segmentation or not translation:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "清洗、切分和翻译提示词均不能为空。")
            return
        self.store.save(
            PromptTemplates(
                cleanup_system=cleanup,
                segmentation_system=segmentation,
                translation_system=translation,
            )
        )
        super().accept()


class LLMProfileDialog(QDialog):
    """Edit one public endpoint profile and save its secret separately."""

    def __init__(self, parent: QWidget, existing: LLMProfile | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("模型服务配置")
        self.setMinimumWidth(520)
        self.existing = existing
        self.profiles = ProfileStore()
        self.secrets = SecretStore()

        layout = QVBoxLayout(self)
        note = QLabel(
            "API 密钥仅保存在当前 Windows 用户的受保护配置中，不会写入项目文件或默认导出配置。"
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)
        profile_group = QGroupBox("Profile 管理")
        profile_layout = QHBoxLayout(profile_group)
        self.profile_selector = QComboBox()
        self.profile_selector.setToolTip("选择当前用于 LLM 清洗、语义切分和批量翻译的模型服务。")
        self.new_profile_button = QPushButton("新建 Profile")
        self.delete_profile_button = QPushButton("删除当前")
        self.profile_selector.currentIndexChanged.connect(self._profile_selected)
        self.new_profile_button.clicked.connect(self.new_profile)
        self.delete_profile_button.clicked.connect(self.delete_current_profile)
        profile_layout.addWidget(QLabel("当前 Profile"))
        profile_layout.addWidget(self.profile_selector, 1)
        profile_layout.addWidget(self.new_profile_button)
        profile_layout.addWidget(self.delete_profile_button)
        layout.addWidget(profile_group)
        self.feedback = QLabel()
        self.feedback.setObjectName("mutedLabel")
        self.feedback.setWordWrap(True)
        utility_row = QHBoxLayout()
        import_button = QPushButton("导入 JSON 配置…")
        import_button.clicked.connect(self.import_json_configuration)
        prompts_button = QPushButton("高级：提示词模板…")
        prompts_button.clicked.connect(self.edit_prompt_templates)
        utility_row.addWidget(import_button)
        utility_row.addWidget(prompts_button)
        utility_row.addStretch(1)
        layout.addLayout(utility_row)
        layout.addWidget(self.feedback)
        form = QFormLayout()
        self.name = QLineEdit(existing.name if existing else "我的 OpenAI 兼容服务")
        self.base_url = QLineEdit(existing.base_url if existing else "https://api.example.com/v1")
        self.model = QLineEdit(existing.model if existing else "")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("留空则保留已保存的密钥")
        self.headers = QLineEdit(
            ""
            if not existing or not existing.extra_headers
            else "; ".join(f"{k}: {v}" for k, v in existing.extra_headers.items())
        )
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 3600)
        self.timeout.setValue(existing.timeout_seconds if existing else 300)
        self.translation_batch_chars = QSpinBox()
        self.translation_batch_chars.setRange(1_000, 100_000)
        self.translation_batch_chars.setSingleStep(1_000)
        self.translation_batch_chars.setValue(
            existing.translation_batch_chars if existing else 2_000
        )
        batch_hint = QLabel(
            "双语翻译会按字幕条目成批发送而非逐条请求。请按模型上下文窗口调整；"
            "单条超出该值时仍会单独发送。"
        )
        batch_hint.setObjectName("mutedLabel")
        batch_hint.setWordWrap(True)
        timeout_hint = QLabel(
            "默认 300 秒。处理超过 30 分钟音频的长稿时，模型返回可能更慢；"
            "如服务稳定，可提高到 600 秒或更长，或按章节分段处理。"
        )
        timeout_hint.setObjectName("mutedLabel")
        timeout_hint.setWordWrap(True)
        form.addRow("名称", self.name)
        form.addRow("API 基础地址", self.base_url)
        form.addRow("模型名称", self.model)
        form.addRow("API 密钥", self.api_key)
        form.addRow("附加请求头", self.headers)
        form.addRow("超时（秒）", self.timeout)
        form.addRow("翻译每批字符数", self.translation_batch_chars)
        form.addRow("批处理提示", batch_hint)
        form.addRow("长文本提示", timeout_hint)
        layout.addLayout(form)
        self._reload_profile_selector(
            existing.id if existing else self.profiles.active_profile_id()
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reload_profile_selector(self, selected_id: str = "") -> None:
        profiles = self.profiles.list()
        active_id = selected_id or self.profiles.active_profile_id()
        self.profile_selector.blockSignals(True)
        self.profile_selector.clear()
        for profile in profiles:
            self.profile_selector.addItem(f"{profile.name}  ·  {profile.model}", profile.id)
        index = self.profile_selector.findData(active_id)
        if index >= 0:
            self.profile_selector.setCurrentIndex(index)
        elif self.profile_selector.count():
            self.profile_selector.setCurrentIndex(0)
        self.profile_selector.blockSignals(False)
        self.delete_profile_button.setEnabled(bool(profiles))
        if profiles:
            current_id = str(self.profile_selector.currentData())
            selected = next(
                (profile for profile in profiles if profile.id == current_id), profiles[0]
            )
            self._load_profile_into_form(selected)
        else:
            self.existing = None

    def _load_profile_into_form(self, profile: LLMProfile) -> None:
        self.existing = profile
        self.name.setText(profile.name)
        self.base_url.setText(profile.base_url)
        self.model.setText(profile.model)
        self.api_key.clear()
        self.api_key.setPlaceholderText("留空则保留当前 Profile 已保存的密钥")
        self.headers.setText(
            "; ".join(f"{key}: {value}" for key, value in profile.extra_headers.items())
        )
        self.timeout.setValue(profile.timeout_seconds)
        self.translation_batch_chars.setValue(profile.translation_batch_chars)

    def _profile_selected(self, _index: int) -> None:
        profile_id = str(self.profile_selector.currentData())
        if not profile_id:
            return
        profile = next((item for item in self.profiles.list() if item.id == profile_id), None)
        if profile is None:
            return
        self.profiles.set_active_profile(profile.id)
        self._load_profile_into_form(profile)
        self.feedback.setText(f"已切换当前 Profile：{profile.name}。后续 LLM 任务将使用此配置。")

    def new_profile(self) -> None:
        self.existing = None
        self.profile_selector.blockSignals(True)
        self.profile_selector.setCurrentIndex(-1)
        self.profile_selector.blockSignals(False)
        self.name.setText("我的 OpenAI 兼容服务")
        self.base_url.setText("https://api.example.com/v1")
        self.model.clear()
        self.api_key.clear()
        self.api_key.setPlaceholderText("新建 Profile 时必须填写 API 密钥")
        self.headers.clear()
        self.timeout.setValue(300)
        self.translation_batch_chars.setValue(2_000)
        self.feedback.setText("正在新建 Profile。填写配置与 API 密钥后点击“保存当前 Profile”。")
        self.name.setFocus()
        self.name.selectAll()

    def delete_current_profile(self) -> None:
        profile_id = str(self.profile_selector.currentData())
        profile = next((item for item in self.profiles.list() if item.id == profile_id), None)
        if profile is None:
            return
        response = QMessageBox.question(
            self,
            "删除模型服务 Profile",
            f"确定删除“{profile.name}”吗？该 Profile 的本地 API 密钥也会一并删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        self.profiles.delete(profile.id)
        if profile.api_key_ref:
            self.secrets.delete(profile.api_key_ref)
        self._reload_profile_selector()
        if self.profile_selector.count() == 0:
            self.new_profile()
        self.feedback.setText(f"已删除 Profile：{profile.name}。")

    def import_json_configuration(self) -> None:
        dialog = JsonImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            imported = parse_imported_profile_json(dialog.raw_json())
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        self.name.setText(imported["name"])
        self.base_url.setText(imported["base_url"])
        self.model.setText(imported["model"])
        self.api_key.setText(imported["api_key"])
        self.headers.setText(
            "; ".join(f"{key}: {value}" for key, value in imported["extra_headers"].items())
        )
        self.timeout.setValue(imported["timeout_seconds"])
        self.translation_batch_chars.setValue(imported["translation_batch_chars"])
        self.feedback.setText("已导入到表单；请核对后点击“保存”。")

    def edit_prompt_templates(self) -> None:
        dialog = PromptTemplateDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.feedback.setText("LLM 提示词模板已保存，可随时再次打开并恢复默认。")

    def profile_and_key(self) -> tuple[LLMProfile, str | None]:
        name = self.name.text().strip()
        base_url = self.base_url.text().strip().rstrip("/")
        model = self.model.text().strip()
        if not name or not base_url or not model:
            raise ValueError("名称、API 基础地址和模型名称均为必填项。")
        extra_headers: dict[str, str] = {}
        for part in self.headers.text().split(";"):
            if not part.strip():
                continue
            key, separator, value = part.partition(":")
            if not separator or not key.strip() or not value.strip():
                raise ValueError("附加请求头请采用“名称: 值; 名称: 值”的格式。")
            extra_headers[key.strip()] = value.strip()
        profile_id = self.existing.id if self.existing else f"profile_{uuid4().hex}"
        return (
            LLMProfile(
                id=profile_id,
                name=name,
                base_url=base_url,
                model=model,
                api_key_ref=f"secret_{profile_id}",
                extra_headers=extra_headers,
                timeout_seconds=self.timeout.value(),
                translation_batch_chars=self.translation_batch_chars.value(),
            ),
            self.api_key.text() or None,
        )

    def accept(self) -> None:
        try:
            profile, api_key = self.profile_and_key()
            if not self.existing and not api_key:
                raise ValueError("新建服务配置时必须填写 API 密钥。")
            existing = [item for item in self.profiles.list() if item.id != profile.id]
            self.profiles.save_all([*existing, profile], active_profile_id=profile.id)
            if api_key:
                self.secrets.put(profile.api_key_ref, api_key)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        super().accept()


class MainWindow(QMainWindow):
    """User-facing workbench with a persistent source-anchor editing model."""

    def __init__(self) -> None:
        super().__init__()
        self.project = SubtitleProject(app_version=VERSION)
        self.project_path: Path | None = None
        self.merge_project = MergeProject()
        self.merge_project_path: Path | None = None
        self._merge_global_entries: list[GlobalMergeEntry] = []
        self._merge_preview_segment_id: str | None = None
        self._merge_preview_path = ""
        self._merge_timeline_scrubbing = False
        self.video_preview_dialog: VideoPreviewDialog | None = None
        self.models = ModelManager()
        self.local_settings = QSettings("Lyra Studio", "LyraSub")
        self.transcriber = LocalTranscriber(self.models)
        self._worker_thread: QThread | None = None
        self._worker: TaskWorker | None = None
        self._task_bridge: MainThreadTaskBridge | None = None
        self._download_cancel_event: Event | None = None
        self._download_active = False
        self._task_started_at: float | None = None
        self._task_title = ""
        self._task_failed_flag = False
        self._pending_local_segmentation = False
        self._table_refreshing = False
        self._project_dirty = False
        self._draft_restored = False
        self._timeline_scrubbing = False
        self._preview_active_row: int | None = None
        self._preview_loaded_path = ""
        self._preview_binding_signature = ""
        self.audio_output = QAudioOutput(self)
        self.audio_player = QMediaPlayer(self)
        self.audio_player.setAudioOutput(self.audio_output)
        self.audio_player.positionChanged.connect(self._preview_position_changed)
        self.audio_player.durationChanged.connect(self._preview_duration_changed)
        self.audio_player.playbackStateChanged.connect(self._preview_playback_state_changed)
        self.preview_sync_audio_output = QAudioOutput(self)
        self.preview_sync_audio_player = QMediaPlayer(self)
        self.preview_sync_audio_player.setAudioOutput(self.preview_sync_audio_output)
        self._preview_audio_volume = 1.0
        self._video_preview_scrubbing = False
        self.merge_audio_output = QAudioOutput(self)
        self.merge_audio_player = QMediaPlayer(self)
        self.merge_audio_player.setAudioOutput(self.merge_audio_output)
        self.merge_audio_player.positionChanged.connect(self._merge_preview_position_changed)
        self.merge_audio_player.durationChanged.connect(self._merge_preview_duration_changed)
        self.merge_audio_player.playbackStateChanged.connect(self._merge_preview_state_changed)
        self._task_elapsed_timer = QTimer(self)
        self._task_elapsed_timer.setInterval(250)
        self._task_elapsed_timer.timeout.connect(self._refresh_task_elapsed)
        self._autosave_interval_seconds = max(
            0, int(self.local_settings.value("project/autosave_interval_seconds", 45))
        )
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_project)
        self._configure_autosave_timer()
        self._build_window()
        self._build_model_manager_dialog()
        self._build_actions()
        self._refresh_all()

    def _build_window(self) -> None:
        self.setWindowTitle(f"{APP_DISPLAY_NAME} {VERSION}")
        self.resize(1600, 900)
        self.setMinimumSize(1100, 680)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪。导入 MP3 或 MP4 媒体后开始制作字幕。")

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_header())
        root_layout.addWidget(self._build_task_progress_panel())

        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace.setChildrenCollapsible(False)
        self.workspace.addWidget(self._build_navigation())
        self.workspace.addWidget(self._build_pages())
        self.inspector_panel = self._build_inspector()
        self.workspace.addWidget(self.inspector_panel)
        self.workspace.setStretchFactor(0, 0)
        self.workspace.setStretchFactor(1, 1)
        self.workspace.setStretchFactor(2, 0)
        self.workspace.setSizes([224, 1060, 300])
        root_layout.addWidget(self.workspace, 1)
        self.setCentralWidget(root)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("topHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 10, 24, 10)
        logo = QLabel("弦")
        logo.setObjectName("logoMark")
        title_box = QVBoxLayout()
        title = QLabel("弦字幕 Plus")
        title.setObjectName("appTitle")
        subtitle = QLabel("LYRASUB PLUS · 影视字幕工作台")
        subtitle.setObjectName("appSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addWidget(logo)
        layout.addLayout(title_box)
        layout.addStretch(1)
        layout.addWidget(QLabel("项目", objectName="appSubtitle"))
        self.project_name_edit = QLineEdit("未命名项目")
        self.project_name_edit.setObjectName("projectTitle")
        self.project_name_edit.setMinimumWidth(240)
        self.project_name_edit.setMaximumWidth(420)
        self.project_name_edit.setPlaceholderText("为当前工程命名")
        self.project_name_edit.setToolTip(
            "项目名称会作为保存工程和导出字幕、文本文件的默认文件名。"
        )
        self.project_name_edit.editingFinished.connect(self.apply_project_title)
        layout.addWidget(self.project_name_edit)
        self.inspector_toggle = QToolButton()
        self.inspector_toggle.setText("隐藏检查器")
        self.inspector_toggle.setCheckable(True)
        self.inspector_toggle.setToolTip("折叠或展开右侧项目检查器，为字幕表格腾出更多空间。")
        self.inspector_toggle.toggled.connect(self.toggle_inspector)
        layout.addWidget(self.inspector_toggle)
        return header

    def _build_task_progress_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("taskProgressPanel")
        panel.setVisible(False)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(24, 8, 24, 8)
        layout.setSpacing(12)
        self.task_progress_title = QLabel("正在处理")
        self.task_progress_title.setObjectName("taskProgressTitle")
        self.task_progress_detail = QLabel("")
        self.task_progress_detail.setObjectName("mutedLabel")
        self.task_progress_detail.setMinimumWidth(190)
        self.task_progress_bar = QProgressBar()
        self.task_progress_bar.setRange(0, 0)
        self.task_progress_bar.setTextVisible(True)
        self.task_cancel_button = QPushButton("停止当前任务")
        self.task_cancel_button.setObjectName("dangerButton")
        self.task_cancel_button.clicked.connect(self.request_task_cancel)
        layout.addWidget(self.task_progress_title)
        layout.addWidget(self.task_progress_bar, 1)
        layout.addWidget(self.task_progress_detail)
        layout.addWidget(self.task_cancel_button)
        self.task_progress_panel = panel
        return panel

    def _build_navigation(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("navPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 20, 12, 12)
        layout.addWidget(QLabel("制作流程", objectName="sectionCaption"))
        self.nav = QListWidget()
        self.nav.setObjectName("workflowNav")
        for label in (
            "01  导入媒体",
            "02  文字识别",
            "03  文本清洗",
            "04  语义切分",
            "05  校对导出",
            "06  分段合并",
        ):
            self.nav.addItem(label)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._change_page)
        layout.addWidget(self.nav, 1)
        return panel

    def _scroll_page(self, content: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _build_pages(self) -> QWidget:
        self.pages = QStackedWidget()
        self.pages.addWidget(self._scroll_page(self._build_import_page()))
        self.pages.addWidget(self._scroll_page(self._build_transcribe_page()))
        self.pages.addWidget(self._scroll_page(self._build_cleanup_page()))
        self.pages.addWidget(self._scroll_page(self._build_segment_page()))
        self.pages.addWidget(self._scroll_page(self._build_review_page()))
        self.pages.addWidget(self._scroll_page(self._build_merge_page()))
        return self.pages

    @staticmethod
    def _page_shell(eyebrow: str, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 28, 32, 36)
        layout.setSpacing(16)
        caption = QLabel(eyebrow)
        caption.setObjectName("sectionCaption")
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        detail = QLabel(description)
        detail.setObjectName("pageDescription")
        detail.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(heading)
        layout.addWidget(detail)
        return page, layout

    def _build_import_page(self) -> QWidget:
        page, layout = self._page_shell(
            "素材准备",
            "导入 MP3 或 MP4 媒体",
            "MP4 会直接读取内嵌音轨交给本地识别，不会额外生成 MP3 副本；项目文件只记录原始媒体路径和字幕数据。",
        )
        card = QGroupBox("源媒体")
        card_layout = QGridLayout(card)
        self.audio_path_label = QLabel("尚未选择媒体文件")
        self.audio_path_label.setWordWrap(True)
        self.audio_path_label.setObjectName("filePath")
        choose = QPushButton("选择 MP3 或 MP4 文件")
        choose.setObjectName("primaryButton")
        choose.clicked.connect(self.import_audio)
        card_layout.addWidget(QLabel("文件"), 0, 0)
        card_layout.addWidget(self.audio_path_label, 0, 1)
        card_layout.addWidget(choose, 1, 1, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(card)
        tip = QFrame()
        tip.setObjectName("infoCallout")
        tip_layout = QVBoxLayout(tip)
        tip_layout.addWidget(QLabel("时间轴保障", objectName="calloutTitle"))
        tip_layout.addWidget(
            QLabel(
                "识别完成后，弦字幕 Plus 会保留词级或字级时间锚点。后续的清洗和换行不会直接覆盖原始锚点。"
            )
        )
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _build_transcribe_page(self) -> QWidget:
        page, layout = self._page_shell(
            "本地识别",
            "使用已下载模型生成可校订转写",
            "模型下载与来源维护位于顶部“模型管理”。本页只选择已经下载到本机的模型，避免重复下载。",
        )
        options = QGroupBox("识别设置")
        form = QFormLayout(options)
        self.language_combo = QComboBox()
        self.language_combo.addItem("自动识别（多语种）", LanguageMode.AUTO)
        self.language_combo.addItem("中文为主", LanguageMode.CHINESE)
        self.language_combo.addItem("英文为主", LanguageMode.ENGLISH)
        self.model_combo = QComboBox()
        for preset in MODEL_PRESETS:
            self.model_combo.addItem(f"{preset.label} — {preset.description}", preset.id)
        self.model_combo.currentIndexChanged.connect(self.refresh_model_download_status)
        self.model_combo.currentIndexChanged.connect(self._mark_dirty)
        self.model_selection_status = QLabel()
        self.model_selection_status.setObjectName("modelNote")
        self.model_selection_status.setWordWrap(True)
        self.open_model_manager_button = QPushButton("打开模型管理")
        self.open_model_manager_button.clicked.connect(self.show_model_manager)
        model_actions = QHBoxLayout()
        model_actions.addWidget(self.open_model_manager_button)
        model_actions.addStretch(1)
        self.device_combo = QComboBox()
        self.device_combo.addItem("自动（选择兼容 GPU 精度）", ProcessingDevice.AUTO)
        self.device_combo.addItem("NVIDIA GPU", ProcessingDevice.NVIDIA_GPU)
        self.device_combo.addItem("CPU", ProcessingDevice.CPU)
        self.language_combo.currentIndexChanged.connect(self._mark_dirty)
        self.device_combo.currentIndexChanged.connect(self._mark_dirty)
        self.gpu_diagnostic_button = QPushButton("运行 GPU 自检")
        self.gpu_diagnostic_button.clicked.connect(self.run_gpu_diagnostic)
        self.gpu_diagnostic_status = QLabel(
            "用于检查 NVIDIA 驱动、CUDA 12 / cuDNN 9、CTranslate2 和推荐计算精度。"
        )
        self.gpu_diagnostic_status.setObjectName("mutedLabel")
        gpu_actions = QHBoxLayout()
        gpu_actions.addWidget(self.gpu_diagnostic_button)
        gpu_actions.addWidget(self.gpu_diagnostic_status, 1)
        self.recognize_button = QPushButton("开始本地识别")
        self.recognize_button.setObjectName("primaryButton")
        self.recognize_button.clicked.connect(self.start_transcription)
        form.addRow("主要语言", self.language_combo)
        form.addRow("已下载模型", self.model_combo)
        form.addRow("模型状态", self.model_selection_status)
        form.addRow("", model_actions)
        form.addRow("运行设备", self.device_combo)
        form.addRow("GPU 自检", gpu_actions)
        form.addRow("", self.recognize_button)
        layout.addWidget(options)
        layout.addWidget(QLabel("识别原文（可直接修改）", objectName="subheading"))
        self.original_editor = TextEdit("识别结果将显示在这里。您可在进入文本清洗前直接校订。")
        self.original_editor.textChanged.connect(self._mark_dirty)
        layout.addWidget(self.original_editor)
        update_original = QPushButton("将校订原文应用到项目")
        update_original.clicked.connect(self.apply_original_text)
        layout.addWidget(update_original, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.refresh_model_download_status()
        return page

    def _build_model_manager_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("本地模型管理")
        dialog.setModal(False)
        dialog.resize(760, 570)
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "模型只需下载一次，保存在当前 Windows 用户的数据目录中。主工作台只选择已下载模型；"
            "补丁更新不会删除模型。"
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        options = QGroupBox("下载与来源设置")
        form = QFormLayout(options)
        self.model_manager_model_combo = QComboBox()
        for preset in MODEL_PRESETS:
            self.model_manager_model_combo.addItem(
                f"{preset.label} — {preset.description}（约 {preset.approximate_download_mb} MB）",
                preset.id,
            )
        self.model_manager_model_combo.currentIndexChanged.connect(
            self.refresh_model_download_status
        )
        self.model_source_combo = QComboBox()
        for source in MODEL_SOURCES:
            self.model_source_combo.addItem(source.label, source.id)
        stored_source = str(self.local_settings.value("model_download/source_id", "cn_mirror"))
        self._set_combo_data(self.model_source_combo, stored_source)
        self.model_source_combo.currentIndexChanged.connect(self._model_source_changed)
        self.custom_model_source = QLineEdit(
            str(self.local_settings.value("model_download/custom_endpoint", ""))
        )
        self.custom_model_source.setPlaceholderText("https://您的可信模型源")
        self.custom_model_source.textChanged.connect(self._save_custom_model_source)
        self.model_proxy = QLineEdit(str(self.local_settings.value("model_download/proxy", "")))
        self.model_proxy.setPlaceholderText("可选，例如 http://127.0.0.1:7890")
        self.model_proxy.textChanged.connect(self._save_model_proxy)
        self.model_source_note = QLabel()
        self.model_source_note.setObjectName("mutedLabel")
        self.model_source_note.setWordWrap(True)
        self.model_download_status = QLabel()
        self.model_download_status.setWordWrap(True)
        self.model_download_status.setObjectName("modelNote")
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 100)
        self.download_progress.setValue(0)
        self.download_progress.setFormat("等待模型下载")
        source_actions = QHBoxLayout()
        self.test_model_source_button = QPushButton("测试连接")
        self.test_model_source_button.clicked.connect(self.test_model_source)
        self.download_model_button = QPushButton("下载选中模型")
        self.download_model_button.setObjectName("primaryButton")
        self.download_model_button.clicked.connect(self.download_selected_model)
        self.stop_download_button = QPushButton("停止下载")
        self.stop_download_button.setEnabled(False)
        self.stop_download_button.clicked.connect(self.stop_model_download)
        source_actions.addWidget(self.test_model_source_button)
        source_actions.addWidget(self.download_model_button)
        source_actions.addWidget(self.stop_download_button)
        source_actions.addStretch(1)
        form.addRow("管理模型", self.model_manager_model_combo)
        form.addRow("模型来源", self.model_source_combo)
        form.addRow("自定义地址", self.custom_model_source)
        form.addRow("HTTP/HTTPS 代理", self.model_proxy)
        form.addRow("来源说明", self.model_source_note)
        form.addRow("", source_actions)
        form.addRow("下载进度", self.download_progress)
        form.addRow("下载状态", self.model_download_status)
        layout.addWidget(options)

        self.model_inventory_status = QLabel()
        self.model_inventory_status.setObjectName("mutedLabel")
        self.model_inventory_status.setWordWrap(True)
        layout.addWidget(self.model_inventory_status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.hide)
        layout.addWidget(buttons)
        self.model_manager_dialog = dialog
        self._model_source_changed()
        self.refresh_model_download_status()

    def show_model_manager(self) -> None:
        self._set_combo_data(self.model_manager_model_combo, self.model_combo.currentData())
        self.refresh_model_download_status()
        self.model_manager_dialog.show()
        self.model_manager_dialog.raise_()
        self.model_manager_dialog.activateWindow()

    def _build_cleanup_page(self) -> QWidget:
        page, layout = self._page_shell(
            "可选增强",
            "清洗口语化文本",
            "启用后仅向您配置的模型服务发送当前文本，不会上传 MP3 音频。每项修改均可拒绝或手工覆写。",
        )
        actions = QHBoxLayout()
        self.cleanup_enabled = QCheckBox("使用 LLM 清洗")
        self.cleanup_enabled.toggled.connect(self._mark_dirty)
        run = QPushButton("执行清洗建议")
        run.clicked.connect(self.run_cleanup)
        large_editor = QPushButton("大字号编辑清洗文本…")
        large_editor.clicked.connect(self.open_large_cleanup_editor)
        export_clean = QPushButton("导出当前清洗文本")
        export_clean.clicked.connect(
            lambda: self.export_text(self.cleaned_editor.toPlainText(), "清洗文本")
        )
        normalize = QPushButton("统一当前文本为简体")
        normalize.clicked.connect(self.normalize_current_project_simplified)
        actions.addWidget(self.cleanup_enabled)
        actions.addWidget(run)
        actions.addWidget(normalize)
        actions.addWidget(large_editor)
        actions.addWidget(export_clean)
        actions.addStretch(1)
        layout.addLayout(actions)
        dual = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("原文（只读）", objectName="subheading"))
        self.cleanup_source_editor = TextEdit(readonly=True)
        left_layout.addWidget(self.cleanup_source_editor)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("清洗后文本（可编辑）", objectName="subheading"))
        self.cleaned_editor = TextEdit("可直接编辑，或先配置兼容 OpenAI 的文本模型。")
        self.cleaned_editor.textChanged.connect(self._mark_dirty)
        right_layout.addWidget(self.cleaned_editor)
        dual.addWidget(left)
        dual.addWidget(right)
        dual.setSizes([500, 500])
        layout.addWidget(dual)
        apply = QPushButton("确认清洗文本，进入语义切分")
        apply.setObjectName("primaryButton")
        apply.clicked.connect(self.apply_cleaned_text)
        layout.addWidget(apply, alignment=Qt.AlignmentFlag.AlignLeft)
        return page

    def _build_segment_page(self) -> QWidget:
        page, layout = self._page_shell(
            "字幕编排",
            "按语义切分与换行",
            "推荐使用 LLM 按自然意群、语气和影视阅读节奏切分；15 字是阅读上限，不是每条字幕的凑字目标。软件负责锚点映射、风险提示和离线兜底。",
        )
        controls = QHBoxLayout()
        self.cjk_limit = QSpinBox()
        self.cjk_limit.setRange(6, 50)
        self.cjk_limit.setValue(DEFAULT_MAX_CJK_CHARS)
        self.latin_limit = QSpinBox()
        self.latin_limit.setRange(12, 100)
        self.latin_limit.setValue(DEFAULT_MAX_LATIN_CHARS)
        self.cjk_limit.valueChanged.connect(self._mark_dirty)
        self.latin_limit.valueChanged.connect(self._mark_dirty)
        self.subtitle_layout_combo = QComboBox()
        self.subtitle_layout_combo.addItem("每条仅一行（推荐）", SubtitleLayoutMode.SINGLE_LINE)
        self.subtitle_layout_combo.addItem("允许一条最多两行", SubtitleLayoutMode.TWO_LINES)
        self.subtitle_layout_combo.setToolTip(
            "单行模式会把 LLM 的换行解释为两条连续字幕；两行模式保留同一时间段内的两行同屏排版。"
        )
        self.subtitle_layout_combo.currentIndexChanged.connect(self._mark_dirty)
        self.simplified_chinese_check = QCheckBox("自动统一简体中文")
        self.simplified_chinese_check.setChecked(True)
        self.simplified_chinese_check.setToolTip(
            "默认把识别和 LLM 生成的繁体字转换为简体；手工在表格中输入的文字保持原样。"
        )
        self.simplified_chinese_check.toggled.connect(self._mark_dirty)
        local = QPushButton("离线兜底切分")
        local.setToolTip("仅在未配置 LLM 或需要快速离线初稿时使用；它不会像 LLM 一样理解复杂语义。")
        local.clicked.connect(self.run_local_segmentation)
        self.llm_segmentation_button = QPushButton("自然语义切分")
        self.llm_segmentation_button.setObjectName("primaryButton")
        self.llm_segmentation_button.setToolTip(
            "按可编辑的影视字幕提示词优先识别自然意群、短句同屏和两行均衡。"
        )
        self.llm_segmentation_button.clicked.connect(self.run_llm_segmentation)
        controls.addWidget(QLabel("每行最大汉字"))
        controls.addWidget(self.cjk_limit)
        controls.addWidget(QLabel("每行最大英文字符"))
        controls.addWidget(self.latin_limit)
        controls.addWidget(QLabel("显示模式"))
        controls.addWidget(self.subtitle_layout_combo)
        controls.addWidget(self.simplified_chinese_check)
        controls.addWidget(local)
        controls.addWidget(self.llm_segmentation_button)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(
            QLabel(
                "字幕条目（右键可修改、拆分、合并或确认复核；黄色表示时间锚点需复核）",
                objectName="subheading",
            )
        )
        self.entry_table = self._make_entry_table()
        layout.addWidget(self.entry_table)
        actions = QHBoxLayout()
        self.apply_table_button = QPushButton("应用切分修改并进入校对导出")
        self.apply_table_button.setObjectName("primaryButton")
        self.apply_table_button.setToolTip(
            "将表格文字、拆分合并与手工时间写入工程，重新映射原始词级锚点后进入第 05 步校对导出。"
        )
        self.apply_table_button.clicked.connect(self.apply_table_and_enter_review)
        self.split_entry_button = QPushButton("拆分为两条连续字幕")
        self.split_entry_button.clicked.connect(self.split_selected_entry)
        self.merge_entry_button = QPushButton("与下一条合并")
        self.merge_entry_button.clicked.connect(self.merge_selected_entry)
        self.confirm_review_button = QPushButton("确认已人工复核")
        self.confirm_review_button.clicked.connect(self.confirm_selected_review)
        export_segment = QPushButton("导出分行文本")
        export_segment.clicked.connect(self.export_segmented_text)
        actions.addWidget(self.apply_table_button)
        actions.addWidget(self.split_entry_button)
        actions.addWidget(self.merge_entry_button)
        actions.addWidget(self.confirm_review_button)
        actions.addWidget(export_segment)
        actions.addStretch(1)
        layout.addLayout(actions)
        return page

    def _build_review_page(self) -> QWidget:
        page, layout = self._page_shell(
            "交付校验",
            "复核时间码并导出 SRT",
            "偏移量仅在预览和导出阶段叠加；项目中始终保留原始锚点时间，便于以后波形精修。",
        )
        controls = QHBoxLayout()
        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(-20_000, 20_000)
        self.offset_spin.setSingleStep(10)
        self.offset_spin.setSuffix(" ms")
        self.offset_spin.valueChanged.connect(self._offset_changed)
        self.review_summary = QLabel("尚无字幕条目")
        validate = QPushButton("检查时间轴")
        validate.clicked.connect(self.show_validation)
        export = QPushButton("导出 SRT")
        export.setObjectName("primaryButton")
        export.clicked.connect(self.export_current_srt)
        controls.addWidget(QLabel("全局偏移量"))
        controls.addWidget(self.offset_spin)
        controls.addWidget(validate)
        controls.addWidget(export)
        controls.addStretch(1)
        layout.addLayout(controls)
        bilingual_controls = QHBoxLayout()
        self.primary_language_edit = QLineEdit("原文")
        self.primary_language_edit.setMaximumWidth(130)
        self.primary_language_edit.setToolTip(
            "原字幕文本的语言名称，仅用于双语显示说明与翻译请求。"
        )
        self.translation_language_edit = QComboBox()
        self.translation_language_edit.setMaximumWidth(190)
        for language in [
            "English（英语）",
            "日本語（日语）",
            "한국어（韩语）",
            "Français（法语）",
            "Deutsch（德语）",
            "Español（西班牙语）",
            "Русский（俄语）",
            "繁體中文（繁体中文）",
        ]:
            self.translation_language_edit.addItem(language, language)
        self.translation_language_edit.addItem("自定义语言…", "__custom_language__")
        self.translation_language_edit.setCurrentIndex(0)
        self._translation_language_last_index = 0
        self.translation_language_edit.setToolTip(
            "从常用语言中选择；最后一项“自定义语言…”会打开输入框，接受中文或英文语言名称。"
        )
        self.translate_button = QPushButton("LLM 批量翻译")
        self.translate_button.setObjectName("primaryButton")
        self.translate_button.setToolTip("按模型服务中设置的每批字符数批量翻译字幕，不会逐条请求。")
        self.translate_button.clicked.connect(self.run_llm_translation)
        self.translation_scope_combo = QComboBox()
        self.translation_scope_combo.addItem("仅翻译空白译文（保护人工校对）", "missing")
        self.translation_scope_combo.addItem("翻译全部并覆盖现有译文", "all")
        self.translation_scope_combo.setToolTip("重新翻译时可保留已由人工修改或已确认的译文。")
        self.subtitle_output_mode_combo = QComboBox()
        self.subtitle_output_mode_combo.addItem("导出原文", SubtitleOutputMode.PRIMARY)
        self.subtitle_output_mode_combo.addItem("仅导出译文", SubtitleOutputMode.TRANSLATION)
        self.subtitle_output_mode_combo.addItem("双语两行", SubtitleOutputMode.BILINGUAL)
        self.bilingual_order_combo = QComboBox()
        self.bilingual_order_combo.addItem("上：原文 / 下：译文", BilingualOrder.PRIMARY_TOP)
        self.bilingual_order_combo.addItem("上：译文 / 下：原文", BilingualOrder.TRANSLATION_TOP)
        self.subtitle_output_mode_combo.currentIndexChanged.connect(
            self._subtitle_output_settings_changed
        )
        self.bilingual_order_combo.currentIndexChanged.connect(
            self._subtitle_output_settings_changed
        )
        self.primary_language_edit.editingFinished.connect(self._subtitle_output_settings_changed)
        self.translation_language_edit.currentIndexChanged.connect(
            self._translation_language_selected
        )
        bilingual_controls.addWidget(QLabel("原文语言"))
        bilingual_controls.addWidget(self.primary_language_edit)
        bilingual_controls.addWidget(QLabel("目标语言"))
        bilingual_controls.addWidget(self.translation_language_edit)
        bilingual_controls.addWidget(self.translate_button)
        bilingual_controls.addWidget(QLabel("翻译范围"))
        bilingual_controls.addWidget(self.translation_scope_combo)
        bilingual_controls.addWidget(QLabel("导出模式"))
        bilingual_controls.addWidget(self.subtitle_output_mode_combo)
        bilingual_controls.addWidget(self.bilingual_order_combo)
        bilingual_controls.addStretch(1)
        layout.addLayout(bilingual_controls)
        layout.addWidget(self.review_summary)
        self.review_table = self._make_entry_table(readonly=True)
        layout.addWidget(self.review_table, 1)

        preview = QFrame()
        preview.setObjectName("audioPreviewPanel")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(18, 14, 18, 14)
        preview_layout.setSpacing(9)
        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("媒体字幕预览", objectName="subheading"))
        self.preview_status_label = QLabel("导入 MP3 或 MP4 后可预览")
        self.preview_status_label.setObjectName("mutedLabel")
        self.preview_video_select_button = QPushButton("选择预览视频…")
        self.preview_video_select_button.setEnabled(False)
        self.preview_video_select_button.setToolTip(
            "识别源为 MP3 时，可在此选择对应 MP4 作为校对画面；不会重新识别或改变锚点。"
        )
        self.preview_video_select_button.clicked.connect(self.select_preview_video)
        self.preview_video_button = QPushButton("打开视频预览")
        self.preview_video_button.setEnabled(False)
        self.preview_video_button.setToolTip("打开与校对时间轴同步的视频画面窗口。")
        self.preview_video_button.clicked.connect(self.open_video_preview)
        self.preview_video_clear_button = QPushButton("清除预览视频")
        self.preview_video_clear_button.setEnabled(False)
        self.preview_video_clear_button.clicked.connect(self.clear_preview_video)
        self.preview_audio_source_combo = QComboBox()
        self.preview_audio_source_combo.addItem("音频：视频音轨", PreviewAudioSource.VIDEO)
        self.preview_audio_source_combo.addItem("音频：识别源", PreviewAudioSource.RECOGNITION)
        self.preview_audio_source_combo.setToolTip(
            "外部 MP4 预览时，可选择听视频自带音轨或最初导入的识别源音频。"
        )
        self.preview_audio_source_combo.currentIndexChanged.connect(
            self._preview_audio_source_changed
        )
        preview_header.addWidget(self.preview_status_label, 1)
        preview_header.addWidget(self.preview_video_select_button)
        preview_header.addWidget(self.preview_video_button)
        preview_header.addWidget(self.preview_video_clear_button)
        preview_header.addWidget(self.preview_audio_source_combo)
        preview_layout.addLayout(preview_header)
        self.preview_subtitle_label = QLabel("当前字幕将在这里叠加显示")
        self.preview_subtitle_label.setObjectName("subtitlePreviewText")
        self.preview_subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_subtitle_label.setWordWrap(True)
        self.preview_subtitle_label.setMinimumHeight(62)
        preview_layout.addWidget(self.preview_subtitle_label)
        transport = QHBoxLayout()
        self.preview_play_button = QPushButton("播放")
        self.preview_play_button.setObjectName("primaryButton")
        self.preview_play_button.setEnabled(False)
        self.preview_play_button.clicked.connect(self._toggle_preview_playback)
        self.preview_insert_button = QPushButton("在此处插入字幕")
        self.preview_insert_button.setEnabled(False)
        self.preview_insert_button.setToolTip(
            "暂停播放后，在当前播放指针所在的无字幕空档插入一条人工定时字幕。"
        )
        self.preview_insert_button.clicked.connect(self.insert_subtitle_at_playback_position)
        self.preview_position_label = QLabel("00:00.000")
        self.preview_timeline = QSlider(Qt.Orientation.Horizontal)
        self.preview_timeline.setRange(0, 0)
        self.preview_timeline.setEnabled(False)
        self.preview_timeline.setToolTip("拖动可快速定位；播放时会自动高亮对应字幕行。")
        self.preview_timeline.sliderPressed.connect(self._preview_timeline_pressed)
        self.preview_timeline.sliderMoved.connect(self._preview_timeline_moved)
        self.preview_timeline.sliderReleased.connect(self._preview_timeline_released)
        self.preview_duration_label = QLabel("00:00.000")
        transport.addWidget(self.preview_play_button)
        transport.addWidget(self.preview_insert_button)
        transport.addWidget(self.preview_position_label)
        transport.addWidget(self.preview_timeline, 1)
        transport.addWidget(self.preview_duration_label)
        preview_layout.addLayout(transport)
        preview_layout.addWidget(
            QLabel(
                "提示：右键或双击表格可编辑字幕和时间；调整全局偏移量后，预览字幕会同步提前或延后。",
                objectName="mutedLabel",
            )
        )
        layout.addWidget(preview)
        return page

    def _build_merge_page(self) -> QWidget:
        page, layout = self._page_shell(
            "Plus · 长片工作流",
            "分段合并与全局时间轴",
            "导入每章 MP3 与对应 SRT 或弦字幕工程，为本地 0 秒设置全片起点；"
            "软件会统一换算、检查跨段冲突并重新编号导出全片 SRT。",
        )
        top = QHBoxLayout()
        self.merge_title_edit = QLineEdit(self.merge_project.title)
        self.merge_title_edit.setPlaceholderText("为分段合并工程命名")
        self.merge_title_edit.editingFinished.connect(self._apply_merge_title)
        new_button = QPushButton("新建合并工程")
        new_button.clicked.connect(self.new_merge_project)
        open_button = QPushButton("打开合并工程…")
        open_button.clicked.connect(self.open_merge_project)
        save_button = QPushButton("保存合并工程")
        save_button.clicked.connect(self.save_merge_project)
        top.addWidget(QLabel("合并工程名称"))
        top.addWidget(self.merge_title_edit, 1)
        top.addWidget(new_button)
        top.addWidget(open_button)
        top.addWidget(save_button)
        layout.addLayout(top)

        segment_group = QGroupBox("章节片段")
        segment_layout = QVBoxLayout(segment_group)
        segment_note = QLabel(
            "每段的本地时间都从 0 开始。请选择“绝对全片起点”，或让当前段连续接在上一段 MP3 时长之后。"
        )
        segment_note.setObjectName("mutedLabel")
        segment_note.setWordWrap(True)
        segment_layout.addWidget(segment_note)
        segment_actions = QHBoxLayout()
        add_srt = QPushButton("添加 SRT 片段…")
        add_srt.clicked.connect(self.add_merge_srt_segment)
        add_project = QPushButton("添加弦字幕工程…")
        add_project.clicked.connect(self.add_merge_project_segment)
        absolute = QPushButton("设置绝对起点…")
        absolute.clicked.connect(self.set_selected_merge_origin)
        continuous = QPushButton("与上一段连续…")
        continuous.clicked.connect(self.set_selected_merge_continuous)
        remove_segment = QPushButton("移除片段")
        remove_segment.clicked.connect(self.remove_selected_merge_segment)
        recalculate = QPushButton("重算连续片段")
        recalculate.clicked.connect(self.recalculate_merge_origins)
        segment_actions.addWidget(add_srt)
        segment_actions.addWidget(add_project)
        segment_actions.addWidget(absolute)
        segment_actions.addWidget(continuous)
        segment_actions.addWidget(remove_segment)
        segment_actions.addWidget(recalculate)
        segment_actions.addStretch(1)
        segment_layout.addLayout(segment_actions)
        self.merge_segment_table = QTableWidget(0, 7)
        self.merge_segment_table.setHorizontalHeaderLabels(
            ["#", "章节", "来源", "本地时长", "全片起点", "定位方式", "字幕数"]
        )
        self.merge_segment_table.verticalHeader().setVisible(False)
        self.merge_segment_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.merge_segment_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.merge_segment_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.merge_segment_table.setAlternatingRowColors(True)
        segment_header = self.merge_segment_table.horizontalHeader()
        segment_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        segment_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 7):
            segment_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        segment_layout.addWidget(self.merge_segment_table)
        layout.addWidget(segment_group)

        global_group = QGroupBox("全片字幕校对")
        global_layout = QVBoxLayout(global_group)
        controls = QHBoxLayout()
        self.merge_summary_label = QLabel("尚未导入片段")
        self.merge_summary_label.setObjectName("mutedLabel")
        validate = QPushButton("检查合并时间轴")
        validate.clicked.connect(self.show_merge_validation)
        export = QPushButton("导出全片 SRT")
        export.setObjectName("primaryButton")
        export.clicked.connect(self.export_merged_srt)
        controls.addWidget(self.merge_summary_label, 1)
        controls.addWidget(validate)
        controls.addWidget(export)
        global_layout.addLayout(controls)
        self.merge_global_table = QTableWidget(0, 7)
        self.merge_global_table.setHorizontalHeaderLabels(
            ["#", "章节", "全片开始", "全片结束", "本地开始", "本地结束", "字幕文本"]
        )
        self.merge_global_table.verticalHeader().setVisible(False)
        self.merge_global_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.merge_global_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.merge_global_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.merge_global_table.setAlternatingRowColors(True)
        global_header = self.merge_global_table.horizontalHeader()
        global_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        global_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(2, 6):
            global_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        global_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.merge_global_table.doubleClicked.connect(
            lambda index: self._edit_merge_entry(index.row())
        )
        self.merge_global_table.cellClicked.connect(
            lambda row, column: self._seek_merge_preview_to_global_row(row)
        )
        global_layout.addWidget(self.merge_global_table)
        layout.addWidget(global_group, 1)

        preview = QFrame()
        preview.setObjectName("audioPreviewPanel")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(18, 14, 18, 14)
        preview_layout.setSpacing(8)
        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("全片 MP3 预览", objectName="subheading"))
        self.merge_preview_status = QLabel("导入片段后可在全片时间轴定位")
        self.merge_preview_status.setObjectName("mutedLabel")
        preview_header.addWidget(self.merge_preview_status, 1)
        preview_layout.addLayout(preview_header)
        self.merge_preview_subtitle = QLabel("")
        self.merge_preview_subtitle.setObjectName("subtitlePreviewText")
        self.merge_preview_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.merge_preview_subtitle.setWordWrap(True)
        self.merge_preview_subtitle.setMinimumHeight(52)
        preview_layout.addWidget(self.merge_preview_subtitle)
        transport = QHBoxLayout()
        self.merge_preview_play_button = QPushButton("播放")
        self.merge_preview_play_button.setObjectName("primaryButton")
        self.merge_preview_play_button.clicked.connect(self._toggle_merge_preview_playback)
        self.merge_preview_position_label = QLabel("00:00:00.000")
        self.merge_preview_timeline = QSlider(Qt.Orientation.Horizontal)
        self.merge_preview_timeline.setRange(0, 0)
        self.merge_preview_timeline.sliderPressed.connect(self._merge_preview_timeline_pressed)
        self.merge_preview_timeline.sliderMoved.connect(self._merge_preview_timeline_moved)
        self.merge_preview_timeline.sliderReleased.connect(self._merge_preview_timeline_released)
        self.merge_preview_duration_label = QLabel("00:00:00.000")
        transport.addWidget(self.merge_preview_play_button)
        transport.addWidget(self.merge_preview_position_label)
        transport.addWidget(self.merge_preview_timeline, 1)
        transport.addWidget(self.merge_preview_duration_label)
        preview_layout.addLayout(transport)
        preview_layout.addWidget(
            QLabel(
                "提示：双击全片字幕行可修改文本和全片起止时间；点击任意行会定位到对应章节 MP3。",
                objectName="mutedLabel",
            )
        )
        layout.addWidget(preview)
        self._refresh_merge_workspace()
        return page

    @staticmethod
    def _format_global_time(milliseconds: int | None) -> str:
        return "待确认" if milliseconds is None else format_srt_time(milliseconds).replace(",", ".")

    def _apply_merge_title(self) -> None:
        title = self.merge_title_edit.text().strip()
        if title:
            self.merge_project.title = title

    def _selected_merge_segment_row(self) -> int | None:
        row = self.merge_segment_table.currentRow()
        return row if 0 <= row < len(self.merge_project.segments) else None

    def _add_merge_segment(self, segment: Any) -> None:
        self.merge_project.segments.append(segment)
        self._refresh_merge_workspace()
        self.statusBar().showMessage(f"已添加片段“{segment.title}”；请确认它的全片起点。", 7000)

    def add_merge_srt_segment(self) -> None:
        path = self._open_file_dialog("添加章节 SRT", "SRT 字幕 (*.srt)")
        if not path:
            return
        audio_path = self._open_file_dialog("可选：选择对应章节 MP3", "MP3 音频 (*.mp3)")
        try:
            self._add_merge_segment(segment_from_srt(path, audio_path or None))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, f"无法导入 SRT 片段：{exc}")

    def add_merge_project_segment(self) -> None:
        path = self._open_file_dialog("添加章节弦字幕工程", "弦字幕项目 (*.lyrasub)")
        if not path:
            return
        audio_path = self._open_file_dialog("可选：选择对应章节 MP3", "MP3 音频 (*.mp3)")
        try:
            self._add_merge_segment(segment_from_lyrasub(path, audio_path or None))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, f"无法导入章节工程：{exc}")

    def set_selected_merge_origin(self) -> None:
        row = self._selected_merge_segment_row()
        if row is None:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先选择一个章节片段。")
            return
        segment = self.merge_project.segments[row]
        default = self._format_global_time(segment.origin_ms or 0)
        value, accepted = QInputDialog.getText(
            self,
            "设置绝对全片起点",
            "请输入本段本地 00:00:00 对应的全片时间（HH:MM:SS.mmm）：",
            text=default,
        )
        if not accepted:
            return
        try:
            segment.origin_ms = parse_srt_time(value)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        segment.placement_mode = SegmentPlacementMode.ABSOLUTE
        self._refresh_merge_workspace()

    def set_selected_merge_continuous(self) -> None:
        row = self._selected_merge_segment_row()
        if row is None or row == 0:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先选择第二个或之后的章节片段。")
            return
        segment = self.merge_project.segments[row]
        gap, accepted = QInputDialog.getInt(
            self,
            "与上一段连续",
            "上一段 MP3 结束到本段开始的间隔（毫秒）：",
            segment.gap_before_ms,
            0,
            3_600_000,
            10,
        )
        if not accepted:
            return
        segment.placement_mode = SegmentPlacementMode.CONTINUOUS
        segment.gap_before_ms = gap
        issues = apply_continuous_origins(self.merge_project)
        self._refresh_merge_workspace()
        if issues:
            QMessageBox.warning(
                self, APP_DISPLAY_NAME, "\n".join(issue.message for issue in issues)
            )

    def remove_selected_merge_segment(self) -> None:
        row = self._selected_merge_segment_row()
        if row is None:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先选择要移除的章节片段。")
            return
        title = self.merge_project.segments[row].title
        response = QMessageBox.question(
            self,
            "移除章节片段",
            f"确定从合并工程移除“{title}”？原始 SRT、MP3 和章节工程不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        self.merge_project.segments.pop(row)
        self._refresh_merge_workspace()

    def recalculate_merge_origins(self) -> None:
        issues = apply_continuous_origins(self.merge_project)
        self._refresh_merge_workspace()
        if issues:
            QMessageBox.warning(
                self, APP_DISPLAY_NAME, "\n".join(issue.message for issue in issues)
            )
        else:
            self.statusBar().showMessage("已按上一段 MP3 时长和间隔重算连续片段起点。", 7000)

    def new_merge_project(self) -> None:
        self.merge_audio_player.stop()
        self.merge_project = MergeProject()
        self.merge_project_path = None
        self._refresh_merge_workspace()

    def open_merge_project(self) -> None:
        path = self._open_file_dialog("打开分段合并工程", "分段合并工程 (*.lyrasubmerge)")
        if not path:
            return
        try:
            self.merge_project = load_merge_project(path)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        self.merge_project_path = Path(path)
        self._refresh_merge_workspace()

    def save_merge_project(self) -> bool:
        self._apply_merge_title()
        if not self.merge_project_path:
            path = self._save_file_dialog(
                "保存分段合并工程",
                f"{self._filename_stem(self.merge_project.title)}.lyrasubmerge",
                "分段合并工程 (*.lyrasubmerge)",
            )
            if not path:
                return False
            self.merge_project_path = Path(path)
        try:
            saved = save_merge_project(self.merge_project, self.merge_project_path)
        except OSError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, f"无法保存分段合并工程：{exc}")
            return False
        self.merge_project_path = saved
        self.statusBar().showMessage(f"已保存分段合并工程：{saved}", 7000)
        return True

    def _refresh_merge_workspace(self) -> None:
        if not hasattr(self, "merge_segment_table"):
            return
        self.merge_title_edit.blockSignals(True)
        self.merge_title_edit.setText(self.merge_project.title)
        self.merge_title_edit.blockSignals(False)
        segments = self.merge_project.segments
        self.merge_segment_table.setRowCount(len(segments))
        for row, segment in enumerate(segments):
            duration = segment.audio_duration_ms or segment.subtitle_end_ms
            values = [
                str(row + 1),
                segment.title,
                "工程" if segment.source_kind.value == "lyrasub_project" else "SRT",
                self._format_global_time(duration) if duration is not None else "—",
                self._format_global_time(segment.origin_ms),
                "与上一段连续"
                if segment.placement_mode == SegmentPlacementMode.CONTINUOUS
                else "绝对起点",
                str(len(segment.entries)),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if segment.origin_ms is None:
                    item.setBackground(QColor("#6b5123"))
                    item.setForeground(QColor("#fff2ca"))
                self.merge_segment_table.setItem(row, column, item)
        self._merge_global_entries = build_global_entries(self.merge_project)
        issues = validate_merge_project(self.merge_project)
        issue_entries = {issue.entry_id for issue in issues if issue.entry_id}
        self.merge_global_table.setRowCount(len(self._merge_global_entries))
        for row, entry in enumerate(self._merge_global_entries):
            values = [
                str(row + 1),
                entry.segment_title,
                self._format_global_time(entry.global_start_ms),
                self._format_global_time(entry.global_end_ms),
                self._format_global_time(entry.local_start_ms),
                self._format_global_time(entry.local_end_ms),
                entry.text,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if entry.entry_id in issue_entries:
                    item.setBackground(QColor("#6b5123"))
                    item.setForeground(QColor("#fff2ca"))
                self.merge_global_table.setItem(row, column, item)
            self.merge_global_table.setRowHeight(row, 30 * max(1, entry.text.count("\n") + 1) + 6)
        maximum = max((entry.global_end_ms for entry in self._merge_global_entries), default=0)
        for segment in segments:
            if segment.origin_ms is not None and segment.audio_duration_ms is not None:
                maximum = max(maximum, segment.origin_ms + segment.audio_duration_ms)
        self.merge_preview_timeline.setRange(0, maximum)
        self.merge_preview_duration_label.setText(self._format_global_time(maximum))
        errors = sum(1 for issue in issues if issue.severity == "error")
        warnings = sum(1 for issue in issues if issue.severity == "warning")
        self.merge_summary_label.setText(
            f"{len(segments)} 个片段，{len(self._merge_global_entries)} 条全片字幕；"
            f"{errors} 项错误，{warnings} 项警告。"
        )
        self._update_merge_preview_subtitle(self.merge_preview_timeline.value())

    def show_merge_validation(self) -> None:
        issues = [
            *apply_continuous_origins(self.merge_project),
            *validate_merge_project(self.merge_project),
        ]
        self._refresh_merge_workspace()
        if not issues:
            QMessageBox.information(self, APP_DISPLAY_NAME, "分段合并时间轴检查通过。")
            return
        text = "\n".join(f"[{issue.severity}] {issue.message}" for issue in issues)
        QMessageBox.warning(self, "分段合并时间轴检查", text)

    def export_merged_srt(self) -> None:
        continuous_issues = apply_continuous_origins(self.merge_project)
        issues = [*continuous_issues, *validate_merge_project(self.merge_project)]
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            QMessageBox.warning(
                self,
                "无法导出全片 SRT",
                "请先处理以下问题：\n" + "\n".join(issue.message for issue in errors),
            )
            return
        warnings = [issue for issue in issues if issue.severity == "warning"]
        if warnings:
            response = QMessageBox.question(
                self,
                "存在时间轴警告",
                "发现跨段或片段内时间重叠。确认已经复核后仍要导出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        path = self._save_file_dialog(
            "导出全片 SRT",
            f"{self._filename_stem(self.merge_project.title)}-全片.srt",
            "SRT 字幕 (*.srt)",
        )
        if not path:
            return
        target = Path(path).with_suffix(".srt")
        target.write_text(render_merged_srt(self.merge_project), encoding="utf-8", newline="\n")
        self.statusBar().showMessage(f"已导出全片 SRT：{target}", 8000)

    def _merge_segment_at_global_position(self, position_ms: int) -> Any | None:
        for segment in self.merge_project.segments:
            if segment.origin_ms is None:
                continue
            duration = segment.audio_duration_ms or segment.subtitle_end_ms or 0
            if segment.origin_ms <= position_ms <= segment.origin_ms + duration:
                return segment
        return None

    def _update_merge_preview_subtitle(self, position_ms: int) -> None:
        self.merge_preview_position_label.setText(self._format_global_time(position_ms))
        current = next(
            (
                entry
                for entry in self._merge_global_entries
                if entry.global_start_ms <= position_ms <= entry.global_end_ms
            ),
            None,
        )
        self.merge_preview_subtitle.setText(current.text if current else "")
        if current:
            row = self._merge_global_entries.index(current)
            self.merge_global_table.selectRow(row)

    def _load_merge_preview_segment(self, segment: Any, position_ms: int) -> bool:
        audio_path = Path(segment.audio_path) if segment.audio_path else None
        if not audio_path or not audio_path.is_file():
            self.merge_preview_status.setText("该片段没有可用 MP3；可校对时间码但无法试听。")
            return False
        path = str(audio_path.resolve())
        if path != self._merge_preview_path:
            self.merge_audio_player.stop()
            self.merge_audio_player.setSource(QUrl.fromLocalFile(path))
            self._merge_preview_path = path
            self._merge_preview_segment_id = segment.id
        self.merge_audio_player.setPosition(max(0, position_ms - (segment.origin_ms or 0)))
        return True

    def _seek_merge_preview_to_global_row(self, row: int) -> None:
        if row < 0 or row >= len(self._merge_global_entries):
            return
        entry = self._merge_global_entries[row]
        self.merge_preview_timeline.setValue(entry.global_start_ms)
        segment = next(
            (item for item in self.merge_project.segments if item.id == entry.segment_id), None
        )
        if segment:
            self._load_merge_preview_segment(segment, entry.global_start_ms)
        self._update_merge_preview_subtitle(entry.global_start_ms)

    def _merge_preview_position_changed(self, local_position_ms: int) -> None:
        if self._merge_timeline_scrubbing or self._merge_preview_segment_id is None:
            return
        segment = next(
            (
                item
                for item in self.merge_project.segments
                if item.id == self._merge_preview_segment_id
            ),
            None,
        )
        if not segment or segment.origin_ms is None:
            return
        global_position = segment.origin_ms + local_position_ms
        self.merge_preview_timeline.setValue(global_position)
        self._update_merge_preview_subtitle(global_position)

    def _merge_preview_duration_changed(self, _duration_ms: int) -> None:
        return

    def _merge_preview_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.merge_preview_play_button.setText(
            "暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放"
        )

    def _toggle_merge_preview_playback(self) -> None:
        position = self.merge_preview_timeline.value()
        segment = self._merge_segment_at_global_position(position)
        if not segment or not self._load_merge_preview_segment(segment, position):
            QMessageBox.information(
                self, APP_DISPLAY_NAME, "请先在此全片位置导入对应章节的 MP3 文件。"
            )
            return
        if self.merge_audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.merge_audio_player.pause()
        else:
            self.merge_audio_player.play()

    def _merge_preview_timeline_pressed(self) -> None:
        self._merge_timeline_scrubbing = True

    def _merge_preview_timeline_moved(self, position_ms: int) -> None:
        self._update_merge_preview_subtitle(position_ms)

    def _merge_preview_timeline_released(self) -> None:
        position = self.merge_preview_timeline.value()
        segment = self._merge_segment_at_global_position(position)
        if segment:
            self._load_merge_preview_segment(segment, position)
        self._merge_timeline_scrubbing = False
        self._update_merge_preview_subtitle(position)

    def _edit_merge_entry(self, row: int) -> None:
        if row < 0 or row >= len(self._merge_global_entries):
            return
        global_entry = self._merge_global_entries[row]
        segment = next(
            (item for item in self.merge_project.segments if item.id == global_entry.segment_id),
            None,
        )
        if not segment or segment.origin_ms is None:
            return
        source = next((item for item in segment.entries if item.id == global_entry.entry_id), None)
        if source is None:
            return
        text, accepted = QInputDialog.getMultiLineText(
            self, "修改全片字幕", "字幕文本：", source.text
        )
        if not accepted or not text.strip():
            return
        start_raw, accepted = QInputDialog.getText(
            self,
            "修改全片字幕开始时间",
            "全片开始时间（HH:MM:SS.mmm）：",
            text=self._format_global_time(global_entry.global_start_ms),
        )
        if not accepted:
            return
        end_raw, accepted = QInputDialog.getText(
            self,
            "修改全片字幕结束时间",
            "全片结束时间（HH:MM:SS.mmm）：",
            text=self._format_global_time(global_entry.global_end_ms),
        )
        if not accepted:
            return
        try:
            start = parse_srt_time(start_raw)
            end = parse_srt_time(end_raw)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        if end <= start:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "结束时间必须晚于开始时间。")
            return
        source.text = text.strip()
        source.local_start_ms = start - segment.origin_ms
        source.local_end_ms = end - segment.origin_ms
        self._refresh_merge_workspace()

    def _make_entry_table(self, readonly: bool = False) -> QTableWidget:
        review_table = readonly
        headers = (
            ["#", "原文字幕", "译文（双击编辑）", "开始", "结束", "锚点状态"]
            if review_table
            else ["#", "字幕文本", "开始", "结束", "锚点状态"]
        )
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setWordWrap(True)
        table.setToolTip(
            "校对表可双击原文或译文分别修改；修改译文不会影响原文、时间轴或语音锚点。"
            if review_table
            else "双击字幕文字可修改内容；右键还可拆分、合并或确认复核。"
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, len(headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        table.setColumnWidth(1, 400 if review_table else 520)
        if review_table:
            table.setColumnWidth(2, 400)
            table.setColumnWidth(3, 104)
            table.setColumnWidth(4, 104)
            table.setColumnWidth(5, 152)
        else:
            table.setColumnWidth(2, 104)
            table.setColumnWidth(3, 104)
            table.setColumnWidth(4, 152)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda pos, source=table: self._show_entry_context_menu(source, pos)
        )
        table.doubleClicked.connect(
            lambda index, source=table: self._on_entry_double_clicked(
                index.row(), source, index.column()
            )
        )
        table.cellClicked.connect(
            lambda row, column, source=table: self._on_entry_table_cell_clicked(source, row, column)
        )
        return table

    def _select_entry_row(self, row: int) -> bool:
        if row < 0 or row >= self.entry_table.rowCount():
            return False
        self.entry_table.selectRow(row)
        return True

    def _on_entry_double_clicked(self, row: int, source: QTableWidget, column: int) -> None:
        if row < 0 or not self._select_entry_row(row):
            return
        if source is self.review_table:
            if column == 2:
                self._edit_entry_translation_dialog(row)
                return
            if column in {3, 4}:
                self._edit_entry_timing_dialog(row)
                return
        self._edit_entry_text_dialog(row)

    def _on_entry_table_cell_clicked(self, source: QTableWidget, row: int, column: int) -> None:
        if source is self.review_table and column == 0:
            self._seek_preview_to_entry(row)

    def _show_entry_context_menu(self, table: QTableWidget, pos: Any) -> None:
        item = table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        table.selectRow(row)
        if not self._select_entry_row(row):
            return
        menu = QMenu(table)
        edit_action = menu.addAction("修改文字…")
        translation_action = None
        if table is self.review_table:
            translation_action = menu.addAction("修改译文…")
        split_action = menu.addAction("拆分为两条")
        merge_action = menu.addAction("与下一条合并")
        timing_action = None
        insert_action = None
        if table is self.review_table:
            menu.addSeparator()
            timing_action = menu.addAction("调整起止时间…")
            insert_action = menu.addAction("插入下一行…")
        menu.addSeparator()
        confirm_action = menu.addAction("确认已人工复核")
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        if chosen is edit_action:
            self._edit_entry_text_dialog(row)
        elif chosen is translation_action:
            self._edit_entry_translation_dialog(row)
        elif chosen is split_action:
            self.split_selected_entry(auto_apply=True)
        elif chosen is merge_action:
            self.merge_selected_entry(auto_apply=True)
        elif chosen is timing_action:
            self._edit_entry_timing_dialog(row)
        elif chosen is insert_action:
            self.insert_subtitle_after_row(row)
        elif chosen is confirm_action:
            self.confirm_selected_review()

    def _edit_entry_text_dialog(self, row: int) -> None:
        if row < 0 or row >= self.entry_table.rowCount():
            return
        item = self.entry_table.item(row, 1)
        if item is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"修改字幕文字 · 第 {row + 1} 条")
        dialog.resize(680, 360)
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "可输入一行字幕；如当前项目允许两行同屏，也可用换行分隔两行。保存后会立即重建文字与原始词级锚点的映射。"
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)
        editor = QPlainTextEdit(item.text())
        editor.setMinimumHeight(190)
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text = editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "字幕文字不能为空。")
            return
        if text == item.text():
            return
        self._table_refreshing = True
        item.setText(text)
        self._table_refreshing = False
        self.apply_table_entries()

    def _edit_entry_translation_dialog(self, row: int) -> None:
        if row < 0 or row >= len(self.project.entries):
            return
        entry = self.project.entries[row]
        dialog = QDialog(self)
        dialog.setWindowTitle(f"修改译文 · 第 {row + 1} 条")
        dialog.resize(720, 400)
        layout = QVBoxLayout(dialog)
        source = QLabel(f"原文：{entry.text}")
        source.setWordWrap(True)
        source.setObjectName("mutedLabel")
        layout.addWidget(source)
        note = QLabel("此处只修改译文，不会改变原文、字幕时间轴或语音锚点。留空可标记为未翻译。")
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)
        editor = QPlainTextEdit(entry.translation_text)
        editor.setMinimumHeight(220)
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        translation = editor.toPlainText().strip()
        if translation == entry.translation_text:
            return
        entry.translation_text = translation
        self._refresh_tables()
        self._update_preview_subtitle(self.audio_player.position(), scroll=False)
        self._mark_dirty()
        self.statusBar().showMessage(f"已更新第 {row + 1} 条译文；原文、时间轴和锚点未改变。", 7000)

    def _timing_upper_limit(self) -> int:
        known_duration = self.project.source.duration_ms or self.audio_player.duration()
        return max(60_000, known_duration or 0, 86_400_000)

    def _entry_timing_fence(self, row: int) -> tuple[int, int | None, int | None, int | None]:
        previous_end = self.project.entries[row - 1].effective_end_ms if row > 0 else None
        next_start = (
            self.project.entries[row + 1].effective_start_ms
            if row + 1 < len(self.project.entries)
            else None
        )
        media_end = self.project.source.duration_ms or self.audio_player.duration() or None
        lower = max(0, previous_end or 0)
        candidates = [value for value in (next_start, media_end) if value is not None]
        upper = min(candidates) if candidates else None
        return lower, upper, previous_end, next_start

    def _validate_timing_bounds(
        self,
        start_ms: int,
        end_ms: int,
        lower: int,
        upper: int | None,
        previous_end: int | None,
        next_start: int | None,
    ) -> str | None:
        if start_ms < 0:
            return "开始时间不能小于 0 ms。"
        if end_ms <= start_ms:
            return "结束时间必须晚于开始时间，字幕需要保留正时长。"
        if start_ms < lower:
            return f"开始时间不得早于上一条字幕的结束时间（{previous_end} ms）。"
        if upper is not None and end_ms > upper:
            if next_start is not None and upper == next_start:
                return f"结束时间不得晚于下一条字幕的开始时间（{next_start} ms）。"
            return f"结束时间不得晚于音频总时长（{upper} ms）。"
        return None

    def _validate_entry_timing(self, row: int, start_ms: int, end_ms: int) -> str | None:
        lower, upper, previous_end, next_start = self._entry_timing_fence(row)
        return self._validate_timing_bounds(
            start_ms, end_ms, lower, upper, previous_end, next_start
        )

    def _save_manual_entry_timing(self, row: int, start_ms: int, end_ms: int) -> bool:
        error = self._validate_entry_timing(row, start_ms, end_ms)
        if error:
            QMessageBox.warning(self, "时间围栏", error)
            return False
        entry = self.project.entries[row]
        entry.manual_start_ms = start_ms
        entry.manual_end_ms = end_ms
        entry.review_confirmed = True
        self._refresh_all()
        self._mark_dirty()
        self.statusBar().showMessage(
            f"已保存第 {row + 1} 条手工时间：{start_ms}–{end_ms} ms。", 6000
        )
        return True

    def _edit_entry_timing_dialog(self, row: int) -> None:
        if row < 0 or row >= len(self.project.entries):
            return
        entry = self.project.entries[row]
        lower, upper, previous_end, next_start = self._entry_timing_fence(row)
        start = entry.effective_start_ms
        end = entry.effective_end_ms
        if start is None:
            start = lower
        if end is None or end <= start:
            end = min(start + 2_000, upper) if upper is not None else start + 2_000
        if end <= start:
            QMessageBox.warning(
                self,
                "无法调整时间",
                "相邻字幕之间没有可用的正时长空档；请先调整相邻字幕，或插入到其他位置。",
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"调整起止时间 · 第 {row + 1} 条")
        dialog.resize(560, 290)
        layout = QVBoxLayout(dialog)
        upper_text = "未限制" if upper is None else f"{upper} ms"
        note = QLabel(
            "时间围栏："
            f"开始不得早于 {lower} ms；结束不得晚于 {upper_text}。"
            "保存后本条会标记为人工定时。"
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)
        if previous_end is not None or next_start is not None:
            neighbor_note = QLabel(
                f"上一条结束：{previous_end if previous_end is not None else '—'} ms；"
                f"下一条开始：{next_start if next_start is not None else '—'} ms"
            )
            neighbor_note.setObjectName("mutedLabel")
            layout.addWidget(neighbor_note)
        form = QFormLayout()
        start_spin = QSpinBox()
        end_spin = QSpinBox()
        limit = self._timing_upper_limit()
        start_spin.setRange(0, limit)
        end_spin.setRange(0, limit)
        start_spin.setSuffix(" ms")
        end_spin.setSuffix(" ms")
        start_spin.setValue(start)
        end_spin.setValue(end)
        form.addRow("开始时间", start_spin)
        form.addRow("结束时间", end_spin)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_manual_entry_timing(row, start_spin.value(), end_spin.value())

    def _insertion_timing_fence(self, index: int) -> tuple[int, int | None, int | None, int | None]:
        previous_end = self.project.entries[index - 1].effective_end_ms if index > 0 else None
        next_start = (
            self.project.entries[index].effective_start_ms
            if index < len(self.project.entries)
            else None
        )
        media_end = self.project.source.duration_ms or self.audio_player.duration() or None
        lower = max(0, previous_end or 0)
        candidates = [value for value in (next_start, media_end) if value is not None]
        upper = min(candidates) if candidates else None
        return lower, upper, previous_end, next_start

    def _insertion_index_at_position(self, position_ms: int) -> int | None:
        for row, entry in enumerate(self.project.entries):
            start = entry.effective_start_ms
            end = entry.effective_end_ms
            if start is not None and end is not None and start <= position_ms <= end:
                return None
            if start is not None and position_ms < start:
                return row
        return len(self.project.entries)

    def _insert_subtitle_dialog(self, index: int, preferred_start_ms: int, title: str) -> None:
        lower, upper, previous_end, next_start = self._insertion_timing_fence(index)
        start_ms = max(lower, preferred_start_ms)
        if upper is not None and start_ms >= upper:
            QMessageBox.warning(
                self,
                "没有可插入的空档",
                "目标位置与相邻字幕之间没有可用于新字幕的正时长空档。",
            )
            return
        end_ms = min(start_ms + 2_000, upper) if upper is not None else start_ms + 2_000
        error = self._validate_timing_bounds(
            start_ms, end_ms, lower, upper, previous_end, next_start
        )
        if error:
            QMessageBox.warning(self, "无法插入字幕", error)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(660, 330)
        layout = QVBoxLayout(dialog)
        upper_text = "未限制" if upper is None else f"{upper} ms"
        note = QLabel(
            f"将插入第 {index + 1} 条字幕，初始时间为 {start_ms}–{end_ms} ms。"
            f"时间围栏：{lower}–{upper_text}。"
            "这是一条人工定时字幕，不会改写原始语音锚点。"
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)
        editor = QPlainTextEdit()
        editor.setPlaceholderText("输入要在静音或音乐段落中显示的字幕文字…")
        editor.setMinimumHeight(160)
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        text = editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "插入字幕时必须输入文字。")
            return
        self.project.entries.insert(
            index,
            SubtitleEntry(
                text=text,
                manual_start_ms=start_ms,
                manual_end_ms=end_ms,
                review_confirmed=True,
            ),
        )
        self._refresh_all()
        self._mark_dirty()
        self.review_table.selectRow(index)
        self._seek_preview_to_entry(index)
        self.statusBar().showMessage(
            f"已在第 {index + 1} 行插入人工定时字幕：{start_ms}–{end_ms} ms。", 7000
        )

    def insert_subtitle_at_playback_position(self) -> None:
        if self.audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.audio_player.pause()
        position_ms = self.audio_player.position()
        index = self._insertion_index_at_position(position_ms)
        if index is None:
            QMessageBox.information(
                self,
                "当前位置已有字幕",
                "播放指针位于既有字幕时间段内。请移动到静音或音乐空档后再插入。",
            )
            return
        self._insert_subtitle_dialog(index, position_ms, "在当前播放位置插入字幕")

    def insert_subtitle_after_row(self, row: int) -> None:
        if row < 0 or row >= len(self.project.entries):
            return
        previous_end = self.project.entries[row].effective_end_ms
        if previous_end is None:
            QMessageBox.warning(
                self,
                "无法插入下一行",
                "当前字幕没有有效结束时间；请先调整其起止时间，再插入下一行。",
            )
            return
        self._insert_subtitle_dialog(row + 1, previous_end, f"在第 {row + 1} 条后插入字幕")

    def _table_item_edited(self, _item: QTableWidgetItem) -> None:
        if not self._table_refreshing:
            self._mark_dirty()

    def _build_inspector(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("inspectorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 22, 18, 18)
        layout.addWidget(QLabel("项目检查器", objectName="inspectorTitle"))
        layout.addWidget(QLabel("本地素材与时间轴状态", objectName="mutedLabel"))
        source_group = QGroupBox("素材")
        source_form = QFormLayout(source_group)
        self.inspector_file = QLabel("—")
        self.inspector_language = QLabel("—")
        self.inspector_device = QLabel("—")
        source_form.addRow("音频", self.inspector_file)
        source_form.addRow("语言", self.inspector_language)
        source_form.addRow("设备", self.inspector_device)
        layout.addWidget(source_group)
        llm_group = QGroupBox("文本模型服务")
        llm_form = QFormLayout(llm_group)
        self.inspector_llm_profile = QLabel("未配置")
        self.inspector_llm_model = QLabel("—")
        self.inspector_llm_profile.setWordWrap(True)
        self.inspector_llm_model.setWordWrap(True)
        llm_form.addRow("当前 Profile", self.inspector_llm_profile)
        llm_form.addRow("当前模型", self.inspector_llm_model)
        layout.addWidget(llm_group)
        check_group = QGroupBox("时间轴")
        check_form = QFormLayout(check_group)
        self.inspector_entries = QLabel("0")
        self.inspector_risk = QLabel("0 条待复核")
        self.inspector_offset = QLabel("0 ms")
        check_form.addRow("字幕条数", self.inspector_entries)
        check_form.addRow("风险提示", self.inspector_risk)
        check_form.addRow("导出偏移", self.inspector_offset)
        layout.addWidget(check_group)
        llm_button = QPushButton("配置文本模型服务")
        llm_button.clicked.connect(self.configure_llm)
        layout.addWidget(llm_button)
        layout.addStretch(1)
        return panel

    def _build_actions(self) -> None:
        bar = QToolBar("项目")
        bar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        actions: list[tuple[str, QKeySequence | None, Callable[[], None]]] = [
            ("新建", QKeySequence.StandardKey.New, self.new_project),
            ("打开", QKeySequence.StandardKey.Open, self.open_project),
            ("保存", QKeySequence.StandardKey.Save, self.save_project),
            ("另存为", QKeySequence("Ctrl+Shift+S"), self.save_project_as),
            ("导入 MP3 / MP4", None, self.import_audio),
            ("导入 SRT", None, self.import_srt),
            ("导出 SRT", None, self.export_current_srt),
            ("模型管理", None, self.show_model_manager),
            ("模型服务", None, self.configure_llm),
            ("自动保存", None, self.configure_autosave),
            ("关于", None, self.show_about),
        ]
        for label, shortcut, callback in actions:
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(callback)
            bar.addAction(action)

    def _configure_autosave_timer(self) -> None:
        self._autosave_timer.stop()
        if self._autosave_interval_seconds <= 0:
            return
        self._autosave_timer.setInterval(self._autosave_interval_seconds * 1000)
        self._autosave_timer.start()

    def configure_autosave(self) -> None:
        if self.project_path:
            location = str(autosave_path(self.project_path))
        else:
            location = "工程尚未首次保存；请先保存 .lyrasub 工程，恢复文件将与它位于同一目录。"
        dialog = AutosaveSettingsDialog(self, self._autosave_interval_seconds, location)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        value = dialog.interval.value()
        self._autosave_interval_seconds = 0 if value == 0 else max(15, value)
        self.local_settings.setValue(
            "project/autosave_interval_seconds", self._autosave_interval_seconds
        )
        self._configure_autosave_timer()
        if self._autosave_interval_seconds:
            self.statusBar().showMessage(
                f"自动保存已设为每 {self._autosave_interval_seconds} 秒；恢复文件与工程位于同一目录。",
                7000,
            )
        else:
            self.statusBar().showMessage("自动保存已关闭；请及时手动保存工程。", 7000)

    def open_large_cleanup_editor(self) -> None:
        dialog = LargeTextEditorDialog(self, self.cleaned_editor.toPlainText())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.cleaned_editor.setPlainText(dialog.edited_text())
        self._mark_dirty()
        self.statusBar().showMessage(
            "已从大字号编辑窗口写回清洗文本；确认后可重新进行语义切分。", 6000
        )

    def _change_page(self, index: int) -> None:
        self.pages.setCurrentIndex(max(0, index))

    def toggle_inspector(self, collapsed: bool) -> None:
        if collapsed:
            self._inspector_width = self.workspace.sizes()[2]
            self.inspector_panel.hide()
            self.workspace.setSizes([224, max(900, self.width() - 224), 0])
            self.inspector_toggle.setText("显示检查器")
            return
        self.inspector_panel.show()
        width = getattr(self, "_inspector_width", 300) or 300
        self.workspace.setSizes([224, max(720, self.width() - 224 - width), width])
        self.inspector_toggle.setText("隐藏检查器")

    def _mark_dirty(self) -> None:
        self._project_dirty = True
        self.setWindowTitle(f"* {APP_DISPLAY_NAME} {VERSION} — {self.project.title}")

    def _clear_dirty_marker(self) -> None:
        self._project_dirty = False
        self.setWindowTitle(f"{APP_DISPLAY_NAME} {VERSION} — {self.project.title}")

    @staticmethod
    def _filename_stem(title: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title).strip(" .")
        return cleaned or "未命名项目"

    def _dialog_directory(self) -> str:
        """Return the last successful file-dialog directory, with a useful fallback."""
        stored = str(self.local_settings.value("files/last_directory", "")).strip()
        if stored and Path(stored).is_dir():
            return stored
        if self.project_path is not None and self.project_path.parent.is_dir():
            return str(self.project_path.parent)
        if self.merge_project_path is not None and self.merge_project_path.parent.is_dir():
            return str(self.merge_project_path.parent)
        return str(Path.home())

    def _remember_dialog_path(self, path: str) -> None:
        if not path:
            return
        directory = Path(path).expanduser().parent
        if directory.is_dir():
            self.local_settings.setValue("files/last_directory", str(directory))

    def _open_file_dialog(self, title: str, file_filter: str) -> str:
        path, _ = QFileDialog.getOpenFileName(self, title, self._dialog_directory(), file_filter)
        self._remember_dialog_path(path)
        return path

    def _save_file_dialog(self, title: str, default_name: str, file_filter: str) -> str:
        initial_path = str(Path(self._dialog_directory()) / default_name)
        path, _ = QFileDialog.getSaveFileName(self, title, initial_path, file_filter)
        self._remember_dialog_path(path)
        return path

    def apply_project_title(self) -> None:
        title = self.project_name_edit.text().strip()
        if not title:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "项目名称不能为空。")
            self.project_name_edit.blockSignals(True)
            self.project_name_edit.setText(self.project.title)
            self.project_name_edit.blockSignals(False)
            return
        if title == self.project.title:
            return
        self.project.title = title
        self._mark_dirty()
        self.statusBar().showMessage(f"项目已命名为“{title}”；保存和导出将使用此名称。", 6000)

    def _refresh_llm_inspector(self) -> None:
        profile = ProfileStore().active_profile()
        if profile is None:
            self.inspector_llm_profile.setText("未配置")
            self.inspector_llm_model.setText("—")
            return
        self.inspector_llm_profile.setText(profile.name or profile.id)
        key_available = bool(profile.api_key_ref and SecretStore().get(profile.api_key_ref))
        availability = "密钥可用" if key_available else "API 密钥不可用"
        self.inspector_llm_model.setText(f"{profile.model}（{availability}）")

    def _refresh_all(self) -> None:
        self.project_name_edit.blockSignals(True)
        self.project_name_edit.setText(self.project.title)
        self.project_name_edit.blockSignals(False)
        source = self.project.source
        self.audio_path_label.setText(source.path or "尚未选择音频文件")
        self.inspector_file.setText(Path(source.path).name if source.path else "—")
        self.inspector_language.setText(
            source.detected_language or self.project.settings.language_mode.value
        )
        self.inspector_device.setText(self.project.settings.device.value)
        self._refresh_llm_inspector()
        self._sync_preview_source()
        self.inspector_entries.setText(str(len(self.project.entries)))
        self.inspector_offset.setText(f"{self.project.settings.offset_ms:+d} ms")
        self.offset_spin.blockSignals(True)
        self.offset_spin.setValue(self.project.settings.offset_ms)
        self.offset_spin.blockSignals(False)
        self.cjk_limit.setValue(self.project.settings.max_cjk_chars)
        self.latin_limit.setValue(self.project.settings.max_latin_chars)
        if self.project.settings.subtitle_output_mode == SubtitleOutputMode.BILINGUAL:
            self.project.settings.subtitle_layout = SubtitleLayoutMode.TWO_LINES
        self._set_combo_data(self.subtitle_layout_combo, self.project.settings.subtitle_layout)
        self.simplified_chinese_check.blockSignals(True)
        self.simplified_chinese_check.setChecked(self.project.settings.normalize_simplified_chinese)
        self.simplified_chinese_check.blockSignals(False)
        self.subtitle_output_mode_combo.blockSignals(True)
        self._set_combo_data(
            self.subtitle_output_mode_combo, self.project.settings.subtitle_output_mode
        )
        self.subtitle_output_mode_combo.blockSignals(False)
        self.bilingual_order_combo.blockSignals(True)
        self._set_combo_data(self.bilingual_order_combo, self.project.settings.bilingual_order)
        self.bilingual_order_combo.blockSignals(False)
        self.primary_language_edit.setText(self.project.settings.primary_language_label)
        self.translation_language_edit.blockSignals(True)
        self._set_translation_language(self.project.settings.translation_language_label)
        self.translation_language_edit.blockSignals(False)
        self.preview_audio_source_combo.blockSignals(True)
        self._set_combo_data(
            self.preview_audio_source_combo, self.project.settings.preview_audio_source
        )
        self.preview_audio_source_combo.blockSignals(False)
        self.bilingual_order_combo.setEnabled(
            self.project.settings.subtitle_output_mode == SubtitleOutputMode.BILINGUAL
        )
        bilingual_preview = (
            self.project.settings.subtitle_output_mode == SubtitleOutputMode.BILINGUAL
        )
        self.preview_subtitle_label.setMinimumHeight(102 if bilingual_preview else 62)
        self._refresh_tables()
        self._preview_active_row = None
        self._update_preview_subtitle(self.audio_player.position(), scroll=False)
        self._refresh_risk()
        self._clear_dirty_marker()

    def _capture_editor_draft(self) -> None:
        """Snapshot visible, unconfirmed controls without mutating confirmed project data."""
        rows: list[dict[str, str]] = []
        for row in range(self.entry_table.rowCount()):
            rows.append(
                {
                    "text": self.entry_table.item(row, 1).text()
                    if self.entry_table.item(row, 1)
                    else "",
                    "start": self.entry_table.item(row, 2).text()
                    if self.entry_table.item(row, 2)
                    else "",
                    "end": self.entry_table.item(row, 3).text()
                    if self.entry_table.item(row, 3)
                    else "",
                }
            )
        pending_title = self.project_name_edit.text().strip()
        if pending_title:
            self.project.title = pending_title
        self.project.settings.language_mode = LanguageMode(str(self.language_combo.currentData()))
        self.project.settings.model_id = str(self.model_combo.currentData())
        self.project.settings.device = ProcessingDevice(str(self.device_combo.currentData()))
        self.project.settings.max_cjk_chars = self.cjk_limit.value()
        self.project.settings.max_latin_chars = self.latin_limit.value()
        self.project.settings.subtitle_layout = SubtitleLayoutMode(
            str(self.subtitle_layout_combo.currentData())
        )
        self.project.settings.normalize_simplified_chinese = (
            self.simplified_chinese_check.isChecked()
        )
        self.project.settings.subtitle_output_mode = SubtitleOutputMode(
            str(self.subtitle_output_mode_combo.currentData())
        )
        self.project.settings.bilingual_order = BilingualOrder(
            str(self.bilingual_order_combo.currentData())
        )
        self.project.settings.primary_language_label = (
            self.primary_language_edit.text().strip() or "原文"
        )
        self.project.settings.translation_language_label = self._current_translation_language()
        draft = self.project.draft
        draft.original_editor_text = self.original_editor.toPlainText()
        draft.cleaned_editor_text = self.cleaned_editor.toPlainText()
        draft.table_rows = rows
        draft.active_page = self.pages.currentIndex()
        draft.captured_at = datetime.now(UTC).isoformat(timespec="seconds")

    def _draft_has_unconfirmed_changes(self) -> bool:
        draft = self.project.draft
        if draft.original_editor_text != self.project.original_text:
            return True
        expected_cleaned = self.project.cleaned_text or self.project.original_text
        if draft.cleaned_editor_text != expected_cleaned:
            return True
        if len(draft.table_rows) != len(self.project.entries):
            return bool(draft.table_rows)
        for row, entry in zip(draft.table_rows, self.project.entries, strict=False):
            if row != {
                "text": entry.text,
                "start": "" if entry.effective_start_ms is None else str(entry.effective_start_ms),
                "end": "" if entry.effective_end_ms is None else str(entry.effective_end_ms),
            }:
                return True
        return False

    def _restore_editor_draft(self) -> bool:
        """Restore a saved UI draft without treating it as confirmed anchor data."""
        draft = self.project.draft
        if not self._draft_has_unconfirmed_changes():
            return False
        self.original_editor.blockSignals(True)
        self.original_editor.setPlainText(draft.original_editor_text or self.project.original_text)
        self.original_editor.blockSignals(False)
        self.cleaned_editor.blockSignals(True)
        self.cleaned_editor.setPlainText(
            draft.cleaned_editor_text or self.project.cleaned_text or self.project.original_text
        )
        self.cleaned_editor.blockSignals(False)
        if draft.table_rows:
            self._table_refreshing = True
            self.entry_table.setRowCount(max(self.entry_table.rowCount(), len(draft.table_rows)))
            for row, saved in enumerate(draft.table_rows):
                for column, key in ((1, "text"), (2, "start"), (3, "end")):
                    item = self.entry_table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self.entry_table.setItem(row, column, item)
                    item.setText(saved[key])
            self._table_refreshing = False
        self._change_page(min(draft.active_page, self.pages.count() - 1))
        self.nav.setCurrentRow(min(draft.active_page, self.nav.count() - 1))
        self._draft_restored = True
        return True

    def _autosave_project(self) -> None:
        if not self.project_path or not self._project_dirty:
            return
        self._capture_editor_draft()
        try:
            snapshot = save_autosave(self.project, self.project_path)
        except OSError as exc:
            self.statusBar().showMessage(f"自动保存失败：{exc}", 8000)
            return
        self.statusBar().showMessage(f"已自动保存恢复草稿：{snapshot.name}", 4000)

    def _entry_status(self, entry: Any, index: dict[str, Any]) -> tuple[str, AnchorState, bool]:
        risk = entry_risk(entry, index)
        requires_review = risk in {AnchorState.ESTIMATED, AnchorState.UNANCHORED}
        if requires_review and entry.review_confirmed:
            return "人工复核 ✓", risk, False
        if risk == AnchorState.ESTIMATED:
            return "需复核：估算时间", risk, True
        if risk == AnchorState.UNANCHORED:
            return "需复核：缺少锚点", risk, True
        if entry.manual_start_ms is not None or entry.manual_end_ms is not None:
            return "人工定时", risk, False
        if risk == AnchorState.MERGED:
            return "已合并锚点", risk, False
        return "已继承", risk, False

    def _refresh_risk(self) -> None:
        index = self.project.anchor_index()
        risks = sum(1 for entry in self.project.entries if self._entry_status(entry, index)[2])
        self.inspector_risk.setText(f"{risks} 条待复核")
        self.review_summary.setText(
            f"共 {len(self.project.entries)} 条字幕；{risks} 条待人工复核。"
            " 校对表与切分表使用同一组字幕；可在表格内部向下滚动查看全部条目。"
        )

    def _fill_table(self, table: QTableWidget, shifted: bool = False) -> None:
        self._table_refreshing = True
        table.setRowCount(len(self.project.entries))
        index = self.project.anchor_index()
        review_table = table is self.review_table
        for row, entry in enumerate(self.project.entries):
            status, _risk, requires_review = self._entry_status(entry, index)
            start = entry.effective_start_ms
            end = entry.effective_end_ms
            if shifted:
                start = None if start is None else max(0, start + self.project.settings.offset_ms)
                end = None if end is None else max(0, end + self.project.settings.offset_ms)
            start_text = "" if start is None else format_srt_time(start) if shifted else str(start)
            end_text = "" if end is None else format_srt_time(end) if shifted else str(end)
            if review_table:
                translation = entry.translation_text or "（未翻译；双击可手动填写）"
                values = [str(row + 1), entry.text, translation, start_text, end_text, status]
            else:
                values = [str(row + 1), entry.text, start_text, end_text, status]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if review_table and column == 2 and not entry.translation_text:
                    item.setForeground(QColor("#9aa5b1"))
                elif requires_review:
                    item.setBackground(QColor("#6b5123"))
                    item.setForeground(QColor("#fff2ca"))
                elif status == "人工复核 ✓":
                    item.setBackground(QColor("#254a3a"))
                    item.setForeground(QColor("#d8f6df"))
                status_column = 5 if review_table else 4
                if column in {0, status_column}:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, column, item)
            text_lines = entry.text.count("\n") + 1
            translation_lines = (
                entry.translation_text.count("\n") + 1 if entry.translation_text else 1
            )
            table.setRowHeight(row, 30 * max(1, text_lines, translation_lines) + 6)
        self._table_refreshing = False

    def _refresh_tables(self) -> None:
        self._fill_table(self.entry_table)
        self._fill_table(self.review_table, shifted=True)

    def import_audio(self) -> None:
        path = self._open_file_dialog(
            "选择 MP3 音频或 MP4 视频",
            "支持的媒体 (*.mp3 *.mp4);;MP3 音频 (*.mp3);;MP4 视频 (*.mp4)",
        )
        if not path:
            return
        self.project.source = SourceAudio(path=path)
        if not self.project.title or self.project.title == "未命名项目":
            self.project.title = Path(path).stem
            self.project_name_edit.blockSignals(True)
            self.project_name_edit.setText(self.project.title)
            self.project_name_edit.blockSignals(False)
        self.audio_path_label.setText(path)
        self.inspector_file.setText(Path(path).name)
        self._sync_preview_source()
        media_kind = (
            "MP4 视频（将直接识别内嵌音轨）" if Path(path).suffix.lower() == ".mp4" else "MP3 音频"
        )
        self.statusBar().showMessage(f"已导入{media_kind}。请前往“文字识别”选择模型和设备。", 7000)
        self.nav.setCurrentRow(1)
        self._mark_dirty()

    def import_srt(self) -> None:
        path = self._open_file_dialog("导入外部 SRT", "SRT 字幕 (*.srt)")
        if not path:
            return
        source = Path(path)
        try:
            entries = parse_srt_text(source.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.critical(self, APP_DISPLAY_NAME, f"无法导入 SRT：{exc}")
            return
        if not entries:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "该 SRT 未包含可导入的有效字幕条目。")
            return
        self.project.anchors = []
        self.project.entries = [
            SubtitleEntry(
                text=entry.text,
                manual_start_ms=entry.local_start_ms,
                manual_end_ms=entry.local_end_ms,
                anchor_ids=[],
                review_confirmed=True,
                force_confirmed=True,
            )
            for entry in entries
        ]
        self.project.original_text = "\n".join(entry.text for entry in entries)
        self.project.cleaned_text = self.project.original_text
        self.project.draft.table_rows = []
        if not self.project.title or self.project.title == "未命名项目":
            self.project.title = source.stem
        self._populate_editors_from_project()
        self.nav.setCurrentRow(4)
        self._mark_dirty()
        self.statusBar().showMessage("已导入外部 SRT，所有条目均为人工定时，不包含语音锚点。", 9000)

    def _set_combo_data(self, combo: QComboBox, value: Any) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _current_translation_language(self) -> str:
        value = str(self.translation_language_edit.currentData() or "").strip()
        if value and value != "__custom_language__":
            return value
        return "English（英语）"

    def _set_translation_language(self, language: str) -> None:
        target = language.strip()
        if target in {"", "译文"}:
            target = "English（英语）"
        combo = self.translation_language_edit
        for index in range(combo.count()):
            if str(combo.itemData(index) or "") == target:
                combo.setCurrentIndex(index)
                self._translation_language_last_index = index
                return
        custom_index = combo.findData("__custom_language__")
        if custom_index < 0:
            custom_index = combo.count()
        combo.insertItem(custom_index, f"自定义：{target}", target)
        combo.setCurrentIndex(custom_index)
        self._translation_language_last_index = custom_index

    def _translation_language_selected(self, index: int) -> None:
        if index < 0:
            return
        combo = self.translation_language_edit
        if str(combo.itemData(index) or "") != "__custom_language__":
            self._translation_language_last_index = index
            self._subtitle_output_settings_changed()
            return
        value, accepted = QInputDialog.getText(
            self,
            "自定义翻译语言",
            "请输入目标语言名称（可用中文或英文，例如“阿拉伯语”或“Portuguese (Brazil)”）：",
        )
        if accepted and value.strip():
            combo.blockSignals(True)
            self._set_translation_language(value)
            combo.blockSignals(False)
            self._subtitle_output_settings_changed()
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(self._translation_language_last_index)
        combo.blockSignals(False)

    def _selected_model_source(self) -> ModelSource:
        source_id = str(self.model_source_combo.currentData())
        return next((item for item in MODEL_SOURCES if item.id == source_id), MODEL_SOURCES[0])

    def _selected_model_endpoint(self) -> str:
        source = self._selected_model_source()
        if source.id == "custom":
            return self.custom_model_source.text().strip()
        return source.endpoint

    def _model_source_changed(self) -> None:
        source = self._selected_model_source()
        is_custom = source.id == "custom"
        self.custom_model_source.setEnabled(is_custom)
        self.local_settings.setValue("model_download/source_id", source.id)
        note = source.description
        if source.id == "official":
            note += " 请确认 VPN/代理已对 LyraSub 生效。"
        elif source.id == "cn_mirror":
            note += " 镜像可用性会变化，下载前请先测试连接。"
        else:
            note += " 地址必须是兼容 Hugging Face Hub 的 https 服务。"
        self.model_source_note.setText(note)
        if hasattr(self, "download_model_button"):
            self.refresh_model_download_status()

    def _save_custom_model_source(self, endpoint: str) -> None:
        self.local_settings.setValue("model_download/custom_endpoint", endpoint.strip())

    def _save_model_proxy(self, proxy_url: str) -> None:
        self.local_settings.setValue("model_download/proxy", proxy_url.strip())

    def _selected_model_proxy(self) -> str:
        return self.model_proxy.text().strip()

    def refresh_model_download_status(self) -> None:
        """Synchronize independent download management with the workbench selection."""
        if not hasattr(self, "model_combo"):
            return
        selected_id = str(self.model_combo.currentData())
        selected = self.models.preset(selected_id)
        selected_downloaded = self.models.is_model_downloaded(selected_id)
        if selected_downloaded:
            size_mb = self.models.downloaded_size_bytes(selected_id) / 1_000_000
            selection_text = f"“{selected.label}”已下载（{size_mb:.0f} MB），可直接识别。"
        else:
            selection_text = f"“{selected.label}”尚未下载。请点击“打开模型管理”下载后再开始识别。"
        if hasattr(self, "model_selection_status"):
            self.model_selection_status.setText(selection_text)
        if hasattr(self, "recognize_button") and not self._download_active:
            self.recognize_button.setEnabled(selected_downloaded)

        if not hasattr(self, "model_manager_model_combo"):
            return
        manager_id = str(self.model_manager_model_combo.currentData())
        manager_preset = self.models.preset(manager_id)
        manager_downloaded = self.models.is_model_downloaded(manager_id)
        if manager_downloaded:
            size_mb = self.models.downloaded_size_bytes(manager_id) / 1_000_000
            manager_text = (
                f"“{manager_preset.label}”已下载（{size_mb:.0f} MB），可直接复用。\\n"
                f"缓存目录：{self.models.model_directory(manager_id)}"
            )
        else:
            manager_text = (
                f"“{manager_preset.label}”尚未下载（预计约 {manager_preset.approximate_download_mb} MB）。\\n"
                "下载完成后可在主工作台直接选择该模型。"
            )
        self.model_download_status.setText(manager_text)
        self.download_model_button.setEnabled(not manager_downloaded and not self._download_active)
        downloaded = [
            preset.label for preset in MODEL_PRESETS if self.models.is_model_downloaded(preset.id)
        ]
        self.model_inventory_status.setText(
            "本机已下载：" + ("、".join(downloaded) if downloaded else "暂无。")
        )

    def run_gpu_diagnostic(self) -> None:
        self.gpu_diagnostic_button.setEnabled(False)

        def operation(report: Callable[[str, int | None], None], cancel_event: Event) -> Any:
            report("正在检查 NVIDIA 驱动、CUDA DLL 与 CTranslate2……", None)
            if cancel_event.is_set():
                raise TaskCancelledError("GPU 自检已停止。")
            diagnostic = self.models.diagnose_gpu()
            if cancel_event.is_set():
                raise TaskCancelledError("GPU 自检已停止。")
            report(diagnostic.summary, 100)
            return diagnostic

        def done(diagnostic: Any) -> None:
            self.gpu_diagnostic_button.setEnabled(True)
            self.gpu_diagnostic_status.setText(diagnostic.summary)
            DownloadDiagnosticDialog(
                self,
                diagnostic.details,
                title="GPU 自检结果",
                prompt_text="请复制完整 GPU 自检结果，以便排查驱动、CUDA 或 cuDNN 问题。",
            ).exec()

        self._run_worker(operation, done, task_title="正在执行 GPU 自检")

    def test_model_source(self) -> None:
        preset_id = str(self.model_manager_model_combo.currentData())
        try:
            endpoint = self.models.sanitize_endpoint(self._selected_model_endpoint())
            proxy = self.models.sanitize_proxy(self._selected_model_proxy())
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        self.test_model_source_button.setEnabled(False)
        self.statusBar().showMessage("正在测试模型源连接，仅请求仓库元数据……")

        def operation(
            _report: Callable[[str, int | None], None], cancel_event: Event
        ) -> SourceProbe:
            if cancel_event.is_set():
                raise TaskCancelledError("模型源连接测试已停止。")
            return self.models.probe_source(preset_id, endpoint, proxy)

        def done(result: SourceProbe) -> None:
            self.test_model_source_button.setEnabled(True)
            if result.ok:
                QMessageBox.information(self, "模型源连接", result.message)
                self.statusBar().showMessage("模型源连接正常，可以开始下载。", 6000)
            else:
                DownloadDiagnosticDialog(self, result.message).exec()
                self.statusBar().showMessage("模型源不可用，请切换来源、代理或检查网络。", 6000)

        self._run_worker(operation, done, task_title="正在测试模型源")

    def _set_download_progress(self, message: str, value: int | None) -> None:
        self.model_download_status.setText(message)
        if value is not None:
            self.download_progress.setValue(value)
            self.download_progress.setFormat(f"{value}%")
        self.statusBar().showMessage(message.splitlines()[0])

    def stop_model_download(self) -> None:
        if self._download_cancel_event and not self._download_cancel_event.is_set():
            self._download_cancel_event.set()
            self.stop_download_button.setEnabled(False)
            self.model_download_status.setText("正在停止下载并清理当前临时文件……")
            self.statusBar().showMessage("正在停止模型下载……")

    def download_selected_model(self) -> None:
        preset_id = str(self.model_manager_model_combo.currentData())
        try:
            endpoint = self.models.sanitize_endpoint(self._selected_model_endpoint())
            proxy = self.models.sanitize_proxy(self._selected_model_proxy())
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        preset = self.models.preset(preset_id)
        response = QMessageBox.question(
            self,
            "下载本地识别模型",
            f"将从以下来源下载“{preset.label}”模型（预计约 {preset.approximate_download_mb} MB）：\\n{endpoint}\\n"
            f"代理：{proxy or '未设置'}\\n\\n"
            "下载过程中可以停止；未完成临时文件不会被当作可用模型。是否开始下载？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        self._download_active = True
        self._download_cancel_event = Event()
        self.download_model_button.setEnabled(False)
        self.test_model_source_button.setEnabled(False)
        self.stop_download_button.setEnabled(True)
        self.download_progress.setValue(0)
        self.download_progress.setFormat("0%")
        self.statusBar().showMessage("正在准备模型下载……")

        def operation(report: Callable[[str, int | None], None], _cancel_event: Event) -> Path:
            return self.models.download_model(
                preset_id,
                endpoint,
                proxy,
                report,
                self._download_cancel_event,
            )

        def done(destination: Path) -> None:
            self._download_active = False
            self._download_cancel_event = None
            self.stop_download_button.setEnabled(False)
            self._set_combo_data(self.model_combo, preset_id)
            self.refresh_model_download_status()
            self.download_progress.setValue(100)
            self.download_progress.setFormat("下载完成")
            self.statusBar().showMessage(f"模型下载完成：{destination}", 10_000)
            QMessageBox.information(
                self,
                "模型下载完成",
                "模型已保存到本地。现在可以点击“开始本地识别”。",
            )

        self._run_worker(
            operation,
            done,
            self._set_download_progress,
            task_title="正在下载本地识别模型",
        )

    def start_transcription(self) -> None:
        if not self.project.source.path:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先导入 MP3 音频文件。")
            self.nav.setCurrentRow(0)
            return
        language = LanguageMode(str(self.language_combo.currentData()))
        model_id = str(self.model_combo.currentData())
        device = ProcessingDevice(str(self.device_combo.currentData()))
        if not self.models.is_model_downloaded(str(model_id)):
            QMessageBox.information(
                self,
                APP_DISPLAY_NAME,
                "所选模型尚未下载。请点击“打开模型管理”下载对应档位后再开始识别。",
            )
            self.refresh_model_download_status()
            return
        self.project.settings.language_mode = language
        self.project.settings.model_id = model_id
        self.project.settings.device = device
        self.recognize_button.setEnabled(False)
        self.statusBar().showMessage("正在启动本地识别任务……")

        def operation(
            report: Callable[[str, int | None], None], cancel_event: Event
        ) -> TranscriptionResult:
            return self.transcriber.transcribe(
                self.project.source.path, model_id, device, language, report, cancel_event
            )

        self._run_worker(operation, self._transcription_finished, task_title="正在本地识别音频")

    def _transcription_finished(self, result: TranscriptionResult) -> None:
        if self.simplified_chinese_check.isChecked():
            result.text = to_simplified_chinese(result.text)
            for anchor in result.anchors:
                anchor.text = to_simplified_chinese(anchor.text)
        self.project.anchors = result.anchors
        self.project.original_text = result.text
        self.project.cleaned_text = result.text
        self.project.source.detected_language = result.language
        self.project.source.detected_language_probability = result.language_probability
        self.project.source.duration_ms = result.duration_ms
        self.project.settings.device = ProcessingDevice(result.execution_device)
        self.original_editor.blockSignals(True)
        self.original_editor.setPlainText(result.text)
        self.original_editor.blockSignals(False)
        self.cleanup_source_editor.setPlainText(result.text)
        self.cleaned_editor.blockSignals(True)
        self.cleaned_editor.setPlainText(result.text)
        self.cleaned_editor.blockSignals(False)
        self._pending_local_segmentation = False
        self._refresh_all()
        detail = "识别完成，已保留可编辑时间锚点。请确认原文或清洗结果后，使用“推荐：LLM 自然语义切分”生成字幕。"
        if result.fallback_note:
            detail += f" {result.fallback_note}"
        self.statusBar().showMessage(detail, 10_000)
        self.recognize_button.setEnabled(True)
        self.nav.setCurrentRow(2)
        self._mark_dirty()

    def normalize_current_project_simplified(self) -> None:
        self.project.original_text = to_simplified_chinese(self.project.original_text)
        self.project.cleaned_text = to_simplified_chinese(self.project.cleaned_text)
        for anchor in self.project.anchors:
            anchor.text = to_simplified_chinese(anchor.text)
        for entry in self.project.entries:
            entry.text = to_simplified_chinese(entry.text)
        self.original_editor.setPlainText(to_simplified_chinese(self.original_editor.toPlainText()))
        self.cleanup_source_editor.setPlainText(
            to_simplified_chinese(self.cleanup_source_editor.toPlainText())
        )
        self.cleaned_editor.setPlainText(to_simplified_chinese(self.cleaned_editor.toPlainText()))
        self._refresh_all()
        self._mark_dirty()
        self.statusBar().showMessage(
            "已将当前工程的识别、清洗、锚点和字幕文本统一为简体中文。", 8000
        )

    def apply_original_text(self) -> None:
        text = self.original_editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "原文不能为空。")
            return
        self.project.original_text = text
        self.project.cleaned_text = text
        self.cleanup_source_editor.setPlainText(text)
        self.cleaned_editor.setPlainText(text)
        self.statusBar().showMessage("已应用原文校订。切分时会根据原始锚点重新映射。", 6000)
        self._mark_dirty()

    def apply_cleaned_text(self) -> None:
        text = self.cleaned_editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "清洗后的文本不能为空。")
            return
        self.project.cleaned_text = text
        self.statusBar().showMessage("已确认清洗文本。请在“语义切分”中生成字幕条目。", 6000)
        self.nav.setCurrentRow(3)
        self._mark_dirty()

    def _active_profile(self) -> tuple[LLMProfile, str] | None:
        profiles = ProfileStore().list()
        if not profiles:
            response = QMessageBox.question(
                self,
                APP_DISPLAY_NAME,
                "尚未配置兼容 OpenAI 的模型服务。是否现在配置？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if response == QMessageBox.StandardButton.Yes:
                self.configure_llm()
            profiles = ProfileStore().list()
        if not profiles:
            return None
        profile = ProfileStore().active_profile()
        if profile is None:
            return None
        key = SecretStore().get(profile.api_key_ref)
        if not key:
            QMessageBox.warning(
                self, APP_DISPLAY_NAME, "所选模型服务没有可用 API 密钥，请重新配置。"
            )
            return None
        return profile, key

    def run_cleanup(self) -> None:
        if not self.cleanup_enabled.isChecked():
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先勾选“使用 LLM 清洗”。")
            return
        text = self.original_editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, APP_DISPLAY_NAME, "没有可供清洗的原文。")
            return
        active = self._active_profile()
        if not active:
            return
        profile, key = active
        self.statusBar().showMessage("正在向您配置的模型服务发送文本清洗请求……")

        def operation(report: Callable[[str, int | None], None], cancel_event: Event) -> Any:
            return OpenAICompatibleClient(profile, key).cleanup(
                text, self.project.source.detected_language or "auto", report, cancel_event
            )

        def done(result: Any) -> None:
            cleaned = (
                to_simplified_chinese(result.text)
                if self.simplified_chinese_check.isChecked()
                else result.text
            )
            self.cleaned_editor.setPlainText(cleaned)
            self.project.workflow_notes.extend(result.notes)
            self.statusBar().showMessage("已收到清洗建议，请在右侧文本框确认或修改。", 8000)

        self._run_worker(operation, done, task_title="正在使用 LLM 清洗文本")

    @staticmethod
    def _apply_layout_mode(lines: list[str], layout: SubtitleLayoutMode) -> list[str]:
        """Interpret an LLM newline as an entry split in single-line mode."""
        if layout == SubtitleLayoutMode.TWO_LINES:
            return [
                "\n".join(part.strip() for part in line.splitlines() if part.strip())
                for line in lines
            ]
        return [part.strip() for line in lines for part in line.splitlines() if part.strip()]

    def _apply_mapped_entries(self, generated: list[Any], entries: list[Any]) -> None:
        known = {anchor.id for anchor in self.project.anchors}
        self.project.anchors.extend(anchor for anchor in generated if anchor.id not in known)
        self.project.entries = entries
        self._refresh_all()
        self._mark_dirty()

    def run_local_segmentation(self) -> None:
        source_text = (
            self.project.cleaned_text
            or self.cleaned_editor.toPlainText().strip()
            or self.project.original_text
        )
        if not source_text:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先完成识别或输入文本。")
            return
        source_anchors = list(self.project.anchors)
        cjk_limit = self.cjk_limit.value()
        latin_limit = self.latin_limit.value()
        layout_mode = SubtitleLayoutMode(str(self.subtitle_layout_combo.currentData()))

        def operation(
            report: Callable[[str, int | None], None], cancel_event: Event
        ) -> tuple[Any, list[Any], list[Any]]:
            report("正在按本地规则分析语义边界……", 15)
            if cancel_event.is_set():
                raise TaskCancelledError("本地语义切分已停止。")
            suggestion = segment_locally(source_text, cjk_limit, latin_limit)
            suggestion.entries = self._apply_layout_mode(suggestion.entries, layout_mode)
            report("正在将切分结果映射回原始词级时间锚点……", 60)
            if cancel_event.is_set():
                raise TaskCancelledError("时间锚点映射已停止；不会写入结果。")
            generated, entries = entries_from_text_list(source_anchors, suggestion.entries)
            if cancel_event.is_set():
                raise TaskCancelledError("时间锚点映射已停止；不会写入结果。")
            report("本地切分与时间锚点映射完成。", 100)
            return suggestion, generated, entries

        def done(result: tuple[Any, list[Any], list[Any]]) -> None:
            suggestion, generated, entries = result
            self.project.settings.max_cjk_chars = cjk_limit
            self.project.settings.max_latin_chars = latin_limit
            self.project.settings.subtitle_layout = layout_mode
            self.project.settings.use_llm_segmentation = False
            self._apply_mapped_entries(generated, entries)
            self.project.workflow_notes = [*self.project.workflow_notes, *suggestion.notes]
            self.statusBar().showMessage(
                "已使用本地规则生成切分建议。您可直接编辑表格文字、换行和时间。", 7000
            )

        self._run_worker(operation, done, task_title="正在执行本地语义切分")

    def run_llm_segmentation(self) -> None:
        text = self.project.cleaned_text or self.cleaned_editor.toPlainText().strip()
        if not text:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先确认要切分的文本。")
            return
        active = self._active_profile()
        if not active:
            return
        profile, key = active
        self.statusBar().showMessage("正在向您配置的模型服务发送语义切分请求……")

        source_anchors = list(self.project.anchors)
        cjk_limit = self.cjk_limit.value()
        latin_limit = self.latin_limit.value()
        layout_mode = SubtitleLayoutMode(str(self.subtitle_layout_combo.currentData()))
        normalize_generated = self.simplified_chinese_check.isChecked()

        def operation(
            report: Callable[[str, int | None], None], cancel_event: Event
        ) -> tuple[Any, list[Any], list[Any]]:
            result = OpenAICompatibleClient(profile, key).segment(
                text,
                cjk_limit,
                latin_limit,
                self.project.source.detected_language or "auto",
                report,
                cancel_event,
            )
            if normalize_generated:
                result.lines = [to_simplified_chinese(line) for line in result.lines]
            result.lines = self._apply_layout_mode(result.lines, layout_mode)
            if cancel_event.is_set():
                raise TaskCancelledError("LLM 语义切分已停止；不会写入结果。")
            report("正在将 LLM 切分结果映射到词级时间锚点……", 95)
            generated, entries = entries_from_text_list(source_anchors, result.lines)
            if cancel_event.is_set():
                raise TaskCancelledError("时间锚点映射已停止；不会写入结果。")
            report("LLM 切分与时间锚点映射完成。", 100)
            return result, generated, entries

        def done(result: tuple[Any, list[Any], list[Any]]) -> None:
            llm_result, generated, entries = result
            self.project.settings.max_cjk_chars = cjk_limit
            self.project.settings.max_latin_chars = latin_limit
            self.project.settings.subtitle_layout = layout_mode
            self.project.settings.use_llm_segmentation = True
            self._apply_mapped_entries(generated, entries)
            self.project.workflow_notes.extend(llm_result.notes)
            self.statusBar().showMessage("已收到语义切分建议。请复核黄色风险条目。", 8000)

        self._run_worker(operation, done, task_title="正在使用 LLM 语义切分")

    def run_llm_translation(self) -> None:
        if not self.project.entries:
            QMessageBox.information(
                self, APP_DISPLAY_NAME, "请先完成切分或导入外部 SRT，再执行字幕翻译。"
            )
            return
        target_language = self._current_translation_language()
        source_language = self.primary_language_edit.text().strip() or "原文"
        if not target_language:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先填写目标语言名称。")
            return
        translation_scope = str(self.translation_scope_combo.currentData() or "missing")
        records = [
            TranslationRecord(entry.id, entry.text)
            for entry in self.project.entries
            if entry.text.strip()
            and (translation_scope == "all" or not entry.translation_text.strip())
        ]
        if not records:
            QMessageBox.information(
                self,
                APP_DISPLAY_NAME,
                "没有需要翻译的字幕。若要重新翻译已有译文，请将“翻译范围”改为“翻译全部并覆盖现有译文”。",
            )
            return
        active = self._active_profile()
        if not active:
            return
        profile, key = active
        try:
            batches = chunk_translation_records(records, profile.translation_batch_chars)
        except ValueError as exc:
            QMessageBox.warning(self, APP_DISPLAY_NAME, str(exc))
            return
        oversized = [
            record for record in records if len(record.text) > profile.translation_batch_chars
        ]
        if oversized:
            self.statusBar().showMessage(
                f"有 {len(oversized)} 条字幕超过每批字符数，将单独发送；可在模型服务中提高上限。",
                8000,
            )

        def operation(
            report: Callable[[str, int | None], None], cancel_event: Event
        ) -> tuple[dict[str, str], list[str]]:
            client = OpenAICompatibleClient(profile, key)
            translated: dict[str, str] = {}
            notes: list[str] = []
            total = len(batches)
            for index, batch in enumerate(batches, start=1):
                if cancel_event.is_set():
                    raise TaskCancelledError("批量字幕翻译已停止；不会写入未完成结果。")
                report(
                    f"正在翻译第 {index}/{total} 批字幕（{len(batch)} 条，{sum(len(item.text) for item in batch)} 字符）……",
                    max(1, round((index - 1) * 100 / total)),
                )

                def translate_resilient(
                    records_to_translate: list[TranslationRecord],
                    batch_index: int = index,
                    batch_total: int = total,
                ) -> Any:
                    try:
                        return client.translate_batch(
                            records_to_translate,
                            source_language,
                            target_language,
                            progress=None,
                            cancel_event=cancel_event,
                        )
                    except LLMResponseError as exc:
                        if (
                            not is_retryable_translation_response_error(str(exc))
                            or len(records_to_translate) < 2
                        ):
                            raise
                        split_at = len(records_to_translate) // 2
                        report(
                            f"第 {batch_index}/{batch_total} 批返回 JSON 或翻译条目不完整；正自动拆为 "
                            f"{split_at} + {len(records_to_translate) - split_at} 条后重试……",
                            max(1, round((batch_index - 1) * 100 / batch_total)),
                        )
                        left = translate_resilient(records_to_translate[:split_at])
                        right = translate_resilient(records_to_translate[split_at:])
                        return type(left)(
                            translations={**left.translations, **right.translations},
                            notes=[*left.notes, *right.notes],
                        )

                result = translate_resilient(batch)
                translated.update(result.translations)
                notes.extend(result.notes)
                report(f"已完成第 {index}/{total} 批字幕翻译。", round(index * 100 / total))
            if set(translated) != {record.entry_id for record in records}:
                raise RuntimeError("翻译批次完成后缺少部分字幕结果；工程未被修改。")
            return translated, notes

        def done(result: tuple[dict[str, str], list[str]]) -> None:
            translated, notes = result
            for entry in self.project.entries:
                if entry.id in translated:
                    entry.translation_text = translated[entry.id]
            self.project.settings.primary_language_label = source_language
            self.project.settings.translation_language_label = target_language
            self.project.workflow_notes.extend(notes)
            self._refresh_all()
            self._mark_dirty()
            self.statusBar().showMessage(
                f"已完成 {len(translated)} 条字幕的批量翻译；可选择单语或双语导出。", 8000
            )

        self._run_worker(operation, done, task_title="正在使用 LLM 批量翻译字幕")

    def _set_entries_from_texts(self, texts: list[str]) -> None:
        generated, entries = entries_from_text_list(self.project.anchors, texts)
        known = {anchor.id for anchor in self.project.anchors}
        self.project.anchors.extend(anchor for anchor in generated if anchor.id not in known)
        self.project.entries = entries
        self._refresh_all()
        self._mark_dirty()

    @staticmethod
    def _parse_milliseconds(value: str, fallback: int | None) -> int | None:
        stripped = value.strip()
        if not stripped:
            return fallback
        if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
            return int(stripped)
        return fallback

    def _selected_entry_row(self) -> int | None:
        row = self.entry_table.currentRow()
        if row < 0 or row >= self.entry_table.rowCount():
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先在字幕表中选择一条字幕。")
            return None
        return row

    @staticmethod
    def _split_suggestion(text: str) -> tuple[str, str]:
        parts = [part.strip() for part in text.splitlines() if part.strip()]
        if len(parts) >= 2:
            return parts[0], "".join(parts[1:])
        compact = text.strip()
        midpoint = max(1, len(compact) // 2)
        candidates = [
            index for index, char in enumerate(compact[:-1], start=1) if char in "，、；：。！？ "
        ]
        split_at = (
            min(candidates, key=lambda index: abs(index - midpoint)) if candidates else midpoint
        )
        return compact[:split_at].rstrip("，、；：。！？ "), compact[split_at:].lstrip(
            "，、；：。！？ "
        )

    def split_selected_entry(self, auto_apply: bool = False) -> None:
        row = self._selected_entry_row()
        if row is None:
            return
        text_item = self.entry_table.item(row, 1)
        if not text_item or not text_item.text().strip():
            QMessageBox.warning(self, APP_DISPLAY_NAME, "所选字幕没有可拆分的文字。")
            return
        first_default, second_default = self._split_suggestion(text_item.text())
        first, accepted = QInputDialog.getText(
            self, "拆分字幕：第一条", "第一条字幕文本", text=first_default
        )
        if not accepted or not first.strip():
            return
        second, accepted = QInputDialog.getText(
            self, "拆分字幕：第二条", "第二条字幕文本", text=second_default
        )
        if not accepted or not second.strip():
            return
        start_item = self.entry_table.item(row, 2)
        end_item = self.entry_table.item(row, 3)
        start = self._parse_milliseconds(start_item.text() if start_item else "", None)
        end = self._parse_milliseconds(end_item.text() if end_item else "", None)
        boundary = None
        if start is not None and end is not None and end > start:
            total = max(1, len(first.strip()) + len(second.strip()))
            boundary = start + round((end - start) * len(first.strip()) / total)
        self._table_refreshing = True
        text_item.setText(first.strip())
        if boundary is not None:
            self.entry_table.item(row, 3).setText(str(boundary))
        self.entry_table.insertRow(row + 1)
        self.entry_table.setItem(row + 1, 0, QTableWidgetItem(str(row + 2)))
        self.entry_table.setItem(row + 1, 1, QTableWidgetItem(second.strip()))
        self.entry_table.setItem(
            row + 1, 2, QTableWidgetItem("" if boundary is None else str(boundary))
        )
        self.entry_table.setItem(row + 1, 3, QTableWidgetItem("" if end is None else str(end)))
        state_item = QTableWidgetItem("待保存并映射")
        state_item.setFlags(state_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.entry_table.setItem(row + 1, 4, state_item)
        self._table_refreshing = False
        self.entry_table.selectRow(row + 1)
        self._mark_dirty()
        if auto_apply:
            self.apply_table_entries()
            self.statusBar().showMessage("已拆分为两条连续字幕，并重新映射原始词级锚点。", 8000)
            return
        self.statusBar().showMessage(
            "已在表格中拆成两条连续字幕；点击“保存表格修改并重新映射”后生效。", 8000
        )

    def merge_selected_entry(self, auto_apply: bool = False) -> None:
        row = self._selected_entry_row()
        if row is None:
            return
        if row >= self.entry_table.rowCount() - 1:
            QMessageBox.information(
                self, APP_DISPLAY_NAME, "所选字幕已经是最后一条，不能与下一条合并。"
            )
            return
        current = self.entry_table.item(row, 1)
        following = self.entry_table.item(row + 1, 1)
        if (
            not current
            or not following
            or not current.text().strip()
            or not following.text().strip()
        ):
            QMessageBox.warning(self, APP_DISPLAY_NAME, "相邻字幕文字不完整，无法合并。")
            return
        left, right = current.text().rstrip(), following.text().lstrip()
        separator = " " if left[-1].isascii() and right[0].isascii() else ""
        self._table_refreshing = True
        current.setText(f"{left}{separator}{right}")
        following_end = self.entry_table.item(row + 1, 3)
        if following_end:
            self.entry_table.item(row, 3).setText(following_end.text())
        self.entry_table.removeRow(row + 1)
        self._table_refreshing = False
        self.entry_table.selectRow(row)
        self._mark_dirty()
        if auto_apply:
            self.apply_table_entries()
            self.statusBar().showMessage("已合并相邻字幕，并重新映射原始词级锚点。", 8000)
            return
        self.statusBar().showMessage(
            "已在表格中合并相邻字幕；点击“保存表格修改并重新映射”后生效。", 8000
        )

    def confirm_selected_review(self) -> None:
        row = self._selected_entry_row()
        if row is None or row >= len(self.project.entries):
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先保存表格修改，再确认复核状态。")
            return
        entry = self.project.entries[row]
        risk = entry_risk(entry, self.project.anchor_index())
        if risk not in {AnchorState.ESTIMATED, AnchorState.UNANCHORED}:
            QMessageBox.information(
                self, APP_DISPLAY_NAME, "该条字幕已有继承时间锚点，无需人工复核。"
            )
            return
        entry.review_confirmed = True
        self._refresh_all()
        self._mark_dirty()
        self.statusBar().showMessage("已标记为人工复核；导出时仍保留该条的时间锚点来源说明。", 7000)

    def apply_table_and_enter_review(self) -> None:
        self.apply_table_entries()
        self.nav.setCurrentRow(4)
        self.statusBar().showMessage("切分修改已应用；已进入“05 校对导出”。", 7000)

    def apply_table_entries(self) -> None:
        if self._table_refreshing:
            return
        rows: list[tuple[str, str, str]] = []
        for row in range(self.entry_table.rowCount()):
            text_item = self.entry_table.item(row, 1)
            if not text_item or not text_item.text().strip():
                continue
            start_item = self.entry_table.item(row, 2)
            end_item = self.entry_table.item(row, 3)
            rows.append(
                (
                    text_item.text().strip(),
                    start_item.text().strip() if start_item else "",
                    end_item.text().strip() if end_item else "",
                )
            )
        current_rows = [
            (
                entry.text,
                "" if entry.effective_start_ms is None else str(entry.effective_start_ms),
                "" if entry.effective_end_ms is None else str(entry.effective_end_ms),
            )
            for entry in self.project.entries
        ]
        if rows == current_rows:
            QMessageBox.information(
                self,
                APP_DISPLAY_NAME,
                "表格没有未保存的文字或时间修改；未重新映射，也不会改变现有自动时间。",
            )
            return

        text_values = [text for text, _, _ in rows]
        generated, entries = entries_from_text_list(self.project.anchors, text_values)
        known = {anchor.id for anchor in self.project.anchors}
        self.project.anchors.extend(anchor for anchor in generated if anchor.id not in known)
        manual_changed = 0
        text_changed = 0
        for row_index, (entry, (_, start_text, end_text)) in enumerate(
            zip(entries, rows, strict=False)
        ):
            prior = (
                self.project.entries[row_index] if row_index < len(self.project.entries) else None
            )
            if prior and entry.text != prior.text:
                text_changed += 1
            # Compare the visible value with the *newly mapped* anchor time.  A merge
            # naturally extends the end time to the next entry's final anchor; it is
            # still inherited timing unless the user actually changed the shown value.
            desired_start = self._parse_milliseconds(start_text, entry.start_ms)
            desired_end = self._parse_milliseconds(end_text, entry.end_ms)
            if desired_start != entry.start_ms:
                entry.manual_start_ms = desired_start
                if not prior or prior.manual_start_ms != desired_start:
                    manual_changed += 1
            if desired_end != entry.end_ms:
                entry.manual_end_ms = desired_end
                if not prior or prior.manual_end_ms != desired_end:
                    manual_changed += 1
            if prior and entry.text == prior.text:
                entry.review_confirmed = prior.review_confirmed
                entry.force_confirmed = prior.force_confirmed
        self.project.entries = entries
        self._refresh_all()
        self._mark_dirty()
        self.statusBar().showMessage(
            f"已保存表格修改：{len(rows)} 条重新映射，文字变更 {text_changed} 条，手工时间变更 {manual_changed} 项。",
            8000,
        )

    @staticmethod
    def _format_preview_time(milliseconds: int) -> str:
        return format_srt_time(max(0, milliseconds)).replace(",", ".")

    def _has_external_preview_video(self) -> bool:
        return bool(self.project.source.preview_video_path.strip())

    def _preview_media_path(self) -> str:
        return self.project.source.preview_video_path.strip() or self.project.source.path.strip()

    def _is_video_source(self) -> bool:
        return Path(self._preview_media_path()).suffix.lower() == ".mp4"

    def _preview_audio_source_changed(self) -> None:
        value = self.preview_audio_source_combo.currentData()
        try:
            selected = PreviewAudioSource(str(value))
        except ValueError:
            selected = PreviewAudioSource.VIDEO
        if selected == self.project.settings.preview_audio_source:
            return
        self.project.settings.preview_audio_source = selected
        self._sync_preview_source()
        self._mark_dirty()
        description = "识别源音轨" if selected == PreviewAudioSource.RECOGNITION else "视频音轨"
        self.statusBar().showMessage(f"预览音频已切换为{description}。", 6000)

    def _apply_preview_audio_source(self) -> None:
        use_recognition_audio = (
            self._has_external_preview_video()
            and self.project.settings.preview_audio_source == PreviewAudioSource.RECOGNITION
        )
        self.audio_output.setVolume(0.0 if use_recognition_audio else self._preview_audio_volume)
        self.preview_sync_audio_output.setVolume(
            self._preview_audio_volume if use_recognition_audio else 0.0
        )

    def select_preview_video(self) -> None:
        if not self.project.source.path.strip():
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先导入用于识别的 MP3 或 MP4 媒体。")
            return
        path = self._open_file_dialog("选择高清视频作为校对预览", "MP4 视频 (*.mp4)")
        if not path:
            return
        self._close_video_preview()
        self.project.source.preview_video_path = path
        self._sync_preview_source()
        self._mark_dirty()
        self.statusBar().showMessage(
            "已绑定外部高清视频用于校对预览；识别源、字幕锚点和时间轴不会被修改。", 8000
        )

    def clear_preview_video(self) -> None:
        if not self._has_external_preview_video():
            return
        self._close_video_preview()
        self.project.source.preview_video_path = ""
        self._sync_preview_source()
        self._mark_dirty()
        self.statusBar().showMessage("已清除外部预览视频，校对预览恢复使用识别源媒体。", 7000)

    def _sync_preview_source(self) -> None:
        recognition_path = self.project.source.path.strip()
        media_path = self._preview_media_path()
        binding_signature = (
            f"{recognition_path}\n{media_path}\n{self.project.settings.preview_audio_source.value}"
        )
        if not recognition_path:
            self._close_video_preview()
            self.audio_player.stop()
            self.audio_player.setSource(QUrl())
            self.preview_sync_audio_player.stop()
            self.preview_sync_audio_player.setSource(QUrl())
            self._preview_loaded_path = ""
            self._preview_binding_signature = ""
            self.preview_play_button.setEnabled(False)
            self.preview_insert_button.setEnabled(False)
            self.preview_video_select_button.setEnabled(False)
            self.preview_video_button.setEnabled(False)
            self.preview_video_clear_button.setEnabled(False)
            self.preview_audio_source_combo.setEnabled(False)
            self.preview_timeline.setEnabled(False)
            self.preview_timeline.setRange(0, 0)
            self.preview_position_label.setText("00:00:00.000")
            self.preview_duration_label.setText("00:00:00.000")
            self.preview_status_label.setText("导入 MP3 或 MP4 后可预览")
            self.preview_subtitle_label.setText("当前字幕将在这里叠加显示")
            return
        if binding_signature == self._preview_binding_signature:
            return
        source_path = Path(media_path)
        if not self._is_video_source():
            self._close_video_preview()
        self.audio_player.stop()
        self.preview_sync_audio_player.stop()
        self._preview_loaded_path = media_path
        self._preview_binding_signature = binding_signature
        self._preview_active_row = None
        self.preview_timeline.setRange(0, 0)
        self.preview_timeline.setValue(0)
        self.preview_position_label.setText("00:00:00.000")
        self.preview_duration_label.setText("00:00:00.000")
        self.preview_video_select_button.setEnabled(True)
        self.preview_video_clear_button.setEnabled(self._has_external_preview_video())
        self.preview_audio_source_combo.setEnabled(self._has_external_preview_video())
        if not source_path.is_file():
            self.audio_player.setSource(QUrl())
            self.preview_play_button.setEnabled(False)
            self.preview_insert_button.setEnabled(False)
            self.preview_video_button.setEnabled(False)
            self.preview_timeline.setEnabled(False)
            missing_note = "找不到外部预览视频；可重新选择或清除后恢复识别源媒体。"
            self.preview_status_label.setText(
                missing_note
                if self._has_external_preview_video()
                else "找不到该媒体文件；请重新导入或恢复文件路径。"
            )
            self.preview_subtitle_label.setText("媒体文件不可用，字幕仍可在表格中编辑。")
            return
        self.audio_player.setSource(QUrl.fromLocalFile(str(source_path.resolve())))
        use_recognition_audio = (
            self._has_external_preview_video()
            and self.project.settings.preview_audio_source == PreviewAudioSource.RECOGNITION
        )
        if use_recognition_audio and Path(recognition_path).is_file():
            self.preview_sync_audio_player.setSource(
                QUrl.fromLocalFile(str(Path(recognition_path).resolve()))
            )
        else:
            self.preview_sync_audio_player.setSource(QUrl())
            if use_recognition_audio:
                self.project.settings.preview_audio_source = PreviewAudioSource.VIDEO
                self.preview_audio_source_combo.blockSignals(True)
                self._set_combo_data(self.preview_audio_source_combo, PreviewAudioSource.VIDEO)
                self.preview_audio_source_combo.blockSignals(False)
        self._apply_preview_audio_source()
        self.preview_play_button.setEnabled(True)
        self.preview_insert_button.setEnabled(True)
        self.preview_video_button.setEnabled(self._is_video_source())
        self.preview_timeline.setEnabled(True)
        if self._has_external_preview_video():
            audio_note = (
                "识别源音轨"
                if self.project.settings.preview_audio_source == PreviewAudioSource.RECOGNITION
                else "视频音轨"
            )
            media_note = f"外部 MP4 预览视频已加载（当前音频：{audio_note}）"
        else:
            media_note = "MP4 视频音轨已加载" if self._is_video_source() else "MP3 音频已加载"
        self.preview_status_label.setText(f"{media_note}：{source_path.name}")
        self.preview_subtitle_label.setText("点击播放，或拖动时间轴预览字幕。")

    def open_video_preview(self) -> None:
        if not self._is_video_source() or not self._preview_loaded_path:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先导入可用的 MP4 视频文件。")
            return
        if self.video_preview_dialog is None:
            dialog = VideoPreviewDialog(self)
            dialog.finished.connect(self._video_preview_closed)
            dialog.play_button.clicked.connect(self._toggle_preview_playback)
            dialog.stop_button.clicked.connect(self._stop_preview_playback)
            dialog.timeline.sliderPressed.connect(self._video_preview_timeline_pressed)
            dialog.timeline.sliderMoved.connect(self._video_preview_timeline_moved)
            dialog.timeline.sliderReleased.connect(self._video_preview_timeline_released)
            dialog.volume_slider.valueChanged.connect(self._preview_volume_changed)
            dialog.font_size_spin.valueChanged.connect(self._preview_subtitle_font_changed)
            saved_font_size = int(self.local_settings.value("preview/subtitle_font_size", 28))
            dialog.font_size_spin.setValue(max(16, min(64, saved_font_size)))
            dialog.volume_slider.setValue(round(self._preview_audio_volume * 100))
            self.video_preview_dialog = dialog
        dialog = self.video_preview_dialog
        self.audio_player.setVideoOutput(dialog.video_widget)
        dialog.timeline.setRange(0, max(0, self.audio_player.duration()))
        dialog.timeline.setValue(self.audio_player.position())
        dialog.duration_label.setText(self._format_preview_time(self.audio_player.duration()))
        self._update_preview_subtitle(self.audio_player.position(), scroll=False)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.status_label.setText("视频画面、播放/暂停和字幕时间轴正与校对页同步。")

    def _video_preview_closed(self, _result: int = 0) -> None:
        self.audio_player.setVideoOutput(None)
        self.video_preview_dialog = None

    def _close_video_preview(self) -> None:
        dialog = self.video_preview_dialog
        if dialog is None:
            return
        self.audio_player.setVideoOutput(None)
        dialog.close()
        self.video_preview_dialog = None

    def _preview_entry_at(self, position_ms: int) -> tuple[int, Any] | None:
        offset = self.project.settings.offset_ms
        for row, entry in enumerate(self.project.entries):
            start = entry.effective_start_ms
            end = entry.effective_end_ms
            if start is None or end is None:
                continue
            shown_start = max(0, start + offset)
            shown_end = max(shown_start, end + offset)
            if shown_start <= position_ms <= shown_end:
                return row, entry
        return None

    def _update_preview_subtitle(self, position_ms: int, scroll: bool = True) -> None:
        self.preview_position_label.setText(self._format_preview_time(position_ms))
        found = self._preview_entry_at(position_ms)
        if found is None:
            self.preview_subtitle_label.setText("")
            if self.video_preview_dialog is not None:
                self.video_preview_dialog.subtitle_label.setText("")
                self.video_preview_dialog.status_label.setText("当前时间没有字幕。")
            self._preview_active_row = None
            return
        row, entry = found
        display_text = subtitle_output_text(
            entry,
            self.project.settings.subtitle_output_mode,
            self.project.settings.bilingual_order,
        )
        self.preview_subtitle_label.setText(display_text)
        if self.video_preview_dialog is not None:
            self.video_preview_dialog.subtitle_label.setText(display_text)
            self.video_preview_dialog.status_label.setText(
                f"当前字幕 · {self._format_preview_time(position_ms)}"
            )
        if row == self._preview_active_row:
            return
        self._preview_active_row = row
        self.review_table.selectRow(row)
        if scroll:
            item = self.review_table.item(row, 1)
            if item is not None:
                self.review_table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _seek_preview_to_entry(self, row: int) -> None:
        if row < 0 or row >= len(self.project.entries):
            return
        start = self.project.entries[row].effective_start_ms
        if start is None:
            self.statusBar().showMessage("该条字幕没有可用于定位的开始时间。", 5000)
            return
        position_ms = max(0, start + self.project.settings.offset_ms)
        self.audio_player.setPosition(position_ms)
        self.preview_timeline.setValue(position_ms)
        self._update_preview_subtitle(position_ms, scroll=False)
        self.statusBar().showMessage(
            f"已定位到第 {row + 1} 条字幕的开始时间：{self._format_preview_time(position_ms)}", 4000
        )

    def _preview_position_changed(self, position_ms: int) -> None:
        if (
            self.project.settings.preview_audio_source == PreviewAudioSource.RECOGNITION
            and self._has_external_preview_video()
            and abs(self.preview_sync_audio_player.position() - position_ms) > 180
        ):
            self.preview_sync_audio_player.setPosition(position_ms)
        if not self._timeline_scrubbing:
            self.preview_timeline.setValue(position_ms)
            self._update_preview_subtitle(position_ms)
        dialog = self.video_preview_dialog
        if dialog is not None and not self._video_preview_scrubbing:
            dialog.timeline.setValue(position_ms)
            dialog.position_label.setText(self._format_preview_time(position_ms))

    def _preview_duration_changed(self, duration_ms: int) -> None:
        self.preview_timeline.setRange(0, max(0, duration_ms))
        self.preview_duration_label.setText(self._format_preview_time(duration_ms))
        self.preview_timeline.setEnabled(duration_ms > 0)
        if self.video_preview_dialog is not None:
            self.video_preview_dialog.timeline.setRange(0, max(0, duration_ms))
            self.video_preview_dialog.duration_label.setText(self._format_preview_time(duration_ms))

    def _preview_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.preview_play_button.setText("暂停" if playing else "播放")
        if self.project.settings.preview_audio_source == PreviewAudioSource.RECOGNITION:
            if playing:
                self.preview_sync_audio_player.play()
            else:
                self.preview_sync_audio_player.pause()
        if self.video_preview_dialog is not None:
            self.video_preview_dialog.play_button.setText("暂停" if playing else "播放")
        if playing:
            self.preview_status_label.setText("正在播放；当前字幕会自动高亮。")
        elif self._preview_loaded_path:
            self.preview_status_label.setText("已暂停；拖动时间轴可快速定位。")

    def _toggle_preview_playback(self) -> None:
        if not self._preview_loaded_path:
            QMessageBox.information(self, APP_DISPLAY_NAME, "请先导入可用的 MP3 或 MP4 媒体文件。")
            return
        if self.audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.audio_player.pause()
        else:
            if self.project.settings.preview_audio_source == PreviewAudioSource.RECOGNITION:
                self.preview_sync_audio_player.setPosition(self.audio_player.position())
            self.audio_player.play()

    def _stop_preview_playback(self) -> None:
        self.audio_player.stop()
        self.preview_sync_audio_player.stop()
        self.audio_player.setPosition(0)
        self.preview_sync_audio_player.setPosition(0)
        self._update_preview_subtitle(0)

    def _preview_timeline_pressed(self) -> None:
        self._timeline_scrubbing = True

    def _preview_timeline_moved(self, position_ms: int) -> None:
        self._update_preview_subtitle(position_ms, scroll=False)

    def _preview_timeline_released(self) -> None:
        position_ms = self.preview_timeline.value()
        self.audio_player.setPosition(position_ms)
        self.preview_sync_audio_player.setPosition(position_ms)
        self._timeline_scrubbing = False
        self._update_preview_subtitle(position_ms)

    def _video_preview_timeline_pressed(self) -> None:
        self._video_preview_scrubbing = True

    def _video_preview_timeline_moved(self, position_ms: int) -> None:
        self._update_preview_subtitle(position_ms, scroll=False)

    def _video_preview_timeline_released(self) -> None:
        dialog = self.video_preview_dialog
        if dialog is None:
            return
        position_ms = dialog.timeline.value()
        self.audio_player.setPosition(position_ms)
        self.preview_sync_audio_player.setPosition(position_ms)
        self._video_preview_scrubbing = False
        self._update_preview_subtitle(position_ms)

    def _preview_volume_changed(self, value: int) -> None:
        self._preview_audio_volume = max(0.0, min(1.0, value / 100.0))
        self._apply_preview_audio_source()

    def _preview_subtitle_font_changed(self, pixel_size: int) -> None:
        dialog = self.video_preview_dialog
        if dialog is None:
            return
        dialog.set_subtitle_font_size(pixel_size)
        self.local_settings.setValue("preview/subtitle_font_size", pixel_size)

    def _subtitle_output_settings_changed(self) -> None:
        mode = SubtitleOutputMode(str(self.subtitle_output_mode_combo.currentData()))
        order = BilingualOrder(str(self.bilingual_order_combo.currentData()))
        self.project.settings.subtitle_output_mode = mode
        self.project.settings.bilingual_order = order
        self.project.settings.primary_language_label = (
            self.primary_language_edit.text().strip() or "原文"
        )
        self.project.settings.translation_language_label = self._current_translation_language()
        bilingual = mode == SubtitleOutputMode.BILINGUAL
        self.bilingual_order_combo.setEnabled(bilingual)
        self.preview_subtitle_label.setMinimumHeight(102 if bilingual else 62)
        self.preview_subtitle_label.setStyleSheet(
            "font-size: 20px; font-weight: 600; line-height: 1.35; padding: 8px 16px;"
            if bilingual
            else ""
        )
        if bilingual:
            self.project.settings.subtitle_layout = SubtitleLayoutMode.TWO_LINES
            self.subtitle_layout_combo.blockSignals(True)
            self._set_combo_data(self.subtitle_layout_combo, SubtitleLayoutMode.TWO_LINES)
            self.subtitle_layout_combo.blockSignals(False)
        self._refresh_tables()
        self._update_preview_subtitle(self.audio_player.position(), scroll=False)
        self._mark_dirty()

    def _offset_changed(self, value: int) -> None:
        self.project.settings.offset_ms = value
        self._refresh_tables()
        self._refresh_risk()
        self._update_preview_subtitle(self.audio_player.position(), scroll=False)
        self._mark_dirty()

    def show_validation(self) -> None:
        issues = validate_entries(
            self.project.entries,
            self.project.settings.offset_ms,
            self.project.settings.subtitle_output_mode,
        )
        if not issues:
            QMessageBox.information(
                self, APP_DISPLAY_NAME, "时间轴检查通过，未发现起止时间或重叠问题。"
            )
            return
        text = "\n".join(
            f"第 {index + 1} 条：{issue.message}" for index, issue in enumerate(issues)
        )
        QMessageBox.warning(self, "时间轴检查", text)

    def export_current_srt(self) -> None:
        if not self.project.entries:
            QMessageBox.information(self, APP_DISPLAY_NAME, "当前没有可导出的字幕条目。")
            return
        issues = validate_entries(
            self.project.entries,
            self.project.settings.offset_ms,
            self.project.settings.subtitle_output_mode,
        )
        critical = [issue for issue in issues if issue.severity == "error"]
        if critical:
            response = QMessageBox.warning(
                self,
                "存在无时间锚点条目",
                "部分条目没有起止时间，无法写入 SRT。建议回到切分页填写毫秒时间。是否仍导出其余条目？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        elif issues:
            response = QMessageBox.question(
                self,
                "发现时间轴警告",
                "检测到重叠或零时长条目。您确认已复核后仍要导出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        default_name = f"{self._filename_stem(self.project.title or '字幕')}.srt"
        destination = self._save_file_dialog("导出 SRT", default_name, "SRT 字幕 (*.srt)")
        if not destination:
            return
        target = export_srt(
            self.project.entries,
            destination,
            self.project.settings.offset_ms,
            self.project.settings.subtitle_output_mode,
            self.project.settings.bilingual_order,
        )
        self.statusBar().showMessage(f"已导出 SRT：{target}", 8000)

    def export_text(self, text: str, label: str) -> None:
        if not text.strip():
            QMessageBox.information(self, APP_DISPLAY_NAME, f"没有可导出的{label}。")
            return
        destination = self._save_file_dialog(
            f"导出{label}",
            f"{self._filename_stem(self.project.title)}-{label}.txt",
            "文本文件 (*.txt)",
        )
        if destination:
            Path(destination).write_text(text, encoding="utf-8", newline="\n")
            self.statusBar().showMessage(f"已导出{label}。", 6000)

    def export_segmented_text(self) -> None:
        self.export_text("\n\n".join(entry.text for entry in self.project.entries), "分行文本")

    def configure_llm(self) -> None:
        store = ProfileStore()
        dialog = LLMProfileDialog(self, store.active_profile())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            active = store.active_profile()
            profile_name = active.name if active else "无"
            self._refresh_llm_inspector()
            self.statusBar().showMessage(
                f"模型服务 Profile 已保存；当前使用：{profile_name}。API 密钥不会写入项目文件。",
                8000,
            )

    def new_project(self) -> None:
        if not self._confirm_discard_if_needed("新建项目"):
            return
        self.project = SubtitleProject(app_version=VERSION)
        self.project_path = None
        self._draft_restored = False
        self.original_editor.clear()
        self.cleanup_source_editor.clear()
        self.cleaned_editor.clear()
        self._refresh_all()
        self.nav.setCurrentRow(0)

    def _confirm_discard_if_needed(self, action: str) -> bool:
        if not self._project_dirty:
            return True
        response = QMessageBox.warning(
            self,
            APP_DISPLAY_NAME,
            f"当前工程有未保存的编辑。是否在{action}前保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if response == QMessageBox.StandardButton.Save:
            return self.save_project()
        if response == QMessageBox.StandardButton.Discard:
            if self.project_path:
                remove_autosave(self.project_path)
            return True
        return False

    def _populate_editors_from_project(self) -> None:
        self.original_editor.blockSignals(True)
        self.original_editor.setPlainText(self.project.original_text)
        self.original_editor.blockSignals(False)
        self.cleanup_source_editor.setPlainText(self.project.original_text)
        self.cleaned_editor.blockSignals(True)
        self.cleaned_editor.setPlainText(self.project.cleaned_text or self.project.original_text)
        self.cleaned_editor.blockSignals(False)
        self._set_combo_data(self.language_combo, self.project.settings.language_mode)
        self._set_combo_data(self.model_combo, self.project.settings.model_id)
        self._set_combo_data(self.device_combo, self.project.settings.device)
        self._refresh_all()

    def _load_recovery_snapshot(self, project_path: Path) -> SubtitleProject | None:
        recovery_path = autosave_path(project_path)
        if not recovery_path.is_file():
            return None
        try:
            recovery = load_project(recovery_path)
        except ProjectFormatError:
            recovery_path.unlink(missing_ok=True)
            return None
        response = QMessageBox.question(
            self,
            "发现自动保存的恢复草稿",
            "检测到上次未完全保存的编辑草稿。是否恢复这份自动保存内容？\n\n"
            f"草稿时间：{recovery.draft.captured_at}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if response == QMessageBox.StandardButton.Yes:
            return recovery
        recovery_path.unlink(missing_ok=True)
        return None

    def open_project(self) -> None:
        if not self._confirm_discard_if_needed("打开其他项目"):
            return
        path = self._open_file_dialog("打开弦字幕项目", "弦字幕项目 (*.lyrasub)")
        if not path:
            return
        project_path = Path(path)
        try:
            loaded = load_project(project_path)
        except ProjectFormatError as exc:
            QMessageBox.critical(self, APP_DISPLAY_NAME, str(exc))
            return
        recovery = self._load_recovery_snapshot(project_path)
        self.project = recovery or loaded
        self.project_path = project_path
        self._draft_restored = False
        self._populate_editors_from_project()
        if self.project.draft.captured_at:
            restored_page = min(self.project.draft.active_page, self.pages.count() - 1)
            self._change_page(restored_page)
            self.nav.setCurrentRow(min(restored_page, self.nav.count() - 1))
        draft_restored = self._restore_editor_draft()
        if recovery:
            self._mark_dirty()
            detail = "已恢复自动保存的阶段成果和未确认编辑草稿。请检查后保存工程。"
        elif draft_restored:
            detail = "已恢复工程内保存的未确认编辑草稿。"
        else:
            detail = "项目已打开；原始锚点和已确认编辑状态已恢复。"
        self.statusBar().showMessage(detail, 9000)

    def save_project(self) -> bool:
        if not self.project_path:
            path = self._save_file_dialog(
                "保存弦字幕项目",
                f"{self._filename_stem(self.project.title)}.lyrasub",
                "弦字幕项目 (*.lyrasub)",
            )
            if not path:
                return False
            self.project_path = Path(path)
        self._capture_editor_draft()
        self.project.app_version = VERSION
        try:
            saved = save_project(self.project, self.project_path)
        except OSError as exc:
            QMessageBox.critical(self, APP_DISPLAY_NAME, f"无法保存工程：{exc}")
            return False
        self.project_path = saved
        remove_autosave(saved)
        self.statusBar().showMessage(f"项目已保存：{saved}", 6000)
        self._clear_dirty_marker()
        return True

    def save_project_as(self) -> bool:
        default_name = f"{self._filename_stem(self.project.title)}.lyrasub"
        path = self._save_file_dialog("弦字幕项目另存为", default_name, "弦字幕项目 (*.lyrasub)")
        if not path:
            return False
        old_path = self.project_path
        self._capture_editor_draft()
        self.project.app_version = VERSION
        try:
            saved = save_project(self.project, Path(path))
        except OSError as exc:
            QMessageBox.critical(self, APP_DISPLAY_NAME, f"无法另存工程：{exc}")
            return False
        self.project_path = saved
        if old_path and old_path != saved:
            remove_autosave(old_path)
        remove_autosave(saved)
        self._clear_dirty_marker()
        self.statusBar().showMessage(f"项目已另存为并切换到新工程：{saved}", 8000)
        return True

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            f"关于 {APP_DISPLAY_NAME}",
            f"<h2>《{APP_DISPLAY_NAME}》</h2><p>版本：{VERSION}</p><p>{ABOUT_PRODUCER}</p><p>{ABOUT_EMAIL}</p>"
            "<p>本地转写 · 可编辑时间锚点 · 影视字幕 SRT 工作流</p>",
        )

    @staticmethod
    def _format_task_seconds(seconds: float) -> str:
        rounded = max(0, round(seconds))
        return f"{rounded // 60:02d}:{rounded % 60:02d}"

    def _task_progress_detail_text(self) -> str:
        elapsed = monotonic() - self._task_started_at if self._task_started_at else 0.0
        detail = f"已用时 {self._format_task_seconds(elapsed)}"
        return detail

    def _refresh_task_elapsed(self) -> None:
        if self._task_started_at is None or self.task_progress_panel.isHidden():
            return
        self.task_progress_detail.setText(self._task_progress_detail_text())

    def _begin_task_progress(self, title: str) -> None:
        self._task_failed_flag = False
        self._task_title = title
        self._task_started_at = monotonic()
        self.task_progress_title.setText(title)
        self.task_progress_detail.setText("已用时 00:00")
        self.task_progress_bar.setRange(0, 0)
        self.task_progress_bar.setFormat("正在准备……")
        self.task_cancel_button.setEnabled(True)
        self.task_progress_panel.setVisible(True)
        self._task_elapsed_timer.start()

    def _update_task_progress(self, message: str, value: int | None) -> None:
        if value is None:
            self.task_progress_bar.setRange(0, 0)
            self.task_progress_bar.setFormat("正在处理……")
        else:
            bounded = max(0, min(100, int(value)))
            self.task_progress_bar.setRange(0, 100)
            self.task_progress_bar.setValue(bounded)
            self.task_progress_bar.setFormat(f"{bounded}%")
        self._refresh_task_elapsed()
        self.statusBar().showMessage(message.splitlines()[0])

    def _finish_task_progress(self, message: str, success: bool) -> None:
        self._task_elapsed_timer.stop()
        elapsed = monotonic() - self._task_started_at if self._task_started_at else 0.0
        self.task_progress_bar.setRange(0, 100)
        self.task_progress_bar.setValue(100 if success else self.task_progress_bar.value())
        self.task_progress_bar.setFormat("完成" if success else "已停止")
        self.task_progress_detail.setText(f"{message} · 用时 {self._format_task_seconds(elapsed)}")
        self.task_cancel_button.setEnabled(False)
        QTimer.singleShot(3500, self._hide_finished_task_progress)

    def _hide_finished_task_progress(self) -> None:
        if not self._worker_thread:
            self.task_progress_panel.setVisible(False)

    def request_task_cancel(self) -> None:
        if not self._worker:
            return
        self._worker.request_cancel()
        if self._download_cancel_event:
            self._download_cancel_event.set()
        self.task_cancel_button.setEnabled(False)
        self.task_progress_detail.setText("已请求停止；正在等待当前安全检查点……")
        self.statusBar().showMessage("正在停止当前任务……")

    def _run_worker(
        self,
        operation: Callable[[Callable[[str, int | None], None], Event], Any],
        on_success: Callable[[Any], None],
        progress_callback: Callable[[str, int | None], None] | None = None,
        task_title: str = "正在处理任务",
    ) -> None:
        if self._worker_thread:
            QMessageBox.information(self, APP_DISPLAY_NAME, "已有任务正在运行，请等待完成。")
            return
        self._begin_task_progress(task_title)
        thread = QThread(self)
        worker = TaskWorker(operation)
        bridge = MainThreadTaskBridge(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # The bridge is owned by MainWindow, hence queued signal delivery always
        # executes UI callbacks on the GUI thread rather than the worker thread.
        worker.progress.connect(bridge.deliver_progress)
        worker.succeeded.connect(bridge.deliver_success)
        worker.failed.connect(bridge.deliver_failure)
        worker.finished.connect(bridge.deliver_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)

        def on_progress(message: str, value: int | None) -> None:
            self._update_task_progress(message, value)
            if progress_callback:
                progress_callback(message, value)

        bridge.progress.connect(on_progress)
        bridge.succeeded.connect(on_success)
        bridge.failed.connect(self._task_failed)
        bridge.finished.connect(self._task_finished)
        thread.finished.connect(thread.deleteLater)
        self._worker_thread = thread
        self._worker = worker
        self._task_bridge = bridge
        thread.start()

    def _task_failed(self, message: str) -> None:
        self._task_failed_flag = True
        if message.startswith(TaskCancelledError.marker) or message.startswith(
            "[DOWNLOAD_CANCELLED]"
        ):
            self._download_active = False
            self._download_cancel_event = None
            self._finish_task_progress("任务已停止", False)
            self.statusBar().showMessage("任务已停止；未确认的结果不会写入项目。", 8000)
            return
        if self._download_active:
            self._download_active = False
            self._download_cancel_event = None
            self.stop_download_button.setEnabled(False)
            self.test_model_source_button.setEnabled(True)
            self.refresh_model_download_status()
            if message.startswith("[DOWNLOAD_CANCELLED]"):
                self.download_progress.setFormat("已停止")
                self.statusBar().showMessage("模型下载已停止；未完成的临时文件已清理。", 8000)
                QMessageBox.information(self, "模型下载已停止", message)
                return
            self.download_progress.setFormat("下载失败")
            self._finish_task_progress("下载失败", False)
            DownloadDiagnosticDialog(self, message).exec()
            self.statusBar().showMessage(
                "模型下载失败。请复制诊断信息并反馈，或切换来源/代理。", 10000
            )
            return
        if hasattr(self, "test_model_source_button"):
            self.test_model_source_button.setEnabled(True)
        if hasattr(self, "download_model_button"):
            self.refresh_model_download_status()
        else:
            self.recognize_button.setEnabled(True)
        self._finish_task_progress("任务未完成", False)
        DownloadDiagnosticDialog(
            self,
            message,
            title="任务未完成",
            prompt_text="请复制完整诊断信息，以便排查 GPU、网络、模型或本地运行环境问题。",
        ).exec()
        self.statusBar().showMessage(
            "任务未完成。请检查模型来源、网络、磁盘空间或本地运行环境。", 8000
        )

    def _task_finished(self) -> None:
        if self._worker_thread and not self._task_failed_flag:
            self._finish_task_progress("任务完成", True)
        if hasattr(self, "download_model_button"):
            self.stop_download_button.setEnabled(False)
            self.test_model_source_button.setEnabled(True)
            self.refresh_model_download_status()
        if hasattr(self, "recognize_button") and not self._download_active:
            self.recognize_button.setEnabled(
                self.models.is_model_downloaded(str(self.model_combo.currentData()))
            )
        if hasattr(self, "gpu_diagnostic_button"):
            self.gpu_diagnostic_button.setEnabled(True)
        self._worker_thread = None
        self._worker = None
        self._task_bridge = None
        if self._pending_local_segmentation and not self._task_failed_flag:
            self._pending_local_segmentation = False
            QTimer.singleShot(0, self.run_local_segmentation)

    def closeEvent(self, event: Any) -> None:
        if self._worker_thread:
            QMessageBox.warning(
                self, APP_DISPLAY_NAME, "识别或文本任务仍在运行，请等待其完成或停止后再关闭。"
            )
            event.ignore()
            return
        if self._confirm_discard_if_needed("退出"):
            event.accept()
        else:
            event.ignore()
