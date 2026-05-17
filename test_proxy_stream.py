"""DeepSeek Eyes — proxy tests.

Run: python3 test_proxy_stream.py
"""

import json
import sys
import traceback

from proxy import (
    _build_upstream_headers,
    _build_upstream_path,
    _forward_sse_stream,
    _prepare_upstream_body,
    _sse_event_is_terminal,
)


PASS = 0
FAIL = 0


def t(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✓ {name}")
    except Exception as e:
        FAIL += 1
        print(f"  ✗ {name}: {e}")
        traceback.print_exc()


class FakeResp:
    def __init__(self, lines):
        self.lines = list(lines)
        self.reads = 0

    def readline(self):
        self.reads += 1
        if not self.lines:
            raise AssertionError("readline called after terminal SSE event")
        return self.lines.pop(0)


class FakeWfile:
    def __init__(self):
        self.parts = []
        self.flushes = 0

    def write(self, data):
        self.parts.append(data)

    def flush(self):
        self.flushes += 1


def test_terminal_anthropic_message_stop():
    lines = [
        b"event: content_block_delta\n",
        b'data: {"type":"content_block_delta"}\n',
        b"\n",
        b"event: message_stop\n",
        b'data: {"type":"message_stop"}\n',
        b"\n",
    ]
    resp = FakeResp(lines)
    wfile = FakeWfile()

    client_closed, end_reason = _forward_sse_stream(resp, wfile)

    assert client_closed is False
    assert end_reason == "terminal_event"
    assert b"".join(wfile.parts).endswith(b'{"type":"message_stop"}\n\n')
    assert resp.reads == 6


def test_terminal_openai_done():
    lines = [
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n',
        b"\n",
        b"data: [DONE]\n",
        b"\n",
    ]
    resp = FakeResp(lines)
    wfile = FakeWfile()

    client_closed, end_reason = _forward_sse_stream(resp, wfile)

    assert client_closed is False
    assert end_reason == "terminal_event"
    assert b"".join(wfile.parts).endswith(b"data: [DONE]\n\n")
    assert resp.reads == 4


def test_nonterminal_event():
    assert _sse_event_is_terminal([b"event: message_delta\n", b"data: {}\n"]) is False


def test_terminal_json_message_stop_type():
    """data: {"type":"message_stop"} → terminal"""
    assert _sse_event_is_terminal([b'data: {"type":"message_stop"}\n']) is True


def test_terminal_json_delta_stop_reason():
    """data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}} → terminal"""
    assert _sse_event_is_terminal([
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n'
    ]) is True


def test_terminal_json_top_level_stop_reason():
    """data: {"stop_reason":"max_tokens"} → terminal"""
    assert _sse_event_is_terminal([b'data: {"stop_reason":"max_tokens"}\n']) is True


def test_nonterminal_json_content_block():
    """data: {"type":"content_block_delta","delta":{"text":"hi"}} → not terminal"""
    assert _sse_event_is_terminal([
        b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n'
    ]) is False


def test_nonterminal_json_empty_stop_reason():
    """data: {"stop_reason":""} → not terminal (empty string)"""
    assert _sse_event_is_terminal([b'data: {"stop_reason":""}\n']) is False


def test_nonterminal_json_null_stop_reason():
    """data: {"stop_reason":null} → not terminal"""
    assert _sse_event_is_terminal([b'data: {"stop_reason":null}\n']) is False


def test_terminal_json_message_delta_empty_stop_reason():
    """data: {"type":"message_delta","delta":{"stop_reason":""}} → not terminal"""
    assert _sse_event_is_terminal([
        b'data: {"type":"message_delta","delta":{"stop_reason":""}}\n'
    ]) is False


def test_nonterminal_json_malformed():
    """Malformed JSON in data: line → not terminal, no crash"""
    assert _sse_event_is_terminal([b'data: {not json}\n']) is False


class FakeIdleTimeoutResp:
    """SSE 上游先发部分数据，然后 socket.timeout。"""
    def __init__(self):
        self._lines = [
            b'data: {"type":"content_block_start"}\n',
            b"\n",
        ]
        self._calls = 0

    def readline(self):
        import socket
        if self._calls < len(self._lines):
            line = self._lines[self._calls]
            self._calls += 1
            return line
        raise socket.timeout("read timed out")


def test_idle_timeout_ends_stream():
    resp = FakeIdleTimeoutResp()
    wfile = FakeWfile()

    client_closed, end_reason = _forward_sse_stream(resp, wfile)

    assert client_closed is False
    assert end_reason == "idle_timeout"
    assert wfile.parts  # 部分数据已转发


def test_idle_timeout_disabled_when_no_conn():
    """conn=None 时不设 socket timeout，旧行为保持不变。"""
    lines = [
        b"data: [DONE]\n",
        b"\n",
    ]
    resp = FakeResp(lines)
    wfile = FakeWfile()

    client_closed, end_reason = _forward_sse_stream(resp, wfile, conn=None)

    assert client_closed is False
    assert end_reason == "terminal_event"


def test_connection_closed_end_reason():
    """上游直接关闭连接 → end_reason=connection_closed"""

    class FakeClosedResp:
        def readline(self):
            return b""

    resp = FakeClosedResp()
    wfile = FakeWfile()

    client_closed, end_reason = _forward_sse_stream(resp, wfile)

    assert client_closed is False
    assert end_reason == "connection_closed"


def test_prepare_no_image_preserves_body_bytes():
    body = b'{ "model" : "x" , "messages" : [ { "role" : "user" , "content" : "hi" } ] }'

    upstream_body, parsed, count = _prepare_upstream_body(body, lambda b, m: "SHOULD_NOT_RUN")

    assert count == 0
    assert upstream_body == body
    assert parsed["messages"][0]["content"] == "hi"


def test_prepare_tool_blocks_preserved():
    payload = {
        "model": "x",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"type": "image"}},
                    {
                        "type": "tool_result",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "ZmFrZQ==",
                                },
                            }
                        ],
                    },
                ],
            }
        ],
    }
    body = json.dumps(payload, separators=(", ", ": ")).encode("utf-8")

    upstream_body, parsed, count = _prepare_upstream_body(body, lambda b, m: "SHOULD_NOT_RUN")

    assert count == 0
    assert upstream_body == body
    assert parsed == payload


