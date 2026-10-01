from lyrasub.anchor_mapping import entries_from_text_list, entry_risk
from lyrasub.models import (
    Anchor,
    AnchorState,
    BilingualOrder,
    SubtitleEntry,
    SubtitleOutputMode,
)
from lyrasub.srt_export import format_srt_time, render_srt, validate_entries


def test_editable_cjk_text_inherits_known_anchor_times() -> None:
    source = [
        Anchor(id="a1", text="你", start_ms=100, end_ms=200),
        Anchor(id="a2", text="好", start_ms=200, end_ms=300),
        Anchor(id="a3", text="吗", start_ms=300, end_ms=400),
    ]
    generated, entries = entries_from_text_list(source, ["你好", "吗"])
    assert generated == []
    assert len(entries) == 2
    assert (entries[0].start_ms, entries[0].end_ms) == (100, 300)
    assert (entries[1].start_ms, entries[1].end_ms) == (300, 400)
    assert entry_risk(entries[0], {anchor.id: anchor for anchor in source}) == AnchorState.INHERITED


def test_inserted_text_inside_known_gap_is_marked_estimated() -> None:
    source = [
        Anchor(id="a1", text="你", start_ms=100, end_ms=200),
        Anchor(id="a2", text="好", start_ms=400, end_ms=500),
    ]
    generated, entries = entries_from_text_list(source, ["你呀好"])
    assert len(entries) == 1
    assert any(
        anchor.text == "呀" and anchor.state == AnchorState.ESTIMATED for anchor in generated
    )
    anchor_index = {anchor.id: anchor for anchor in [*source, *generated]}
    assert entry_risk(entries[0], anchor_index) == AnchorState.ESTIMATED


def test_edge_rewrite_is_explicitly_unanchored() -> None:
    source = [Anchor(id="a1", text="好", start_ms=200, end_ms=300)]
    generated, entries = entries_from_text_list(source, ["你好"])
    assert any(
        anchor.text == "你" and anchor.state == AnchorState.UNANCHORED for anchor in generated
    )
    anchor_index = {anchor.id: anchor for anchor in [*source, *generated]}
    assert entry_risk(entries[0], anchor_index) == AnchorState.UNANCHORED


def test_srt_offset_is_applied_only_on_export() -> None:
    entry = SubtitleEntry(text="你好", start_ms=200, end_ms=1200)
    result = render_srt([entry], offset_ms=-200)
    assert "00:00:00,000 --> 00:00:01,000" in result
    assert entry.start_ms == 200
    assert format_srt_time(3_661_123) == "01:01:01,123"


def test_bilingual_srt_renders_selected_language_order() -> None:
    entry = SubtitleEntry(
        text="你好\n世界",
        translation_text="Hello\nworld",
        start_ms=100,
        end_ms=900,
    )
    primary_top = render_srt(
        [entry],
        output_mode=SubtitleOutputMode.BILINGUAL,
        bilingual_order=BilingualOrder.PRIMARY_TOP,
    )
    translation_top = render_srt(
        [entry],
        output_mode=SubtitleOutputMode.BILINGUAL,
        bilingual_order=BilingualOrder.TRANSLATION_TOP,
    )
    assert "你好 世界\nHello world" in primary_top
    assert "Hello world\n你好 世界" in translation_top


def test_missing_timing_is_an_export_error() -> None:
    issues = validate_entries([SubtitleEntry(text="新增说明")])
    assert len(issues) == 1
    assert issues[0].severity == "error"
