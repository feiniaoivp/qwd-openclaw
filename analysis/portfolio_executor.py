#!/usr/bin/env python3
"""
Portfolio Executor - 连接 Portfolio Core 与 ExecutionPort
=========================================================

核心功能：
- 将 portfolio_core.py 的信号/风控/仓位管理逻辑复用
- 通过 ExecutionPort 抽象层实现：模拟盘/实盘/回测 三模式一致
- 保持核心逻辑（策略切换强平、重复BUY钝化、030风控、ATR止损、斐波止盈、哑铃仓位、030仓位管理）不变
- 仅执行层可替换

使用示例：
    # 模拟盘
    port = SimulationPort(initial_capital=100_000)
    executor = PortfolioExecutor(port, stocks=STOCKS)
    result = executor.run_daily_scan()

    # 实盘
    port = LivePort(broker="yigui", api_key="xxx")
    executor = PortfolioExecutor(port, stocks=STOCKS)
    result = executor.run_daily_scan()

    # 回测
    port = BacktestPort(price_data=price_data, initial_capital=100_000)
    executor = PortfolioExecutor(port, stocks=STOCKS)
    result = executor.run_full_backtest()
"""

import os
import sys
import json
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
sys.path.insert(0, WORKSPACE)

# 导入 ExecutionPort 抽象层
from analysis.execution_port import (
    ExecutionPort, SimulationPort, LivePort, BacktestPort,
    Position, AccountInfo,
    create_port,
)

# 导入 portfolio_core 的核心逻辑（复用，不复制）
from analysis.portfolio_core import (
    STRATEGY_LABELS, COMMISSION, SLIPPAGE, INITIAL_CAPITAL, ATR_STOP_MULT,
    STOCKS, SIGNAL_FUNCS,
    load_state, save_state, load_strategy_map,
    init_position, sync_strategy_map, compute_fib_targets,
    _compute_atr_stop, classify_bucket,
    RiskGuard, append_trade, append_equity_snapshot,
    calc_position_value, execute_trade, _check_barbell_constraints,
    generate_human_report,
) 
# 权威扫描实现（唯一事实来源）
from analysis.portfolio_core import run_portfolio_scan as _core_run_portfolio_scan


@dataclass
class ScanResult:
    """单日扫描结果"""
    date: str
    stock_count: int
    portfolio: Dict
    errors: List[str]
    details: List[Dict]
    messages: List[str]
    state: Dict


