import os
from pathlib import Path
from threading import Event
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QFileDialog, QToolBar

import lyrasub.main_window as main_window_module
from lyrasub.llm_config import LLMProfile, ProfileStore, SecretStore
from lyrasub.main_window import LargeTextEditorDialog, MainWindow
from lyrasub.models import (
    Anchor,
    BilingualOrder,
    PreviewAudioSource,
    SubtitleEntry,
    SubtitleOutputMode,
    SubtitleProject,
)
from lyrasub.project_io import autosave_path, load_project, save_project
from lyrasub.segment_merge import MergeEntry, MergeProject, MergeSegment, SegmentSourceKind
from lyrasub.tasks import TaskCancelledError
from lyrasub.transcription import SUPPORTED_MEDIA_SUFFIXES


def test_main_window_builds_at_design_size() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1600, 900)
    window.show()
    app.processEvents()
    assert window.width() == 1600
    assert window.height() == 900
    assert "弦字幕 Plus（LyraSub Plus）" in window.windowTitle()
    assert "v260822A" in window.windowTitle()
    assert window.nav.count() == 6
    assert window.pages.count() == 6
    assert "导入媒体" in window.nav.item(0).text()
    assert "分段合并" in window.nav.item(5).text()
    assert window.entry_table.columnCount() == 5
    assert window.model_manager_dialog.windowTitle() == "本地模型管理"
    assert window.open_model_manager_button.isEnabled()
    window.close()


def test_plus_merge_workspace_and_autosave_toolbar_entry() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.merge_project = MergeProject(
        title="电影合并",
        segments=[
            MergeSegment(
                title="第一章",
                source_kind=SegmentSourceKind.SRT,
                source_path="chapter-01.srt",
                origin_ms=0,
                audio_duration_ms=10_000,
                entries=[MergeEntry(text="第一章字幕", local_start_ms=1_000, local_end_ms=2_000)],
            ),
            MergeSegment(
                title="第二章",
                source_kind=SegmentSourceKind.SRT,
                source_path="chapter-02.srt",
                origin_ms=12_000,
                entries=[MergeEntry(text="第二章字幕", local_start_ms=0, local_end_ms=1_000)],
            ),
        ],
    )
    window._refresh_merge_workspace()
    app.processEvents()

    assert window.merge_segment_table.rowCount() == 2
    assert window.merge_global_table.rowCount() == 2
    assert window.merge_global_table.item(1, 2).text() == "00:00:12.000"
    toolbar = window.findChild(QToolBar)
    assert toolbar is not None
    labels = [action.text() for action in toolbar.actions()]
    assert labels.index("自动保存") + 1 == labels.index("关于")
    window.close()


def test_llm_profile_dialog_switches_and_deletes_profiles(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    store = ProfileStore(tmp_path / "profiles")
    primary = LLMProfile(
        id="primary",
        name="主服务",
        base_url="https://primary.example/v1",
        model="model-primary",
        api_key_ref="secret_primary",
    )
    backup = LLMProfile(
        id="backup",
        name="备用服务",
        base_url="https://backup.example/v1",
        model="model-backup",
        api_key_ref="secret_backup",
    )
    store.save_all([primary, backup], active_profile_id=primary.id)
    monkeypatch.setattr(main_window_module, "ProfileStore", lambda: store)
    dialog = main_window_module.LLMProfileDialog(None, store.active_profile())
    dialog.secrets = SecretStore(tmp_path / "secrets")
    assert dialog.profile_selector.count() == 2

    dialog.profile_selector.setCurrentIndex(dialog.profile_selector.findData("backup"))
    app.processEvents()
    assert store.active_profile_id() == "backup"
    assert dialog.name.text() == "备用服务"

    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "question",
        lambda *args, **kwargs: main_window_module.QMessageBox.StandardButton.Yes,
    )
    dialog.delete_current_profile()
    assert [profile.id for profile in store.list()] == ["primary"]
    assert store.active_profile_id() == "primary"
    dialog.close()


