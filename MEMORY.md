# DeepSeek Eyes — MEMORY

## 方案决策

### 为什么选豆包而非 Gemini/Qwen-VL
- **豆包 (Doubao Seed)**: 国内直连，中文 OCR 最强，火山引擎 ARK API 标准 OpenAI 兼容，价格低
- **Gemini Flash 2.0**: 免费 1500 张/天，但国内需代理，延迟不可控
- **Qwen-VL (阿里百炼)**: 中文也好，API 也方便，作为备选

### 为什么描述法 + 坐标定位双输出
- 描述法：语义理解（"这是一个登录页面"）
- 坐标定位：空间推理（"按钮在 (400,500)-(600,545)"）
- 一次 API 调用同时完成，省时省钱

### 为什么缓存默认不写磁盘
- cache.json 里是 OCR 文字 + 图片描述 + 界面元素，等同截图内容
- 默认内存缓存 + 可选磁盘持久化，用户主动开启

### 为什么 DeepSeek Key 从入站透传
- 入站有 x-api-key 或 Authorization，原样转发
- 代理不存储 DeepSeek Key
- 避免 Bearer Bearer 拼接错误

### 为什么 ThreadingHTTPServer 而非 aiohttp
- v1 优先零依赖，Python 标准库足够
- threading 模式下每个请求独立线程，不互相阻塞
- 后续可升级到异步框架

### v1.1-lite: 固定本地代理启动入口
- 当前优先修复方向是确保 Claude Code 的 `ANTHROPIC_BASE_URL` 指向 `http://127.0.0.1:8788`
- 不要直连 `https://api.deepseek.com/anthropic`，否则请求绕过 deepseek-eyes，图片不会被转换
- 推荐入口是 `bash claude-with-eyes.sh`，它会启动代理、设置本地 base URL，再执行 `claude`
- Claude Code 会读取 `~/.claude/settings.json` 的 `env.ANTHROPIC_BASE_URL`；这个全局配置会覆盖 shell export，所以启动脚本必须同步修正该文件
- 暂不采用 model spoof/model rewrite；只有在请求已经进入代理但客户端仍不发送 image block 时再考虑

### 2026-05-15 排障结论
- 症状：Claude Code 仍提示 DeepSeek 没有视觉能力，并倾向调用 OCR skill，`/tmp/deepseek-eyes.log` 没有 `event=received`
- 判断：不是豆包读错，也不是图片 transform 失败；代理根本没有收到 Claude Code 的 `/v1/messages`
- 关键证据：代理监听 8788 且 `/health` 正常；Claude 子进程继承了 `ANTHROPIC_BASE_URL=http://127.0.0.1:8788`，但全局 `~/.claude/settings.json` 仍写着 `https://api.deepseek.com/anthropic`
- 根因：Claude Code 实际读取全局 settings 中的 env，覆盖/绕过 shell export，导致请求直连 DeepSeek
- 修复：`claude-with-eyes.sh` 启动前同步修正 `~/.claude/settings.json` 的 `env.ANTHROPIC_BASE_URL`
- 验证口径：发图后必须在日志看到 `event=received`；若有图片 block，还应看到 `has_image=true image_count=1`

### 2026-05-15 豆包 404 / Doubao Seed 2.0 lite
- 用户当前使用 Doubao-Seed-2.0-lite；火山控制台模型详情 URL 中的 `Id=doubao-seed-2-0-lite` 是模型族/详情页 ID，不等同于 Chat API 最稳妥的版本化调用 ID
- 普通在线推理 Chat API 使用 `ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3`，视觉模型应传 `doubao-seed-2-0-lite-260215`
- Coding Plan API 使用 `https://ark.cn-beijing.volces.com/api/coding/v3`，模型名是 `doubao-seed-2.0-lite`
- 如果把 Coding Plan 的 `doubao-seed-2.0-lite` 直接发到普通 `/api/v3/chat/completions`，火山侧可能返回 HTTP 404 / model or endpoint not found
- `config.py` 已新增模型名归一化：普通 `/api/v3` 下把 `doubao-seed-2.0-lite`、`doubao-seed-2-0-lite` 映射到 `doubao-seed-2-0-lite-260215`；`/api/coding/v3` 下反向映射到 `doubao-seed-2.0-lite`
- `vision.py` 已增强 HTTPError 日志，豆包错误会带响应 body、`base_url` 和实际 `model`，便于区分模型名错、权限错、图片参数错
- 真实链路验证通过：20x20 四色 PNG 经 `vision.analyze_image` 成功返回红/绿/蓝/白四块、坐标和空间关系，说明当前豆包识图链路已可用

