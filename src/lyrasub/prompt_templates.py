"""Local editable prompt templates for OpenAI-compatible LLM operations."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_config_path

from .constants import APP_ID

DEFAULT_CLEANUP_SYSTEM_PROMPT = (
    "你是影视字幕文本清洗助手。只清理口语赘词、明显重复、无意义停顿和多余空白；"
    "严格保留事实、语气、专名、数字、原语言及原有标点句读，不翻译、不创作、不补写。"
    "不要用空格替换标点，也不要人为添加词间空格；标点处理留给后续字幕切分阶段。"
    "必须仅返回 JSON 对象。"
)

DEFAULT_TRANSLATION_SYSTEM_PROMPT = (
    "你是专业影视字幕翻译师。严格忠实翻译每条影视字幕，保留语气、专名、数字、"
    "换行和阅读节奏；不得合并、拆分、遗漏、添加或重排条目。"
    "输出目标字幕时删除常规句读标点；但必须保留英文撇号 '、连字符 -，以及专名、"
    "缩写、数字、小数、日期时间、URL、邮箱、货币、编程和技术等必要特殊记号。"
    "删除标点后不得产生连续空格。必须仅返回 JSON 对象。"
)

DEFAULT_SEGMENTATION_SYSTEM_PROMPT = """你是专业影视字幕语义切分师。你的首要目标是让字幕读起来自然、像影视对白，
而不是把每条字幕机械凑到最大字数。你必须仅返回 JSON 对象。

本次阅读上限：中文每行建议不超过 {max_cjk_chars} 个可见字符；英文每行建议不超过
{max_latin_chars} 个字符。它们是阅读上限和风险提示，不是每条字幕的目标长度。

请严格遵守以下影视字幕策略：
1. 先按完整句子、自然意群、称呼、问答、转折、呼吸和语气切分。短而完整的意群应保持短，
   不得为了接近上限把两个不自然的意群硬拼在一起。
2. 两个连续、彼此独立但都非常短的意群，若在同一阅读节拍内更自然，可同屏写为一条，
   以一个普通空格隔开。例如“大家好 我是海舟”。不要为凑字数而滥用这种合并。
3. 较长但语义完整的句子可用一个换行符拆为同一条字幕的两行；应优先在语义边界换行，
   并尽量让两行视觉长度均衡。不要把一句的前半和后半拆成两个时间相邻却阅读别扭的字幕。
4. 专名、数字、英文单词、固定短语不得被拆断。允许少量超过上限，优先保障语义完整和自然阅读。
5. 除第 6 条允许的常规标点处理外，不得改变、翻译、遗漏、编造、总结或重新排序任何原文。
   每个数组元素是一条字幕，可含最多一个换行符代表两行。
6. 对中文正文删除常规全角标点。若标点原本位于两个应保持可读分隔的中文语义片段之间，
   以一个普通空格替换；不得产生连续空格。不得删除或拆坏英文单词、英文缩写、所有格、
   连字符词、数字、小数、日期时间、URL、邮箱、专名或技术记号。换行仅用于同一字幕的两行版式，
   不得以标点替代换行。

示例：对“大家好我是海舟 很高兴今天能够再一次和大家在互联网上空相会”，
更自然的结构可以是 ["大家好 我是海舟", "很高兴今天能够再一次", "和大家在互联网\\n上空相会"]。
这只是策略示例；请始终以实际文本的语义和节奏为准。"""


@dataclass(slots=True)
class PromptTemplates:
    cleanup_system: str = DEFAULT_CLEANUP_SYSTEM_PROMPT
    segmentation_system: str = DEFAULT_SEGMENTATION_SYSTEM_PROMPT
    translation_system: str = DEFAULT_TRANSLATION_SYSTEM_PROMPT

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> PromptTemplates:
        return cls(
            cleanup_system=str(payload.get("cleanup_system") or DEFAULT_CLEANUP_SYSTEM_PROMPT),
            segmentation_system=str(
                payload.get("segmentation_system") or DEFAULT_SEGMENTATION_SYSTEM_PROMPT
            ),
            translation_system=str(
                payload.get("translation_system") or DEFAULT_TRANSLATION_SYSTEM_PROMPT
            ),
        )


class PromptTemplateStore:
    """Stores user edits separately from projects and API credentials."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or user_config_path(APP_ID, appauthor=False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.template_file = self.root / "llm_prompt_templates.json"

    @staticmethod
    def defaults() -> PromptTemplates:
        return PromptTemplates()

    def load(self) -> PromptTemplates:
        if not self.template_file.exists():
            return self.defaults()
        try:
            payload = json.loads(self.template_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self.defaults()
        return PromptTemplates.from_dict(payload if isinstance(payload, dict) else {})

    def save(self, templates: PromptTemplates) -> None:
        temporary = self.template_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(templates.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.template_file)

    def reset(self) -> PromptTemplates:
        templates = self.defaults()
        self.save(templates)
        return templates


def render_segmentation_prompt(template: str, max_cjk_chars: int, max_latin_chars: int) -> str:
    """Render only documented placeholders without interpreting unrelated braces."""
    return template.replace("{max_cjk_chars}", str(max_cjk_chars)).replace(
        "{max_latin_chars}", str(max_latin_chars)
    )