def test_editor_draft_snapshot_round_trips_with_project(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.original_text = "已确认原文"
    window.project.cleaned_text = "已确认清洗文本"
    window.original_editor.setPlainText("正在校订的原文")
    window.cleaned_editor.setPlainText("正在修改、尚未确认的清洗文本")
    window.nav.setCurrentRow(3)
    app.processEvents()
    window._capture_editor_draft()
    destination = save_project(window.project, tmp_path / "draft")
    restored = load_project(destination)
    assert restored.draft.original_editor_text == "正在校订的原文"
    assert restored.draft.cleaned_editor_text == "正在修改、尚未确认的清洗文本"
    assert restored.draft.active_page == 3
    window._clear_dirty_marker()
    window.close()


def test_imported_srt_creates_manual_timed_entries_without_anchors(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "external.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,250\n外部字幕\n\n2\n00:00:03,000 --> 00:00:04,000\n第二条\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(source), "SRT 字幕 (*.srt)"),
    )
    window = MainWindow()
    window.import_srt()
    app.processEvents()

    assert window.project.anchors == []
    assert len(window.project.entries) == 2
    assert window.project.entries[0].anchor_ids == []
    assert window.project.entries[0].manual_start_ms == 1_000
    assert window.project.entries[0].manual_end_ms == 2_250
    assert window.nav.currentRow() == 4
    window._clear_dirty_marker()
    window.close()


def test_file_dialog_memory_is_shared_by_open_and_save_operations(
    tmp_path: Path, monkeypatch
) -> None:
    QApplication.instance() or QApplication([])
    first_folder = tmp_path / "first"
    second_folder = tmp_path / "second"
    first_folder.mkdir()
    second_folder.mkdir()
    selected_open = first_folder / "input.srt"
    selected_save = second_folder / "output.srt"
    calls: list[tuple[object, ...]] = []

    def choose_open(*args: object, **_kwargs: object) -> tuple[str, str]:
        calls.append(args)
        return str(selected_open), "SRT 字幕 (*.srt)"

    def choose_save(*args: object, **_kwargs: object) -> tuple[str, str]:
        calls.append(args)
        return str(selected_save), "SRT 字幕 (*.srt)"

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose_open)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_save)
    window = MainWindow()
    window.local_settings.remove("files/last_directory")

    assert window._open_file_dialog("导入字幕", "SRT 字幕 (*.srt)") == str(selected_open)
    assert window._dialog_directory() == str(first_folder)
    assert window._save_file_dialog("导出字幕", "output.srt", "SRT 字幕 (*.srt)") == str(
        selected_save
    )
    assert calls[1][2] == str(first_folder / "output.srt")
    assert window._dialog_directory() == str(second_folder)
    window.local_settings.remove("files/last_directory")
    window.close()


def test_translation_language_dropdown_handles_common_and_custom_values(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    calls: list[str] = []
    monkeypatch.setattr(
        window,
        "_subtitle_output_settings_changed",
        lambda: calls.append(window._current_translation_language()),
    )

    assert not window.translation_language_edit.isEditable()
    japanese_index = window.translation_language_edit.findData("日本語（日语）")
    window.translation_language_edit.setCurrentIndex(japanese_index)
    assert calls == ["日本語（日语）"]

    monkeypatch.setattr(
        main_window_module.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("阿拉伯语", True),
    )
    custom_index = window.translation_language_edit.findData("__custom_language__")
    window.translation_language_edit.setCurrentIndex(custom_index)
    assert window._current_translation_language() == "阿拉伯语"
    assert window.translation_language_edit.currentText() == "自定义：阿拉伯语"
    assert calls == ["日本語（日语）", "阿拉伯语"]
    window.close()


def test_save_as_switches_project_path_without_removing_original(
    tmp_path: Path, monkeypatch
) -> None:
    QApplication.instance() or QApplication([])
    original_path = save_project(
        SubtitleProject(title="原工程", original_text="旧内容"), tmp_path / "old"
    )
    new_path = tmp_path / "new-copy.lyrasub"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(new_path), "弦字幕项目 (*.lyrasub)"),
    )
    window = MainWindow()
    window.project_path = original_path
    window.project.title = "新工程"
    window.project.original_text = "新内容"

    assert window.save_project_as()
    assert window.project_path == new_path
    assert original_path.is_file()
    assert load_project(original_path).original_text == "旧内容"
    assert load_project(new_path).original_text == "新内容"
    window.close()


