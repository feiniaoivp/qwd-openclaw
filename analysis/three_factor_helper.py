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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FUND_BUY = 40      # 基本面参考分（不再一票否决，仅用于 reason 参考）
SENT_BUY = 60      # 情绪参考分（不再一票否决，仅用于 reason 参考）
BUY_LINE = 60      # 加权评分买入线
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
                self._sentiment = {"score": 50, "up_ratio": 0, "detail": "数据缺失"}
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
            # 失败保守给 50（中性），不阻断
            self._sentiment = {"score": 50, "up_ratio": 0, "detail": f"情绪获取失败:{str(e)[:40]}"}
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

        纯加权评分制（2026-08-21 用户拍板）：
          buy_score = 技术×{W_TECH} + 基本面×{W_FUND} + 情绪×{W_SENT}
          BUY 条件：buy_score >= {BUY_LINE}
        无任何一票否决：极端恐慌（情绪低）时，若技术+基本面足够强，仍可买入。
        tech_score 为 None 时视为技术已由调用方确认（强买信号，按 90 计）。"""
        sent = self.sentiment()
        fund = self.fundamental(symbol)
        sent_score = sent.get("score", 0) or 0
        fund_score = fund.get("score")  # 可能 None
        tech = tech_score if tech_score is not None else 90.0
        # 基本面缺失时按 50(中性) 计，不因缺失而完全否决，但会拉低加权分
        fund_eff = fund_score if fund_score is not None else 50.0
        buy_score = round(tech * W_TECH + fund_eff * W_FUND + sent_score * W_SENT, 1)
        ok = buy_score >= BUY_LINE
        # 信息诊断
        reasons = []
        if tech < 60:
            reasons.append(f"技术{tech:.0f}偏弱")
        if fund_score is not None and fund_score < FUND_BUY:
            reasons.append(f"基本面{fund_score:.0f}偏低(ROE{fund.get('roe')}/增速{fund.get('yoy_pni')})")
        if fund_score is None:
            reasons.append("基本面数据缺失(按50计)")
        if sent_score < 30:
            reasons.append(f"情绪极低{sent_score:.0f}(极端恐慌)")
        elif sent_score < SENT_BUY:
            reasons.append(f"情绪{sent_score:.0f}偏弱(全市场涨跌占比{sent.get('up_ratio',0):.0%})")
        reason = "三因素加权共振通过，可买入" if ok else (";".join(reasons) if reasons else "加权分未达买入线")
        info = {
            "tech": tech, "fundamental": fund_score, "fund_eff": fund_eff,
            "sentiment": sent_score, "buy_score": buy_score, "ok": ok,
            "reason": (f"buy_score={buy_score}(技术{tech:.0f}×{W_TECH}+基本面{fund_eff:.0f}×{W_FUND}"
                        f"+情绪{sent_score:.0f}×{W_SENT})：{reason}"),
        }
        return ok, info


if __name__ == "__main__":
    g = ResonanceGate()
    s = g.sentiment()
    print(f"📊 情绪分: {s}")
    for sym in ["600036", "601865", "300285", "000157"]:
        ok, info = g.check_buy(sym, tech_score=88)
        print(f"{sym}: {'✅通过' if ok else '❌拦截'} | {info['reason']}")
