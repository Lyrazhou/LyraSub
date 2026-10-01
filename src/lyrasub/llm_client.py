"""OpenAI-compatible LLM operations limited to text cleanup and semantic segmentation."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any

import requests

from .llm_config import LLMProfile
from .prompt_templates import PromptTemplateStore, render_segmentation_prompt
from .tasks import TaskCancelledError
from .translation import TranslationRecord

ProgressCallback = Callable[[str, int | None], None]


class LLMResponseError(RuntimeError):
    """Raised for a service failure or a response that cannot be trusted by the editor."""


@dataclass(slots=True)
class CleanupResult:
    text: str
    notes: list[str]


@dataclass(slots=True)
class SegmentationResult:
    lines: list[str]
    notes: list[str]


@dataclass(slots=True)
class TranslationResult:
    translations: dict[str, str]
    notes: list[str]


class OpenAICompatibleClient:
    def __init__(self, profile: LLMProfile, api_key: str) -> None:
        if not api_key:
            raise ValueError("该服务配置没有可用的 API 密钥。")
        self.profile = profile
        self.api_key = api_key

    @staticmethod
    def _check_cancel(cancel_event: Event | None, message: str) -> None:
        if cancel_event and cancel_event.is_set():
            raise TaskCancelledError(message)

    def _chat_json(
        self,
        system: str,
        user: str,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        self._check_cancel(cancel_event, "LLM 任务已在发送前停止。")
        endpoint = f"{self.profile.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.profile.extra_headers,
        }
        body = {
            "model": self.profile.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            if progress:
                progress("正在连接您配置的 LLM 服务……", 15)
            response = requests.post(
                endpoint, headers=headers, json=body, timeout=self.profile.timeout_seconds
            )
            self._check_cancel(cancel_event, "LLM 任务已停止；收到的结果不会写入项目。")
            if progress:
                progress("已收到 LLM 返回，正在校验结构……", 80)
            response.raise_for_status()
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except TaskCancelledError:
            raise
        except (
            requests.RequestException,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise LLMResponseError(f"LLM 请求或返回格式异常：{exc}") from exc
        if not isinstance(parsed, dict):
            raise LLMResponseError("LLM 返回的结构不是 JSON 对象。")
        return parsed

    def cleanup(
        self,
        text: str,
        language_hint: str = "auto",
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> CleanupResult:
        system = PromptTemplateStore().load().cleanup_system
        user = json.dumps(
            {
                "task": "清洗下列转写文本，保留语言和含义。",
                "language_hint": language_hint,
                "return_schema": {"text": "string", "notes": ["string"]},
                "text": text,
            },
            ensure_ascii=False,
        )
        payload = self._chat_json(system, user, progress, cancel_event)
        cleaned = payload.get("text")
        if not isinstance(cleaned, str) or not cleaned.strip():
            raise LLMResponseError("LLM 没有返回可用的清洗文本。")
        self._check_cancel(cancel_event, "LLM 清洗已停止；不会写入结果。")
        notes = payload.get("notes", [])
        if progress:
            progress("文本清洗完成，正在等待您确认。", 100)
        return CleanupResult(cleaned.strip(), [str(item) for item in notes if str(item).strip()])

    def segment(
        self,
        text: str,
        max_cjk_chars: int,
        max_latin_chars: int,
        language_hint: str = "auto",
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> SegmentationResult:
        templates = PromptTemplateStore().load()
        system = render_segmentation_prompt(
            templates.segmentation_system, max_cjk_chars, max_latin_chars
        )
        user = json.dumps(
            {
                "task": "为以下文本生成字幕切分。",
                "language_hint": language_hint,
                "limits": {
                    "max_cjk_chars_per_line": max_cjk_chars,
                    "max_latin_characters_per_line": max_latin_chars,
                    "max_lines_per_subtitle": 2,
                },
                "return_schema": {"entries": ["string"], "notes": ["string"]},
                "text": text,
            },
            ensure_ascii=False,
        )
        payload = self._chat_json(system, user, progress, cancel_event)
        entries = payload.get("entries")
        if not isinstance(entries, list) or not all(
            isinstance(item, str) and item.strip() for item in entries
        ):
            raise LLMResponseError("LLM 没有返回可用的字幕切分数组。")
        self._check_cancel(cancel_event, "LLM 语义切分已停止；不会写入结果。")
        lines = [item.strip() for item in entries]
        notes = payload.get("notes", [])
        if progress:
            progress("语义切分完成，正在映射字幕时间锚点……", 90)
        return SegmentationResult(lines, [str(item) for item in notes if str(item).strip()])

    def translate_batch(
        self,
        records: list[TranslationRecord],
        source_language: str,
        target_language: str,
        progress: ProgressCallback | None = None,
        cancel_event: Event | None = None,
    ) -> TranslationResult:
        if not records:
            return TranslationResult({}, [])
        expected_ids = [record.entry_id for record in records]
        if len(set(expected_ids)) != len(expected_ids):
            raise ValueError("翻译批次包含重复的字幕 ID。")
        system = PromptTemplateStore().load().translation_system
        user = json.dumps(
            {
                "task": "将下列字幕逐条翻译为目标语言。",
                "source_language": source_language,
                "target_language": target_language,
                "rules": [
                    "每个输入 id 必须恰好返回一次。",
                    "不得合并、拆分、删除、添加或重排条目。",
                    "保留换行、专名、数字和字幕阅读节奏。",
                    "删除常规句读标点；保留英文撇号 '、连字符 -，以及专名、缩写、数字、小数、日期时间、URL、邮箱和必要技术记号。",
                    "删除标点后不得产生连续空格。",
                ],
                "return_schema": {
                    "translations": [{"id": "string", "text": "string"}],
                    "notes": ["string"],
                },
                "entries": [{"id": record.entry_id, "text": record.text} for record in records],
            },
            ensure_ascii=False,
        )
        payload = self._chat_json(system, user, progress, cancel_event)
        raw_translations = payload.get("translations")
        if not isinstance(raw_translations, list):
            raise LLMResponseError("LLM 没有返回 translations 数组。")
        translated: dict[str, str] = {}
        for item in raw_translations:
            if not isinstance(item, dict):
                raise LLMResponseError("LLM 返回的翻译条目格式无效。")
            entry_id = str(item.get("id", "")).strip()
            text = str(item.get("text", "")).strip()
            if not entry_id or not text or entry_id in translated:
                raise LLMResponseError("LLM 返回的翻译条目缺少唯一 ID 或文本。")
            translated[entry_id] = text
        if set(translated) != set(expected_ids):
            missing = len(set(expected_ids) - set(translated))
            unexpected = len(set(translated) - set(expected_ids))
            raise LLMResponseError(
                f"LLM 返回的翻译条目与输入不一致（缺少 {missing} 条，额外 {unexpected} 条）。"
            )
        self._check_cancel(cancel_event, "LLM 翻译已停止；不会写入结果。")
        notes = payload.get("notes", [])
        return TranslationResult(
            translations={entry_id: translated[entry_id] for entry_id in expected_ids},
            notes=[str(item) for item in notes if str(item).strip()],
        )
