#!/usr/bin/env python3
"""
Seed Pool — 种子库动态进化引擎（2026-11-09 用户拍板方案落地）
=============================================================
层级: core(人工管理,不自动降级) > watch > observe > candidate(池外候选)

规则(对齐拍板原文「动态调整关注池」):
  晋升: 非 core 票连续 promotion_days 日进入 L3 候选前 promotion_top_pct → observe
  转正: observe 满 confirmation_days 且期间评分中位数 > 全池中位数 → watch
  降级: 非 core 票连续 demotion_days 日评分 < demotion_score → candidate(池外)
  种子保护: core 层仅人工调整，引擎永不自动踢出

状态文件: data/universe/seed_pool.json
审计日志: data/universe/seed_pool_audit.log (追加写，晋升/降级全留痕)
"""
#!/usr/bin/env python3
import os
import sys
import json
import shutil
from datetime import datetime

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

UNI_DIR = os.path.join(WORKSPACE, "data", "universe")
POOL_PATH = os.path.join(UNI_DIR, "seed_pool.json")
AUDIT_PATH = os.path.join(UNI_DIR, "seed_pool_audit.log")

# 规则参数（与 universe_config.SeedPoolConfig 对齐；此处独立可调）
PROMOTION_DAYS = 3
PROMOTION_TOP_PCT = 0.15
CONFIRMATION_DAYS = 7
DEMOTION_DAYS = 10
DEMOTION_SCORE = 35.0
HISTORY_WINDOW = 20
WATCH_CAP = 50


def _bak(path):
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as e:
        print(f"⚠️ 备份失败(不阻断): {e}")