### 2026-05-15 SSE 结束卡住排障结论
- 症状：Claude Code 已显示完整回答，但底部继续 loading；等待一段时间后才自然结束。用户确认这是每次回答后都会出现，不是立即结束
- 关键证据：`/tmp/deepseek-eyes.log` 中普通 SSE 请求已有 `upstream_status=200 sse=true client_closed=false`，但 `duration_ms` 常见 8-13 秒；说明内容结束后代理还在等上游连接关闭
- 根因：`proxy.py` 原先用 `resp.read(8192)` 转发 SSE。对事件流来说，这会等缓冲区填满或等上游关闭 TCP/HTTP 连接，不能在模型逻辑结束时立即返回给 Claude Code
- 修复：SSE 改成 `readline()` 按行/事件转发；看到 Anthropic 兼容流的 `event: message_stop` 或 OpenAI 风格的 `data: [DONE]` 后主动停止读取并关闭上游连接
- 测试：新增 `test_proxy_stream.py`，覆盖 `message_stop`、`[DONE]`、非终止事件三类场景；同时保留 `test_transformer.py` 回归
- 启动兼容性：后台/`screen` 环境可能使用较旧 Python，`bytes | None` 会报 `TypeError`；已改为 `Optional[bytes]`
- 当前运行状态：修复后代理已在 `127.0.0.1:8788` 恢复，health endpoint 返回 ok

### v1.2-lite: 图片增强代理边界收窄
- 新原则：8788 只做图片预处理，不做完整 Anthropic 兼容层；有图时替换图片 block，无图时请求体 byte-for-byte 透传到 DeepSeek
- `transformer.py` 只扫描 `messages[].content` 的直接 `type=image` block；不再进入 `tool_result.content`，也不校验或拦截 document / MCP / search_result 等非 image block
- `proxy.py` 新增无图零改写路径：解析只用于检测图片；`image_count=0` 时转发原始 body bytes，避免 JSON 重排、字段顺序变化或工具参数被误动
- 工具调用边界：`tool_use.input`、`tool_result.content`、Bash/Read/Write/MCP 相关内容全部透明转发
- 本地环境注意：如果 shell 设置了 `http_proxy`，`curl 127.0.0.1:8788` 可能被本机代理劫持成 502；`test_smoke.sh` 已统一使用 `curl --noproxy '*'`
- 版本号：`config.PROJECT_VERSION = "v1.2-lite"`，启动日志会显示版本

## API 参考

### 豆包 Chat API (Vision)
```
POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
Authorization: Bearer {ARK_API_KEY}
Content-Type: application/json

{
  "model": "doubao-seed-2-0-lite-260215",
  "messages": [{"role": "user", "content": [
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    {"type": "text", "text": "分析图片"}
  ]}],
  "max_tokens": 4096,
  "temperature": 0.0,
  "response_format": {"type": "json_object"}
}
```

### DeepSeek Anthropic-compatible
```
POST https://api.deepseek.com/anthropic/v1/messages
x-api-key: {DEEPSEEK_KEY}
Content-Type: application/json
Anthropic-Version: 2023-06-01
```

### 坐标归一化
- 0-1000 范围，左上角 (0,0)，右下角 (1000,1000)
- 仅辅助空间推理，不映射真实屏幕坐标

## 替代方案记录（不采用）

| 方案 | 原因 |
|------|------|
| deepcode-v4 (mvmv1428) | 本地跑 Ollama 视觉模型，内存压力大；静默删 MCP block |
| cc-vision-gateway (ChenZengQing) | Go + Docker，太重；功能完整但复杂度高 |
| OpenHanako (liliMozi) | 完整桌面应用，不嵌入 Claude Code |
| anthropic-image-proxy | 只修 tool_result 图片，不做视觉理解 |
