import pytest

from lyrasub.translation import (
    TranslationRecord,
    chunk_translation_records,
    is_retryable_translation_response_error,
)


def test_translation_records_are_chunked_only_at_subtitle_boundaries() -> None:
    records = [
        TranslationRecord("s1", "甲" * 600),
        TranslationRecord("s2", "乙" * 500),
        TranslationRecord("s3", "丙" * 300),
    ]
    batches = chunk_translation_records(records, 1_000)
    assert [[record.entry_id for record in batch] for batch in batches] == [["s1"], ["s2", "s3"]]


def test_overlong_translation_record_remains_whole_in_own_batch() -> None:
    records = [TranslationRecord("s1", "甲" * 1_250), TranslationRecord("s2", "乙" * 10)]
    batches = chunk_translation_records(records, 1_000)
    assert [[record.entry_id for record in batch] for batch in batches] == [["s1"], ["s2"]]
    assert batches[0][0].text == "甲" * 1_250


def test_retryable_translation_response_errors_include_truncation_and_missing_id() -> None:
    assert is_retryable_translation_response_error(
        "LLM 请求或返回格式异常：Unterminated string starting at: line 345"
    )
    assert is_retryable_translation_response_error("LLM 返回的翻译条目缺少唯一 ID 或文本。")
    assert is_retryable_translation_response_error(
        "LLM 返回的翻译条目与输入不一致（缺少 1 条，额外 0 条）。"
    )
    assert not is_retryable_translation_response_error("请求超时，请检查网络。")


def test_translation_chunking_rejects_invalid_limit_and_empty_record() -> None:
    with pytest.raises(ValueError, match="不能小于"):
        chunk_translation_records([TranslationRecord("s1", "文本")], 999)
    with pytest.raises(ValueError, match="稳定 ID"):
        chunk_translation_records([TranslationRecord("", "文本")], 1_000)