def test_prepare_direct_image_rewrites_body():
    payload = {
        "model": "x",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "ZmFrZQ==",
                        },
                    }
                ],
            }
        ],
    }

    upstream_body, parsed, count = _prepare_upstream_body(
        json.dumps(payload).encode("utf-8"),
        lambda b, m: f"DESC {len(b)} {m}",
    )

    assert count == 1
    assert upstream_body != json.dumps(payload).encode("utf-8")
    block = parsed["messages"][0]["content"][0]
    assert block["type"] == "text"
    assert "DESC 4 image/png" in block["text"]


def test_build_upstream_headers_preserves_anthropic_headers():
    inbound = {
        "Content-Type": "application/json",
        "Anthropic-Version": "2024-01-01",
        "Anthropic-Beta": "tools-2024-05-01",
        "User-Agent": "ClaudeCode/1.0",
        "Connection": "keep-alive",
        "Accept-Encoding": "gzip",
    }

    out = _build_upstream_headers(inbound, {"x-api-key": "sk-test"})

    assert out["Content-Type"] == "application/json"
    assert out["Anthropic-Version"] == "2024-01-01"
    assert out["Anthropic-Beta"] == "tools-2024-05-01"
    assert out["User-Agent"] == "ClaudeCode/1.0"
    assert out["x-api-key"] == "sk-test"
    assert "Connection" not in out
    assert "Accept-Encoding" not in out


def test_build_upstream_path_prefixes():
    assert _build_upstream_path("/v1/messages") == "/anthropic/v1/messages"
    assert _build_upstream_path("/v1/messages/count_tokens") == "/anthropic/v1/messages/count_tokens"
    assert _build_upstream_path("/v1/models") == "/anthropic/v1/models"
    assert _build_upstream_path("/health") == "/anthropic/health"


