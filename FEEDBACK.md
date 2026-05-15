# DeepSeek Eyes — FEEDBACK

## 2026-05-15: v1.1-lite 启动链路排障

### 发生了什么
- 用户通过 `bash claude-with-eyes.sh` 启动 Claude Code，界面显示 `ANTHROPIC_BASE_URL=http://127.0.0.1:8788`
- 但上传图片后，Claude Code 仍回答“DeepSeek 没有原生视觉能力”，并尝试调用 `paddleocr-text-recognition`
- `tail -f /tmp/deepseek-eyes.log` 没有任何 `/v1/messages` 请求日志

### 有效判断
- 如果日志没有 `event=received`，说明问题在 Claude Code 到代理之间，不在豆包或 transformer
- 如果日志有 `event=received` 但没有 `has_image=true`，再考虑客户端没有发送 image block
- 如果日志有 `has_image=true` 但回答不对，才排查豆包视觉质量、prompt 或注入文本格式

### 根因
- `~/.claude/settings.json` 里的 `env.ANTHROPIC_BASE_URL` 写死为 `https://api.deepseek.com/anthropic`
- 这个全局配置会影响 Claude Code 实际请求路径；仅在 shell 中 `export ANTHROPIC_BASE_URL=http://127.0.0.1:8788` 不够稳

### 修复动作
- 新增 `claude-with-eyes.sh` 作为唯一推荐入口
- `claude-with-eyes.sh` 启动前自动把 `~/.claude/settings.json` 的 `env.ANTHROPIC_BASE_URL` 改为 `http://127.0.0.1:8788`
- `proxy.py` 在收到 `/v1/messages` 时立即写 `event=received`，便于确认请求是否进入代理
- `start.sh` 明确提示不要直连 `https://api.deepseek.com/anthropic`

### 后续建议
- 暂不做 model spoof/model rewrite，除非日志证明请求已进入代理但 Claude Code 仍不发送 image block
- 下一次验收必须以日志为准：`event=received` -> `has_image=true image_count=1` -> `upstream_status=200`
- 如果换 Claude Code/cmux 版本后失效，优先检查 `~/.claude/settings.json` 和 cmux 是否有新的 provider/env 覆盖点

## 2026-05-15: 豆包图片请求 HTTP 404

### 发生了什么
- 用户指出图片请求在豆包侧出现 `HTTP 404 Not Found`
- 用户确认当前用的是 `Doubao-Seed-2.0-lite`
- 进一步提供火山控制台模型详情链接：`Id=doubao-seed-2-0-lite`

### 有效判断
- 控制台链接中的 `doubao-seed-2-0-lite` 是模型族/详情页 ID；公开文档列出图像理解模型 `Doubao-seed-2-0-lite`，版本为 `260215`
- 普通在线推理 `/api/v3/chat/completions` 与 Coding Plan `/api/coding/v3` 是两套入口，模型名格式不同
- `/api/v3` 应使用版本化 ID：`doubao-seed-2-0-lite-260215`
- `/api/coding/v3` 才使用 Coding Plan 名称：`doubao-seed-2.0-lite`

### 根因
- 把 Coding Plan 风格的 `doubao-seed-2.0-lite` 用在普通在线推理 `/api/v3` 时，火山侧找不到对应模型/接入点，因此返回 404
- 之前错误处理只显示 `豆包 API 错误 (HTTP 404): Not Found`，缺少火山响应 body、实际 base_url 和 model，导致定位成本偏高

### 修复动作
- `config.py` 默认视觉模型改为 `doubao-seed-2-0-lite-260215`
- 新增模型名归一化：根据 `ARK_BASE_URL` 自动在在线推理版本化 ID 与 Coding Plan 名称之间映射 Doubao 2.0 lite
- `vision.py` 捕获 `HTTPError` 时读取错误响应 body，并输出 `base_url` 与实际 `model`

### 验证结果
- 1x1 PNG 探针返回 `Image dimensions are too small` 的 HTTP 400，说明请求已命中正确模型，不再是模型 404
- 20x20 四色 PNG 真实识图成功：返回红/绿/蓝/白四个色块、归一化坐标和空间关系
- `python3 test_transformer.py` 通过：15 passed

