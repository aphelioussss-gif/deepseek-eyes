"""DeepSeek Eyes — transformer 单元测试 (TDD)

运行: python3 test_transformer.py
"""

import json
import sys
import traceback

from transformer import (
    scan_content,
    replace_images,
    ImageSourceError,
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


def eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}: {a!r} != {b!r}")


def contains(a, b, msg=""):
    if b not in a:
        raise AssertionError(f"{msg}: {b!r} not in {a!r}")


# ── image block helpers ──

def img_block(data="aaaa", media_type="image/png"):
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def text_block(text="hello"):
    return {"type": "text", "text": text}


def tool_result_block(content_text="result"):
    return {"type": "tool_result", "content": [{"type": "text", "text": content_text}]}


# ── scan_content tests ──

def test_scan_no_image():
    """字符串 content 无图片，image_count=0"""
    result, count = scan_content("hello world")
    eq(count, 0, "count")
    eq(result, "hello world", "content unchanged")


def test_scan_list_no_image():
    """list content 无图片，image_count=0"""
    content = [text_block("hello"), text_block("world")]
    result, count = scan_content(content)
    eq(count, 0, "count")
    eq(len(result), 2, "length unchanged")


def test_scan_single_image():
    """单张图片被替换为 text block"""
    content = [img_block(), text_block("describe this")]
    mock_vision = lambda b, m: "MOCK_DESC"
    result, count = scan_content(content, mock_vision)
    eq(count, 1, "image_count")
    eq(result[0]["type"], "text", "image → text")
    contains(result[0]["text"], "[视觉分析]", "prefix")
    contains(result[0]["text"], "MOCK_DESC", "description")
    eq(result[1]["type"], "text", "text block unchanged")


def test_scan_tool_result_nested_image():
    """tool_result.content 中的图片不被代理介入"""
    content = [
        text_block("check this screenshot"),
        {
            "type": "tool_result",
            "content": [
                {"type": "text", "text": "read file:"},
                img_block(),
            ],
        },
    ]
    mock_vision = lambda b, m: "NESTED_DESC"
    result, count = scan_content(content, mock_vision)
    eq(count, 0, "image_count")
    eq(result, content, "tool_result must stay untouched")


def test_scan_tool_use_input_untouched():
    """tool_use.input 中的 {"type":"image"} 不被误伤"""
    content = [
        text_block("use this tool"),
        {
            "type": "tool_use",
            "id": "tool_001",
            "name": "search",
            "input": {"query": "find image", "type": "image"},  # 业务数据，不应替换
        },
        img_block(),  # 真正的图片
    ]
    mock_vision = lambda b, m: "REAL_IMG_DESC"
    result, count = scan_content(content, mock_vision)
    eq(count, 1, "只替换了一张真正的图片")
    # tool_use.input 的 type=image 不应被替换
    eq(result[1]["input"]["type"], "image", "tool_use.input untouched")
    eq(result[1]["input"]["query"], "find image", "tool_use.input.query untouched")


def test_scan_unknown_dict_not_recursed():
    """未知 dict 类型不递归进入（防止意外修改）"""
    content = [
        text_block("some text"),
        {"type": "custom_future_block", "nested": {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "xxxx"}}},
    ]
    mock_vision = lambda b, m: "SHOULD_NOT_APPEAR"
    result, count = scan_content(content, mock_vision)
    eq(count, 0, "未知 dict 不扫描内部")


def test_scan_deeply_nested_tool_result():
    """多层嵌套 tool_result 中的图片也不处理"""
    content = [
        {
            "type": "tool_result",
            "content": [
                text_block("level 1"),
                {
                    "type": "tool_result",
                    "content": [text_block("level 2"), img_block()],
                },
            ],
        },
    ]
    mock_vision = lambda b, m: "DEEP_DESC"
    result, count = scan_content(content, mock_vision)
    eq(count, 0, "深层嵌套工具结果不处理")
    eq(result, content, "nested tool_result unchanged")


# ── ImageSourceError tests ──

def test_source_url_raises():
    """source.type=url 抛出 ImageSourceError"""
    block = {"type": "image", "source": {"type": "url", "url": "https://example.com/img.png"}}
    try:
        replace_images({"source": block["source"]}, lambda b, m: "x")
        raise AssertionError("应该抛异常")
    except ImageSourceError as e:
        eq(e.status_code, 400)


