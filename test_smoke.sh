#!/bin/bash
# DeepSeek Eyes — 冒烟测试
# 前提: DEEPSEEK_EYES_DEBUG=1 DEEPSEEK_EYES_FAKE_VISION=1 python3 proxy.py &
# 运行: bash test_smoke.sh

set -e

BASE="http://127.0.0.1:8788"
PASS=0
FAIL=0
TMPBODY="/tmp/deepseek-eyes-body.txt"
TEST_IMG_B64=$(echo -n "test-image-data" | base64)

cleanup() { :; }
trap cleanup EXIT

# 用 python 构造紧凑 JSON
json() {
    python3 -c "import json,sys; print(json.dumps($1, ensure_ascii=False))"
}

post() {
    curl --noproxy '*' -s -o "$TMPBODY" -w "%{http_code}" -X POST "$BASE$1" \
        -H "Content-Type: application/json" \
        -d "$2" 2>/dev/null
}

post_auth() {
    local path="$1" data="$2" key="$3" bearer="$4"
    local args=()
    [ -n "$key" ] && args+=(-H "x-api-key: $key")
    [ -n "$bearer" ] && args+=(-H "Authorization: Bearer $bearer")
    curl --noproxy '*' -s -o "$TMPBODY" -w "%{http_code}" -X POST "$BASE$path" \
        -H "Content-Type: application/json" \
        "${args[@]}" \
        -d "$data" 2>/dev/null
}

get() {
    curl --noproxy '*' -s -o "$TMPBODY" -w "%{http_code}" "$BASE$1" 2>/dev/null
}

bcat() { cat "$TMPBODY"; }

check() {
    local name="$1" code="$2" expected="$3" type="$4" value="$5"
    if [ "$code" != "$expected" ]; then
        echo "  ✗ $name: http $code (expected $expected)"
        FAIL=$((FAIL + 1)); return
    fi
    local body; body=$(bcat)
    case "$type" in
        contains)
            if printf '%s' "$body" | grep -qF "$value"; then
                echo "  ✓ $name"; PASS=$((PASS + 1))
            else
                echo "  ✗ $name: missing '$value'"
                echo "     $(printf '%s' "$body" | head -c 200)"
                FAIL=$((FAIL + 1))
            fi ;;
        json_int)
            # $value = "field_name=expected_value"  e.g. "image_count=1"
            local field="${value%%=*}" expected="${value##*=}"
            local val
            val=$(printf '%s' "$body" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('$field',-999))
" 2>/dev/null)
            if [ "$val" = "$expected" ]; then
                echo "  ✓ $name: $field=$val"; PASS=$((PASS + 1))
            else
                echo "  ✗ $name: expected $field=$expected, got $val"; FAIL=$((FAIL + 1))
            fi ;;
        py_pass)
            if printf '%s' "$body" | python3 -c "$value" 2>/dev/null; then
                echo "  ✓ $name"; PASS=$((PASS + 1))
            else
                echo "  ✗ $name: python check failed"; FAIL=$((FAIL + 1))
            fi ;;
    esac
}

echo "DeepSeek Eyes 冒烟测试 ($(date +%H:%M:%S))"
echo "================================"

# ── 健康检查 ──
echo "── 健康检查 ──"
check "GET /health=200" "$(get /health)" "200" "contains" "ok"

# ── 路由白名单 ──
echo "── 路由白名单 ──"
check "GET /404" "$(get /nonexistent)" "404" "contains" "proxy_error"
check "POST /404" "$(post /nonexistent '{}')" "404" "contains" "proxy_error"

# ── Auth 透传 ──
echo "── Auth header ──"
NOIMG=$(json '{"model":"x","messages":[{"role":"user","content":"hi"}],"max_tokens":10}')
check "x-api-key" "$(post_auth /v1/messages "$NOIMG" "sk-test-001" "")" "200" "contains" "sk-test-001"
check "Bearer" "$(post_auth /v1/messages "$NOIMG" "" "sk-bearer-002")" "200" "contains" "sk-bearer-002"