### 后续建议
- 如继续使用普通在线推理，保持 `ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3` 和 `VISION_MODEL=doubao-seed-2-0-lite-260215`
- 如切到 Coding Plan，再把 `ARK_BASE_URL` 改为 `https://ark.cn-beijing.volces.com/api/coding/v3`，模型名使用 `doubao-seed-2.0-lite`
- 真实端到端验收仍以 `/tmp/deepseek-eyes.log` 为准：先看到 `event=received`，再看到 `has_image=true image_count=1`，最后检查 DeepSeek upstream 状态

## 2026-05-15: SSE 回答结束后继续 loading

### 发生了什么
- 用户反馈 Claude Code 对话已经回答完，但界面底部仍显示 loading
- 进一步确认：不是立刻结束，而是跑完一段时间后才突然结束；且每次回答完都会这样

### 有效判断
- 这是代理层 SSE 收尾问题，不是 Claude Code UI 偶发卡住
- 现象符合“模型逻辑流已结束，但代理还在等上游 HTTP 连接自然关闭”
- 日志中 `upstream_status=200 sse=true` 且 `duration_ms` 明显偏长，是判断这个问题的关键证据

### 根因
- `proxy.py` 旧实现用 `resp.read(8192)` 读 SSE
- SSE 不应该用大块 read 等 EOF；如果上游 keep-alive 或延迟关闭连接，客户端就会一直以为请求还没结束

### 修复动作
- 新增 `_forward_sse_stream()`，按行转发 SSE 并在事件边界 flush
- 新增 `_sse_event_is_terminal()`，识别 `event: message_stop` 和 `data: [DONE]`
- 收到终止事件后主动停止读取上游，避免等待连接自然超时
- 将 `_read_body()` 的返回类型从 `bytes | None` 改为 `Optional[bytes]`，避免旧 Python 后台启动时报类型注解错误
- 新增 `test_proxy_stream.py` 覆盖 Anthropic `message_stop`、OpenAI `[DONE]` 和非终止事件

### 验收结果
- `python3 test_proxy_stream.py`：3 passed
- `python3 test_transformer.py`：15 passed
- `python3 -m py_compile proxy.py test_proxy_stream.py`：通过
- 修复后代理已恢复运行，`curl http://127.0.0.1:8788/health` 返回 `{"status": "ok"}`

### 后续建议
- 之后如果再次出现回答结束后 loading，先看 `/tmp/deepseek-eyes.log` 中 SSE 请求的 `duration_ms`
- 如果 duration 仍明显偏长，需要抓一段原始 SSE 事件，确认上游是否没有发送 `message_stop` 或 `[DONE]`
- 图片请求里另有豆包 `HTTP 404 Not Found` 记录，这是独立问题，后续应单独排查 `ARK_BASE_URL` 和 `VISION_MODEL`

## 2026-05-15: v1.2-lite 图片增强代理收窄

### 发生了什么
- 用户明确选择保留 8788 代理，但将其定位为“图片增强代理”，不是“全量魔改 Claude Code 后端”
- 用户要求：有图片才介入；无图片、Bash、Read、Write、MCP、tool_use/tool_result 都尽量透明转发；streaming 结束事件完整返回

### 修复动作
- `transformer.py` 从递归替换改为只处理 `messages[].content` 直接子级的 `type=image`
- 不再进入 `tool_result.content`，也不再由代理拦截 document / MCP / search_result 等非 image block
- `proxy.py` 新增 `_prepare_upstream_body()`：`image_count=0` 时返回原始 request body bytes，避免无图请求被 JSON 重新序列化
- `test_proxy_stream.py` 增加无图 byte-for-byte 透传、tool_use/tool_result 保持不变、直接 image 才重写的测试
- `test_smoke.sh` 使用 `curl --noproxy '*'`，避免本机 `http_proxy` 把 127.0.0.1 请求劫持成 502
- `config.PROJECT_VERSION` 更新为 `v1.2-lite`，启动日志显示版本

### 验收结果
- `python3 test_transformer.py`：15 passed
- `python3 test_proxy_stream.py`：6 passed
- `python3 -m py_compile proxy.py transformer.py test_proxy_stream.py test_transformer.py`：通过
- `bash test_smoke.sh`：14 passed
