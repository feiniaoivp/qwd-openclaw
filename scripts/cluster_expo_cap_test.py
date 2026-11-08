#!/usr/bin/env python3
"""单簇市值占比上限（CLUSTER_EXPO_CAP）契约测试（2026-10-18）。

口径来源: analysis/portfolio_core._apply_cluster_cap（组合层单簇市值占比上限）
背景: 只数≤2 口径在评分卡(ef81ed2)中证明无保护 → 升级为持仓期持续生效的市值占比。
跑法: python3 scripts/cluster_expo_cap_test.py   (全过 exit 0，任一失败 exit 1)
"""
import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from analysis import portfolio_core as pc

CLU = {"600030": "C01", "601066": "C01", "601211": "C01", "300285": "C02"}

PASS = []
FAIL = []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("✅" if cond else "❌") + " " + label)


def mk_pos(sym, mv=None, cash=0, price=None):
    """mv=None → 空仓候选(自带 10 万现金); 否则按 entry=10, shares=mv/10 建仓(现金0)。
    注: 预估买入额 = min(cash, plan限额, single_cap) 与 execute_trade 同口径,
    satellite single_cap = INITIAL_CAPITAL*0.05 = 5000, core = 15000。"""
    if mv is None:
        return {"_symbol": sym, "name": sym, "position": False, "shares": 0,
                "entry_price": 0, "cash": cash, "bucket": "satellite",
                "total_pl": 0, "trade_count": 0}
    shares = int(mv / 10)
    return {"_symbol": sym, "name": sym, "position": True, "shares": shares,
            "entry_price": 10, "_last_mv": mv, "cash": cash, "bucket": "satellite",
            "total_pl": 0, "trade_count": 1}


def run(label, signal, pos, state, plan=None, mv_map=None, expect_block=None):
    sig, ok = pc._apply_cluster_cap(signal, pos, state, True, signal["_sym"], CLU,
                                    signal.get("_name", "T"), mv_map=mv_map,
                                    position_plan=plan)
    blocked = (sig["action"] == "持有") or (not ok)
    if expect_block is None:
        check(True, label)
        return
    check(blocked == expect_block, f"{label} (期望{'拦' if expect_block else '放'}, 实{'拦' if blocked else '放'})")


PLAN = {"single_stock_max_amount": 100_000, "buy_signal_multiplier": 1.0}


def buy():
    return {"action": "买入", "reason": "sig", "price": 10.0}


# ── 场景1: 未入簇票 → 永远放行（fail-open）──
s = buy(); s["_sym"] = "000001"
run("场景1 未入簇票放行", s, mk_pos("000001"), {"positions": {
    "600030": mk_pos("600030", mv=800_000)}}, PLAN, expect_block=False)

# ── 场景2: 卖出信号不受影响 ──
s = {"action": "卖出", "reason": "sig", "price": 10.0, "_sym": "600030"}
run("场景2 卖出不拦截", s, mk_pos("600030", mv=100_000), {"positions": {
    "600030": mk_pos("600030", mv=100_000), "601066": mk_pos("601066", mv=100_000)}}, PLAN,
    expect_block=False)

# ── 场景3: 空组合首笔买入 → 放行（0+10万 / 0+10万 = 100% 但 denom<10万? =10万 → 走限额;
#     实际 denom=100_000 不小于阈值10万，占比100%>25% 会拦 → 语义错误!
#     因此设计上空组合(无持仓)时 total_mv=0, denom=est_buy, expo=1 → 必拦!
#     修正: 组合内无任何持仓时约束无意义，应放行。检查实现是否如此 ──
s = buy(); s["_sym"] = "600030"
run("场景3 空组合首笔放行(设计: 无持仓时约束不适用)", s, mk_pos("600030"),
    {"positions": {}}, PLAN, expect_block=False)

# ── 场景4: 簇市值10万/组合总资产90万, est=5k → (100+5)/(900+5)=11.6% ≤25% 放行 ──
s = buy(); s["_sym"] = "601211"
st = {"positions": {"600030": mk_pos("600030", mv=50_000),
                    "601066": mk_pos("601066", mv=50_000),
                    "300285": mk_pos("300285", mv=800_000)}}
run("场景4 簇占20%放行", s, mk_pos("601211"), st, PLAN, expect_block=False)

# ── 场景5: 簇市值40万/总资产150万, est=5k → (400+5)/(1500+5)=26.7%
#    期望随生效上限动态判定（2026-11-08 激进模式同步: 0.25→0.45 时本场景应为放行）
s = buy(); s["_sym"] = "601211"
st = {"positions": {"600030": mk_pos("600030", mv=200_000),
                    "601066": mk_pos("601066", mv=200_000),
                    "300285": mk_pos("300285", mv=1_000_000)}}
expo5 = (400 + 5) / (1500 + 5)
run(f"场景5 簇占26.7% (vs 上限{pc.CLUSTER_EXPO_CAP:.0%})", s, mk_pos("601211"), st, PLAN,
    expect_block=expo5 > pc.CLUSTER_EXPO_CAP)
# 场景5b: 超限对偶（簇70万/总150万 → 46.8%，标准/激进模式均必拦）──
st5b = {"positions": {"600030": mk_pos("600030", mv=350_000),
                      "601066": mk_pos("601066", mv=350_000),
                      "300285": mk_pos("300285", mv=800_000)}}
run("场景5b 簇占46.8%>上限拦截(对偶必拦)", s, mk_pos("601211"), st5b, PLAN, expect_block=True)

