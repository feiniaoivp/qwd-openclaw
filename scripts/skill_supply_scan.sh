#!/usr/bin/env bash
# =============================================================================
# skill_supply_scan.sh - 技能供应链安全扫描
# 用途: 安装新技能前 / 每周审计时, 扫描 skills/ 目录中的高危模式
# 用法: bash scripts/skill_supply_scan.sh [skills路径]
#       默认扫描 workspace/skills/, 可传参指定其他路径
# 退出码: 0=全部通过, 1=发现高危(含警告), 2=扫描自身异常
# =============================================================================

SKILLS_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)/skills}"

if [ ! -d "$SKILLS_DIR" ]; then
  echo "❌ 技能目录不存在: $SKILLS_DIR"
  exit 2
fi

echo "🔍 技能供应链扫描开始: $SKILLS_DIR"
echo "=============================================================="

# 模式定义
declare -a HIGH_RISK_DESC
declare -a HIGH_RISK_PATTERNS
declare -a WARN_DESC
declare -a WARN_PATTERNS

# 高危: 破坏性 / 数据外泄 / 权限提升
HIGH_RISK_DESC=(
  "递归删除" "强制删除" "sudo提权" "网络下载+执行" "反向shell"
  "bash反弹shell" "编码后门(base64)" "Python动态执行(eval/exec)"
)
HIGH_RISK_PATTERNS=(
  'rm[[:space:]]+-rf' 'rm[[:space:]]+-[a-z]*f' 'sudo[[:space:]]+(rm|dd|mkfs|chown)'
  "curl[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(sh|bash|python)" 
  '/dev/tcp/' 'bash[[:space:]]+-i[[:space:]]*>' 'eval\([^)]*base64' 'os\.system\(' 'subprocess.*shell=True'
)

# 警告: 网络请求 / secret引用 / 外部发送
WARN_DESC=(
  "网络请求(curl/wget/requests)" "明文secret/API Key字样" "邮件/telegram/推送"
  "环境变量secret引用" "HTTP明文传输" "pip/npm自动安装"
)
WARN_PATTERNS=(
  'curl[[:space:]]+(-s|-L)?[[:space:]]*https?://' 'wget[[:space:]]' 'requests\.(get|post)\('
  'api[_-]?key[[:space:]]*=' 'apikey' 'sk-[A-Za-z0-9]{10,}' "AKIA[0-9A-Z]{16}"
  'smtplib' 'telegram' 'webhook' 'send_mail'
  'os\.environ\[[^]]*(KEY|TOKEN|PASS|SECRET)' 'http://' 'pip[[:space:]]+install' 'npm[[:space:]]+install'
)

RISK_COUNT=0
WARN_COUNT=0

scan_file() {
  local file="$1"
  local rel="${file#$SKILLS_DIR/}"
  local i

  # 只扫描文本文件
  if file "$file" 2>/dev/null | grep -qiE "text|script|json|yaml|markdown|source"; then
    for i in "${!HIGH_RISK_PATTERNS[@]}"; do
      if grep -rnoE "${HIGH_RISK_PATTERNS[$i]}" "$file" 2>/dev/null | head -1 | grep -q .; then
        echo "🔴 高危 [$rel] 匹配: ${HIGH_RISK_DESC[$i]}"
        grep -noE "${HIGH_RISK_PATTERNS[$i]}" "$file" 2>/dev/null | head -2 | while read -r line; do
          echo "      → $line"
        done
        RISK_COUNT=$((RISK_COUNT+1))
      fi
    done
    for i in "${!WARN_PATTERNS[@]}"; do
      if grep -rnoE "${WARN_PATTERNS[$i]}" "$file" 2>/dev/null | head -1 | grep -q .; then
        echo "🟡 警告 [$rel] 匹配: ${WARN_DESC[$i]}"
        WARN_COUNT=$((WARN_COUNT+1))
      fi
    done
  fi
}

# 扫描所有文件
while IFS= read -r -d '' f; do
  scan_file "$f"
done < <(find "$SKILLS_DIR" -type f -not -path "*/.clawhub/*" -not -name ".DS_Store" -print0 2>/dev/null)

# 总结
echo "=============================================================="
if [ "$RISK_COUNT" -eq 0 ] && [ "$WARN_COUNT" -eq 0 ]; then
  echo "✅ 扫描通过: 无高危, 无警告"
  exit 0
elif [ "$RISK_COUNT" -eq 0 ]; then
  echo "⚠️ 发现 ${WARN_COUNT} 处警告 (需人工确认, 非必禁)"
  exit 1
else
  echo "🔴 发现 ${RISK_COUNT} 处高危! 请勿安装/使用该技能"
  echo "   (警告 ${WARN_COUNT} 处)"
  exit 1
fi