class PortfolioExecutor:
    """
    组合执行器 - 统一入口
    
    核心逻辑完全复用 portfolio_core.py，仅通过 ExecutionPort 替换执行层。
    支持三种模式：
    - mode="sim": 模拟盘（默认）
    - mode="live": 实盘
    - mode="backtest": 回测
    """
    
    def __init__(
        self,
        execution_port: ExecutionPort,
        stocks: List[Tuple[str, str]] = None,
        strategy_map: Dict[str, str] = None,
        default_strategy: str = "ema_cross",
        state_file: str = None,
    ):
        self.port = execution_port
        self.stocks = stocks or STOCKS
        self.strategy_map = strategy_map or load_strategy_map()
        self.default_strategy = default_strategy
        self.state_file = state_file or os.path.join(WORKSPACE, "data", "portfolio_sim_state.json")

        # 回测模式：使用独立的内存 state，绝不读写生产 state 文件
        # （资金桶初始化在 _run_backtest_inner 中按标的逐个完成，此处只建空壳）
        self._is_backtest = execution_port.__class__.__name__ == "BacktestPort"
        if self._is_backtest:
            self.state = {"last_signal_date": None, "positions": {}}
        else:
            self.state = load_state() if os.path.exists(self.state_file) \
                else {"last_signal_date": None, "positions": {}}

        # 同步执行端口的初始状态
        self._sync_port_state()
    
    def _sync_port_state(self):
        """将内部状态同步到执行端口"""
        if hasattr(self.port, 'set_initial_state'):
            from analysis.execution_port import Position
            positions_dict = self.state.get("positions", {})
            # 转换 dict 为 Position 对象
            positions = {}
            for sym, p in positions_dict.items():
                if isinstance(p, dict):
                    pos = Position(
                        symbol=sym,
                        quantity=p.get("shares", 0),
                        avg_cost=p.get("entry_price", 0),
                        market_value=p.get("shares", 0) * p.get("entry_price", 0),
                        unrealized_pnl=0,
                        realized_pnl=p.get("total_pl", 0),
                        entry_date=datetime.fromisoformat(p["entry_date"]) if p.get("entry_date") else None,
                        strategy=p.get("strategy", ""),
                        bucket=p.get("bucket", "satellite"),
                    )
                else:
                    pos = p
                positions[sym] = pos
            
            cash = sum(p.get("cash", 0) for p in positions_dict.values())
            if not positions:
                cash = INITIAL_CAPITAL * len(self.stocks)
            self.port.set_initial_state(positions, cash)
    
    def _load_price_data(self, symbols: List[str], start: str = "20250101") -> Dict[str, pd.DataFrame]:
        """加载历史价格数据"""
        from analysis.data_layer import get_router
        router = get_router()
        price_data = {}
        
        for sym, name in self.stocks:
            if sym in symbols:
                df = router.get_daily(sym, name, start_date=start)
                if df is not None and len(df) >= 60:
                    price_data[sym] = df.set_index("date")
        return price_data
    
    def run_daily_scan(self, today_str: str = None, fetch_data_func: Callable = None) -> ScanResult:
        """
        运行每日扫描（模拟盘/实盘模式）

        单一事实来源：直接委托 portfolio_core.run_portfolio_scan（权威信号/风控/仓位逻辑）。
        本层不再重复实现扫描逻辑，仅在其后把成交镜像到 ExecutionPort（供实盘对接）。
        """
        if today_str is None:
            today_str = datetime.now().strftime("%Y-%m-%d")

        if fetch_data_func is None:
            fetch_data_func = self._default_fetch_data

        # 权威扫描（唯一实现）
        raw = _core_run_portfolio_scan(
            stocks=self.stocks,
            fetch_data_func=fetch_data_func,
            today_str=today_str,
            state=self.state,
            strategy_map=self.strategy_map,
            default_strategy=self.default_strategy,
        )
        self.state = raw.get("state", self.state)

        # 将本次成交镜像到 ExecutionPort（模拟盘为 no-op 记录，实盘为下单）
        self._mirror_trades_to_port(raw)

        return ScanResult(
            date=raw["date"],
            stock_count=raw["stock_count"],
            portfolio=raw["portfolio"],
            errors=raw["errors"],
            details=raw["details"],
            messages=raw["messages"],
            state=raw["state"],
        )

    def _default_fetch_data(self, symbol: str, start: str = "20250101"):
        """默认数据获取：走 DataRouter"""
        from analysis.data_layer import get_router
        df = get_router().get_daily(symbol, "", start_date=start)
        return df

    def _mirror_trades_to_port(self, raw: Dict) -> None:
        """把权威扫描产生的成交镜像到执行端口。

        仅用于实盘对接；模拟盘下本地状态已是权威源，此处只做订单记录，
        避免出现第二套持仓/现金账本（历史 bug：双账本口径互相打架）。
        """
        for msg in raw.get("messages", []):
            if "买入" not in msg and "卖出" not in msg:
                continue
            # 仅登记，不在 port 内重新计算持仓（避免重复扣款/双账本）
            try:
                self.port.record_external_trade(msg)
            except AttributeError:
                pass  # 端口未实现该方法时忽略（Simulation/Backtest 无需）

    def _precompute_signals(self, price_data: Dict[str, pd.DataFrame],
                            trading_days: List, symbols: List[str]) -> Dict[str, Dict[str, Dict]]:
        """
        逐窗信号预计算。

        说明：策略函数 (SIGNAL_FUNCS) 的契约是「接收一段历史 df，返回最后一根 K 线的
        信号」，即天然是「滚动窗口」语义，无法用纯 pandas 全序列向量化一次性改写。
        因此这里采用「每根 K 线切片 + 策略调用」的方式；为保证回测可跑完，
        限制到最近 MEMORY_WINDOW 根 K 线即可（历史信号对当日决策无影响）。

        返回: {symbol: {date_str: signal_dict}}
        """
        MEMORY_WINDOW = 260  # 滑动窗口上限（约1年），保证指标预热充分且不 O(n^2)
        all_signals: Dict[str, Dict[str, Dict]] = {}
        for sym in symbols:
            df = price_data.get(sym)
            if df is None or len(df) < 60:
                continue
            strat = self.strategy_map.get(sym, self.default_strategy)
            sig_func = SIGNAL_FUNCS.get(strat, SIGNAL_FUNCS["ema_cross"])

            sym_days = [d for d in trading_days if d in df.index]
            if len(sym_days) < 2:
                continue

            # 预先把 df 转成 numpy 数组，避免每根 K 线都走 pandas 切片/索引开销
            prev_idx = None
            sig_cache: Dict[str, Dict] = {}
            df_pos = {d: i for i, d in enumerate(df.index)}
            for day in sym_days:
                end_pos = df_pos[day] + 1
                start_pos = max(0, end_pos - MEMORY_WINDOW)
                if end_pos - start_pos < 60:
                    continue
                window = df.iloc[start_pos:end_pos]
                try:
                    sig_cache[day.strftime("%Y-%m-%d")] = sig_func(window)
                except Exception:
                    pass
            all_signals[sym] = sig_cache
        return all_signals

    def run_backtest(
        self,
        start_date: str = "20240101",
        end_date: str = None,
    ) -> Dict:
        """
        运行完整回测（信号预计算 + 逐日驱动，复用 portfolio_core 核心逻辑）

        资金口径：与 portfolio_sim 的 per-stock bucket 模型一致
        （每只股票独立 INITIAL_CAPITAL 现金桶），BACKTEST_MODE=True 抑制落盘副作用。
        """
        if end_date is None:
            end_date = datetime.now().strftime("%Y%m%d")

        print(f"🚀 回测开始 | {start_date} ~ {end_date} | {len(self.stocks)} 只股票")

        # 0. 回测模式：抑制 append_trade / append_equity_snapshot 落盘副作用
        import analysis.portfolio_core as _core
        _prev_backtest = getattr(_core, "BACKTEST_MODE", False)
        _core.BACKTEST_MODE = True
        try:
            return self._run_backtest_inner(start_date, end_date)
        finally:
            _core.BACKTEST_MODE = _prev_backtest

    def _run_backtest_inner(self, start_date: str, end_date: str) -> Dict:
        # 1. 加载所有价格数据（一次性）
        price_data = self._load_price_data([s for s, _ in self.stocks], start=start_date)
        if not price_data:
            return {"error": "价格数据加载失败"}

        symbols = [s for s, _ in self.stocks]

        # 2. 预计算所有交易日
        all_dates = set()
        for df in price_data.values():
            all_dates.update(df.index)
        trading_days = sorted([d for d in all_dates
                               if pd.Timestamp(start_date) <= d <= pd.Timestamp(end_date)
                               and d.weekday() < 5])

        print(f"📅 共 {len(trading_days)} 个交易日")

        # 3. 初始化持仓状态 - 每只股票独立资金桶，各 INITIAL_CAPITAL
        for sym, name in self.stocks:
            pos = init_position(sym, name,
                                self.strategy_map.get(sym, self.default_strategy))
            pos["cash"] = INITIAL_CAPITAL
            pos["position"] = False
            pos["shares"] = 0
            pos["entry_price"] = 0
            pos["entry_date"] = None
            pos["total_pl"] = 0.0
            pos["trade_count"] = 0
            pos["_buy_streak"] = 0
            pos["_stop_cooldown_days"] = 0
            self.state["positions"][sym] = pos
        self.state["last_signal_date"] = None

        # 4. 预计算所有股票所有交易日的信号
        print("🔄 预计算信号...")
        all_signals = self._precompute_signals(price_data, trading_days, symbols)
        print(f"✅ 信号预计算完成，覆盖 {len(all_signals)} 只股票")
        
        # 5. 创建 BacktestPort（仅用于撮合）
        backtest_port = BacktestPort(
            initial_capital=INITIAL_CAPITAL * len(self.stocks),
            commission=COMMISSION,
            slippage=SLIPPAGE,
            price_data=price_data,
        )
        
        # 6. 初始化风控缓存（只在周一刷新一次）
        last_risk_refresh = None
        cached_fatal_risk = {"fatal_triggered": False, "high_risk_triggered": False}
        cached_position_plan = {
            "buy_signal_multiplier": 1.0, "single_stock_max_pct": 0.05,
            "total_limit_pct": 0.5, "total_limit_amount": 1_500_000,
            "direction_allocation": {},
        }
        
        # 7. 逐日驱动（使用预计算的信号）
        daily_equity: List[Dict] = []
        trade_log: List[Dict] = []
        for i, day in enumerate(trading_days):
            day_str = day.strftime("%Y-%m-%d")
            
            # 回测风控：历史日期无法真实复现当时的 030 市场环境，
            # 因此整个回测只刷新一次（用当下风控状态作为常量上下文），
            # 避免逐周网络调用（252 交易日 × 3s ≈ 每只股票数分钟）。
            if last_risk_refresh is None:
                RiskGuard.refresh(cache_key=f"backtest:{start_date}")
                cached_fatal_risk = RiskGuard.get_fatal_risk()
                cached_position_plan = RiskGuard.get_position_plan()
                last_risk_refresh = day
            
            # 同步策略映射
            sync_strategy_map(self.state, self.strategy_map, self.default_strategy)
            
            all_msgs = []
            trade_count = 0
            errors = []
            results = []
            
            for sym, name in self.stocks:
                pos = self.state["positions"].get(sym)
                if pos is None:
                    pos = init_position(sym, name, self.strategy_map.get(sym, self.default_strategy))
                    self.state["positions"][sym] = pos
                pos["_symbol"] = sym
                
                # 每日递减止损冷却期
                RiskGuard.decay_stop_cooldown(pos)
                
                strat = pos["strategy"]
                slabel = STRATEGY_LABELS.get(strat, strat)
                
                # 获取预计算的信号
                sig_cache = all_signals.get(sym, {})
                signal = sig_cache.get(day_str)
                
                if signal is None:
                    # 信号未预计算，实时计算（兜底）
                    df = price_data.get(sym)
                    if df is not None and day in df.index:
                        day_df = df[df.index <= day]
                        if len(day_df) >= 60:
                            try:
                                sig_func = SIGNAL_FUNCS.get(strat, SIGNAL_FUNCS["ema_cross"])
                                signal = sig_func(day_df)
                            except Exception:
                                signal = None
                
                if signal is None:
                    errors.append(f"{name}({sym}) 信号缺失")
                    results.append({"symbol": sym, "name": name, "error": "信号缺失",
                                    "position": pos["position"], "strategy": slabel})
                    continue
                
                # 030 风控覆盖（使用缓存）
                if cached_fatal_risk.get("fatal_triggered", False):
                    if pos.get("position"):
                        signal["action"] = "卖出"
                        signal["reason"] = "☠️ 030致命风险触发：强制清仓"
                    else:
                        signal["action"] = "持有"
                        signal["reason"] = "☠️ 030致命风险触发：禁止买入"
                elif cached_fatal_risk.get("high_risk_triggered", False):
                    if pos.get("position"):
                        signal["action"] = "卖出"
                        signal["reason"] = "🟡 030高风险防御：核心中军≤-3%，减仓止损"
                    else:
                        signal["action"] = "持有"
                        signal["reason"] = "🟡 030高风险防御：不开新仓"
                
                # 策略切换强平
                signal = RiskGuard.check_strategy_switch(signal, pos)
                # 重复 BUY 钝化
                signal, should_exec = RiskGuard.apply_buy_streak_dampening(signal, pos)
                # 去重
                if not RiskGuard.check_dedup(signal, pos, day_str):
                    should_exec = False
                
                # 仅新交易日执行（首个交易日也允许执行）
                last_sig_date = self.state.get("last_signal_date")
                is_new_day = last_sig_date is None or day_str != last_sig_date
                if should_exec and is_new_day:
                    # 哑铃策略硬约束检查
                    ok, reason = _check_barbell_constraints(pos, signal, cached_position_plan, self.state)
                    if not ok:
                        signal["action"] = "持有"
                        signal["reason"] = f"{signal.get('reason', '')} | {reason}"
                        should_exec = False
                    
                    if should_exec:
                        action, msg = execute_trade(
                            pos, signal, day_str, strategy=strat, position_plan=cached_position_plan
                        )
                        if action:
                            trade_count += 1
                            all_msgs.append(msg)
                            # 注意：回测不重复提交到 BacktestPort ——
                            # execute_trade 已修改权威状态（per-stock cash 桶）。
                            # BacktestPort 仅供实盘级撮合实验，不参与回测口径。
                            if action == "BUY":
                                trade_log.append({
                                    "date": day_str, "symbol": sym, "name": name,
                                    "action": "BUY", "price": signal["price"],
                                    "shares": pos["shares"], "pnl": 0.0,
                                    "strategy": strat, "bucket": pos.get("bucket", "satellite"),
                                })
                            elif action == "SELL":
                                # 从 state 的 total_pl 推断不出单笔盈亏，用成本与卖价复算
                                shares = pos.get("_last_sell_shares", 0)
                                entry = pos.get("_last_sell_entry", 0)
                                px = signal["price"]
                                gross = shares * px * (1 - SLIPPAGE)
                                cost = shares * entry * (1 + SLIPPAGE)
                                pnl = gross - gross * COMMISSION - cost
                                trade_log.append({
                                    "date": day_str, "symbol": sym, "name": name,
                                    "action": "SELL", "price": px,
                                    "shares": shares, "pnl": round(pnl, 2),
                                    "strategy": strat, "bucket": pos.get("bucket", "satellite"),
                                })
                
                # 计算持仓价值
                pos_val = calc_position_value(pos, signal["price"])
                pos_pl = pos["total_pl"]
                if pos.get("position"):
                    pos_pl += (pos["shares"] * signal["price"]) - (pos["shares"] * pos["entry_price"] * (1 + SLIPPAGE))
                pos_return = round((pos_val / INITIAL_CAPITAL - 1) * 100, 2)
                pos_pl_round = round(pos_pl, 2)
                
                bucket = pos.get("bucket", "unknown")
                if bucket == "unknown":
                    bucket, _, _ = classify_bucket(sym)
                    pos["bucket"] = bucket
                
                results.append({
                    "symbol": sym, "name": name,
                    "strategy": slabel,
                    "price": signal["price"],
                    "position": pos["position"],
                    "entry_price": pos["entry_price"] if pos["position"] else 0,
                    "entry_date": pos["entry_date"],
                    "shares": pos["shares"],
                    "cash": round(pos["cash"], 2),
                    "value": round(pos_val, 2),
                    "unrealized_pl": round(pos_pl_round, 2),
                    "return_pct": pos_return,
                    "trade_count": pos["trade_count"],
                    "signal": signal["action"],
                    "reason": signal["reason"],
                    "fib_targets": pos.get("fib_targets", {}),
                    "atr_stop": signal.get("atr_stop"),
                    "fatal_risk_override": cached_fatal_risk.get("fatal_triggered", False) or cached_fatal_risk.get("high_risk_triggered", False),
                    "position_mult": cached_position_plan.get("buy_signal_multiplier", 1.0),
                    "max_buy_amount": round(cached_position_plan.get("single_stock_max_amount", 100_000) * cached_position_plan.get("buy_signal_multiplier", 1.0), 2),
                    "bucket": bucket,
                })
            
            self.state["last_signal_date"] = day_str
            save_state(self.state)
            
            # 进度日志（每 100 天或最后一天）
            # 权益口径：直接由权威持仓状态计算（cash + shares×price），
            # 不依赖 results（信号缺失的标的不会出现在 results 里，
            # 若用它求和会把现金漏计成 0，导致日权益序列失真）。
            total_value = 0.0
            for _sym, _pos in self.state["positions"].items():
                _df = price_data.get(_sym)
                if _df is not None and day in _df.index:
                    _px = float(_df.loc[day, "close"])
                else:
                    _px = float(_pos.get("entry_price", 0) or 0)
                total_value += calc_position_value(_pos, _px)
            total_initial = INITIAL_CAPITAL * len(self.stocks)
            ret = (total_value - total_initial) / total_initial * 100
            pos_cnt = sum(1 for p in self.state["positions"].values() if p.get("position"))
            daily_equity.append({
                "date": day_str,
                "total_value": round(total_value, 2),
                "return_pct": round(ret, 4),
                "positions_held": pos_cnt,
            })
            if i % 100 == 0 or i == len(trading_days) - 1:
                print(f"  [{i+1}/{len(trading_days)}] {day_str} | 权益: ¥{total_value:,.0f} ({ret:+.2f}%) | 持仓: {pos_cnt}")

        # 8. 最终结果 - 使用 executor 状态计算权益
        final_equity = 0
        for sym, name in self.stocks:
            pos = self.state["positions"].get(sym)
            if pos:
                # 获取最后一天的收盘价
                df = price_data.get(sym)
                if df is not None and len(df) > 0:
                    last_close = float(df["close"].iloc[-1])
                    final_equity += calc_position_value(pos, last_close)
                else:
                    final_equity += pos.get("cash", 0)

        total_initial = INITIAL_CAPITAL * len(self.stocks)
        total_return = (final_equity - total_initial) / total_initial * 100

        # ── 绩效统计 ──
        sell_trades = [t for t in trade_log if t["action"] == "SELL"]
        win_trades = len([t for t in sell_trades if t.get("pnl", 0) > 0])
        closed = len(sell_trades)
        stats = {
            "total_trades": len(trade_log),
            "closed_trades": closed,
            "win_trades": win_trades,
            "win_rate_pct": round(win_trades / closed * 100, 2) if closed else 0.0,
            "total_realized_pnl": round(sum(t.get("pnl", 0) for t in sell_trades), 2),
            "max_drawdown_pct": self._calc_max_drawdown(daily_equity),
            "avg_equity": round(
                sum(d["total_value"] for d in daily_equity) / len(daily_equity), 2
            ) if daily_equity else 0.0,
        }

        print(f"✅ 回测完成 | 最终权益: ¥{final_equity:,.2f} | 总收益: {total_return:.2f}%")
        print(f"   交易: 总{stats['total_trades']}笔 / 平仓{stats['closed_trades']}笔 / "
              f"胜率{stats['win_rate_pct']}% / 已实现盈亏¥{stats['total_realized_pnl']:,.2f} / "
              f"最大回撤-{stats['max_drawdown_pct']}%")

        return {
            "final_equity": round(final_equity, 2),
            "total_return": round(total_return, 2),
            "daily_results": daily_equity,
            "trades": trade_log,
            **stats,
        }
    
    def _calc_max_drawdown(self, equity_series: List[Dict]) -> float:
        """最大回撤（%），基于逐日权益序列"""
        if not equity_series:
            return 0.0
        peak = 0.0
        max_dd = 0.0
        for row in equity_series:
            val = row.get("total_value", 0)
            if val > peak:
                peak = val
            if peak > 0:
                dd = (peak - val) / peak * 100
                if dd > max_dd:
                    max_dd = dd
        return round(max_dd, 2)

    def get_positions_summary(self) -> Dict:
        """获取当前持仓摘要"""
        positions = self.port.get_positions()
        account = self.port.get_account()
        
        return {
            "account": {
                "total_equity": account.total_equity,
                "cash": account.cash,
                "market_value": account.market_value,
                "buying_power": account.buying_power,
            },
            "positions": {
                sym: {
                    "quantity": pos.quantity,
                    "avg_cost": pos.avg_cost,
                    "market_value": pos.market_value,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "realized_pnl": pos.realized_pnl,
                    "strategy": pos.strategy,
                    "bucket": pos.bucket,
                }
                for sym, pos in positions.items()
            },
        }


def create_executor(
    mode: str = "sim",
    stocks: List[Tuple[str, str]] = None,
    **kwargs
) -> PortfolioExecutor:
    """
    工厂函数：创建执行器
    
    Args:
        mode: "sim" | "live" | "backtest"
        stocks: 股票列表
        **kwargs: 传给 ExecutionPort 的参数
    
    Returns:
        PortfolioExecutor 实例
    """
    port = create_port(mode, **kwargs)
    return PortfolioExecutor(port, stocks=stocks)


# ── 兼容旧接口 ──

if __name__ == "__main__":
    # 简单自测
    print("🧪 测试 PortfolioExecutor (模拟盘模式)...")
    executor = create_executor("sim")
    result = executor.run_daily_scan()
    print(generate_human_report({
        "date": result.date,
        "portfolio": result.portfolio,
        "details": result.details,
        "messages": result.messages,
        "errors": result.errors,
    }))