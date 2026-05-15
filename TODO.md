# DeepSeek Eyes — TODO

## v1.2-lite (当前)

### 核心功能
- [ ] proxy.py: HTTP 代理收发 Anthropic 请求
- [ ] transformer.py: 只替换 messages[].content 直接 image block，不介入工具块
- [ ] vision.py: 豆包 Chat API 调用，JSON 输���（描述+坐标）
- [ ] vision_fake.py: 假视觉 mock
- [ ] cache.py: 线程安全缓存，in-flight lock
- [ ] 流式 SSE 转发 + 非流式转发到 DeepSeek
- [ ] DEBUG 模式返回转换后请求体
- [ ] /health 端点

### 运维
- [ ] .env.example + config.py
- [ ] start.sh / stop.sh
- [ ] 日志不记录敏感内容
- [ ] CLAUDE.md / MEMORY.md

### 验收
- [ ] test_transformer.py: 15/15
- [ ] test_smoke.sh: 14/14 (DEBUG + FAKE_VISION)
- [ ] 无图本地 transform overhead < 5ms
- [ ] 不误伤 tool_use.input / tool_result.content
- [ ] source.url → 400
- [ ] 非 image block 透明转发，交给 DeepSeek 上游处理
- [ ] Auth 透传 x-api-key / Authorization
- [ ] 真实豆包 DEBUG 模式通过
- [ ] 真实 DeepSeek 转发通过（无图 + 有图）

### v1.1 候选
- [ ] Prompt 策略自动分类（代码/UI/文档）
- [ ] 多图并发处理
- [ ] Grounding → 真实屏幕坐标映射
- [ ] 费用统计
- [ ] launchd 开机自启

### 已知局限
- 豆包 seed-1-6 vision 不支持视频帧
- 坐标精度对极小元素（<20px）可能不准
- 图片超过 10MB 直接拒绝，不缩放
- 缓存默认不写磁盘（含隐私数据）

### 版本记录
- v1.2-lite (2026-05-15): 图片增强代理边界收窄；无图请求原始 body 透传；tool_use/tool_result 透明转发；测试绕过本机 proxy 环境变量
- v1.1-lite (2026-05-15): 固定 Claude Code 本地代理入口，修复 SSE 结束后 loading
- v1.0 (2026-05-15): 初始实现，描述+坐标双输出
