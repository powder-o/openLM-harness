"""Paths and settings. Everything reads env lazily so tests can repoint the data dir per test."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]  # study-app/
BACKEND_ROOT = APP_ROOT / "backend"
FRONTEND_DIST = APP_ROOT / "frontend" / "dist"

DEFAULT_NOTEBOOK_SETTINGS: dict = {
    "ocr": "auto",  # auto | force | off
    "ocr_lang": ["en"],
    "table_structure": True,
    "chunk_max_tokens": 350,
    "chunk_overlap_tokens": 40,
    "describe_figures": True,
    "extract_concepts": True,
    "graph_section_depth": 2,
}

_DEFAULTS: dict = {
    "deepseek_api_key": None,
    "deepseek_base_url": "https://api.deepseek.com",
    "chat_model": "deepseek-v4-flash",
    "extract_model": "deepseek-v4-flash",
    "vision_model": "deepseek-v4-flash-vision-exp",
    "embed_model": "BAAI/bge-small-en-v1.5",
}
_ENV = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_base_url": "DEEPSEEK_BASE_URL",
    "chat_model": "STUDY_CHAT_MODEL",
    "extract_model": "STUDY_EXTRACT_MODEL",
    "vision_model": "STUDY_VISION_MODEL",
    "embed_model": "STUDY_EMBED_MODEL",
}
_lock = threading.Lock()


def data_dir() -> Path:
    p = Path(os.environ.get("STUDY_DATA_DIR") or APP_ROOT / "data").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "study.db"


def document_dir(document_id: int) -> Path:
    p = data_dir() / "files" / str(document_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def dsh_bin() -> Path | None:
    """Optional override for the dsh CLI (e.g. a harness checkout's apps/cli/lib/bin.js).

    Unset by default: the SDK then launches the runtime executable bundled in the
    `deepseek-harness-runtime-bin` wheel that `uv sync` installs.
    """
    value = os.environ.get("DSH_BIN")
    return Path(value).expanduser().resolve() if value else None


def port() -> int:
    return int(os.environ.get("STUDY_PORT", "8765"))


def fake_llm() -> bool:
    return os.environ.get("STUDY_LLM_FAKE") == "1"


def fake_embed() -> bool:
    return os.environ.get("STUDY_EMBED_FAKE") == "1"


def _settings_file() -> Path:
    return data_dir() / "settings.json"


def get_settings() -> dict:
    """Defaults < env < data/settings.json (saved from the UI)."""
    s = dict(_DEFAULTS)
    for key, env in _ENV.items():
        if os.environ.get(env):
            s[key] = os.environ[env]
    f = _settings_file()
    if f.exists():
        try:
            saved = json.loads(f.read_text())
        except json.JSONDecodeError:
            saved = {}
        s.update({k: v for k, v in saved.items() if k in _DEFAULTS and v not in (None, "")})
    return s


def save_settings(patch: dict) -> dict:
    with _lock:
        f = _settings_file()
        saved = json.loads(f.read_text()) if f.exists() else {}
        for k, v in patch.items():
            if k in _DEFAULTS and v is not None:
                saved[k] = v
        f.write_text(json.dumps(saved, indent=2))
    return get_settings()


def notebook_settings(settings_json: str | None) -> dict:
    s = dict(DEFAULT_NOTEBOOK_SETTINGS)
    if settings_json:
        try:
            s.update(json.loads(settings_json))
        except json.JSONDecodeError:
            pass
    return s
