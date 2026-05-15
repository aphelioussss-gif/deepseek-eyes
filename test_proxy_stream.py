"""DeepSeek Eyes — proxy tests.

Run: python3 test_proxy_stream.py
"""

import json
import sys
import traceback

from proxy import _forward_sse_stream, _prepare_upstream_body, _sse_event_is_terminal


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

    client_closed = _forward_sse_stream(resp, wfile)

    assert client_closed is False
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

    client_closed = _forward_sse_stream(resp, wfile)

    assert client_closed is False
    assert b"".join(wfile.parts).endswith(b"data: [DONE]\n\n")
    assert resp.reads == 4


def test_nonterminal_event():
    assert _sse_event_is_terminal([b"event: message_delta\n", b"data: {}\n"]) is False


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


if __name__ == "__main__":
    print("DeepSeek Eyes proxy stream tests")
    print("================================")
    t("Anthropic message_stop ends stream", test_terminal_anthropic_message_stop)
    t("OpenAI [DONE] ends stream", test_terminal_openai_done)
    t("nonterminal event continues", test_nonterminal_event)
    t("no-image request body is byte-for-byte passthrough", test_prepare_no_image_preserves_body_bytes)
    t("tool_use/tool_result are preserved", test_prepare_tool_blocks_preserved)
    t("direct image rewrites body", test_prepare_direct_image_rewrites_body)
    print("================================")
    print(f"结果: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")
    sys.exit(1 if FAIL else 0)
