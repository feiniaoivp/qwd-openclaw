#!/usr/bin/env python3
"""
组合级风控管理器
================

提供投资组合层面的风险控制，包括：
- 总回撤限制
- 板块/行业集中度限制
- 相关性仓位限制
- 总杠杆/敞口限制
- 日亏损限制
- 组合级仓位管理

可同时用于：实盘、模拟盘、回测
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict

import pandas as pd
import numpy as np

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data")

# ── 板块映射（简化版，实际可接入东财/同花顺行业分类）──
SECTOR_MAP = {
    # 证券/金融
    "600030": "券商", "601066": "券商", "600036": "银行",
    "601995": "券商", "000987": "金融",
    # 半导体/TMT
    "600584": "半导体", "688981": "半导体", "002156": "半导体", "002413": "军工",
    # 新能源/储能
    "300014": "锂电", "002466": "锂电",
    # 高端制造/材料
    "300285": "新材料", "603308": "机械", "300124": "工控", "601100": "液压",
    "002318": "特钢", "300719": "轴承", "002335": "工控", "300748": "稀土永磁",
    # 化工
    "600160": "化工", "600346": "化工",
    # 钢铁/特钢
    "000708": "特钢",
    # 消费/其他
    "600660": "玻璃", "600570": "软件", "605566": "医疗", "000157": "工程机械", "601061": "有色金属",
    # 电网装备/特高压
    "600089": "电网", "600406": "电网", "000400": "电网", "601179": "电网",
    "002028": "电网", "002270": "电网", "002130": "电网", "600312": "电网",
}


@dataclass
class RiskLimit:
    """风控限额配置"""
    # 总体限制
    max_total_drawdown: float = 0.15          # 最大总回撤 15%
    max_daily_loss: float = 0.03              # 单日最大亏损 3%
    max_total_exposure: float = 1.0           # 最大总敞口 100%（无杠杆）
    
    # 集中度限制
    max_sector_exposure: float = 0.30         # 单板块最大敞口 30%
    max_single_stock: float = 0.10            # 单只最大 10%
    max_correlated_group: float = 0.40        # 高相关组合最大 40%
    
    # 相关性阈值
    correlation_threshold: float = 0.7        # 相关性 > 0.7 视为高相关
    lookback_days: int = 60                   # 相关性计算回看天数
    
    # 止损/止盈
    portfolio_stop_loss: float = 0.10         # 组合级止损 10%
    trailing_stop: float = 0.05               # 移动止损 5%
    
    # 强平触发
    force_liquidate_on_breach: bool = True    # 触发限额强制平仓


@dataclass
class PortfolioState:
    """组合状态快照"""
    timestamp: datetime
    total_equity: float
    cash: float
    positions: Dict[str, Dict]  # symbol -> {shares, price, value, sector}
    daily_pnl: float = 0.0
    max_equity: float = 0.0


class CorrelationTracker:
    """相关性追踪器（增量更新）"""
    
    def __init__(self, lookback: int = 60, threshold: float = 0.7):
        self.lookback = lookback
        self.threshold = threshold
        self.price_history: Dict[str, List[float]] = defaultdict(list)
        self.correlation_cache: Dict[Tuple[str, str], float] = {}
        self.last_update: Optional[datetime] = None
    
    def update(self, symbols: List[str], prices: Dict[str, float]):
        """增量更新价格历史"""
        for sym in symbols:
            if sym in prices:
                self.price_history[sym].append(prices[sym])
                if len(self.price_history[sym]) > self.lookback:
                    self.price_history[sym].pop(0)
        self.last_update = datetime.now()
        self.correlation_cache.clear()
    
    def get_correlation(self, sym1: str, sym2: str) -> float:
        """获取两只股票的相关性"""
        key = tuple(sorted([sym1, sym2]))
        if key in self.correlation_cache:
            return self.correlation_cache[key]
        
        h1 = self.price_history.get(sym1, [])
        h2 = self.price_history.get(sym2, [])
        if len(h1) < 20 or len(h2) < 20:
            return 0.0
        
        # 对齐长度
        n = min(len(h1), len(h2))
        h1, h2 = h1[-n:], h2[-n:]
        
        # 计算收益率相关性
        r1 = np.diff(h1) / h1[:-1]
        r2 = np.diff(h2) / h2[:-1]
        
        if len(r1) < 2 or np.std(r1) == 0 or np.std(r2) == 0:
            corr = 0.0
        else:
            corr = np.corrcoef(r1, r2)[0, 1]
        
        self.correlation_cache[key] = corr
        return corr
    
    def get_highly_correlated_groups(self, symbols: List[str]) -> List[List[str]]:
        """获取高相关性分组"""
        groups = []
        visited = set()
        
        for i, s1 in enumerate(symbols):
            if s1 in visited:
                continue
            group = [s1]
            for s2 in symbols[i+1:]:
                if s2 in visited:
                    continue
                corr = self.get_correlation(s1, s2)
                if corr > self.threshold:
                    group.append(s2)
                    visited.add(s2)
            if len(group) > 1:
                groups.append(group)
            visited.add(s1)
        
        return groups


class PortfolioRiskManager:
    """
    组合级风控管理器
    
    用法：
        risk_mgr = PortfolioRiskManager()
        risk_mgr.set_limits(RiskLimit(...))
        
        # 每日收盘后检查
        violations = risk_mgr.check_portfolio(portfolio_state)
        
        # 下单前预检
        ok, reason = risk_mgr.pre_trade_check(symbol, side, qty, price, portfolio_state)
    """
    
    def __init__(self, limits: RiskLimit = None):
        self.limits = limits or RiskLimit()
        self.correlation_tracker = CorrelationTracker(
            lookback=self.limits.lookback_days,
            threshold=self.limits.correlation_threshold
        )
        self.state_history: List[PortfolioState] = []
        self.daily_pnl_history: List[Tuple[datetime, float]] = []
        self._max_equity = 0.0
        self._breach_triggered = False
    
    def set_limits(self, limits: RiskLimit):
        """更新风控限额"""
        self.limits = limits
        self.correlation_tracker.lookback = limits.lookback_days
        self.correlation_tracker.threshold = limits.correlation_threshold
    
    def update_market_data(self, prices: Dict[str, float], positions: Dict[str, Dict]):
        """更新行情数据（每日收盘调用）"""
        symbols = list(positions.keys())
        self.correlation_tracker.update(symbols, prices)
    
    def record_portfolio_state(self, state: PortfolioState):
        """记录组合状态快照"""
        self.state_history.append(state)
        self.daily_pnl_history.append((state.timestamp, state.daily_pnl))
        
        if state.total_equity > self._max_equity:
            self._max_equity = state.total_equity
        
        # 保持历史记录在合理范围
        if len(self.state_history) > 252:
            self.state_history = self.state_history[-252:]
        if len(self.daily_pnl_history) > 252:
            self.daily_pnl_history = self.daily_pnl_history[-252:]
    
    def _calc_current_drawdown(self, current_equity: float) -> float:
        """计算当前回撤"""
        if self._max_equity <= 0:
            return 0.0
        return (self._max_equity - current_equity) / self._max_equity
    
    def _calc_sector_exposure(self, positions: Dict[str, Dict]) -> Dict[str, float]:
        """计算板块敞口"""
        sector_val = defaultdict(float)
        total_val = 0.0
        for sym, pos in positions.items():
            val = pos.get("value", pos.get("shares", 0) * pos.get("price", 0))
            sector = SECTOR_MAP.get(sym, "其他")
            sector_val[sector] += val
            total_val += val
        
        if total_val > 0:
            return {s: v/total_val for s, v in sector_val.items()}
        return {}
    
    def _calc_correlated_exposure(self, positions: Dict[str, Dict]) -> float:
        """计算高相关组合敞口"""
        symbols = list(positions.keys())
        if len(symbols) < 2:
            return 0.0
        
        groups = self.correlation_tracker.get_highly_correlated_groups(symbols)
        max_group_exposure = 0.0
        total_val = sum(p.get("value", p.get("shares", 0) * p.get("price", 0)) for p in positions.values())
        
        for group in groups:
            group_val = sum(
                positions[s].get("value", positions[s].get("shares", 0) * positions[s].get("price", 0))
                for s in group
            )
            if total_val > 0:
                exposure = group_val / total_val
                max_group_exposure = max(max_group_exposure, exposure)
        
        return max_group_exposure
    
    def check_portfolio(self, state: PortfolioState) -> List[Dict]:
        """
        组合级风控检查（每日收盘后调用）
        
        Returns:
            违规列表，每项包含 {type, severity, message, current_value, limit_value}
        """
        violations = []
        positions = state.positions
        equity = state.total_equity
        
        # 1. 总回撤检查
        dd = self._calc_current_drawdown(equity)
        if dd > self.limits.max_total_drawdown:
            violations.append({
                "type": "TOTAL_DRAWDOWN",
                "severity": "CRITICAL",
                "message": f"总回撤 {dd:.2%} 超过限额 {self.limits.max_total_drawdown:.2%}",
                "current_value": dd,
                "limit_value": self.limits.max_total_drawdown,
            })
        
        # 2. 单日亏损检查
        if state.daily_pnl < 0 and abs(state.daily_pnl) / equity > self.limits.max_daily_loss:
            violations.append({
                "type": "DAILY_LOSS",
                "severity": "HIGH",
                "message": f"单日亏损 {abs(state.daily_pnl)/equity:.2%} 超过限额 {self.limits.max_daily_loss:.2%}",
                "current_value": abs(state.daily_pnl) / equity,
                "limit_value": self.limits.max_daily_loss,
            })
        
        # 3. 总敞口检查
        total_mv = sum(p.get("value", p.get("shares", 0) * p.get("price", 0)) for p in positions.values())
        exposure = total_mv / equity if equity > 0 else 0
        if exposure > self.limits.max_total_exposure:
            violations.append({
                "type": "TOTAL_EXPOSURE",
                "severity": "HIGH",
                "message": f"总敞口 {exposure:.2%} 超过限额 {self.limits.max_total_exposure:.2%}",
                "current_value": exposure,
                "limit_value": self.limits.max_total_exposure,
            })
        
        # 4. 单只股票集中度
        for sym, pos in positions.items():
            val = pos.get("value", pos.get("shares", 0) * pos.get("price", 0))
            pct = val / equity if equity > 0 else 0
            if pct > self.limits.max_single_stock:
                violations.append({
                    "type": "SINGLE_STOCK_CONCENTRATION",
                    "severity": "MEDIUM",
                    "message": f"{sym} 仓位 {pct:.2%} 超过单只限额 {self.limits.max_single_stock:.2%}",
                    "current_value": pct,
                    "limit_value": self.limits.max_single_stock,
                    "symbol": sym,
                })
        
        # 5. 板块集中度
        sector_exp = self._calc_sector_exposure(positions)
        for sector, exp in sector_exp.items():
            if exp > self.limits.max_sector_exposure:
                violations.append({
                    "type": "SECTOR_CONCENTRATION",
                    "severity": "HIGH",
                    "message": f"板块 {sector} 敞口 {exp:.2%} 超过限额 {self.limits.max_sector_exposure:.2%}",
                    "current_value": exp,
                    "limit_value": self.limits.max_sector_exposure,
                    "sector": sector,
                })
        
        # 6. 高相关组合敞口
        corr_exp = self._calc_correlated_exposure(positions)
        if corr_exp > self.limits.max_correlated_group:
            violations.append({
                "type": "CORRELATED_GROUP_EXPOSURE",
                "severity": "HIGH",
                "message": f"高相关组合敞口 {corr_exp:.2%} 超过限额 {self.limits.max_correlated_group:.2%}",
                "current_value": corr_exp,
                "limit_value": self.limits.max_correlated_group,
            })
        
        return violations
    
    def pre_trade_check(
        self,
        symbol: str,
        side: str,  # "BUY" / "SELL"
        qty: int,
        price: float,
        state: PortfolioState
    ) -> Tuple[bool, str]:
        """
        下单前风控预检
        
        Returns:
            (是否通过, 拦截原因)
        """
        positions = state.positions
        equity = state.total_equity
        trade_val = qty * price
        
        # 买入检查
        if side == "BUY":
            # 1. 单只限额
            current_val = positions.get(symbol, {}).get("value", 0)
            new_pct = (current_val + trade_val) / equity if equity > 0 else 1
            if new_pct > self.limits.max_single_stock:
                return False, f"单只限额: {symbol} 买入后将达 {new_pct:.2%} > {self.limits.max_single_stock:.2%}"
            
            # 2. 板块限额
            sector = SECTOR_MAP.get(symbol, "其他")
            sector_val = sum(
                p.get("value", p.get("shares", 0) * p.get("price", 0))
                for s, p in positions.items()
                if SECTOR_MAP.get(s, "其他") == sector
            )
            new_sector_pct = (sector_val + trade_val) / equity if equity > 0 else 1
            if new_sector_pct > self.limits.max_sector_exposure:
                return False, f"板块限额: {sector} 买入后将达 {new_sector_pct:.2%} > {self.limits.max_sector_exposure:.2%}"
            
            # 3. 高相关组合限额
            corr_symbols = [s for s in positions if self.correlation_tracker.get_correlation(symbol, s) > self.limits.correlation_threshold]
            corr_val = sum(positions[s].get("value", 0) for s in corr_symbols)
            new_corr_pct = (corr_val + trade_val) / equity if equity > 0 else 1
            if new_corr_pct > self.limits.max_correlated_group:
                return False, f"高相关组合限额: 买入后高相关组合将达 {new_corr_pct:.2%} > {self.limits.max_correlated_group:.2%}"
            
            # 4. 总敞口
            total_mv = sum(p.get("value", p.get("shares", 0) * p.get("price", 0)) for p in positions.values())
            new_exposure = (total_mv + trade_val) / equity if equity > 0 else 1
            if new_exposure > self.limits.max_total_exposure:
                return False, f"总敞口限额: 买入后将达 {new_exposure:.2%} > {self.limits.max_total_exposure:.2%}"
            
            # 5. 单日亏损保护（如果今日已亏损接近限额，限制加仓）
            if self.daily_pnl_history:
                today = datetime.now().date()
                today_pnl = sum(pnl for dt, pnl in self.daily_pnl_history if dt.date() == today)
                if today_pnl < -equity * self.limits.max_daily_loss * 0.8:
                    return False, f"单日亏损接近限额，暂停新开仓"
        
        # 卖出检查
        elif side == "SELL":
            current_shares = positions.get(symbol, {}).get("shares", 0)
            if qty > current_shares:
                return False, f"可卖股份不足: {symbol} 仅持有 {current_shares} 股"
        
        return True, "通过"
    
    def get_portfolio_metrics(self, state: PortfolioState) -> Dict:
        """获取组合风控指标仪表盘"""
        positions = state.positions
        equity = state.total_equity
        
        total_mv = sum(p.get("value", p.get("shares", 0) * p.get("price", 0)) for p in positions.values())
        cash = equity - total_mv
        
        dd = self._calc_current_drawdown(equity)
        sector_exp = self._calc_sector_exposure(positions)
        corr_exp = self._calc_correlated_exposure(positions)
        
        return {
            "timestamp": datetime.now().isoformat(),
            "equity": equity,
            "cash": cash,
            "total_market_value": total_mv,
            "exposure_pct": total_mv / equity if equity > 0 else 0,
            "drawdown_pct": dd,
            "max_drawdown_limit": self.limits.max_total_drawdown,
            "daily_pnl": state.daily_pnl,
            "daily_pnl_limit": self.limits.max_daily_loss,
            "sector_exposure": sector_exp,
            "max_sector_exposure": max(sector_exp.values()) if sector_exp else 0,
            "sector_limit": self.limits.max_sector_exposure,
            "correlated_group_exposure": corr_exp,
            "correlated_limit": self.limits.max_correlated_group,
            "max_single_stock": max(
                (p.get("value", p.get("shares", 0) * p.get("price", 0)) / equity 
                 for p in positions.values() if equity > 0),
                default=0
            ),
            "single_stock_limit": self.limits.max_single_stock,
            "drawdown_usage": dd / self.limits.max_total_drawdown if self.limits.max_total_drawdown > 0 else 0,
            "exposure_usage": (total_mv / equity) / self.limits.max_total_exposure if equity > 0 and self.limits.max_total_exposure > 0 else 0,
        }
    
    def should_force_liquidate(self, state: PortfolioState) -> Tuple[bool, str]:
        """是否触发强制平仓"""
        violations = self.check_portfolio(state)
        
        critical = [v for v in violations if v["severity"] == "CRITICAL"]
        if critical:
            self._breach_triggered = True
            return True, f"触发强制平仓: {', '.join(v['message'] for v in critical)}"
        
        # 移动止损
        if state.total_equity > 0 and self._max_equity > 0:
            dd = (self._max_equity - state.total_equity) / self._max_equity
            if dd > self.limits.trailing_stop and self._breach_triggered:
                return True, f"移动止损触发: 回撤 {dd:.2%} > {self.limits.trailing_stop:.2%}"
        
        return False, ""
    
    def save_state(self, filepath: str = None):
        """保存风控状态到文件"""
        if filepath is None:
            filepath = os.path.join(DATA_DIR, "portfolio_risk_state.json")
        
        data = {
            "limits": {
                "max_total_drawdown": self.limits.max_total_drawdown,
                "max_daily_loss": self.limits.max_daily_loss,
                "max_total_exposure": self.limits.max_total_exposure,
                "max_sector_exposure": self.limits.max_sector_exposure,
                "max_single_stock": self.limits.max_single_stock,
                "max_correlated_group": self.limits.max_correlated_group,
                "correlation_threshold": self.limits.correlation_threshold,
                "portfolio_stop_loss": self.limits.portfolio_stop_loss,
                "trailing_stop": self.limits.trailing_stop,
            },
            "max_equity": self._max_equity,
            "breach_triggered": self._breach_triggered,
            "timestamp": datetime.now().isoformat(),
        }
        
        with open(filepath, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def load_state(cls, filepath: str = None) -> "PortfolioRiskManager":
        """从文件加载风控状态"""
        if filepath is None:
            filepath = os.path.join(DATA_DIR, "portfolio_risk_state.json")
        
        mgr = cls()
        if os.path.exists(filepath):
            with open(filepath) as f:
                data = json.load(f)
            
            mgr.limits = RiskLimit(
                max_total_drawdown=data["limits"].get("max_total_drawdown", 0.15),
                max_daily_loss=data["limits"].get("max_daily_loss", 0.03),
                max_total_exposure=data["limits"].get("max_total_exposure", 1.0),
                max_sector_exposure=data["limits"].get("max_sector_exposure", 0.30),
                max_single_stock=data["limits"].get("max_single_stock", 0.10),
                max_correlated_group=data["limits"].get("max_correlated_group", 0.40),
                correlation_threshold=data["limits"].get("correlation_threshold", 0.7),
                portfolio_stop_loss=data["limits"].get("portfolio_stop_loss", 0.10),
                trailing_stop=data["limits"].get("trailing_stop", 0.05),
            )
            mgr._max_equity = data.get("max_equity", 0.0)
            mgr._breach_triggered = data.get("breach_triggered", False)
            mgr.correlation_tracker.lookback = mgr.limits.lookback_days
            mgr.correlation_tracker.threshold = mgr.limits.correlation_threshold
        
        return mgr


# ── 便捷函数 ──

def create_portfolio_state(
    positions: Dict[str, Dict],
    cash: float,
    timestamp: datetime = None
) -> PortfolioState:
    """从持仓字典创建组合状态"""
    if timestamp is None:
        timestamp = datetime.now()
    
    total_mv = sum(
        p.get("value", p.get("shares", 0) * p.get("price", 0))
        for p in positions.values()
    )
    
    # 补充板块信息
    enriched = {}
    for sym, p in positions.items():
        enriched[sym] = {
            **p,
            "value": p.get("value", p.get("shares", 0) * p.get("price", 0)),
            "sector": SECTOR_MAP.get(sym, "其他"),
        }
    
    return PortfolioState(
        timestamp=timestamp,
        total_equity=sum(p.get("value", p.get("shares", 0) * p.get("price", 0)) for p in positions.values()) + cash,
        cash=cash,
        positions=enriched,
    )


# ── 使用示例 ──

if __name__ == "__main__":
    # 创建风控管理器
    limits = RiskLimit(
        max_total_drawdown=0.15,
        max_daily_loss=0.03,
        max_sector_exposure=0.30,
        max_single_stock=0.10,
        max_correlated_group=0.40,
    )
    mgr = PortfolioRiskManager(limits)
    
    # 模拟组合状态
    positions = {
        "600030": {"shares": 10000, "price": 27.0, "value": 270000},
        "600584": {"shares": 2000, "price": 68.0, "value": 136000},
        "688981": {"shares": 1000, "price": 350.0, "value": 350000},
    }
    
    state = create_portfolio_state(positions, cash=500000)
    
    # 更新相关性（需历史价格，此处跳过）
    
    # 检查风控
    violations = mgr.check_portfolio(state)
    print("风控检查结果:")
    for v in violations:
        print(f"  [{v['severity']}] {v['type']}: {v['message']}")
    
    # 预交易检查
    ok, reason = mgr.pre_trade_check("600030", "BUY", 10000, 27.0, state)
    print(f"\n买入预检: {'通过' if ok else '拦截'} - {reason}")
    
    # 获取仪表盘指标
    metrics = mgr.get_portfolio_metrics(state)
    print("\n组合风控指标:")
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")