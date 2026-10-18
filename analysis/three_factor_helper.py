#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三因素共振门控 —— 共享判定模块
================================
供 adaptive_dual(模拟盘/仲裁) 与 nodes(规则建议) 共用。

纯加权评分制（用户拍板 2026-08-21 更新）：
  buy_score = 技术×0.45 + 基本面×0.30 + 情绪×0.25
  BUY 条件：buy_score >= 60
  - 无任何一票否决：即使极端恐慌（情绪低），只要技术+基本面足够强，
    加权分仍可到 60 以上，允许买入（技术+基本面足以推动强买）。
  - 情绪分/基本面分每日缓存（情绪全市场共享，基本面按个股）
  - 技术分由各调用方自带（信号级/仲裁已含），本模块负责加权合成

用法：
  from analysis.three_factor_helper import ResonanceGate
  gate = ResonanceGate()                       # 首次调用构建缓存
  ok, info = gate.check_buy(symbol)            # 返回 (是否通过, 明细dict)
  gate.sentiment                                 # 当日情绪分 0~100
"""
import os, sys, time
from datetime import datetime

# 网络兜底：进程级 socket 默认超时（防数据源挂起导致永久阻塞）
try:
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from analysis.net_guard import install_default_timeout as _install_net_timeout
    _install_net_timeout()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FUND_BUY = 40      # 基本面参考分（不再一票否决，仅用于 reason 参考）
SENT_BUY = 60      # 情绪参考分（不再一票否决，仅用于 reason 参考）
# 加权评分买入线（2026-10-18 标定下调 60→55，见 check_buy docstring 第 2 条）
GATE_BUY_LINE = float(os.getenv("GATE_BUY_LINE", "55"))
BUY_LINE = GATE_BUY_LINE  # 兼容旧引用
W_TECH, W_FUND, W_SENT = 0.45, 0.30, 0.25   # 三因素权重


class ResonanceGate:
    """三因素共振门控：情绪(全市场) + 基本面(按个股)，技术维度由调用方提供。"""

    def __init__(self, cache_ttl: int = 6 * 3600):
        self._sentiment = None
        self._sent_ts = 0.0
        self._fund_cache = {}
        self._cache_ts = 0.0
        self.ttl = cache_ttl
        self._build_fund = None  # 惰性注入，避免循环 import

    # ---- 情绪维度（全市场共享，缓存6h）----
    def sentiment(self) -> dict:
        now = time.time()
        if self._sentiment is not None and (now - self._sent_ts) < self.ttl:
            return self._sentiment
        try:
            import akshare as ak
            df = ak.stock_market_activity_legu()
            kv = {str(r["item"]): r["value"] for _, r in df.iterrows()}
            up = self._ff(kv.get("上涨")); down = self._ff(kv.get("下跌"))
            active = self._ff(kv.get("活跃度"))
            total = (up or 0) + (down or 0)
            if up is None or down is None or total <= 0:
                # 缺失 ≠ 中性（ERR-20261018-C03）：score=None 向判定层传播，
                # 由 GATE_MISSING_POLICY 决定 fail-open/fail-closed，此处不得塞默认分。
                self._sentiment = {"score": None, "up_ratio": 0, "detail": "数据缺失"}
            else:
                up_ratio = up / total
                active_norm = (active / 100.0) if active is not None else 0.5
                score = round(up_ratio * 70 + active_norm * 30, 1)
                self._sentiment = {
                    "score": score, "up_ratio": round(up_ratio, 3),
                    "up": up, "down": down, "active_pct": active,
                    "date": kv.get("统计日期", "")}
            self._sent_ts = now
        except Exception as e:
            # 取数失败：score=None 传播（缺失 ≠ 中性），策略由判定层决定
            self._sentiment = {"score": None, "up_ratio": 0, "detail": f"情绪获取失败:{str(e)[:40]}"}
            self._sent_ts = now
        return self._sentiment

    # ---- 基本面维度（按个股，会话加固已内置）----
    def fundamental(self, symbol: str) -> dict:
        cached = self._fund_cache.get(symbol)
        if cached is not None:
            return cached
        res = self._fetch_fund_resonance(symbol)
        self._fund_cache[symbol] = res
        return res

    def _fetch_fund_resonance(self, symbol: str) -> dict:
        """获取该股基本面板分（截面归一）。首次调用全池构建归一基准。"""
        # 惰性加载 three_factor_resonance 的取数函数（避免重复实现）
        try:
            from analysis.three_factor_resonance import fetch_fund
        except Exception:
            from analysis import three_factor_resonance as _tfr
            fetch_fund = _tfr.fetch_fund
        got = None
        for _ in range(2):
            r = fetch_fund(symbol, "")
            if r.get("roe") is not None or r.get("np_margin") is not None or r.get("yoy_pni") is not None:
                got = r; break
            time.sleep(0.5)
        if got is None:
            got = fetch_fund(symbol, "")
        # 构建全池归一基准（保证截面一致）
        try:
            from analysis.service import WATCHLIST
        except Exception:
            WATCHLIST = []
        roes = []; yoys = []; npms = []
        # 只取当前股：简化版单点归一（若全池不可得，用硬阈值）
        roe, yoy, npm = got.get("roe"), got.get("yoy_pni"), got.get("np_margin")
        s_roe = self._norm01(abs(roe or 0), None, 0, 0.25)          # 0~25% ROE
        s_yoy = self._norm01(yoy, None, -100, 100)                  # -100%~100%增速
        s_npm = self._norm01(npm, None, 0, 30)                      # 0~30% 净利率
        parts = [p for p in (s_roe, s_yoy, s_npm) if p is not None]
        score = round(sum(parts) / len(parts) * 100, 1) if parts else None
        return {
            "score": score, "roe": roe, "yoy_pni": yoy, "np_margin": npm,
            "stat": got.get("stat"),
        }

    @staticmethod
    def _norm01(v, _col, lo, hi):
        if v is None:
            return None
        if hi <= lo:
            return 0.5
        return max(0.0, min(1.0, (v - lo) / (hi - lo)))

    @staticmethod
    def _ff(v):
        try:
            if v is None or v == "" or v == "--":
                return None
            return float(str(v).replace("%", "").replace(",", ""))
        except Exception:
            return None

    # ---- 门控判定（情绪+基本面两维；技术由调用方传入）----
    def check_buy(self, symbol: str, tech_score: float = None) -> tuple:
        """返回 (是否通过BUY门控, 明细dict)。

        2026-10-18 口径修订（用户拍板方案 A，ERR-20261018-C03）：
          1. **缺失 ≠ 中性**：情绪/基本面取数失败时以 None 传播，绝不再按 50 计入。
             原实现让「技术90 + 双缺」= 68 分恒过线，缺失惩罚方向反了。
          2. **门槛下调 60 → GATE_BUY_LINE(55)**：标定研究
             (analysis/research/*_gate_threshold_study.json, 1062 采样/615 日) 显示
             代理分 65~70 档超额不优于 50~55 档（差 +0.12pp，CI95 [-2.36,+2.16] 含 0），
             原 60 线无区分度 → 取下界（比全样本均值 +0.44% 高出 3.4pp 的历史拦截下限 52）。
             调高门槛会让全池几乎全拦（实测 65 线拦截 1019/1062 = 96%），不可采纳。
          3. **缺失项处置由 GATE_MISSING_POLICY 决定**（默认 fail_closed）：
               fail_closed —— 任一维缺失即不放行（缺数据=不买，防假共振）
               fail_open   —— 缺失维按 50 计（旧行为，仅紧急回退用）
               ignore      —— 缺失维从加权中剔除，剩余权重重归一
          3b. 技术分缺失（tech_score=None）仍按 90 计：调用方走到这里即代表
              信号层已确认强买，此口径与生产历史一致，不改。
        """
        sent = self.sentiment()
        fund = self.fundamental(symbol)
        sent_score = sent.get("score")            # 可能 None（缺失）
        fund_score = fund.get("score")            # 可能 None（缺失）
        tech = tech_score if tech_score is not None else 90.0

        missing = []
        if sent_score is None:
            missing.append(f"情绪({sent.get('detail', '缺失')})")
        if fund_score is None:
            missing.append("基本面")

        policy = (os.getenv("GATE_MISSING_POLICY", "fail_closed") or "fail_closed").lower()
        reasons = []
        buy_score = None
        if missing and policy == "fail_closed":
            ok = False
            reasons.append(f"数据缺失不买入[{'/'.join(missing)}]（fail_closed）")
        else:
            weights = {"tech": W_TECH, "fund": W_FUND, "sent": W_SENT}
            parts = {"tech": tech}
            if fund_score is not None:
                parts["fund"] = fund_score
            if sent_score is not None:
                parts["sent"] = sent_score
            if missing and policy == "fail_open":       # 旧行为回退
                if fund_score is None:
                    parts["fund"] = 50.0
                if sent_score is None:
                    parts["sent"] = 50.0
            wsum = sum(weights[k] for k in parts)
            if wsum <= 0:
                ok = False
                reasons.append("无可用因子")
            else:
                buy_score = round(sum(v * weights[k] for k, v in parts.items()) / wsum, 1)
                ok = buy_score >= GATE_BUY_LINE
                if missing:
                    reasons.append(f"缺失维剔除({'/'.join(missing)})后重归一")

        # 诊断信息
        if tech < 60:
            reasons.append(f"技术{tech:.0f}偏弱")
        if fund_score is not None and fund_score < FUND_BUY:
            reasons.append(f"基本面{fund_score:.0f}偏低(ROE{fund.get('roe')}/增速{fund.get('yoy_pni')})")
        if sent_score is not None and sent_score < 30:
            reasons.append(f"情绪极低{sent_score:.0f}(极端恐慌)")
        elif sent_score is not None and sent_score < SENT_BUY:
            reasons.append(f"情绪{sent_score:.0f}偏弱(全市场涨跌占比{sent.get('up_ratio',0):.0%})")
        reason = "三因素加权共振通过，可买入" if ok else (";".join(reasons) if reasons else "加权分未达买入线")
        info = {
            "tech": tech, "fundamental": fund_score, "sentiment": sent_score,
            "buy_score": buy_score, "ok": ok, "missing": missing, "policy": policy,
            "line": GATE_BUY_LINE,
            "reason": (f"buy_score={buy_score}(线{GATE_BUY_LINE}; 技术{tech:.0f},"
                        f"基本面{fund_score if fund_score is not None else '缺失'},"
                        f"情绪{sent_score if sent_score is not None else '缺失'})：{reason}"),
        }
        return ok, info


if __name__ == "__main__":
    g = ResonanceGate()
    s = g.sentiment()
    print(f"📊 情绪分: {s}")
    for sym in ["600036", "601865", "300285", "000157"]:
        ok, info = g.check_buy(sym, tech_score=88)
        print(f"{sym}: {'✅通过' if ok else '❌拦截'} | {info['reason']}")
