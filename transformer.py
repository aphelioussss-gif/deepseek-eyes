"""DeepSeek Eyes — Anthropic 图片替换器。

只扫描 Anthropic Messages API 的 messages[].content 直接子 block，
将真正的 ``{"type": "image"}`` content block 替换为 text。
不进入 tool_use.input / tool_result.content / metadata / 未知 dict。
"""

import base64
from typing import Any, Callable, Optional

SUPPORTED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif"}

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


def scan_content(
    node: Any,
    vision_fn: Optional[Callable[[bytes, str], str]] = None,
) -> tuple:
    """扫描 Anthropic message.content，替换直接 image block。

    扫描边界：
    ✓ messages[].content (str / list)
    ✓ content block: type=image
    ✗ tool_result.content（工具输出必须透明转发）
    ✗ tool_use.input（业务数据）
    ✗ 未知 dict type（未来兼容但保守）
    ✗ system prompt string

    Args:
        node: Anthropic content 节点
        vision_fn: (raw_bytes, mime) -> description_text。
                   为 None 时仅做 unsupported block 检测。

    Returns:
        (transformed_node, image_count)。没有图片时返回原节点。
    """
    if vision_fn is None:
        vision_fn = lambda b, m: "[无视觉处理器]"

    # 字符串 content → 直接返回
    if isinstance(node, str):
        return node, 0

    # 列表 content → 逐元素扫描
    if isinstance(node, list):
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

            # 非 image block 全部保留，不递归、不校验、不兼容化。
            result.append(elem)

        if total_count == 0:
            return node, 0
        return result, total_count

    # 其他类型（不应出现）→ 原样返回
    return node, 0