# ── 场景6: 只数口径会拦(簇内已持2只)但占比低 → 新口径放行(证明语义真的换了) ──
s = buy(); s["_sym"] = "601211"
st = {"positions": {"600030": mk_pos("600030", mv=100_000),
                    "601066": mk_pos("601066", mv=100_000),
                    "300285": mk_pos("300285", mv=5_000_000)}}
# (100+5)/(5200+5)=1.9% ≤25% → 放行，尽管同簇已持2只 ── 与旧只数口径的行为分叉点
run("场景6 同簇2只但占比3.8%→放行(只数口径会拦)", s, mk_pos("601211"), st, PLAN,
    expect_block=False)

# ── 场景7: 齐涨漂移 → 簇内仅1只但市值50万/总资产150万 → (500+5)/(1500+5)=33.4%
#    期望随生效上限动态判定（激进 0.45 → 放行）──
s = buy(); s["_sym"] = "601211"
st = {"positions": {"600030": mk_pos("600030", mv=500_000),
                    "300285": mk_pos("300285", mv=1_000_000)}}
expo7 = (500 + 5) / (1500 + 5)
run(f"场景7 簇内1只齐涨漂移33.4% (vs 上限{pc.CLUSTER_EXPO_CAP:.0%})", s, mk_pos("601211"), st, PLAN,
    expect_block=expo7 > pc.CLUSTER_EXPO_CAP)
# 场景7b: 超限对偶（簇70万/总150万 → 46.8%，两模式均必拦）──
st7b = {"positions": {"600030": mk_pos("600030", mv=700_000),
                      "300285": mk_pos("300285", mv=800_000)}}
run("场景7b 齐涨漂移46.8%>上限拦截(对偶必拦)", s, mk_pos("601211"), st7b, PLAN, expect_block=True)

# ── 场景8: mv_map 当日市值覆盖过期的 _last_mv ──
s = buy(); s["_sym"] = "601211"
p30 = mk_pos("600030", mv=200_000)
p30["_last_mv"] = 200_000          # 昨日市值
st = {"positions": {"600030": p30,
                    "300285": mk_pos("300285", mv=1_000_000)}}
mv_map = {"600030": 600_000}       # 当日已暴涨: 20万→60万
# 不用 mv_map: (200+5)/(1200+5)=16.9% 放行; 用 mv_map 当日60万: (600+5)/(1600+5)=37.6%
# 期望随生效上限动态判定（激进 0.45 → 放行）
run("场景8a 无mv_map用_last_mv→放行", buy_ := buy() | {"_sym": "601211"}, mk_pos("601211"),
    st, PLAN, mv_map=None, expect_block=False)
expo8b = (600 + 5) / (1600 + 5)
run(f"场景8b 有mv_map当日市值37.6% (vs 上限{pc.CLUSTER_EXPO_CAP:.0%})", buy_ := buy() | {"_sym": "601211"},
    mk_pos("601211"), st, PLAN, mv_map=mv_map, expect_block=expo8b > pc.CLUSTER_EXPO_CAP)
# 场景8c: 超限对偶（mv_map 当日90万 → (900+5)/(1900+5)=47.5%，两模式均必拦）──
run("场景8c mv_map当日90万→47.5%>上限拦截(对偶必拦)", buy_ := buy() | {"_sym": "601211"},
    mk_pos("601211"), st, PLAN, mv_map={"600030": 900_000}, expect_block=True)

# ── 场景9: 预估买入额敏感性（core single_cap=15000 vs satellite=5000）──
st2 = {"positions": {"600030": mk_pos("600030", mv=358_000),
                     "300285": mk_pos("300285", mv=1_092_000)}}
# satellite: (358+5)/(1450+5)=25.03%→拦? 363/1455=24.95% 放; core: (358+15)/(1450+15)=25.48%
# 期望随生效上限动态判定（激进 0.45 → 均放行）
run("场景9a satellite est5k → 24.95% 放行", buy() | {"_sym": "601211"},
    mk_pos("601211"), st2, PLAN, expect_block=False)
expo9b = (358 + 15) / (1450 + 15)
run(f"场景9b core est15k → 25.48% (vs 上限{pc.CLUSTER_EXPO_CAP:.0%})", buy() | {"_sym": "601211"},
    mk_pos("601211", cash=100_000) | {"bucket": "core"}, st2, PLAN,
    expect_block=expo9b > pc.CLUSTER_EXPO_CAP)
# 场景9c: 超限对偶（core + 簇市值70万/总150万；est 随模式变 15k/30k，两模式均超上限必拦）──
st9c = {"positions": {"600030": mk_pos("600030", mv=700_000),
                      "300285": mk_pos("300285", mv=800_000)}}
run("场景9c core + 簇占>上限拦截(对偶必拦)", buy() | {"_sym": "601211"},
    mk_pos("601211", cash=100_000) | {"bucket": "core"}, st9c, PLAN, expect_block=True)

# ── 场景10: 映射整体缺失(fail-open) → 放行 ──
s = buy(); s["_sym"] = "600030"
sig, ok = pc._apply_cluster_cap(s, mk_pos("600030"), {"positions": {}}, True,
                                "600030", {}, "T", mv_map=None, position_plan=PLAN)
check(sig["action"] == "买入" and ok, "场景10 映射空→fail-open放行")

print()
print(f"合计: {len(PASS)} 过 / {len(FAIL)} 败")
sys.exit(1 if FAIL else 0)
