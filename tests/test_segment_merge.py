from pathlib import Path

from lyrasub.segment_merge import (
    MergeEntry,
    MergeProject,
    MergeSegment,
    SegmentPlacementMode,
    SegmentSourceKind,
    apply_continuous_origins,
    build_global_entries,
    load_merge_project,
    parse_srt_text,
    render_merged_srt,
    save_merge_project,
    validate_merge_project,
)


def _segment(
    title: str,
    origin_ms: int | None,
    start_ms: int,
    end_ms: int,
    *,
    duration_ms: int | None = None,
    placement: SegmentPlacementMode = SegmentPlacementMode.ABSOLUTE,
    gap_ms: int = 0,
) -> MergeSegment:
    return MergeSegment(
        title=title,
        source_kind=SegmentSourceKind.SRT,
        source_path=f"{title}.srt",
        entries=[MergeEntry(text=f"{title}字幕", local_start_ms=start_ms, local_end_ms=end_ms)],
        origin_ms=origin_ms,
        audio_duration_ms=duration_ms,
        placement_mode=placement,
        gap_before_ms=gap_ms,
    )


def test_srt_import_and_global_time_rendering() -> None:
    entries = parse_srt_text(
        "1\n00:00:01,000 --> 00:00:02,500\n第一行\n第二行\n\n"
        "2\n00:00:04,000 --> 00:00:05,000\n下一句\n"
    )
    assert [(item.local_start_ms, item.local_end_ms) for item in entries] == [
        (1_000, 2_500),
        (4_000, 5_000),
    ]
    assert entries[0].text == "第一行\n第二行"

    project = MergeProject(
        title="整片",
        delivery_offset_ms=-200,
        segments=[
            MergeSegment(
                title="第一章",
                source_kind=SegmentSourceKind.SRT,
                source_path="first.srt",
                origin_ms=0,
                entries=entries,
            ),
            _segment("第二章", 10_000, 0, 1_000),
        ],
    )
    global_entries = build_global_entries(project)
    assert [item.global_start_ms for item in global_entries] == [1_000, 4_000, 10_000]
    assert not [issue for issue in validate_merge_project(project) if issue.severity == "error"]
    rendered = render_merged_srt(project)
    assert "00:00:00,800 --> 00:00:02,300" in rendered
    assert "00:00:09,800 --> 00:00:10,800" in rendered


def test_continuous_segment_origin_uses_previous_audio_duration_and_gap() -> None:
    first = _segment("第一章", 0, 0, 1_000, duration_ms=42_016)
    second = _segment(
        "第二章",
        None,
        12_500,
        15_000,
        placement=SegmentPlacementMode.CONTINUOUS,
        gap_ms=2_000,
    )
    project = MergeProject(segments=[first, second])

    assert apply_continuous_origins(project) == []
    assert second.origin_ms == 44_016
    global_entry = build_global_entries(project)[1]
    assert (global_entry.global_start_ms, global_entry.global_end_ms) == (56_516, 59_016)


def test_continuous_segment_requires_known_previous_audio_duration() -> None:
    first = _segment("第一章", 0, 0, 1_000)
    second = _segment(
        "第二章",
        None,
        0,
        1_000,
        placement=SegmentPlacementMode.CONTINUOUS,
    )
    project = MergeProject(segments=[first, second])

    issues = apply_continuous_origins(project)
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert second.origin_ms is None


def test_merge_validation_reports_cross_segment_overlap_and_round_trips(tmp_path: Path) -> None:
    first = _segment("第一章", 0, 0, 3_000)
    second = _segment("第二章", 2_500, 0, 2_000)
    project = MergeProject(title="合并工程", segments=[first, second])

    issues = validate_merge_project(project)
    assert any("跨段重叠" in issue.message for issue in issues)
    saved = save_merge_project(project, tmp_path / "movie")
    assert saved.name == "movie.lyrasubmerge"
    restored = load_merge_project(saved)
    assert restored.title == "合并工程"
    assert len(restored.segments) == 2
    assert restored.segments[1].origin_ms == 2_500
