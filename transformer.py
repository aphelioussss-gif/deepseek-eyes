"""DeepSeek Eyes — 递归图片替换器。

扫描 Anthropic Messages API 的 content 结构，将 type=image 替换为 type=text。
只扫描 Anthropic content surface（messages[].content, tool_result.content），
不进入 tool_use.input / metadata / 未知 dict。
"""

import base64
from typing import Any, Callable, Optional

SUPPORTED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}

DEEPSEEK_UNSUPPORTED_BLOCKS = {
    "document",
    "search_result",
    "server_tool_use",
    "mcp_tool_use",
    "mcp_tool_result",
}

SCANNABLE_BLOCK_TYPES = {
    "text",
    "image",
    "tool_use",
    "tool_result",
    "thinking",
    "redacted_thinking",
}


class ImageSourceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class BlockNotSupportedError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def validate_and_decode_image(source: Optional[dict]) -> bytes:
    """返回 raw bytes。source 不合法时抛 ImageSourceError。

    source.type=base64 → 解码并返回
    source.type=url → 抛 400（v1 不支持）
    缺 source → 抛 400
    其他 type → 抛 400
    """
    if not source or not isinstance(source, dict):
        raise ImageSourceError("image block 缺少 source", 400)

    src_type = source.get("type")
    if src_type == "url":
        raise ImageSourceError("v1 不支持远程图片 URL，请使用 base64", 400)
    if src_type != "base64":
        raise ImageSourceError(f"不支持的 image source 类型: {src_type}", 400)

    mime = source.get("media_type", "")
    if mime not in SUPPORTED_MIME:
        raise ImageSourceError(f"不支持的图片格式: {mime} (支持: {', '.join(sorted(SUPPORTED_MIME))})", 400)

    data = source.get("data")
    if not data or not isinstance(data, str):
        raise ImageSourceError("base64 data 缺失或格式错误", 400)

    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as e:
        raise ImageSourceError(f"base64 解码失败: {e}", 400)

    return raw


def replace_images(
    block: dict,
    vision_fn: Callable[[bytes, str], str],
) -> tuple:
    """处理单个 image block，调 vision_fn 获取描述文字，返回替换后的 text block。

    Returns:
        (transformed_block, image_count): 转换后的 block + 处理的图片数 (0 或 1)
    """
    source = block.get("source")
    raw_bytes = validate_and_decode_image(source)
    mime = source.get("media_type", "image/png")
    description = vision_fn(raw_bytes, mime)
    text = (
        f"[视觉分析]\n"
        f"图片格式: {mime}\n"
        f"图片大小: {len(raw_bytes)} bytes\n"
        f"分析结果:\n{description}"
    )
    return {"type": "text", "text": text}, 1


def _validate_blocks(content: list) -> None:
    """检测 DeepSeek 不支持的 block type，检测到则抛出 BlockNotSupportedError。"""
    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type")
        if bt in DEEPSEEK_UNSUPPORTED_BLOCKS:
            raise BlockNotSupportedError(
                f"DeepSeek 不支持 content block type: {bt}。代理 v1 只转换 image。",
                400,
            )


def scan_content(
    node: Any,
    vision_fn: Optional[Callable[[bytes, str], str]] = None,
) -> tuple:
    """递归扫描 Anthropic content 节点，替换所有 image block。

    扫描边界：
    ✓ messages[].content (str / list)
    ✓ content block: type=image
    ✓ content block: type=tool_result → 递归 .content
    ✗ tool_use.input（业务数据）
    ✗ 未知 dict type（未来兼容但保守）
    ✗ system prompt string

    Args:
        node: Anthropic content 节点
        vision_fn: (raw_bytes, mime) -> description_text。
                   为 None 时仅做 unsupported block 检测。

    Returns:
        (transformed_node, image_count)
    """
    if vision_fn is None:
        vision_fn = lambda b, m: "[无视觉处理器]"

    # 字符串 content → 直接返回
    if isinstance(node, str):
        return node, 0

    # 列表 content → 逐元素扫描
    if isinstance(node, list):
        _validate_blocks(node)

        total_count = 0
        result = []
        for elem in node:
            if not isinstance(elem, dict):
                result.append(elem)
                continue

            bt = elem.get("type")

            # image block → 替换
            if bt == "image":
                transformed, count = replace_images(elem, vision_fn)
                result.append(transformed)
                total_count += count
                continue

            # tool_result → 递归进入 .content
            if bt == "tool_result":
                inner = elem.get("content", [])
                transformed_inner, count = scan_content(inner, vision_fn)
                total_count += count
                result.append({**elem, "content": transformed_inner})
                continue

            # tool_use → 不进入 .input
            # text / thinking / 其他已知 scannable block → 保留
            if bt in SCANNABLE_BLOCK_TYPES:
                result.append(elem)
                continue

            # 未知 block type → 保留原样，不递归（保守）
            result.append(elem)

        return result, total_count

    # 其他类型（不应出现）→ 原样返回
    return node, 0