def test_multiline_entry_expands_table_row_and_review_count_matches() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.anchors = [
        Anchor(id="a1", text="我", start_ms=0, end_ms=100),
        Anchor(id="a2", text="好", start_ms=100, end_ms=200),
    ]
    window.project.entries = [
        SubtitleEntry(
            text="我利用Vibax平台\n设计制作的AI应用工具",
            anchor_ids=["a1", "a2"],
            start_ms=0,
            end_ms=200,
        )
    ]
    window._refresh_all()
    app.processEvents()
    assert window.entry_table.rowCount() == 1
    assert window.review_table.rowCount() == 1
    assert window.entry_table.rowHeight(0) >= 60
    window.close()


def test_review_preview_and_entry_table_editing_workflow() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.anchors = [
        Anchor(id="a1", text="大家好", start_ms=0, end_ms=800),
        Anchor(id="a2", text="我是海舟", start_ms=800, end_ms=1600),
    ]
    window.project.entries = [
        SubtitleEntry(
            text="大家好 我是海舟",
            anchor_ids=["a1", "a2"],
            start_ms=0,
            end_ms=1600,
        )
    ]
    window._refresh_all()
    window._preview_position_changed(900)
    app.processEvents()

    assert window.preview_subtitle_label.text() == "大家好 我是海舟"
    assert window.review_table.currentRow() == 0
    assert window.entry_table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert window.entry_table.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    assert window.review_table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert window.llm_segmentation_button.text() == "自然语义切分"
    window.preview_timeline.setRange(0, 5_000)
    window._on_entry_table_cell_clicked(window.review_table, 0, 0)
    assert window.preview_timeline.value() == 0
    window._update_preview_subtitle(3_000)
    assert window.preview_subtitle_label.text() == ""
    window.close()


def test_bilingual_preview_uses_selected_order() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.entries = [
        SubtitleEntry(text="你好", translation_text="Hello", start_ms=0, end_ms=1_000)
    ]
    window.project.settings.subtitle_output_mode = SubtitleOutputMode.BILINGUAL
    window.project.settings.bilingual_order = BilingualOrder.TRANSLATION_TOP
    window._refresh_all()
    window._preview_position_changed(500)
    app.processEvents()

    assert window.preview_subtitle_label.text() == "Hello\n你好"
    assert str(window.subtitle_layout_combo.currentData()) == "two_lines"
    window.close()


def test_review_table_exposes_translation_and_bilingual_safe_area() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.entries = [
        SubtitleEntry(text="你好", translation_text="Hello", start_ms=0, end_ms=1_000)
    ]
    window.project.settings.subtitle_output_mode = SubtitleOutputMode.BILINGUAL
    window._refresh_all()
    app.processEvents()

    assert window.review_table.columnCount() == 6
    assert window.review_table.item(0, 1).text() == "你好"
    assert window.review_table.item(0, 2).text() == "Hello"
    assert window.preview_subtitle_label.minimumHeight() >= 102
    japanese_index = window.translation_language_edit.findData("日本語（日语）")
    window.translation_language_edit.setCurrentIndex(japanese_index)
    assert window.project.settings.translation_language_label == "日本語（日语）"
    window._clear_dirty_marker()
    window.close()


