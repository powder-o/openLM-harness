"""Integration test: the bundled dsh runtime + tests/mock_llm.py + our MCP server + chat API,
end to end.

Marked `dsh` (slow-ish, launches the runtime executable and a mock LLM server) — run with
`uv run pytest -m dsh tests/test_chat_mock.py`. Everything else in the suite runs without it.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest

from study import config
from tests.mock_llm import MockLlmServer

pytestmark = pytest.mark.dsh


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http_ok(url: str, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1)
            if r.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001 - retry until timeout
            last_exc = exc
        time.sleep(0.1)
    raise RuntimeError(f"server at {url} did not become ready: {last_exc}")


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        if event_name is not None:
            payload = json.loads("\n".join(data_lines)) if data_lines else {}
            events.append((event_name, payload))

    for line in body.splitlines():
        if line == "":
            flush()
            event_name, data_lines = None, []
        elif line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    flush()
    return events


def test_chat_turn_cites_real_chunk_and_strips_bogus_marker(sample_notebook, monkeypatch):
    import uvicorn

    from study.app import create_app

    notebook_id = sample_notebook["notebook_id"]
    entropy_chunk_id = sample_notebook["chunks"]["entropy"]

    backend_port = _free_port()
    mock_port = _free_port()
    monkeypatch.setenv("STUDY_PORT", str(backend_port))
    monkeypatch.setenv("STUDY_MCP_URL", f"http://127.0.0.1:{backend_port}/mcp/")

    config.save_settings(
        {
            "deepseek_api_key": "mock-key",
            "deepseek_base_url": f"http://127.0.0.1:{mock_port}/v1",
            "chat_model": "deepseek-v4-flash",
        }
    )

    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=backend_port, log_level="warning"))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    success_text = (
        f"Entropy measures the number of accessible microstates of a system [c:{entropy_chunk_id}]. "
        "Unrelated aside [c:999999]."
    )
    mock = MockLlmServer(
        port=mock_port,
        api_key="mock-key",
        sequence=["tool_call_success", "success"],
        repeat_last=True,
        tool_name="mcp__study__search",
        tool_arguments=json.dumps({"scope": f"notebook:{notebook_id}", "query": "entropy"}),
        success_text=success_text,
    )
    mock.start()
    try:
        _wait_http_ok(f"http://127.0.0.1:{backend_port}/docs")

        with httpx.Client(base_url=f"http://127.0.0.1:{backend_port}", timeout=60) as client:
            resp = client.post("/api/chat/sessions", json={"scope": f"notebook:{notebook_id}"})
            assert resp.status_code == 200, resp.text
            session = resp.json()

            resp = client.post(
                f"/api/chat/sessions/{session['id']}/messages",
                json={"content": "What is entropy?"},
            )
            assert resp.status_code == 200, resp.text
            events = _parse_sse(resp.text)
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        mock.stop()

    names = [name for name, _ in events]
    assert "user_saved" in names, events
    assert not [d for e, d in events if e == "error"], events

    tool_calls = [d for e, d in events if e == "tool_call"]
    assert any(d.get("name") == "search" for d in tool_calls), events
    tool_results = [d for e, d in events if e == "tool_result"]
    assert any(d.get("name") == "search" for d in tool_results), events

    done = [d for e, d in events if e == "done"]
    assert done, events
    message = done[-1]["message"]

    assert "c:999999" not in message["content"]

    chunk_citations = {c["chunk_id"]: c for c in message["citations"] if c["kind"] == "chunk"}
    assert entropy_chunk_id in chunk_citations, message["citations"]
    entropy_citation = chunk_citations[entropy_chunk_id]
    assert entropy_citation["page"] == 2
    assert entropy_citation["boxes"]
    assert all(999999 != c.get("chunk_id") for c in message["citations"])
