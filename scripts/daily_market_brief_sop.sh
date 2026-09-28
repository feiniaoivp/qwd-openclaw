#!/usr/bin/env bash
# daily_market_brief_sop.sh — 市场简报标准作业流程（SOP）一键执行
# 用法: ./daily_market_brief_sop.sh [--date YYYY-MM-DD] [--skip-scan]
# 
# 流程：
#   1. 确认交易日
#   2. 跑真实库存 (close_scan_v2.py) → 生成 analysis/daily/{date}_dual.md / _adaptive.md / _next_day_sim.md
#   3. 验证生成的日报无裸数字引用
#   4. 可选：对指定文本做发送前自检
#
# 退出码：0=全部通过，1=有违规/失败

set -euo pipefail

WORKSPACE="/Users/duguke/.openclaw/workspace"
cd "$WORKSPACE"

DATE="${1:-$(date +%F)}"
SKIP_SCAN=false

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --date)
            DATE="$2"
            shift 2
            ;;
        --skip-scan)
            SKIP_SCAN=true
            shift
            ;;
        --check-text)
            CHECK_TEXT="$2"
            shift 2
            ;;
        --check-file)
            CHECK_FILE="$2"
            shift 2
            ;;
        -h|--help)
            echo "用法: $0 [--date YYYY-MM-DD] [--skip-scan] [--check-text \"文本\"] [--check-file 文件路径]"
            echo "  --date YYYY-MM-DD    指定日期（默认今天）"
            echo "  --skip-scan          跳过 close_scan_v2.py 扫描（仅验证已有报告）"
            echo "  --check-text \"文本\"  对指定文本做 pre_response_guard 检查"
            echo "  --check-file 文件    对指定文件做 verify_market_data 检查"
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            exit 2
            ;;
    esac
done

echo "============================================================"
echo "📋 市场简报 SOP — 日期: $DATE"
echo "============================================================"

# ------------------------------------------------------------
# 步骤 1: 确认交易日
# ------------------------------------------------------------
echo ""
echo "🔍 步骤 1/5: 确认交易日..."
if python3 -c "from analysis.close_scan_v2 import is_trading_day; print('✅ 交易日' if is_trading_day() else '❌ 非交易日')"; then
    echo "   交易日确认通过"
else
    echo "   ⚠️ 非交易日，建议仅做复盘/计划，不做实盘简报"
fi

# ------------------------------------------------------------
# 步骤 2: 跑真实库存
# ------------------------------------------------------------
if [[ "$SKIP_SCAN" == "false" ]]; then
    echo ""
    echo "📊 步骤 2/5: 跑真实库存扫描 (close_scan_v2.py)..."
    if python3 analysis/close_scan_v2.py 2>&1 | tee "/tmp/scan_${DATE}.log"; then
        echo "   ✅ 扫描完成"
    else
        echo "   ❌ 扫描失败，查看日志: /tmp/scan_${DATE}.log"
        exit 1
    fi
else
    echo ""
    echo "⏭️  步骤 2/5: 跳过扫描 (--skip-scan)"
fi

# ------------------------------------------------------------
# 步骤 3: 验证生成的日报
# ------------------------------------------------------------
echo ""
echo "🛡️ 步骤 3/5: 验证生成的日报无裸数字引用..."

DUAL_MD="analysis/daily/${DATE}_dual.md"
ADAPTIVE_MD="analysis/daily/${DATE}_adaptive.md"
NEXT_DAY_MD="analysis/daily/${DATE}_next_day_sim.md"

ALL_OK=true

for f in "$DUAL_MD" "$ADAPTIVE_MD" "$NEXT_DAY_MD"; do
    if [[ -f "$f" ]]; then
        echo "   检查 $f ..."
        if python3 scripts/verify_market_data.py --report "$f" >/dev/null 2>&1; then
            echo "   ✅ $f 通过"
        else
            echo "   ❌ $f 存在未验证引用："
            python3 scripts/verify_market_data.py --report "$f" | sed 's/^/      /'
            ALL_OK=false
        fi
    else
        echo "   ⚠️ $f 不存在（可能未生成）"
    fi
done

# ------------------------------------------------------------
# 步骤 4: 可选 - 对指定文本/文件做发送前自检
# ------------------------------------------------------------
if [[ -n "${CHECK_TEXT:-}" ]]; then
    echo ""
    echo "🔍 步骤 4/5: pre_response_guard 检查指定文本..."
    if python3 scripts/pre_response_guard.py --text "$CHECK_TEXT"; then
        echo "   ✅ 文本通过"
    else
        ALL_OK=false
    fi
fi

if [[ -n "${CHECK_FILE:-}" ]]; then
    echo ""
    echo "🔍 步骤 4/5: verify_market_data 检查指定文件..."
    if python3 scripts/verify_market_data.py --report "$CHECK_FILE"; then
        echo "   ✅ 文件通过"
    else
        ALL_OK=false
    fi
fi

# ------------------------------------------------------------
# 步骤 5: 汇总
# ------------------------------------------------------------
echo ""
echo "============================================================"
if [[ "$ALL_OK" == "true" ]]; then
    echo "🎉 SOP 全部通过！可基于真实库存撰写简报。"
    echo ""
    echo "📄 可用数据源文件："
    [[ -f "$DUAL_MD" ]] && echo "   - $DUAL_MD"
    [[ -f "$ADAPTIVE_MD" ]] && echo "   - $ADAPTIVE_MD"
    [[ -f "$NEXT_DAY_MD" ]] && echo "   - $NEXT_DAY_MD"
    echo ""
    echo "✍️ 写作时请遵循引用格式："
    echo "   指标 数值 [来源: <授权源key>, <文件路径>:<行号>]"
    echo "   例如：中信证券 26.29 (-1.87%) [来源: close_scan_v2, $DUAL_MD:45]"
    exit 0
else
    echo "❌ SOP 存在违规/失败，请修正后重试。"
    exit 1
fi