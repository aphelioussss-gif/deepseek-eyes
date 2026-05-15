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

## API 参考

### 豆包 Chat API (Vision)
```
POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
Authorization: Bearer {ARK_API_KEY}
Content-Type: application/json

{
  "model": "doubao-seed-1-6-vision-250815",
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
