"""Lazy singleton around the DeepSeek Harness Python SDK.

One `DeepSeekHarness` is kept alive for the whole process, restarted when the
API key / base url / chat model change, and closed on app shutdown. A single
lock serializes turns (fine for this single-user POC). The SDK
(`deepseek-harness-sdk`, pinned in pyproject.toml) is a normal dependency; the
dsh CLI it drives is the standalone executable bundled in that package's
`deepseek-harness-runtime-bin` wheel, unless `DSH_BIN` points elsewhere.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from .. import config

log = logging.getLogger("study.agent.harness")

_TEMPLATE_PATH = Path(__file__).with_name("study.patch.template.yml")
_MCP_URL_PLACEHOLDER = "__STUDY_MCP_URL__"


def mcp_url() -> str:
    return os.environ.get("STUDY_MCP_URL") or f"http://127.0.0.1:{config.port()}/mcp/"


def runtime_path() -> Path | None:
    """The dsh executable that will be launched: `DSH_BIN` if set, else the bundled
    runtime. None when neither is usable (missing platform wheel, bad override)."""
    override = config.dsh_bin()
    if override is not None:
        return override if override.exists() else None
    try:
        from deepseek_harness_runtime import bundled_runtime_path

        return bundled_runtime_path()
    except Exception:  # noqa: BLE001 - ImportError or FileNotFoundError: report "not found"
        return None


def _render_patch() -> Path:
    dsh_home = config.data_dir() / "dsh-home"
    dsh_home.mkdir(parents=True, exist_ok=True)
    rendered = _TEMPLATE_PATH.read_text().replace(_MCP_URL_PLACEHOLDER, mcp_url())
    out = dsh_home / "study.patch.yml"
    out.write_text(rendered)
    return out


class HarnessManager:
    """Owns the single DeepSeekHarness instance for this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._harness = None
        self._key: tuple | None = None
        self._error: str | None = None

    @staticmethod
    def _settings_key(settings: dict) -> tuple:
        return (settings.get("deepseek_api_key"), settings.get("deepseek_base_url"), settings.get("chat_model"))

    def status(self) -> dict:
        settings = config.get_settings()
        return {
            "ready": self._harness is not None and self._error is None,
            "api_key_set": bool(settings.get("deepseek_api_key")),
            "runtime_found": runtime_path() is not None,
            "error": self._error,
        }

    @property
    def turn_lock(self) -> threading.Lock:
        """Serializes dsh turns: one at a time is fine for this POC."""
        return self._turn_lock

    def get(self):
        """Return a started DeepSeekHarness, (re)starting it if credentials/model changed.

        Raises RuntimeError if no API key is configured, or whatever exception the SDK raises
        while starting the runtime.
        """
        settings = config.get_settings()
        if not settings.get("deepseek_api_key"):
            raise RuntimeError("DeepSeek API key is not configured")
        key = self._settings_key(settings)
        with self._lock:
            if self._harness is not None and self._key == key:
                return self._harness
            if self._harness is not None:
                self._close_locked()
            try:
                self._harness = self._start(settings)
                self._key = key
                self._error = None
            except Exception as exc:
                self._error = str(exc)
                self._harness = None
                self._key = None
                raise
            return self._harness

    def _start(self, settings: dict):
        from deepseek_harness import DeepSeekHarness

        workspace = config.data_dir() / "dsh-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        dsh_home = config.data_dir() / "dsh-home"
        dsh_home.mkdir(parents=True, exist_ok=True)
        patch_path = _render_patch()

        override = config.dsh_bin()
        harness = DeepSeekHarness(
            # None -> the SDK launches its bundled runtime executable.
            dsh_bin=str(override) if override is not None else None,
            profile="sdk",
            dsh_home=str(dsh_home),
            cwd=str(workspace),
            patches=(str(patch_path),),
            provider="deepseek-official",
            model=settings["chat_model"],
            api_key=settings.get("deepseek_api_key"),
            base_url=settings.get("deepseek_base_url"),
        )
        harness.start()
        return harness

    def _close_locked(self) -> None:
        try:
            self._harness.close()
        except Exception:
            log.exception("error closing previous dsh harness")
        self._harness = None
        self._key = None

    def close(self) -> None:
        with self._lock:
            if self._harness is not None:
                self._close_locked()


_manager = HarnessManager()


def get_manager() -> HarnessManager:
    return _manager


def status() -> dict:
    return _manager.status()


@asynccontextmanager
async def lifespan(app):  # noqa: ARG001 - contract requires the app parameter
    try:
        yield
    finally:
        _manager.close()