class SeedPool:
    def __init__(self):
        self.pool = {}
        self.load()

    def load(self):
        if os.path.exists(POOL_PATH):
            with open(POOL_PATH, "r", encoding="utf-8") as f:
                self.pool = json.load(f)
        else:
            self._init_from_watchlist()

    def _init_from_watchlist(self):
        from analysis.fib_extension_scan import WATCHLIST
        today = datetime.now().strftime("%Y-%m-%d")
        for sym, name in WATCHLIST.items():
            self.pool[sym] = {
                "name": name, "tier": "core", "entry_date": today,
                "score_history": [], "rank_history": [], "top_streak": 0, "low_streak": 0,
                "observe_days": 0, "last_action": "",
            }
        self.save()
        self._audit("INIT", "-", f"从关注池初始化 {len(WATCHLIST)} 只 core")

    def save(self):
        os.makedirs(UNI_DIR, exist_ok=True)
        _bak(POOL_PATH)
        tmp = POOL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.pool, f, ensure_ascii=False, indent=2)
        os.replace(tmp, POOL_PATH)

    def _audit(self, action, sym, detail):
        os.makedirs(UNI_DIR, exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} | {action:10s} | {sym:8s} | {detail}\n")

    def ensure_member(self, sym, name, tier="candidate"):
        """确保票在池中（首次进入 L3 候选的票自动入 candidate 层）"""
        if sym not in self.pool:
            self.pool[sym] = {
                "name": name, "tier": tier,
                "entry_date": datetime.now().strftime("%Y-%m-%d"),
                "score_history": [], "rank_history": [], "top_streak": 0, "low_streak": 0,
                "observe_days": 0, "last_action": "",
            }
            self._audit("NEW", sym, f"入池 tier={tier}")

    def update_daily(self, candidates: list, all_scores: list):
        """每日 L3 打分后调用。

        candidates: L3 候选名单 [{symbol, total_score, name?...}]（已排序，前 top_k）
        all_scores: 全池评分 [{symbol, total_score, name?...}]（含未进候选的）
        """
        today = datetime.now().strftime("%Y-%m-%d")
        score_map = {c["symbol"]: float(c.get("total_score") or 0) for c in all_scores}
        rank_map = {c["symbol"]: i + 1 for i, c in enumerate(all_scores)}
        top_n = max(1, int(len(all_scores) * PROMOTION_TOP_PCT))
        top_syms = {c["symbol"] for c in all_scores[:top_n]}
        pool_scores_today = [v for v in score_map.values() if v > 0]
        pool_median = sorted(pool_scores_today)[len(pool_scores_today) // 2] if pool_scores_today else 0

        # 首次出现的候选 → 入池 candidate 层
        for c in all_scores:
            self.ensure_member(c["symbol"], c.get("name", ""), tier="candidate")

        actions = []
        # watch 上限约束：晋升前检查
        watch_count = sum(1 for s in self.pool.values() if s["tier"] in ("watch", "observe"))

        for sym, st in self.pool.items():
            score = score_map.get(sym, 0.0)
            rank = rank_map.get(sym, 999)
            st["score_history"].append(score)
            st["rank_history"].append(rank)
            for k in ("score_history", "rank_history"):
                if len(st[k]) > HISTORY_WINDOW:
                    st[k] = st[k][-HISTORY_WINDOW:]

            tier = st["tier"]
            if tier == "core":
                continue  # 种子保护

            # 晋升 streak 维护
            st["top_streak"] = st["top_streak"] + 1 if sym in top_syms else 0
            st["low_streak"] = st["low_streak"] + 1 if 0 < score < DEMOTION_SCORE else 0

            # 降级（先判降，防低分票占 observe 坑）
            if st["low_streak"] >= DEMOTION_DAYS:
                old = tier
                st.update({"tier": "candidate", "observe_days": 0, "top_streak": 0,
                           "low_streak": 0, "entry_date": today, "last_action": "demote"})
                actions.append(f"⬇️ {sym}({st['name']}) {old}→candidate: 连续{DEMOTION_DAYS}日评分<{DEMOTION_SCORE}")
                self._audit("DEMOTE", sym, f"{old}→candidate low_streak={DEMOTION_DAYS}")
                continue

            # 晋升: candidate → observe
            if tier == "candidate" and st["top_streak"] >= PROMOTION_DAYS:
                if watch_count < WATCH_CAP:
                    st.update({"tier": "observe", "observe_days": 0, "entry_date": today,
                               "last_action": "promote"})
                    watch_count += 1
                    actions.append(f"⬆️ {sym}({st['name']}) candidate→observe: 连续{PROMOTION_DAYS}日进前{PROMOTION_TOP_PCT:.0%}")
                    self._audit("PROMOTE", sym, f"candidate→observe top_streak={PROMOTION_DAYS}")
                else:
                    self._audit("PROMOTE_BLOCKED", sym, f"watch 池已满 {WATCH_CAP}")

            # 转正: observe → watch
            elif tier == "observe":
                st["observe_days"] += 1
                if st["observe_days"] >= CONFIRMATION_DAYS:
                    recent = [s for s in st["score_history"][-CONFIRMATION_DAYS:] if s > 0]
                    recent_median = sorted(recent)[len(recent) // 2] if recent else 0
                    if recent_median > pool_median and watch_count < WATCH_CAP:
                        st.update({"tier": "watch", "observe_days": 0, "entry_date": today,
                                   "last_action": "confirm"})
                        actions.append(f"✅ {sym}({st['name']}) observe→watch: {CONFIRMATION_DAYS}日中位 {recent_median:.1f} > 池中位 {pool_median:.1f}")
                        self._audit("CONFIRM", sym, f"observe→watch median={recent_median:.1f}>pool={pool_median:.1f}")
                    elif recent_median <= pool_median:
                        st.update({"tier": "candidate", "observe_days": 0, "last_action": "observe_expire"})
                        actions.append(f"↩️ {sym}({st['name']}) observe→candidate: {CONFIRMATION_DAYS}日中位 {recent_median:.1f} ≤ 池中位 {pool_median:.1f}")
                        self._audit("OBSERVE_EXPIRE", sym, f"median={recent_median:.1f}<=pool={pool_median:.1f}")

        self.save()
        return actions

    def scan_universe(self) -> list:
        """参与 L3 扫描的池 = core + watch + observe + candidate（candidate 也扫，才有晋升数据）"""
        return list(self.pool.keys())

    def status(self) -> dict:
        out = {}
        for tier in ("core", "watch", "observe", "candidate"):
            syms = [s for s, st in self.pool.items() if st["tier"] == tier]
            out[tier] = {"count": len(syms), "symbols": syms}
        return out


def main():
    """CLI: python3 analysis/seed_pool.py --status"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    pool = SeedPool()
    st = pool.status()
    print("种子库状态:")
    for tier, d in st.items():
        print(f"  {tier:10s}: {d['count']:3d} 只 {d['symbols'][:8]}{'...' if d['count'] > 8 else ''}")
    if os.path.exists(AUDIT_PATH):
        print("\n最近审计:")
        with open(AUDIT_PATH, encoding="utf-8") as f:
            for line in f.readlines()[-8:]:
                print("  " + line.rstrip())


if __name__ == "__main__":
    main()
