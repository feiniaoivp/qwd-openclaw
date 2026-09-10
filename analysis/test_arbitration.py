#!/usr/bin/env python3
"""
仲裁逻辑自动化测试
验证 validate_strategies.py 中的 confidence_guard 和仲裁决策树
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# 直接复制 validate_strategies.py 中的核心仲裁函数（避免导入完整脚本的副作用）
def confidence_guard(score, sharpe, trades):
    """统一的置信度护栏"""
    if score < 25:
        return False, f"得分{score}<25"
    thr = 0.4 if trades < 6 else 0.25
    if sharpe < thr:
        return False, f"夏普{sharpe}<{thr}(低笔数{int(trades)}笔需更严)"
    return True, ""

def arbitrate(val_s, tune_s, old_s, val_score, val_sharpe, val_trades, tune_score, tune_sharpe, tune_trades):
    """完整仲裁决策树"""
    val_ok, val_reason = confidence_guard(val_score, val_sharpe, val_trades)
    tune_ok, tune_reason = confidence_guard(tune_score, tune_sharpe, tune_trades)
    
    final_s = old_s
    reason = ""
    
    if val_s == tune_s:
        if val_ok or tune_ok:
            final_s = val_s
            reason = f"双方一致({val_s}) 验证={'✅' if val_ok else '❌'} 调优={'✅' if tune_ok else '❌'}"
        else:
            reason = f"双方一致({val_s})但均未过护栏，保留原策略"
    elif val_s != tune_s:
        if tune_ok and tune_score > val_score + 10 and tune_sharpe > val_sharpe + 0.1 and tune_trades >= 6:
            final_s = tune_s
            reason = f"调优显著更优: {tune_s}(分{tune_score} 夏普{tune_sharpe}) > {val_s}(分{val_score} 夏普{val_sharpe})"
        elif val_ok:
            final_s = val_s
            reason = f"采信验证(跨窗口稳健): {val_s}(分{val_score} 夏普{val_sharpe}) vs 调优:{tune_s}(分{tune_score} 夏普{tune_sharpe})"
        else:
            reason = f"验证未过护栏({val_reason})，调优{'过' if tune_ok else '未过'}护栏，保留原策略"
    else:
        if val_ok:
            final_s = val_s
            reason = f"仅验证有候选且过护栏: {val_s}"
        else:
            reason = f"验证候选未过护栏({val_reason})，保留原策略"
    
    return final_s, reason, val_ok, tune_ok


# ════════════════════════════════════════
# 测试用例
# ════════════════════════════════════════

TESTS = [
    # (测试名, 输入参数, 期望 final_s, 期望包含的理由关键词)
    ("test_both_agree_pass",
     {"val_s": "ema_cross", "tune_s": "ema_cross", "old_s": "macd",
      "val_score": 40, "val_sharpe": 0.3, "val_trades": 10,
      "tune_score": 38, "tune_sharpe": 0.28, "tune_trades": 8},
     "ema_cross", "双方一致"),
    
    ("test_both_agree_fail",
     {"val_s": "macd", "tune_s": "macd", "old_s": "ema_cross",
      "val_score": 20, "val_sharpe": 0.2, "val_trades": 5,
      "tune_score": 22, "tune_sharpe": 0.22, "tune_trades": 4},
     "ema_cross", "保留原策略"),
    
    ("test_val_ok_tune_fail",
     {"val_s": "bollinger", "tune_s": "ema_cross", "old_s": "macd",
      "val_score": 30, "val_sharpe": 0.3, "val_trades": 10,
      "tune_score": 28, "tune_sharpe": 0.2, "tune_trades": 5},
     "bollinger", "采信验证"),
    
    ("test_tune_significantly_better",
     {"val_s": "ema_cross", "tune_s": "macd", "old_s": "bollinger",
      "val_score": 40, "val_sharpe": 0.3, "val_trades": 10,
      "tune_score": 55, "tune_sharpe": 0.45, "tune_trades": 8},
     "macd", "调优显著更优"),
    
    ("test_tune_marginally_better",
     {"val_s": "ema_cross", "tune_s": "macd", "old_s": "bollinger",
      "val_score": 44, "val_sharpe": 0.45, "val_trades": 10,
      "tune_score": 48, "tune_sharpe": 0.42, "tune_trades": 10},
     "ema_cross", "采信验证"),
    
    ("test_low_sample_penalty",
     {"val_s": "kdj_cci", "tune_s": "ema_cross", "old_s": "macd",
      "val_score": 30, "val_sharpe": 0.35, "val_trades": 4,
      "tune_score": 35, "tune_sharpe": 0.38, "tune_trades": 5},
     "macd", "保留原策略"),
    
    ("test_missing_tune",
     {"val_s": "bull_trend", "tune_s": None, "old_s": "ema_cross",
      "val_score": 30, "val_sharpe": 0.3, "val_trades": 8,
      "tune_score": -999, "tune_sharpe": -999, "tune_trades": 0},
     "bull_trend", "采信验证"),
    
    ("test_tune_ok_but_insufficient_margin",
     {"val_s": "ema_cross", "tune_s": "macd", "old_s": "bollinger",
      "val_score": 42, "val_sharpe": 0.35, "val_trades": 8,
      "tune_score": 48, "tune_sharpe": 0.4, "tune_trades": 7},
     "ema_cross", "采信验证"),  # 调优仅领先6分<10，夏普领先0.05<0.1
    
    ("test_tune_ok_low_trades",
     {"val_s": "ema_cross", "tune_s": "macd", "old_s": "bollinger",
      "val_score": 40, "val_sharpe": 0.3, "val_trades": 10,
      "tune_score": 55, "tune_sharpe": 0.5, "tune_trades": 4},
     "ema_cross", "采信验证"),  # 调优 trades=4 < 6，不满足反胜条件
    
    ("test_val_fail_tune_pass",
     {"val_s": "ema_cross", "tune_s": "macd", "old_s": "bollinger",
      "val_score": 20, "val_sharpe": 0.2, "val_trades": 5,
      "tune_score": 30, "tune_sharpe": 0.3, "tune_trades": 8},
     "bollinger", "保留原策略"),  # 验证未过，调优虽过但非显著更优(分差10不>10,夏普差0.1不>0.1) -> 保留原策略
]


def build_telegram_summary(passed, failed, total, results):
    """生成适合 Telegram 的简洁摘要"""
    lines = []
    lines.append(f"🧪 <b>仲裁逻辑测试报告</b>")
    lines.append(f"📊 {passed}/{total} 通过 | ⏱️ {sum(r['elapsed'] for r in results):.2f}s")
    lines.append("")
    
    for r in results:
        icon = "✅" if r['ok'] else "❌"
        lines.append(f"{icon} <b>{r['name']}</b>")
        lines.append(f"   {r['reason_short']}")
        if not r['ok']:
            lines.append(f"   ⚠️ 期望: {r['expected']} | 实际: {r['actual']}")
        lines.append("")
    
    if failed == 0:
        lines.append("🎉 <b>全部通过</b>")
    else:
        lines.append(f"⚠️ <b>{failed} 个失败</b>")
    
    return "\n".join(lines)


def run_tests():
    passed = 0
    failed = 0
    results = []
    
    print("=" * 70)
    print("仲裁逻辑自动化测试")
    print("=" * 70)
    
    import time
    for name, params, expected_final, expected_keyword in TESTS:
        t0 = time.time()
        final_s, reason, val_ok, tune_ok = arbitrate(**params)
        elapsed = time.time() - t0
        
        ok = (final_s == expected_final and expected_keyword in reason)
        status = "✅ PASS" if ok else "❌ FAIL"
        
        # 控制台详细输出
        print(f"\n{status} {name}")
        print(f"  输入: val={params['val_s']}(分{params['val_score']} 夏普{params['val_sharpe']} 笔{params['val_trades']}) "
              f"tune={params['tune_s']}(分{params['tune_score']} 夏普{params['tune_sharpe']} 笔{params['tune_trades']}) "
              f"old={params['old_s']}")
        print(f"  期望: final={expected_final}, 理由包含='{expected_keyword}'")
        print(f"  实际: final={final_s}, 理由='{reason}'")
        print(f"  护栏: val_ok={val_ok}, tune_ok={tune_ok}")
        
        # Telegram 摘要用短理由
        reason_short = reason[:80] + ("..." if len(reason) > 80 else "")
        
        results.append({
            'name': name,
            'ok': ok,
            'reason_short': reason_short,
            'expected': f"final={expected_final}, 关键词={expected_keyword}",
            'actual': f"final={final_s}, 理由={reason_short}",
            'elapsed': elapsed
        })
        
        if ok:
            passed += 1
        else:
            failed += 1
    
    print("\n" + "=" * 70)
    print(f"测试汇总: {passed} 通过, {failed} 失败, 共 {len(TESTS)} 个")
    print("=" * 70)
    
    # 输出 Telegram 摘要到 stdout (供 cron 捕获推送)
    print("\n=====TELEGRAM_SUMMARY=====")
    print(build_telegram_summary(passed, failed, len(TESTS), results))
    print("=====TELEGRAM_END=====")
    
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
