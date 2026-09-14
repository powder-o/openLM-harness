"""Exercises the real `DeepSeekLLM` request path (SPEC "Tasks" §3).

`FakeLLM` (used everywhere else in the offline test suite) bypasses `DeepSeekLLM` entirely, so
nothing exercises the actual HTTP requests it builds for the OpenAI-compatible DeepSeek API. This
module spins up a tiny stub OpenAI-compatible server (FastAPI + uvicorn, in a background thread),
points `deepseek_base_url` at it with a dummy key and `STUDY_LLM_FAKE` unset, and drives
`DeepSeekLLM.chat_json` / `chat_text` / `describe_image` directly, plus one real graph concept
extraction call and one real flashcard generation call through the actual prompt code in
`study.graph.concepts` / `study.study_tools.flashcards`.

Checks: requests are well-formed (correct model per task, `response_format` set for JSON-mode
calls and absent otherwise, an `image_url` + `text` content part for the vision call), and -
because DeepSeek's JSON mode requires the word "json" to appear somewhere in the prompt - every
JSON-mode request's combined system+user text contains "json" (case-insensitive).
"""
from __future__ import annotations

import base64
import json
import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from study import config, llm

# A minimal valid 1x1 transparent PNG, used as the vision call's input image.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_stub_app(recorded: list[dict]) -> FastAPI:
    """A tiny OpenAI-compatible `/v1/chat/completions` that records every request body and
    returns a canned response shaped to whichever real prompt is asking (recognised by a
    distinctive phrase from that prompt's system message), so the real extraction/flashcard code
    gets JSON it can actually parse and validate."""
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        recorded.append(body)
        messages = body.get("messages", [])
        system = next((m.get("content") for m in messages if m.get("role") == "system"), "") or ""
        user_msg = next((m for m in messages if m.get("role") == "user"), {})
        user_content = user_msg.get("content", "")

        if isinstance(user_content, list):
            # Vision call: content is a list of {"type": "text"|"image_url", ...} parts.
            content = "A stub description of the figure."
        elif "concept extraction agent" in system.lower():
            chunk_ids = [int(m) for m in re.findall(r'id="chunk:(\d+)"', user_content)]
            content = json.dumps({
                "concepts": [{
                    "name": "Stub Concept", "kind": "concept", "summary": "A concept invented by the stub.",
                    "chunk_ids": chunk_ids or [1], "evidence": "stub evidence",
                }],
                "relations": [],
            })
        elif "you write flashcards" in system.lower():
            chunk_ids = [int(m) for m in re.findall(r'id="chunk:(\d+)"', user_content)]
            content = json.dumps({"cards": [{
                "front": "What is the stub concept?", "back": "It is a concept invented by the stub.",
                "concept": "Stub Concept", "chunk_id": (chunk_ids or [1])[0],
            }]})
        elif (body.get("response_format") or {}).get("type") == "json_object":
            content = json.dumps({"ok": True})
        else:
            content = "stub plain-text response"

        return JSONResponse({
            "id": "stub-1", "object": "chat.completion", "created": 0, "model": body.get("model", "stub"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    return app


@pytest.fixture
def stub_llm(monkeypatch, data_dir):
    """Starts the stub server, points settings at it with a dummy key, and forces
    `config.fake_llm()` off so `llm.get_llm()` returns a real `DeepSeekLLM`. Yields the list of
    recorded request bodies (appended to live as calls happen)."""
    recorded: list[dict] = []
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(_make_stub_app(recorded), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(f"{base_url}/docs", timeout=0.5)
            break
        except Exception:
            time.sleep(0.05)
    else:
        pytest.fail("stub LLM server did not start in time")

    monkeypatch.delenv("STUDY_LLM_FAKE", raising=False)
    config.save_settings({
        "deepseek_api_key": "sk-test-dummy-key",
        "deepseek_base_url": f"{base_url}/v1",
        "chat_model": "test-chat-model",
        "extract_model": "test-extract-model",
        "vision_model": "test-vision-model",
    })
    try:
        yield recorded
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _system_and_user(request_body: dict) -> tuple[str, object]:
    messages = request_body["messages"]
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    return system, user


def test_chat_json_chat_text_and_vision_requests_are_well_formed(stub_llm, tmp_path):
    assert config.fake_llm() is False  # sanity: we really are exercising DeepSeekLLM, not FakeLLM
    client = llm.DeepSeekLLM()
    assert client.available()

    out = client.chat_json(task="generic", system="Output ONLY valid JSON.", user="Say hi as json.", max_tokens=100)
    assert out == {"ok": True}

    text = client.chat_text(task="generic", system="Write one short sentence.", user="Say hi.", max_tokens=50)
    assert text == "stub plain-text response"

    img_path = tmp_path / "tiny.png"
    img_path.write_bytes(_TINY_PNG)
    description = client.describe_image(str(img_path), "a test figure of a triangle")
    assert description == "A stub description of the figure."

    assert len(stub_llm) == 3
    json_req, text_req, vision_req = stub_llm

    # Model routing: chat_json/chat_text default to the configured extract_model, describe_image
    # always uses the vision_model (SPEC §10).
    assert json_req["model"] == "test-extract-model"
    assert text_req["model"] == "test-extract-model"
    assert vision_req["model"] == "test-vision-model"

    # JSON mode is requested only for the chat_json call.
    assert json_req.get("response_format") == {"type": "json_object"}
    assert "response_format" not in text_req
    assert "response_format" not in vision_req

    # DeepSeek's JSON mode requires the word "json" to appear in the prompt.
    sys_msg, user_msg = _system_and_user(json_req)
    assert "json" in f"{sys_msg} {user_msg}".lower()

    # The vision request carries a single user message with both an image_url and a text part.
    vision_content = vision_req["messages"][0]["content"]
    assert vision_req["messages"][0]["role"] == "user"
    assert isinstance(vision_content, list)
    types = {p["type"] for p in vision_content}
    assert {"text", "image_url"} <= types
    image_part = next(p for p in vision_content if p["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_real_concept_extraction_and_flashcard_prompts_are_json_mode_and_produce_valid_output(stub_llm, conn):
    """Drives the actual (non-fake) prompt-building code in study.graph.concepts and
    study.study_tools.flashcards through the stub, so we know their real prompts - not just the
    fake-handler stand-ins used elsewhere - are well-formed JSON-mode requests."""
    from study.graph.concepts import extract_for_document
    from study.study_tools import flashcards as flashcards_mod
    from study.index import embed

    archive_id = conn.execute("INSERT INTO archives (name) VALUES ('A')").lastrowid
    notebook_id = conn.execute(
        "INSERT INTO notebooks (archive_id, name) VALUES (?, 'NB')", (archive_id,)
    ).lastrowid
    document_id = conn.execute(
        "INSERT INTO documents (notebook_id, title, kind, source_name, status)"
        " VALUES (?, 'Doc', 'md', 'x.md', 'ready')",
        (notebook_id,),
    ).lastrowid
    text = "This stub chunk exists only to give the real concept-extraction prompt something to describe."
    vec = embed.embed_texts([text])[0]
    chunk_id = conn.execute(
        "INSERT INTO chunks (document_id, notebook_id, seq, kind, text, heading_path, block_ids_json,"
        " token_count, embedding) VALUES (?,?,?,?,?,?,?,?,?)",
        (document_id, notebook_id, 0, "text", text, "Doc", "[]", len(text.split()), embed.to_blob(vec)),
    ).lastrowid

    result = extract_for_document(conn, document_id)
    assert result["concepts"], result
    assert result["concepts"][0]["name"] == "Stub Concept"
    assert result["concepts"][0]["chunk_ids"] == [chunk_id]

    cards = flashcards_mod.generate(conn, notebook_id, {"kind": "notebook", "id": notebook_id}, count=3)
    assert cards, "expected the stub-backed real flashcards.generate() to produce at least one card"
    assert cards[0]["front"] == "What is the stub concept?"

    json_calls = [r for r in stub_llm if (r.get("response_format") or {}).get("type") == "json_object"]
    assert len(json_calls) >= 2, "expected at least one concept-extraction call and one flashcards call"
    for r in json_calls:
        sys_msg, user_msg = _system_and_user(r)
        assert "json" in f"{sys_msg} {user_msg}".lower(), r
