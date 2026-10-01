from lyrasub.llm_config import LLMProfile, ProfileStore, parse_imported_profile_json
from lyrasub.prompt_templates import (
    DEFAULT_CLEANUP_SYSTEM_PROMPT,
    DEFAULT_SEGMENTATION_SYSTEM_PROMPT,
    DEFAULT_TRANSLATION_SYSTEM_PROMPT,
    PromptTemplates,
    PromptTemplateStore,
    render_segmentation_prompt,
)


def test_imported_openai_style_json_normalizes_common_fields() -> None:
    imported = parse_imported_profile_json(
        """{
          "provider": "官方 OpenAI",
          "api_base": "https://api.example.com/v1/",
          "model_name": "gpt-4o-mini",
          "apiKey": "test-secret",
          "headers": {"X-Client": "LyraSub"},
          "timeout": 180
        }"""
    )
    assert imported == {
        "name": "官方 OpenAI",
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o-mini",
        "api_key": "test-secret",
        "extra_headers": {"X-Client": "LyraSub"},
        "timeout_seconds": 180,
        "translation_batch_chars": 2000,
    }


def test_imported_profile_uses_300_second_default_and_accepts_long_text_timeout() -> None:
    defaulted = parse_imported_profile_json(
        '{"base_url": "https://example.com/v1", "model": "model-a", "api_key": "key"}'
    )
    assert defaulted["timeout_seconds"] == 300
    long_text = parse_imported_profile_json(
        '{"base_url": "https://example.com/v1", "model": "model-a", "api_key": "key", "timeout": 3600}'
    )
    assert long_text["timeout_seconds"] == 3600


def test_imported_profiles_json_accepts_single_profile() -> None:
    imported = parse_imported_profile_json(
        '{"profiles": [{"name": "测试", "base_url": "https://example.com/v1", "model": "model-a", "api_key": "key"}]}'
    )
    assert imported["name"] == "测试"
    assert imported["base_url"] == "https://example.com/v1"
    assert imported["model"] == "model-a"


def test_profile_store_persists_active_selection_and_deletion_fallback(tmp_path) -> None:
    store = ProfileStore(tmp_path)
    first = LLMProfile(
        id="first",
        name="主服务",
        base_url="https://primary.example/v1",
        model="model-primary",
        api_key_ref="secret_first",
    )
    second = LLMProfile(
        id="second",
        name="备用服务",
        base_url="https://backup.example/v1",
        model="model-backup",
        api_key_ref="secret_second",
    )
    store.save_all([first, second], active_profile_id=second.id)

    reloaded = ProfileStore(tmp_path)
    assert [profile.id for profile in reloaded.list()] == ["first", "second"]
    assert reloaded.active_profile_id() == "second"
    assert reloaded.active_profile() == second

    reloaded.set_active_profile("first")
    assert ProfileStore(tmp_path).active_profile_id() == "first"
    deleted = reloaded.delete("first")
    assert deleted == first
    assert [profile.id for profile in ProfileStore(tmp_path).list()] == ["second"]
    assert ProfileStore(tmp_path).active_profile_id() == "second"


def test_default_prompt_templates_apply_confirmed_punctuation_rules() -> None:
    assert "保留事实、语气、专名、数字、原语言及原有标点句读" in DEFAULT_CLEANUP_SYSTEM_PROMPT
    assert "不要用空格替换标点" in DEFAULT_CLEANUP_SYSTEM_PROMPT
    assert "对中文正文删除常规全角标点" in DEFAULT_SEGMENTATION_SYSTEM_PROMPT
    assert "以一个普通空格替换" in DEFAULT_SEGMENTATION_SYSTEM_PROMPT
    assert "英文撇号" in DEFAULT_TRANSLATION_SYSTEM_PROMPT
    assert "删除常规句读标点" in DEFAULT_TRANSLATION_SYSTEM_PROMPT


def test_prompt_template_store_round_trip_and_reset(tmp_path) -> None:
    store = PromptTemplateStore(tmp_path)
    custom = PromptTemplates(
        cleanup_system="清洗模板",
        segmentation_system="上限 {max_cjk_chars}/{max_latin_chars}",
        translation_system="翻译模板",
    )
    store.save(custom)
    assert store.load() == custom
    assert render_segmentation_prompt(store.load().segmentation_system, 15, 42) == "上限 15/42"
    restored = store.reset()
    assert restored.cleanup_system != "清洗模板"
    assert "自然意群" in restored.segmentation_system
    assert "翻译" in restored.translation_system