# ── 图片替换 ──
echo "── 图片替换 ──"
IMG=$(json '{"model":"x","messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"image","source":{"type":"base64","media_type":"image/png","data":"'"$TEST_IMG_B64"'"}}]}],"max_tokens":10}')
check "image→text" "$(post /v1/messages "$IMG")" "200" "contains" "[视觉分析]"
check "FAKE标记" "$(post /v1/messages "$IMG")" "200" "contains" "[FAKE]"
check "image_count=1" "$(post /v1/messages "$IMG")" "200" "json_int" "image_count=1"

# ── tool_use.input 保护 ──
echo "── tool_use.input ──"
TOOL=$(json '{"model":"x","messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"tool_use","id":"t1","name":"s","input":{"type":"image","query":"f"}},{"type":"image","source":{"type":"base64","media_type":"image/png","data":"'"$TEST_IMG_B64"'"}}]}],"max_tokens":10}')
check "input未误伤" "$(post /v1/messages "$TOOL")" "200" "py_pass" "
import sys,json
d=json.load(sys.stdin)
for m in d['transformed_request']['messages']:
    for b in m['content']:
        if b.get('type')=='tool_use' and b['input'].get('type')=='image':
            sys.exit(0)
sys.exit(1)
"

# ── tool_result 透明转发 ──
echo "── tool_result ──"
NESTED=$(json '{"model":"x","messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"tool_result","content":[{"type":"text","text":"r"},{"type":"image","source":{"type":"base64","media_type":"image/png","data":"'"$TEST_IMG_B64"'"}}]}]}],"max_tokens":10}')
check "嵌套不替换" "$(post /v1/messages "$NESTED")" "200" "py_pass" "
import sys,json
d=json.load(sys.stdin)
b=d['transformed_request']['messages'][0]['content'][1]['content'][1]
assert b.get('type') == 'image'
assert d['image_count'] == 0
sys.exit(0)
"

# ── source.url → 400 ──
echo "── source.url ──"
URL=$(json '{"model":"x","messages":[{"role":"user","content":[{"type":"image","source":{"type":"url","url":"https://x.com/i.png"}}]}],"max_tokens":10}')
check "url→400" "$(post /v1/messages "$URL")" "400" "contains" "远程图片"

# ── 非 image block 透明转发 ──
echo "── non-image passthrough ──"
check "doc透明转发" "$(post /v1/messages '{"model":"x","messages":[{"role":"user","content":[{"type":"text","text":"hi"},{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":"aaaa"}}]}],"max_tokens":10}')" "200" "py_pass" "
import sys,json
d=json.load(sys.stdin)
blocks=d['transformed_request']['messages'][0]['content']
assert blocks[1].get('type') == 'document'
assert d['image_count'] == 0
sys.exit(0)
"

# ── 无图 ──
echo "── 无图请求 ──"
check "无图 count=0" "$(post /v1/messages "$NOIMG")" "200" "json_int" "image_count=0"

# ── 坐标范围 ──
echo "── 坐标验证 ──"
check "坐标0-1000" "$(post /v1/messages "$IMG")" "200" "py_pass" "
import sys,json,re
d=json.load(sys.stdin)
for m in d['transformed_request']['messages']:
    for b in m['content']:
        if isinstance(b,dict) and '[视觉分析]' in b.get('text',''):
            for x1,y1,x2,y2 in re.findall(r'bbox\[(\d+),(\d+),(\d+),(\d+)\]', b['text']):
                for v in (int(x1),int(y1),int(x2),int(y2)):
                    assert 0<=v<=1000, f'coord {v} out of range'
sys.exit(0)
"

# ── 结果 ──
echo "================================"
echo "结果: $PASS passed, $FAIL failed, $((PASS + FAIL)) total"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
