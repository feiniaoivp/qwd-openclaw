#!/usr/bin/env python3
"""promotion_guard.py — MEMORY.md promote 区块「预算淘汰」静默丢失门禁.

背景（2026-10-05 源码坐实）
    memory-core 插件的 applyShortTermPromotions() 会把短期记忆晋升写入 MEMORY.md，
    但写入前调用 compactMemoryForBudget()，预算为
    `DEFAULT_MEMORY_FILE_MAX_CHARS = 1e4`（10,000 字符）。
    当 MEMORY.md 人写内容已远超该预算时，会**按日期升序静默丢弃最老的 promote 区块**
    来腾出空间（人写内容无条件保留）。

    关键缺陷：droppedDates 只存在于函数内存返回值，
    `memory/.dreams/events.jsonl` 里的 `memory.promotion.applied` 事件
    **只记录 applied / candidates，完全没有 dropped 字段**
    → 66 次 journal 全报"成功"，实际晋升被日抛，长期沉淀通道断裂。

    实测：本仓 MEMORY.md 约 203KB（人写区 ~117KB），promote 区仅剩当日 6 条；
    前一日的 promote 区块次日即归零。

    另：`options.memoryFileMaxChars` 虽在函数签名中存在，但已核查
    全部 2 处调用点（dist/dreaming-*.js、dist/cli.runtime-*.js）**均未传该参数**，
    且插件配置 schema 无此键 → 无法通过调配置止血，只能改变使用口径 + 加门禁。

口径（本仓正式采纳）
    **promote 区 = 易失缓冲（volatile buffer），不是长期存储。**
    需要长期保留的教训必须由人工/会话写入 MEMORY.md 的
    "Lessons Learned" 等人写区（该区无条件保留、不受预算淘汰影响）。

门禁逻辑
    1. 读 events.jsonl 中最近 N 次（默认 7）`memory.promotion.applied` 事件的 candidate keys。
    2. 读 MEMORY.md 现存 `openclaw-memory-promotion:<key>` marker 集合。
    3. 最近一次 promote 的 keys 若**全部**缺失 → promote 通道已失效 → exit 1。
    4. 历史 promote 的 keys 缺失比例 → 仅告警（设计上就会被淘汰，不能当新增违规）。
    5. 棘轮：记录"最近一次 promote 存活率"到 data/，连续恶化则升级为 exit 1。

退出码
    0 = 通过（最近一次 promote 仍落盘，或本窗口无 promote 事件）
    1 = 违规（最近一次 promote 全被淘汰 / 存活率连续恶化）
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
import shutil

def _bak(path):
    """写前 .bak 备份（backup_guard 铁律 2026-10-31 接入；失败仅告警不阻断）"""
    try:
        if os.path.exists(path):
            shutil.copy2(str(path), str(path) + ".bak")
    except Exception as _e:
        print(f"⚠️ 写前备份失败(不阻断): {path}: {_e}", file=sys.stderr)


WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

EVENTS_PATH = os.path.join(WORKSPACE, "memory", ".dreams", "events.jsonl")
MEMORY_PATH = os.path.join(WORKSPACE, "MEMORY.md")
STATE_PATH = os.path.join(WORKSPACE, "data", "promotion_guard_state.json")
MARKER_PREFIX = "openclaw-memory-promotion:"


def load_promotion_events(events_path: str):
    """返回 [(timestamp_iso, [keys...]), ...] 按时间升序。"""
    events = []
    if not os.path.exists(events_path):
        return events
    with open(events_path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "memory.promotion.applied":
                continue
            keys = [c.get("key") for c in rec.get("candidates", []) if c.get("key")]
            if keys:
                events.append((rec.get("timestamp", ""), keys))
    events.sort(key=lambda x: x[0])
    return events


def load_memory_markers(memory_path: str):
    if not os.path.exists(memory_path):
        return set()
    with open(memory_path, encoding="utf-8", errors="ignore") as fh:
        content = fh.read()
    # marker 形式: <!-- openclaw-memory-promotion:<key> --> ，key 含日期中的 '-'，不能按 '-' 截断
    return set(re.findall(r"openclaw-memory-promotion:(\S+?)(?:\s+-->)", content))


def main() -> int:
    ap = argparse.ArgumentParser(description="MEMORY.md promote 淘汰门禁")
    ap.add_argument("--lookback", type=int, default=7, help="回溯最近 N 次 promote 事件")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    events = load_promotion_events(EVENTS_PATH)
    markers = load_memory_markers(MEMORY_PATH)

    # 2026-10-20 用戶拍板关闭 deep 自动 promote（openclaw.json
    # plugins.entries.memory-core.config.dreaming.phases.deep.enabled=false）
    # → 不再产生新 promote 事件，棘轮的「连续零存活=通道断裂」语义不再适用，
    # 检测到 deep-off 时跳过判定（避免自我误报），人工蒸馏由每日会话承担。
    # 2026-10-21 修法变体：deep.enabled=true + minRecallCount=999999（sweep 保活 +
    # promote 在 rank/apply 双层被硬过滤，同样永不产生新事件）。此时棘轮只能对冻结的
    # 旧事件重复判定并报「✅ 完整落盘」，把「有意退役」误读成「通道健康」→ 一并跳过。
    try:
        _cfg = json.load(open("/Users/duguke/.openclaw/openclaw.json", encoding="utf-8"))
        _deep = (
            _cfg.get("plugins", {}).get("entries", {}).get("memory-core", {})
            .get("config", {}).get("dreaming", {})
            .get("phases", {}).get("deep", {})
        )
        _deep_off = _deep.get("enabled") is False
        _mrc = _deep.get("minRecallCount")
        _hard_gate = isinstance(_mrc, (int, float)) and _mrc >= 999999
    except (OSError, json.JSONDecodeError):
        _deep_off = False
        _hard_gate = False
    if _deep_off or _hard_gate:
        _reason = "deep_promotion_disabled" if _deep_off else "deep_min_recall_hard_gate"
        _detail = (
            "deep 自动 promote 已关闭（10-20 用户拍板，纯人工蒸馏模式）"
            if _deep_off
            else f"deep promote 硬闸模式（minRecallCount={_mrc}，10-21 拍板：sweep 保活 + promote 退役）"
        )
        print(f"ℹ️  {_detail} → 跳过棘轮判定")
        if args.json:
            print(json.dumps({"skipped": True, "reason": _reason}, ensure_ascii=False))
        return 0

    if not events:
        print("ℹ️  events.jsonl 无 memory.promotion.applied 事件，跳过判定")
        return 0

    recent = events[-args.lookback:]
    latest_ts, latest_keys = recent[-1]

    latest_missing = [k for k in latest_keys if k not in markers]
    latest_survival = 1.0 - len(latest_missing) / len(latest_keys)

    # 历史窗口整体存活率（仅观测：设计上就会被淘汰）
    hist_keys = [k for _, ks in recent for k in ks]
    hist_missing = [k for k in hist_keys if k not in markers]
    hist_survival = 1.0 - len(hist_missing) / len(hist_keys) if hist_keys else 1.0

    # 棘轮：连续 2 次 promote 存活率为 0 → 通道确认断裂
    prev = {}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as fh:
                prev = json.load(fh)
        except (json.JSONDecodeError, OSError):
            prev = {}
    prev_zero_streak = int(prev.get("zero_survival_streak", 0))
    zero_streak = prev_zero_streak + 1 if latest_survival == 0.0 else 0
    prev_ts = prev.get("latest_ts")
    if prev_ts == latest_ts:
        # 同一次 promote 重复检查，不重复累加
        zero_streak = prev_zero_streak

    state = {
        "latest_ts": latest_ts,
        "latest_keys": len(latest_keys),
        "latest_missing": len(latest_missing),
        "latest_survival": round(latest_survival, 4),
        "window_promotions": len(recent),
        "window_survival": round(hist_survival, 4),
        "zero_survival_streak": zero_streak,
        "memory_markers_total": len(markers),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    _bak(STATE_PATH)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(state, ensure_ascii=False))

    violation = zero_streak >= 2
    print("=== promotion_guard ===")
    print(f"最近一次 promote: {latest_ts}  条数={len(latest_keys)}  存活率={latest_survival:.0%}")
    print(f"回溯 {len(recent)} 次 promote 整体存活率: {hist_survival:.0%}（设计即为易失缓冲，仅观测）")
    print(f"连续零存活次数: {zero_streak}")

    if violation:
        print(
            "❌ 违规：连续 ≥2 次 promote 的区块被预算淘汰完全丢弃 → "
            "自动晋升通道对本仓已失效。\n"
            "   处置：长期保留内容必须人工写入 MEMORY.md 人写区（如 Lessons Learned 段）；\n"
            "   该行为已由源码坐实为确定性设计（DEFAULT_MEMORY_FILE_MAX_CHARS=1e4，"
            "调用点不传 memoryFileMaxChars，无配置可改）。"
        )
        return 1

    if latest_survival < 1.0:
        print("⚠️ 告警：最近一次 promote 存在部分淘汰（人写区已挤占预算）。")
    else:
        print("✅ 最近一次 promote 完整落盘。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