def test_source_missing_raises():
    """缺 source 抛出 ImageSourceError"""
    block = {"type": "image"}
    try:
        replace_images({"source": block.get("source")}, lambda b, m: "x")
        raise AssertionError("应该抛异常")
    except ImageSourceError as e:
        eq(e.status_code, 400)


def test_source_unknown_type_raises():
    """非 base64/url 的 source.type 抛出 ImageSourceError"""
    block = {"type": "image", "source": {"type": "quantum", "data": "xxx"}}
    try:
        replace_images({"source": block["source"]}, lambda b, m: "x")
        raise AssertionError("应该抛异常")
    except ImageSourceError as e:
        eq(e.status_code, 400)


def test_invalid_mime_raises():
    """不支持的 MIME 抛出 ImageSourceError"""
    block = {"type": "image", "source": {"type": "base64", "media_type": "image/tiff", "data": "aaaa"}}
    try:
        replace_images({"source": block["source"]}, lambda b, m: "x")
        raise AssertionError("应该抛异常")
    except ImageSourceError as e:
        eq(e.status_code, 400)


def test_bad_base64_raises():
    """非法 base64 抛出 ImageSourceError"""
    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "!!!not-valid-base64!!!"}}
    try:
        replace_images({"source": block["source"]}, lambda b, m: "x")
        raise AssertionError("应该抛异常")
    except ImageSourceError as e:
        eq(e.status_code, 400)


# ── unsupported block tests ──

def test_unsupported_block_passthrough():
    """非 image block 不由代理拦截，交给 DeepSeek 上游处理"""
    content = [text_block("hello"), {"type": "document", "data": "xxx"}]
    result, count = scan_content(content)
    eq(count, 0)
    eq(result, content)


def test_supported_blocks_pass():
    """type=text, image, tool_use, tool_result 不抛异常"""
    content = [
        text_block("hello"),
        {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
        {"type": "tool_result", "content": [{"type": "text", "text": "ok"}]},
    ]
    result, count = scan_content(content)
    eq(count, 0)


# ── replace_images (pure) tests ──

def test_replace_images_with_fake_vision():
    """replace_images 用 fake callback 替换图片"""
    block = img_block("ZmFrZQ==")  # "fake" in base64
    fake_cb = lambda raw_bytes, mime: f"DESC: {len(raw_bytes)} bytes, {mime}"
    result, count = replace_images(block, fake_cb)
    eq(count, 1)
    eq(result["type"], "text")
    contains(result["text"], "DESC: 4 bytes")
    contains(result["text"], "image/png")
    contains(result["text"], "[视觉分析]")


# ── 主入口 ──

print("\ntransformer 单元测试")
print("=" * 60)

# scan_content 测试
print("\n── scan_content ──")
t("字符串 content 无图片", test_scan_no_image)
t("列表 content 无图片", test_scan_list_no_image)
t("单张图片替换", test_scan_single_image)
t("tool_result 嵌套图片", test_scan_tool_result_nested_image)
t("tool_use.input 不被误伤", test_scan_tool_use_input_untouched)
t("未知 dict 不递归", test_scan_unknown_dict_not_recursed)
t("多层嵌套 tool_result", test_scan_deeply_nested_tool_result)

# ImageSourceError 测试
print("\n── ImageSourceError ──")
t("source.url 抛 400", test_source_url_raises)
t("缺 source 抛 400", test_source_missing_raises)
t("未知 source.type 抛 400", test_source_unknown_type_raises)
t("不支持 MIME 抛 400", test_invalid_mime_raises)
t("非法 base64 抛 400", test_bad_base64_raises)

# 非 image block 透明转发
print("\n── non-image passthrough ──")
t("unsupported block 透明转发", test_unsupported_block_passthrough)
t("合法 block 不抛异常", test_supported_blocks_pass)

# replace_images 纯函数测试
print("\n── replace_images ──")
t("fake vision 替换图片", test_replace_images_with_fake_vision)

# 结果
print("\n" + "=" * 60)
print(f"结果: {PASS} passed, {FAIL} failed, {PASS + FAIL} total")

if FAIL > 0:
    sys.exit(1)
