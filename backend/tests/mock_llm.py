"""A scriptable OpenAI-compatible chat-completions mock for the `dsh` tests.

A Python port of the subset of the harness repo's `@deepseek-ai/dsh-llm-mock-server`
that test_chat_mock.py used, so the test needs no harness checkout or Node. Each
POST to `.../chat/completions` consumes the next behavior in `sequence`:

- `tool_call_success`: streams one function tool call (`tool_name` / `tool_arguments`,
  arguments split across two SSE chunks) and finishes with `finish_reason: tool_calls`.
- `success`: streams `success_text` in small chunks and finishes with `stop`.

With `repeat_last`, the final behavior repeats once the sequence is exhausted;
otherwise further requests get a 500 like the original.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHUNK_SIZE = 48


def _split(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


class MockLlmServer:
    def __init__(
        self,
        *,
        port: int,
        sequence: list[str],
        api_key: str | None = None,
        repeat_last: bool = False,
        tool_name: str = "mock_tool",
        tool_arguments: str = "{}",
        success_text: str = "Mock success response.",
        host: str = "127.0.0.1",
    ) -> None:
        self.host = host
        self.port = port
        self.sequence = list(sequence)
        self.api_key = api_key
        self.repeat_last = repeat_last
        self.tool_name = tool_name
        self.tool_arguments = tool_arguments
        self.success_text = success_text
        self.requests: list[dict] = []
        self._cursor = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def _next_behavior(self) -> str:
        with self._lock:
            if self._cursor < len(self.sequence):
                behavior = self.sequence[self._cursor]
            elif self.repeat_last and self.sequence:
                behavior = self.sequence[-1]
            else:
                behavior = "script_exhausted"
            self._cursor += 1
            return behavior

    def start(self) -> None:
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:  # keep pytest output quiet
                pass

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _sse(self, payload) -> None:
                data = payload if isinstance(payload, str) else json.dumps(payload)
                self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.flush()

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                if not self.path.split("?")[0].endswith("/chat/completions"):
                    self.send_response(404)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if mock.api_key is not None and self.headers.get("authorization") != f"Bearer {mock.api_key}":
                    self._json(401, {"error": {"message": "invalid mock bearer token", "code": "invalid_api_key"}})
                    return
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    self._json(400, {"error": {"message": "request body must be valid JSON", "code": "invalid_json"}})
                    return

                behavior = mock._next_behavior()
                mock.requests.append({"behavior": behavior, "body": body})
                if behavior == "script_exhausted":
                    self._json(500, {"error": {"message": "mock script exhausted", "code": "MOCK_SCRIPT_EXHAUSTED"}})
                    return

                self.send_response(200)
                self.send_header("content-type", "text/event-stream; charset=utf-8")
                self.send_header("cache-control", "no-cache")
                self.send_header("connection", "close")
                self.end_headers()

                if behavior == "tool_call_success":
                    args = mock.tool_arguments
                    mid = max(1, len(args) // 2)
                    self._sse({"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
                    self._sse(
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": "mock-call-1",
                                                "type": "function",
                                                "function": {"name": mock.tool_name, "arguments": args[:mid]},
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ]
                        }
                    )
                    self._sse(
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": args[mid:]}}]},
                                    "finish_reason": None,
                                }
                            ]
                        }
                    )
                    self._sse(
                        {
                            "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "tool_calls"}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                        }
                    )
                elif behavior == "success":
                    for chunk in _split(mock.success_text, CHUNK_SIZE):
                        self._sse({"choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]})
                    self._sse(
                        {
                            "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 3, "completion_tokens": len(mock.success_text)},
                        }
                    )
                else:
                    raise ValueError(f"unsupported mock behavior: {behavior}")
                self._sse("[DONE]")

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
