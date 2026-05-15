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
