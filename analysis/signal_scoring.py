#!/usr/bin/env python3
"""
L3 Signal Resonance — 多维信号打分（2026-11-09 用户拍板方案落地）
=================================================================
输入: L2 scan_universe (core+extended+seed) + 本地 K 线 CSV
维度: trend(趋势) / volume(量能) / volatility(波动) / chip(筹码)
      valuation — 数据源缺位，当前不参与（权重重分配至其它维度，输出留痕）
chip 数据源（2026-11-10 接入，用户批准）:
  - 经典衰减法自算筹码分布，完整复用 scripts/chip_distribution_study.py
    的 chip_metrics（同参 400 档/turn 衰减/三角分布，禁第二套算法）
  - 数据全本地: 数据湖/stock_data CSV 自带 turn 列，零网络成本
  - 打分证据边界（analysis/research/chip_distribution_study.json，全池 h5 五分位 + 三年 Spearman）:
      * cost_bias 三年 Spearman 全负(-0.056/-0.039/-0.025) → 追高惩罚有据（最一致）
      * winner_pct 中段(40-70%) h5 超额最高、两端差 → 带状给分
      * conc90 证据最弱(2026 Spearman 0.001) → 只展示不打分
政权: regime_allocator 阈值法 → boost 乘数

铁律:
  - 缺数据维度 = 该维度不参与 + 权重归一化重分配（绝不塞中性 50 分）
    对齐 ERR-20261018-C03: 缺数据 ≠ 中性分
  - 因子缺失值 → 该子项 0 分（可见），不做 ffill 伪造
  - 产出 candidates_daily.json + 全池评分（供 seed_pool 晋升降级）

运行: python3 analysis/signal_scoring.py            # 每日 15:40 cron
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

import pandas as pd
import numpy as np

UNI_DIR = os.path.join(WORKSPACE, "data", "universe")
STOCK_DIR = os.path.join(WORKSPACE, "data", "stock_data")
L2_PATH = os.path.join(UNI_DIR, "universe_core.json")
CAND_PATH = os.path.join(UNI_DIR, "candidates_daily.json")

# 阈值 SSOT: universe_config.UniverseFilterConfig（data/universe_filter_config.json，
# 缺文件回落 dataclass 默认）。此前硬编码与 dataclass 默认存在 fork
# （HARD_GATES 0.15/0.92 vs 0.20/0.90），2026-11-10 以生产实跑值修正 dataclass 后改读 CFG。
from analysis.universe_config import CFG as _UCFG

TOP_K = int(_UCFG.l3.top_k)
MIN_SCORE = float(_UCFG.l3.min_score)
HARD_GATES = dict(_UCFG.l3.hard_gates)
REGIME_BOOST = {"trend_up": 1.10, "sideways": 1.00, "trend_down": 0.85, "high_vol": 0.90}

# 维度基础权重（valuation 数据缺位时重分配；chip 2026-11-10 起在场）
BASE_WEIGHTS = {"trend": 0.30, "volume": 0.25, "chip": 0.20, "volatility": 0.15, "valuation": 0.10}


def _bak(path):
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
    except Exception as e:
        print(f"⚠️ 备份失败(不阻断): {e}")


def load_price_data(symbols, max_days=300):
    """加载本地 CSV（两源合并: 数据湖 data/universe/daily 优先，data/stock_data 兑底）"""
    lake_dir = os.path.join(WORKSPACE, "data", "universe", "daily")
    data, missing = {}, []
    for sym in symbols:
        # 源1: 数据湖（sh.600000.csv 格式 → sh600000.csv）
        fp = None
        for cand in (os.path.join(lake_dir, f"sh{sym}.csv") if sym.startswith("6") else None,
                     os.path.join(lake_dir, f"sz{sym}.csv") if not sym.startswith("6") else None,
                     os.path.join(STOCK_DIR, f"{sym}.csv")):
            if cand and os.path.exists(cand):
                fp = cand
                break
        if not fp:
            missing.append(sym)
            continue
        try:
            df = pd.read_csv(fp)
            df.columns = [c.lower() for c in df.columns]
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index().tail(max_days)
            need = {"open", "high", "low", "close", "volume"}
            if not need.issubset(df.columns) or len(df) < 60:
                missing.append(sym)
                continue
            data[sym] = df
        except Exception:
            missing.append(sym)
    return data, missing


def chip_snapshot(df):
    """末端筹码指标（复用 chip_distribution_study.chip_metrics，经典衰减法 400 档）。

    缺 turn 列 / 不足 60 行 / 计算异常 → 返回 None（chip 维缺席，权重重分配，
    对齐「缺数据 ≠ 中性分」铁律）。异常必须可见，禁止静默吞噬。
    """
    if df is None or "turn" not in df.columns or len(df) < 60:
        return None
    try:
        _scripts_dir = os.path.join(WORKSPACE, "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        from chip_distribution_study import chip_metrics
        # load_price_data 把 date set_index 了，chip_metrics 需要普通列 → reset_index
        d2 = df.reset_index().rename(columns={df.index.name or "index": "date"})
        if "date" not in d2.columns:
            return None
        m = chip_metrics(d2)
        if m is None or not len(m):
            return None
        last = m.iloc[-1]
        out = {}
        for k in ("winner_pct", "cost_bias", "conc90"):
            v = last.get(k)
            out[k] = float(v) if v is not None and pd.notna(v) else None
        return out if any(v is not None for v in out.values()) else None
    except Exception as e:
        print(f"⚠️ chip 指标计算失败(该票 chip 维缺席): {type(e).__name__}: {e}")
        return None


def compute_factor_snapshot(data):
    """计算 L3 所需因子最新值（复用 factor_engine，MVP 用手写向量化避免缓存过期坑）"""
    out = {}
    for sym, df in data.items():
        close = pd.to_numeric(df["close"], errors="coerce")
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        vol = pd.to_numeric(df["volume"], errors="coerce")
        if close.notna().sum() < 60:
            continue
        # trend
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        macd_sig = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - macd_sig
        # ADX14 (Wilder)
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        up = high.diff()
        dn = -low.diff()
        plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
        a = 1 / 14
        tr_s = tr.ewm(alpha=a, adjust=False).mean()
        pdi = 100 * plus_dm.ewm(alpha=a, adjust=False).mean() / tr_s.replace(0, np.nan)
        mdi = 100 * minus_dm.ewm(alpha=a, adjust=False).mean() / tr_s.replace(0, np.nan)
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
        adx = dx.ewm(alpha=a, adjust=False).mean()
        # volume
        vol_z = (vol - vol.rolling(20).mean()) / vol.rolling(20).std().replace(0, np.nan)
        direction = np.sign(close.diff().fillna(0))
        obv = (direction * vol).cumsum()
        obv_up = bool(obv.iloc[-1] > obv.iloc[-6]) if len(obv) > 6 else None  # 5日 OBV 斜率
        # volatility
        ma20 = close.rolling(20).mean()
        sd20 = close.rolling(20).std()
        bb_pos = ((close - (ma20 - 2 * sd20)) / ((ma20 + 2 * sd20) - (ma20 - 2 * sd20))).iloc[-1]
        hv20 = close.pct_change().rolling(20).std().iloc[-1] * np.sqrt(252)
        # valuation（eps/bv 缺位 = NaN → valuation 维不参与）
        snap = {
            "ema_cross": int(ema12.iloc[-1] > ema26.iloc[-1]) if pd.notna(ema12.iloc[-1]) and pd.notna(ema26.iloc[-1]) else None,
            "close_above_ema20": bool(close.iloc[-1] > ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else None,
            "macd_hist": float(macd_hist.iloc[-1]) if pd.notna(macd_hist.iloc[-1]) else None,
            "adx": float(adx.iloc[-1]) if pd.notna(adx.iloc[-1]) else None,
            "vol_zscore": float(vol_z.iloc[-1]) if pd.notna(vol_z.iloc[-1]) else None,
            "obv_rising_5d": obv_up,
            "bb_pos": float(bb_pos) if pd.notna(bb_pos) else None,
            "hv20": float(hv20) if pd.notna(hv20) else None,
            "close": float(close.iloc[-1]),
            "amount_20d": float((close * vol).tail(20).mean()),
        }
        chip = chip_snapshot(df)
        if chip:
            snap.update(chip)
        out[sym] = snap
    return out


def score_stock(snap: dict, weights=None) -> tuple:
    """单票打分。返回 (total_score 0-100, dim_detail, active_dims)

    各维度归一 0-100；缺数据子项 = 0 分（可见）；
    缺席维度（chip/valuation）不参与 + 权重重分配。
    weights: regime 联动覆盖（None = 全局 BASE_WEIGHTS；相对比例即可，内部归一）。
    """
    w_map = weights if weights else BASE_WEIGHTS
    dims = {}

    # ── trend (0-100) ──
    t = 0.0
    if snap.get("ema_cross") == 1:
        t += 35
    if snap.get("close_above_ema20"):
        t += 25
    mh = snap.get("macd_hist")
    if mh is not None and mh > 0:
        t += 20
    adx = snap.get("adx")
    if adx is not None:
        if adx > 25:
            t += 20
        elif adx > 20:
            t += 10
    dims["trend"] = t

    # ── volume (0-100) ──
    v = 0.0
    vz = snap.get("vol_zscore")
    if vz is not None:
        if vz > 1.5:
            v += 40
        elif vz > 0.5:
            v += 25
    if snap.get("obv_rising_5d"):
        v += 30
    amt = snap.get("amount_20d")
    if amt is not None and amt >= 1e8:  # 量级加分: 日均成交额过亿
        v += 30
    dims["volume"] = v

    # ── volatility (0-100) ──
    vo = 0.0
    bb = snap.get("bb_pos")
    if bb is not None and 0.3 <= bb <= 0.8:
        vo += 50
    elif bb is not None and 0.2 <= bb < 0.3:
        vo += 30
    hv = snap.get("hv20")
    if hv is not None:
        if hv < 0.35:
            vo += 30
        elif hv < 0.55:
            vo += 15
    dims["volatility"] = vo

    # ── chip (0-100) 筹码结构健康度（2026-11-10 接入，证据边界见模块 docstring）──
    ch = 0.0
    wp = snap.get("winner_pct")   # 获利盘 0-1
    cb = snap.get("cost_bias")    # close/avg_cost - 1
    if wp is not None:
        if 0.40 <= wp <= 0.70:    # 中段最优（h5 五分位 Q2 峰值）
            ch += 50
        elif 0.25 <= wp < 0.40 or 0.70 < wp <= 0.80:
            ch += 30
        else:                     # <25% 无人获利 / >80% 过热全获利
            ch += 10
    if cb is not None:
        if 0.0 <= cb <= 0.10:     # 浅获利: 站稳成本上方未过热
            ch += 50
        elif -0.05 <= cb < 0.0:
            ch += 35
        elif 0.10 < cb <= 0.20:
            ch += 20
        elif cb < -0.05:          # 深套: 上方套牢盘抛压（trend 维另行压分）
            ch += 15
        else:                     # >+20% 深度获利（Spearman 全负，最差邻域）
            ch += 5
    if wp is not None or cb is not None:
        dims["chip"] = ch

    # ── valuation: 数据源缺位（eps/bv 面板仅 33 只覆盖且滞后）→ 本版整体缺席 ──
    active = [d for d in w_map if d in dims]
    wsum = sum(w_map[d] for d in active)
    total = sum(dims[d] * (w_map[d] / wsum) for d in active)
    return round(total, 2), dims, active


def detect_regime(data) -> dict:
    """阈值法政权检测（复用 regime_allocator 思路，独立轻量版防缓存坑）

    2026-11-10 晚加 2 日确认迟滞（外部建议采纳，判据: 1416 日实测闪烁
    19.1 次/年、短段≤2日占 11.8%）：新 regime 需连续 2 日一致才切档，
    不一致保持昨日档（保守方向）。避免日级噪声触发 TOP_K 档位跳动。
    """
    try:
        closes = pd.DataFrame({s: d["close"] for s, d in data.items()}).dropna(how="all")
        ew = closes.pct_change().mean(axis=1).add(1).cumprod()
        ret = ew.pct_change().dropna()
        ma5, ma20, ma60 = ew.rolling(5).mean(), ew.rolling(20).mean(), ew.rolling(60).mean()
        sp5_20 = (ma5 - ma20) / ma20
        sp20_60 = (ma20 - ma60) / ma60
        vol20 = ret.rolling(20).std() * np.sqrt(252)
        vol60 = ret.rolling(60).std() * np.sqrt(252)

        def _cls(j):
            if vol20.iloc[j] > vol60.iloc[j] * 1.5:
                return "high_vol"
            if sp5_20.iloc[j] > 0.01 and sp20_60.iloc[j] > 0:
                return "trend_up"
            if sp5_20.iloc[j] < -0.01 and sp20_60.iloc[j] < 0:
                return "trend_down"
            return "sideways"

        i = -1
        r_today = _cls(i)
        r_yest = _cls(i - 1) if len(sp5_20) > 2 else r_today
        r = r_today if r_today == r_yest else r_yest   # 2 日确认，否则保持昨日档
        if r != r_today:
            print(f"🌦️ regime 迟滞: 今日{_cls(i)}与昨日不一致 → 保持昨日档 {r}（2日确认）")
        return {"regime": r, "boost": REGIME_BOOST.get(r, 1.0),
                "sp5_20": float(sp5_20.iloc[i]), "vol_ratio": float(vol20.iloc[i] / vol60.iloc[i]) if vol60.iloc[i] else None}
    except Exception as e:
        print(f"⚠️ 政权检测失败 fail-open(sideways): {e}")
        return {"regime": "sideways", "boost": 1.0}


def main():
    l2 = json.load(open(L2_PATH, encoding="utf-8")) if os.path.exists(L2_PATH) else None
    # 🔴 数据源优先级（2026-11-09 与 11-05 存量数据湖对齐后定案）:
    #   1. data/universe/daily/ (build_universe.py 数据湖，800只 hs300+zz500，5年 qfq)
    #   2. data/stock_data/     (universe_filter 补拉，覆盖全 L2 池)
    # 两处合并去重：数据湖优先（更新更勤、含 amount/pctChg）
    lake_dir = os.path.join(WORKSPACE, "data", "universe", "daily")
    lake_syms = set()
    if os.path.isdir(lake_dir):
        for f in os.listdir(lake_dir):
            if f.endswith(".csv"):
                lake_syms.add(f.replace(".csv", "").replace("sh", "").replace("sz", "").zfill(6))
    if l2:
        universe = l2["scan_universe"]
        names = l2.get("names", {})
        mode = f"universe(L2)+lake({len(lake_syms & set(universe))})"
    else:
        # 回退模式: L2 未就绪时先扫现有关注池（今晚可端到端跑通，不阻塞）
        from analysis.fib_extension_scan import WATCHLIST
        universe = list(WATCHLIST.keys())
        names = dict(WATCHLIST)
        mode = "watchlist-fallback(L2未就绪)"
        print(f"⚠️ universe_core.json 缺失，回退关注池模式 ({len(universe)} 只)")
    seed_names = {}
    try:
        from analysis.fib_extension_scan import WATCHLIST
        seed_names = dict(WATCHLIST)
    except Exception:
        pass

    data, missing = load_price_data(universe)
    print(f"📊 L3 打分: 数据可用 {len(data)}/{len(universe)} (缺 CSV {len(missing)})")
    if not data:
        print("❌ 无可用行情数据")
        return

    snaps = compute_factor_snapshot(data)
    regime = detect_regime(data)
    print(f"🌦️ 政权: {regime['regime']} (boost ×{regime['boost']})")

    # regime 联动覆盖（universe_config.l3_regime；默认零行为变化，参数由归因填充）
    _ov = _UCFG.l3_regime
    _rname = regime["regime"]
    eff_top_k = int(_ov.regime_top_k.get(_rname, 0) or TOP_K)
    eff_weights = {**BASE_WEIGHTS, **(_ov.regime_dim_weights.get(_rname) or {})}
    chip_on = _rname in (_ov.chip_enabled_regimes or list(BASE_WEIGHTS))
    if not chip_on:
        print(f"🌦️ regime 联动: {_rname} 段 chip 维缺席（chip_enabled_regimes 配置）")
    if eff_top_k != TOP_K:
        print(f"🌦️ regime 联动: {_rname} 段 TOP_K {TOP_K}→{eff_top_k}")

    scored = []
    for sym, snap in snaps.items():
        # 打分入参: chip 关闭的 regime 下抹掉 chip 键（维度缺席重分配）；
        # 展示用 snap 保留 chip 数据供 chip_display
        score_snap = snap if chip_on else {k: v for k, v in snap.items()
                                            if k not in ("winner_pct", "cost_bias", "conc90")}
        total, dims, active = score_stock(score_snap, weights=eff_weights)
        total = round(total * regime["boost"], 2)
        rec = {
            "symbol": sym, "name": names.get(sym) or seed_names.get(sym) or "",
            "total_score": total,
            "dims": dims, "active_dims": active,
            "close": snap.get("close"), "amount_20d": snap.get("amount_20d"),
        }
        # chip 展示字段（打分不参与后，筹码数据仍供 L4 推送/人工核验展示）
        if snap.get("winner_pct") is not None:
            rec["chip_display"] = {"winner_pct": snap.get("winner_pct"),
                                   "cost_bias": snap.get("cost_bias"), "conc90": snap.get("conc90")}
        scored.append(rec)
    scored.sort(key=lambda x: x["total_score"], reverse=True)

    # regime Top-1 绝对地板（2026-11-10 晚采纳外部建议）：
    # Top-1 < 阈值 → 当日候选归零（判据: sideways 段 Top-1≥90 日超额 +0.1bp vs 80-90 带 -21.0bp）
    _floor = float(_ov.regime_top1_floor.get(_rname, 0) or 0)
    if _floor > 0 and scored and scored[0]["total_score"] < _floor:
        print(f"🌦️ regime 地板: {_rname} 段 Top-1={scored[0]['total_score']} < {_floor} → 当日候选归零（无独立趋势票）")
        scored_gate_note = f"top1_floor({_floor}) 未达"
    else:
        scored_gate_note = None

    # 硬门槛（共振条件）: 只对进候选的执行
    candidates = []
    if scored_gate_note:
        pass  # Top-1 未达地板 → 候选保持空（种子库 update_daily 仅依赖 all_scores，已确认容错）
    for c in scored:
        if scored_gate_note:
            break
        snap = snaps[c["symbol"]]
        bb = snap.get("bb_pos")
        if bb is not None and not (HARD_GATES["bb_pos_low"] <= bb <= HARD_GATES["bb_pos_high"]):
            c["gate_blocked"] = f"bb_pos={bb:.2f}"
            continue
        if c["total_score"] < MIN_SCORE:
            c["gate_blocked"] = f"score<{MIN_SCORE}"
            continue
        candidates.append(c)
        if len(candidates) >= eff_top_k:
            break

    today = datetime.now().strftime("%Y-%m-%d")
    out = {
        "built_at": datetime.now().isoformat(), "as_of": today,
        "mode": mode,
        "regime": regime, "n_scored": len(scored),
        "active_dims_note": "valuation 缺席重分配；chip 数据已接入但按归因证据不参与打分（chip边际全负），筹码指标在 chip_display 字段/L4 推送展示",
        "candidates": candidates,
        "all_scores": scored[:80],
    }
    _bak(CAND_PATH)
    tmp = CAND_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CAND_PATH)

    print(f"✅ L3 候选: {len(candidates)} 只 (门槛后) / 全池 {len(scored)} → {CAND_PATH}")
    for c in candidates[:10]:
        d = c["dims"]
        snap = snaps[c["symbol"]]
        if snap.get("winner_pct") is not None:
            chip_s = f" chip(wp={snap.get('winner_pct') or 0:.0%},cb={snap.get('cost_bias') or 0:+.1%})"
        else:
            chip_s = " chip(缺)"
        print(f"  {c['symbol']} {c['name'][:6]:8s} {c['total_score']:6.1f} "
              f"(T{d.get('trend',0):.0f} V{d.get('volume',0):.0f} Vo{d.get('volatility',0):.0f} C{d.get('chip','-')}){chip_s}")

    # 种子库每日进化
    try:
        from analysis.seed_pool import SeedPool
        pool = SeedPool()
        actions = pool.update_daily(candidates, scored)
        if actions:
            print("🌱 种子库动态:")
            for a in actions:
                print(f"  {a}")
        else:
            print("🌱 种子库: 今日无晋升/降级动作")
    except Exception as e:
        print(f"⚠️ seed_pool 更新失败: {e}")


if __name__ == "__main__":
    main()
