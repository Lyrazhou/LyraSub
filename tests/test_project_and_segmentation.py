import json
from pathlib import Path

from lyrasub.main_window import MainWindow
from lyrasub.models import (
    Anchor,
    BilingualOrder,
    PreviewAudioSource,
    ProjectDraft,
    SubtitleEntry,
    SubtitleLayoutMode,
    SubtitleOutputMode,
    SubtitleProject,
)
from lyrasub.project_io import (
    autosave_path,
    load_project,
    remove_autosave,
    save_autosave,
    save_project,
)
from lyrasub.segmentation import segment_locally
from lyrasub.text_normalization import to_simplified_chinese


def test_project_round_trip_preserves_anchor_and_manual_override(tmp_path: Path) -> None:
    project = SubtitleProject(title="测试项目")
    project.source.path = "D:/audio/识别源.mp3"
    project.source.preview_video_path = "E:/video/高清预览.mp4"
    project.anchors = [Anchor(id="a1", text="你", start_ms=100, end_ms=180)]
    project.entries = [
        SubtitleEntry(
            id="s1",
            text="你",
            translation_text="You",
            anchor_ids=["a1"],
            start_ms=100,
            end_ms=180,
            manual_start_ms=90,
            force_confirmed=True,
            review_confirmed=True,
        )
    ]
    project.settings.subtitle_output_mode = SubtitleOutputMode.BILINGUAL
    project.settings.bilingual_order = BilingualOrder.TRANSLATION_TOP
    project.settings.primary_language_label = "中文"
    project.settings.translation_language_label = "English"
    project.settings.preview_audio_source = PreviewAudioSource.RECOGNITION
    project.draft = ProjectDraft(
        original_editor_text="你好吗",
        cleaned_editor_text="你好吗",
        table_rows=[{"text": "你好吗", "start": "90", "end": "180"}],
        active_page=3,
        captured_at="2026-08-20T15:00:00+00:00",
    )
    destination = save_project(project, tmp_path / "demo")
    assert destination.suffix == ".lyrasub"
    loaded = load_project(destination)
    assert loaded.title == "测试项目"
    assert loaded.anchors[0].start_ms == 100
    assert loaded.entries[0].manual_start_ms == 90
    assert loaded.entries[0].force_confirmed is True
    assert loaded.entries[0].review_confirmed is True
    assert loaded.schema_version == 6
    assert loaded.entries[0].translation_text == "You"
    assert loaded.settings.subtitle_output_mode == SubtitleOutputMode.BILINGUAL
    assert loaded.settings.bilingual_order == BilingualOrder.TRANSLATION_TOP
    assert loaded.settings.primary_language_label == "中文"
    assert loaded.settings.translation_language_label == "English"
    assert loaded.settings.preview_audio_source == PreviewAudioSource.RECOGNITION
    assert loaded.source.path == "D:/audio/识别源.mp3"
    assert loaded.source.preview_video_path == "E:/video/高清预览.mp4"
    assert loaded.draft.table_rows[0]["text"] == "你好吗"
    assert loaded.draft.active_page == 3


def test_legacy_v1_project_loads_without_draft(tmp_path: Path) -> None:
    legacy = {
        "schema_version": 1,
        "app_version": "v260820F",
        "title": "旧工程",
        "source": {},
        "settings": {},
        "original_text": "旧原文",
        "cleaned_text": "",
        "anchors": [],
        "entries": [],
        "workflow_notes": [],
        "future_waveform": {"manual_overrides": []},
    }
    path = tmp_path / "legacy.lyrasub"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    loaded = load_project(path)
    assert loaded.schema_version == 1
    assert loaded.original_text == "旧原文"
    assert loaded.draft.table_rows == []


def test_autosave_round_trip_is_independent_from_user_project(tmp_path: Path) -> None:
    project = SubtitleProject(title="可恢复工程", original_text="原文")
    destination = save_project(project, tmp_path / "recoverable")
    project.cleaned_text = "清洗后"
    project.draft = ProjectDraft(cleaned_editor_text="未确认清洗草稿", captured_at="now")
    recovery = save_autosave(project, destination)
    assert recovery == autosave_path(destination)
    assert recovery.is_file()
    assert load_project(destination).cleaned_text == ""
    restored = load_project(recovery)
    assert restored.cleaned_text == "清洗后"
    assert restored.draft.cleaned_editor_text == "未确认清洗草稿"
    remove_autosave(destination)
    assert not recovery.exists()


def test_local_segmentation_returns_editable_entries() -> None:
    result = segment_locally("大家好，欢迎使用弦字幕。我们现在开始制作电影字幕！", 8, 42)
    assert result.entries
    assert all(entry.strip() for entry in result.entries)
    assert any("弦字幕" in entry for entry in result.entries)


def test_simplified_chinese_normalization_preserves_latin_and_converts_traditional() -> None:
    assert to_simplified_chinese("海報及PPT單頁輔助設計 Vibax") == "海报及PPT单页辅助设计 Vibax"


def test_single_line_layout_splits_llm_internal_newline_into_sequential_entries() -> None:
    lines = MainWindow._apply_layout_mode(
        ["我利用Vibax平台\n设计制作的AI应用工具"], SubtitleLayoutMode.SINGLE_LINE
    )
    assert lines == ["我利用Vibax平台", "设计制作的AI应用工具"]


def test_two_line_layout_retains_llm_internal_newline() -> None:
    lines = MainWindow._apply_layout_mode(
        ["我利用Vibax平台\n设计制作的AI应用工具"], SubtitleLayoutMode.TWO_LINES
    )
    assert lines == ["我利用Vibax平台\n设计制作的AI应用工具"]


def test_local_segmentation_does_not_split_english_word() -> None:
    result = segment_locally("Welcome to LyraSub subtitle workstation.", 15, 16)
    joined = " ".join(entry.replace("\n", " ") for entry in result.entries)
    assert "workstation" in joined
