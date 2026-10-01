import pytest

from lyrasub.llm_client import LLMResponseError, OpenAICompatibleClient
from lyrasub.llm_config import LLMProfile
from lyrasub.translation import TranslationRecord


def _client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        LLMProfile(
            id="test",
            name="测试服务",
            base_url="https://example.invalid/v1",
            model="test-model",
        ),
        "test-key",
    )


def test_translate_batch_preserves_input_id_order(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client,
        "_chat_json",
        lambda *args, **kwargs: {
            "translations": [{"id": "s2", "text": "Second"}, {"id": "s1", "text": "First"}],
            "notes": ["保留专名"],
        },
    )

    result = client.translate_batch(
        [TranslationRecord("s1", "第一条"), TranslationRecord("s2", "第二条")],
        "中文",
        "English",
    )

    assert list(result.translations) == ["s1", "s2"]
    assert result.translations == {"s1": "First", "s2": "Second"}
    assert result.notes == ["保留专名"]


def test_translate_batch_sends_confirmed_punctuation_rules(monkeypatch) -> None:
    client = _client()
    captured: dict[str, str] = {}

    def fake_chat(system: str, user: str, *args, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return {"translations": [{"id": "s1", "text": "Hello"}]}

    monkeypatch.setattr(client, "_chat_json", fake_chat)
    client.translate_batch([TranslationRecord("s1", "你好！")], "中文", "English")

    assert "删除常规句读标点" in captured["user"]
    assert "英文撇号" in captured["user"]


def test_translate_batch_rejects_mismatched_response_ids(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client,
        "_chat_json",
        lambda *args, **kwargs: {"translations": [{"id": "unexpected", "text": "错误"}]},
    )

    with pytest.raises(LLMResponseError, match="不一致"):
        client.translate_batch([TranslationRecord("s1", "第一条")], "中文", "English")