def test_mp4_preview_entry_and_subtitle_sync(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    media = tmp_path / "movie.mp4"
    media.write_bytes(b"not-a-real-video")
    window = MainWindow()
    window.project.source.path = str(media)
    window.project.entries = [SubtitleEntry(text="视频字幕", start_ms=500, end_ms=1_500)]
    window._sync_preview_source()
    app.processEvents()

    assert ".mp4" in SUPPORTED_MEDIA_SUFFIXES
    assert window._is_video_source()
    assert window.preview_video_button.isEnabled()
    window.open_video_preview()
    dialog = window.video_preview_dialog
    assert dialog is not None
    assert dialog.play_button.text() == "播放"
    assert dialog.stop_button.text() == "停止"
    assert dialog.timeline.minimum() == 0
    dialog.set_subtitle_font_size(40)
    assert dialog.subtitle_label.minimumHeight() >= 140
    window._update_preview_subtitle(1_000)
    assert dialog.subtitle_label.text() == "视频字幕"
    window._close_video_preview()
    assert window.video_preview_dialog is None
    window.close()


def test_external_preview_video_can_bind_to_mp3_source(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    audio = tmp_path / "recognition-source.mp3"
    video = tmp_path / "high-resolution-preview.mp4"
    audio.write_bytes(b"not-a-real-audio")
    video.write_bytes(b"not-a-real-video")
    window = MainWindow()
    window.project.source.path = str(audio)
    window._sync_preview_source()
    app.processEvents()

    assert window._preview_media_path() == str(audio)
    assert not window._is_video_source()
    window.project.source.preview_video_path = str(video)
    window._sync_preview_source()
    app.processEvents()
    assert window.project.source.path == str(audio)
    assert window._has_external_preview_video()
    assert window._preview_media_path() == str(video)
    assert window._is_video_source()
    assert window.preview_video_button.isEnabled()
    assert window.preview_video_clear_button.isEnabled()
    assert window.preview_audio_source_combo.isEnabled()
    window.project.settings.preview_audio_source = PreviewAudioSource.RECOGNITION
    window._sync_preview_source()
    assert (
        window.preview_sync_audio_player.source().toLocalFile().endswith("recognition-source.mp3")
    )
    assert window.audio_output.volume() == 0.0

    window.clear_preview_video()
    assert window.project.source.path == str(audio)
    assert window.project.source.preview_video_path == ""
    assert window._preview_media_path() == str(audio)
    assert not window._is_video_source()
    window._clear_dirty_marker()
    window.close()


def test_review_sequence_click_seeks_to_shifted_entry_start() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.anchors = [Anchor(id="a1", text="字幕", start_ms=1_200, end_ms=2_000)]
    window.project.entries = [
        SubtitleEntry(text="测试字幕", anchor_ids=["a1"], start_ms=1_200, end_ms=2_000)
    ]
    window.project.settings.offset_ms = -200
    window._refresh_all()
    window.preview_timeline.setRange(0, 5_000)

    window._on_entry_table_cell_clicked(window.review_table, 0, 0)
    app.processEvents()

    assert window.preview_timeline.value() == 1_000
    assert window.preview_position_label.text() == "00:00:01.000"
    assert window.preview_subtitle_label.text() == "测试字幕"
    window.close()


def test_manual_timing_fence_and_insertion_gap_detection() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.entries = [
        SubtitleEntry(text="第一条", start_ms=0, end_ms=1_000),
        SubtitleEntry(text="第二条", start_ms=2_000, end_ms=3_000),
        SubtitleEntry(text="第三条", start_ms=4_000, end_ms=5_000),
    ]

    assert window._validate_entry_timing(1, 1_000, 4_000) is None
    assert "上一条字幕" in (window._validate_entry_timing(1, 999, 3_000) or "")
    assert "下一条字幕" in (window._validate_entry_timing(1, 1_500, 4_001) or "")
    assert "正时长" in (window._validate_entry_timing(1, 2_500, 2_500) or "")
    assert window._insertion_index_at_position(1_500) == 1
    assert window._insertion_index_at_position(2_500) is None
    assert window._insertion_index_at_position(5_500) == 3
    assert window._insertion_timing_fence(1)[:2] == (1_000, 2_000)
    app.processEvents()
    window.close()


def test_inspector_shows_active_llm_profile_without_exposing_key(
    tmp_path: Path, monkeypatch
) -> None:
    app = QApplication.instance() or QApplication([])
    profile_store = ProfileStore(tmp_path / "profiles")
    secret_store = SecretStore(tmp_path / "secrets")
    key_ref = secret_store.put("profile_test", "secret-value")
    profile_store.save_all(
        [
            LLMProfile(
                id="profile_test",
                name="测试服务",
                base_url="https://example.com/v1",
                model="model-test",
                api_key_ref=key_ref,
            )
        ],
        active_profile_id="profile_test",
    )
    monkeypatch.setattr(main_window_module, "ProfileStore", lambda: profile_store)
    monkeypatch.setattr(main_window_module, "SecretStore", lambda: secret_store)
    window = MainWindow()
    window._refresh_llm_inspector()
    app.processEvents()

    assert window.inspector_llm_profile.text() == "测试服务"
    assert window.inspector_llm_model.text() == "model-test（密钥可用）"
    assert "secret-value" not in window.inspector_llm_model.text()
    window.close()


def test_apply_segmentation_changes_enters_review_page() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.anchors = [Anchor(id="a1", text="原文", start_ms=0, end_ms=1_000)]
    window.project.entries = [
        SubtitleEntry(text="原文", anchor_ids=["a1"], start_ms=0, end_ms=1_000)
    ]
    window._refresh_all()
    window.entry_table.item(0, 1).setText("修改后的字幕")
    window.apply_table_and_enter_review()
    app.processEvents()

    assert window.nav.currentRow() == 4
    assert window.apply_table_button.text() == "应用切分修改并进入校对导出"
    window._clear_dirty_marker()
    window.close()


def test_inspector_toggle_reclaims_workspace_width() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1600, 900)
    window.show()
    app.processEvents()

    window.inspector_toggle.setChecked(True)
    app.processEvents()
    assert not window.inspector_panel.isVisible()
    assert window.inspector_toggle.text() == "显示检查器"

    window.inspector_toggle.setChecked(False)
    app.processEvents()
    assert window.inspector_panel.isVisible()
    assert window.inspector_toggle.text() == "隐藏检查器"
    window.close()


def test_project_name_updates_project_and_safe_export_stem() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project_name_edit.setText("海舟：互联网/相会")
    window.apply_project_title()
    app.processEvents()
    assert window.project.title == "海舟：互联网/相会"
    assert window._filename_stem(window.project.title) == "海舟：互联网_相会"
    window._clear_dirty_marker()
    window.close()


def test_automatic_recovery_snapshot_captures_unconfirmed_editor_text(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project_path = tmp_path / "in_progress.lyrasub"
    window.project.original_text = "已确认原文"
    window.project.cleaned_text = "已确认清洗文本"
    window.original_editor.setPlainText("尚未确认的原文草稿")
    window.cleaned_editor.setPlainText("尚未确认的清洗草稿")
    app.processEvents()
    window._autosave_project()
    recovery = autosave_path(window.project_path)
    assert recovery.is_file()
    restored = load_project(recovery)
    assert restored.draft.original_editor_text == "尚未确认的原文草稿"
    assert restored.draft.cleaned_editor_text == "尚未确认的清洗草稿"
    window._clear_dirty_marker()
    window.close()


def test_large_cleanup_editor_and_autosave_timer_controls() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    editor = LargeTextEditorDialog(window, "初始文本")
    assert "font-size: 20px" in editor.editor.styleSheet()
    editor._change_font_size(4)
    editor.editor.setPlainText("大字号修改后的文本")
    assert "font-size: 24px" in editor.editor.styleSheet()
    assert editor.edited_text() == "大字号修改后的文本"

    window._autosave_interval_seconds = 30
    window._configure_autosave_timer()
    assert window._autosave_timer.isActive()
    assert window._autosave_timer.interval() == 30_000
    window._autosave_interval_seconds = 0
    window._configure_autosave_timer()
    assert not window._autosave_timer.isActive()
    editor.close()
    window.close()


def test_merge_keeps_inherited_anchor_timing_without_manual_override() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.project.anchors = [
        Anchor(id="a1", text="你", start_ms=0, end_ms=100),
        Anchor(id="a2", text="好", start_ms=100, end_ms=200),
        Anchor(id="a3", text="世", start_ms=200, end_ms=300),
        Anchor(id="a4", text="界", start_ms=300, end_ms=400),
    ]
    window.project.entries = [
        SubtitleEntry(text="你好", anchor_ids=["a1", "a2"], start_ms=0, end_ms=200),
        SubtitleEntry(text="世界", anchor_ids=["a3", "a4"], start_ms=200, end_ms=400),
    ]
    window._refresh_all()
    window.entry_table.setCurrentCell(0, 1)
    window.entry_table.selectRow(0)
    window.merge_selected_entry(auto_apply=True)
    app.processEvents()

    assert len(window.project.entries) == 1
    merged = window.project.entries[0]
    assert (merged.effective_start_ms, merged.effective_end_ms) == (0, 400)
    assert merged.manual_start_ms is None
    assert merged.manual_end_ms is None
    assert window._entry_status(merged, window.project.anchor_index())[0] == "已继承"
    window._clear_dirty_marker()
    window.close()


def test_task_elapsed_heartbeat_refreshes_without_worker_progress() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._begin_task_progress("正在等待 LLM 返回")
    window._task_started_at = monotonic() - 65
    window._refresh_task_elapsed()
    app.processEvents()

    assert window._task_elapsed_timer.isActive()
    assert window.task_progress_detail.text() == "已用时 01:05"
    assert "预计剩余" not in window.task_progress_detail.text()
    window._finish_task_progress("任务完成", True)
    assert not window._task_elapsed_timer.isActive()
    window.close()


def test_worker_callbacks_return_to_gui_thread() -> None:
    import time

    from PySide6.QtCore import QThread

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    received: list[tuple[object, bool]] = []

    def operation(report: object, cancel_event: object) -> str:
        typed_report = report
        typed_report("后台进度", 50)  # type: ignore[operator]
        assert not cancel_event.is_set()  # type: ignore[union-attr]
        return "完成"

    def on_success(value: object) -> None:
        received.append((value, QThread.currentThread() == window.thread()))

    window._run_worker(operation, on_success)
    deadline = time.monotonic() + 3
    while not received and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert received == [("完成", True)]
    deadline = time.monotonic() + 1
    while window._worker_thread and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window._worker_thread is None
    window.close()


def test_worker_cancellation_does_not_commit_result() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    started = Event()
    completed: list[str] = []

    def operation(report: object, cancel_event: Event) -> str:
        report("正在模拟可停止任务……", 25)  # type: ignore[operator]
        started.set()
        while not cancel_event.wait(0.01):
            pass
        raise TaskCancelledError("模拟任务已停止。")

    window._run_worker(operation, completed.append, task_title="正在测试停止功能")
    deadline = monotonic() + 3
    while not started.is_set() and monotonic() < deadline:
        app.processEvents()
        sleep(0.01)
    assert started.is_set()

    window.request_task_cancel()
    deadline = monotonic() + 3
    while window._worker_thread and monotonic() < deadline:
        app.processEvents()
        sleep(0.01)

    assert completed == []
    assert window._worker_thread is None
    assert not window.task_cancel_button.isEnabled()
    assert window.task_progress_bar.format() == "已停止"
    window.close()


def test_worker_allows_sequential_download_style_tasks() -> None:
    import time

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    completed: list[str] = []

    def wait_for_idle() -> None:
        deadline = time.monotonic() + 3
        while window._worker_thread and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert window._worker_thread is None

    for label in ("快速", "平衡"):
        window._run_worker(
            lambda _report, _cancel_event, value=label: value,
            completed.append,
        )
        wait_for_idle()

    assert completed == ["快速", "平衡"]
    window.close()
