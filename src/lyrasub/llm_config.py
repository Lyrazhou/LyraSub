"""Local OpenAI-compatible configuration with protected API-key storage."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from platformdirs import user_config_path

from .constants import APP_ID


@dataclass(slots=True)
class LLMProfile:
    id: str
    name: str
    base_url: str
    model: str
    api_key_ref: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 300
    translation_batch_chars: int = 2_000

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LLMProfile:
        return cls(
            id=str(payload["id"]),
            name=str(payload.get("name", payload["id"])),
            base_url=str(payload["base_url"]).rstrip("/"),
            model=str(payload["model"]),
            api_key_ref=str(payload.get("api_key_ref", "")),
            extra_headers={str(k): str(v) for k, v in payload.get("extra_headers", {}).items()},
            timeout_seconds=int(payload.get("timeout_seconds", 300)),
            translation_batch_chars=int(payload.get("translation_batch_chars", 2_000)),
        )


def parse_imported_profile_json(raw: str) -> dict[str, Any]:
    """Normalize a user-supplied OpenAI-compatible JSON configuration.

    This intentionally accepts only connection metadata and a single API key. The
    caller previews the values and must explicitly save before the key is stored.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("导入内容不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise ValueError("导入 JSON 的根节点必须是对象。")
    profiles = payload.get("profiles")
    if isinstance(profiles, list):
        if len(profiles) != 1 or not isinstance(profiles[0], dict):
            raise ValueError("导入 profiles 时请只保留一项服务配置。")
        payload = profiles[0]
    for nested_key in ("openai", "llm", "connection"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            payload = {**payload, **nested}
            break

    def first_text(*keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    headers = payload.get("extra_headers", payload.get("headers", {}))
    if not isinstance(headers, dict):
        raise ValueError("JSON 中的 headers / extra_headers 必须是对象。")
    normalized = {
        "name": first_text("name", "profile_name", "provider") or "导入的模型服务",
        "base_url": first_text("base_url", "api_base", "apiBaseUrl", "endpoint", "url").rstrip("/"),
        "model": first_text("model", "model_name", "modelName", "deployment"),
        "api_key": first_text("api_key", "apiKey", "openai_api_key", "token", "key"),
        "extra_headers": {str(key): str(value) for key, value in headers.items()},
        "timeout_seconds": int(payload.get("timeout_seconds", payload.get("timeout", 300))),
        "translation_batch_chars": int(payload.get("translation_batch_chars", 2_000)),
    }
    if not normalized["base_url"] or not normalized["model"]:
        raise ValueError("导入 JSON 必须包含 base_url（或 api_base）和 model。")
    if not 10 <= normalized["timeout_seconds"] <= 3600:
        raise ValueError("导入的 timeout 必须在 10 到 3600 秒之间。")
    if not 1_000 <= normalized["translation_batch_chars"] <= 100_000:
        raise ValueError("导入的 translation_batch_chars 必须在 1000 到 100000 之间。")
    return normalized


class SecretStore:
    """Stores each secret separately from projects and from exported settings.

    On Windows, DPAPI binds data to the current Windows user. The encrypted-file
    fallback is for test/non-Windows environments only and derives its key from a
    per-user random salt plus the agreed ``Yvaine`` seed.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or user_config_path(APP_ID, appauthor=False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.secret_file = self.root / "secrets.json"
        self.salt_file = self.root / ".secret_salt"

    def _salt(self) -> bytes:
        if not self.salt_file.exists():
            self.salt_file.write_bytes(os.urandom(32))
        return self.salt_file.read_bytes()

    def _fallback_fernet(self) -> Fernet:
        key = hashlib.pbkdf2_hmac("sha256", b"Yvaine", self._salt(), 240_000, dklen=32)
        return Fernet(base64.urlsafe_b64encode(key))

    def _load(self) -> dict[str, str]:
        if not self.secret_file.exists():
            return {}
        return json.loads(self.secret_file.read_text(encoding="utf-8"))

    def _save(self, payload: dict[str, str]) -> None:
        temporary = self.secret_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.secret_file)

    @staticmethod
    def _dpapi_protect(raw: bytes) -> bytes:
        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        data = (ctypes.c_byte * len(raw)).from_buffer_copy(raw)
        input_blob = DataBlob(len(raw), data)
        output_blob = DataBlob()
        if not crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "LyraSub API Key",
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)

    @staticmethod
    def _dpapi_unprotect(raw: bytes) -> bytes:
        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        data = (ctypes.c_byte * len(raw)).from_buffer_copy(raw)
        input_blob = DataBlob(len(raw), data)
        output_blob = DataBlob()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            kernel32.LocalFree(output_blob.pbData)

    def put(self, key: str, secret: str) -> str:
        if not key or not secret:
            raise ValueError("密钥标识和 API 密钥均不能为空。")
        records = self._load()
        raw = secret.encode("utf-8")
        if sys.platform == "win32":
            token = "dpapi:" + base64.b64encode(self._dpapi_protect(raw)).decode("ascii")
        else:
            token = "fernet:" + self._fallback_fernet().encrypt(raw).decode("ascii")
        records[key] = token
        self._save(records)
        return key

    def get(self, key: str) -> str | None:
        token = self._load().get(key)
        if not token:
            return None
        scheme, _, payload = token.partition(":")
        try:
            if scheme == "dpapi":
                if sys.platform != "win32":
                    return None
                return self._dpapi_unprotect(base64.b64decode(payload)).decode("utf-8")
            if scheme == "fernet":
                return self._fallback_fernet().decrypt(payload.encode("ascii")).decode("utf-8")
        except Exception:
            return None
        return None

    def delete(self, key: str) -> None:
        records = self._load()
        if key in records:
            del records[key]
            self._save(records)


class ProfileStore:
    """Stores only non-secret OpenAI-compatible endpoint metadata."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or user_config_path(APP_ID, appauthor=False)
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile_file = self.root / "llm_profiles.json"

    def _read_payload(self) -> dict[str, Any]:
        if not self.profile_file.exists():
            return {"profiles": [], "active_profile_id": ""}
        try:
            raw = json.loads(self.profile_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("模型服务配置文件无法读取或格式无效。") from exc
        if not isinstance(raw, dict):
            raise ValueError("模型服务配置文件根节点必须是对象。")
        profiles = raw.get("profiles", [])
        if not isinstance(profiles, list):
            raise ValueError("模型服务配置文件中的 profiles 必须是数组。")
        return {"profiles": profiles, "active_profile_id": str(raw.get("active_profile_id", ""))}

    def list(self) -> list[LLMProfile]:
        raw = self._read_payload()
        return [LLMProfile.from_dict(item) for item in raw["profiles"] if isinstance(item, dict)]

    def active_profile_id(self) -> str:
        profiles = self.list()
        if not profiles:
            return ""
        stored = str(self._read_payload()["active_profile_id"])
        if any(profile.id == stored for profile in profiles):
            return stored
        return profiles[0].id

    def active_profile(self) -> LLMProfile | None:
        active_id = self.active_profile_id()
        return next((profile for profile in self.list() if profile.id == active_id), None)

    def save_all(self, profiles: list[LLMProfile], active_profile_id: str | None = None) -> None:
        ids = {profile.id for profile in profiles}
        if active_profile_id not in ids:
            existing_active = self.active_profile_id() if profiles else ""
            active_profile_id = (
                existing_active if existing_active in ids else (profiles[0].id if profiles else "")
            )
        payload = {
            "profiles": [profile.public_dict() for profile in profiles],
            "active_profile_id": active_profile_id,
        }
        temporary = self.profile_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.profile_file)

    def set_active_profile(self, profile_id: str) -> None:
        profiles = self.list()
        if not any(profile.id == profile_id for profile in profiles):
            raise ValueError("要选择的模型服务 Profile 不存在。")
        self.save_all(profiles, active_profile_id=profile_id)

    def delete(self, profile_id: str) -> LLMProfile | None:
        profiles = self.list()
        deleted = next((profile for profile in profiles if profile.id == profile_id), None)
        if deleted is None:
            return None
        remaining = [profile for profile in profiles if profile.id != profile_id]
        active_id = self.active_profile_id()
        self.save_all(remaining, active_profile_id=active_id if active_id != profile_id else None)
        return deleted

    def export_public_json(self, profiles: list[LLMProfile], destination: str | Path) -> Path:
        target = Path(destination)
        target.write_text(
            json.dumps(
                {"profiles": [profile.public_dict() for profile in profiles]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return target