def test_prepare_count_tokens_passthrough():
    """count_tokens 类请求（相同的 messages 结构）也应透明通过。"""
    payload = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "user", "content": "count these tokens please"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "sure"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"cmd": "ls"}},
            ]},
        ],
        "system": "you are helpful",
    }
    body = json.dumps(payload, separators=(", ", ": ")).encode("utf-8")

    upstream_body, parsed, count = _prepare_upstream_body(body, lambda b, m: "SHOULD_NOT_RUN")

    assert count == 0
    assert upstream_body == body
    assert parsed["model"] == "deepseek-v4-pro"
    assert parsed["system"] == "you are helpful"
    assert len(parsed["messages"]) == 2
    assert parsed["messages"][1]["content"][1]["type"] == "tool_use"


class FakeUpstreamResp:
    """模拟上游非 SSE 响应。"""
    def __init__(self, status, headers, body):
        self.status = status
        self._headers = headers
        self._body = body

    def getheader(self, name, default=None):
        for k, v in self._headers:
            if k.lower() == name.lower():
                return v
        return default

    def getheaders(self):
        return list(self._headers)

    def read(self):
        return self._body

    def readline(self):
        raise AssertionError("should not be called for non-SSE")


class FakeSSEResp:
    """模拟上游 SSE 流式响应。"""
    def __init__(self, lines):
        self.status = 200
        self._headers = [("Content-Type", "text/event-stream")]
        self._lines = list(lines)
        self._closed = False

    def getheader(self, name, default=None):
        for k, v in self._headers:
            if k.lower() == name.lower():
                return v
        return default

    def getheaders(self):
        return list(self._headers)

    def readline(self):
        if not self._lines:
            self._closed = True
            return b""
        return self._lines.pop(0)

    def read(self):
        raise AssertionError("should not be called for SSE")


class FakeWfile:
    def __init__(self):
        self.parts = []
        self.flushes = 0
        self.headers_sent = False

    def write(self, data):
        self.parts.append(data)

    def flush(self):
        self.flushes += 1


if __name__ == "__main__":
    print("DeepSeek Eyes proxy stream tests")
    print("================================")
    t("Anthropic message_stop ends stream", test_terminal_anthropic_message_stop)
    t("OpenAI [DONE] ends stream", test_terminal_openai_done)
    t("nonterminal event continues", test_nonterminal_event)
    t("JSON message_stop type → terminal", test_terminal_json_message_stop_type)
    t("JSON delta.stop_reason → terminal", test_terminal_json_delta_stop_reason)
    t("JSON top-level stop_reason → terminal", test_terminal_json_top_level_stop_reason)
    t("JSON content_block_delta → not terminal", test_nonterminal_json_content_block)
    t("JSON empty stop_reason → not terminal", test_nonterminal_json_empty_stop_reason)
    t("JSON null stop_reason → not terminal", test_nonterminal_json_null_stop_reason)
    t("JSON delta empty stop_reason → not terminal", test_terminal_json_message_delta_empty_stop_reason)
    t("JSON malformed → not terminal, no crash", test_nonterminal_json_malformed)
    t("idle timeout ends stream", test_idle_timeout_ends_stream)
    t("conn=None preserves old behaviour", test_idle_timeout_disabled_when_no_conn)
    t("connection closed end_reason", test_connection_closed_end_reason)
    t("no-image request body is byte-for-byte passthrough", test_prepare_no_image_preserves_body_bytes)
    t("tool_use/tool_result are preserved", test_prepare_tool_blocks_preserved)
    t("direct image rewrites body", test_prepare_direct_image_rewrites_body)
    t("Anthropic headers are preserved", test_build_upstream_headers_preserves_anthropic_headers)
    t("_build_upstream_path prefixes /anthropic", test_build_upstream_path_prefixes)
    t("count_tokens-like request passthrough", test_prepare_count_tokens_passthrough)
    print("================================")
    print(f"结果: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
    sys.exit(1 if FAIL else 0)
