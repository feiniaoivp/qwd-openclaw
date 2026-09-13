#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ExecutionPort - 统一执行接口抽象层
===================================
定义标准化的执行接口，使 portfolio_core.py 的核心逻辑能同时复用于：
- 模拟盘 (SimulationPort) - 当前 portfolio_sim.py 的行为
- 实盘 (LivePort) - 对接券商 API (易柜/佣金宝/富途/同花顺等)
- 回测 (BacktestPort) - 向量化回测引擎，用于快速策略验证

核心原则：策略信号生成、风控、仓位管理完全复用，仅执行层可替换。
"""

from __future__ import annotations

import abc
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
DATA_DIR = os.path.join(WORKSPACE, "data")


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    """标准订单结构"""
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    strategy: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    """标准持仓结构"""
    symbol: str
    quantity: int = 0
    avg_cost: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    entry_date: Optional[datetime] = None
    strategy: str = ""
    bucket: str = "satellite"  # core / satellite


@dataclass
class AccountInfo:
    """账户信息"""
    total_equity: float = 0.0
    cash: float = 0.0
    market_value: float = 0.0
    buying_power: float = 0.0
    total_pnl: float = 0.0
    daily_pnl: float = 0.0


class ExecutionPort(abc.ABC):
    """执行端口抽象基类 - 定义标准执行接口"""
    
    @abc.abstractmethod
    def connect(self) -> bool:
        """建立连接"""
        pass
    
    @abc.abstractmethod
    def disconnect(self) -> None:
        """断开连接"""
        pass
    
    @abc.abstractmethod
    def is_connected(self) -> bool:
        """检查连接状态"""
        pass
    
    @abc.abstractmethod
    def get_account(self) -> AccountInfo:
        """获取账户信息"""
        pass
    
    @abc.abstractmethod
    def get_positions(self) -> Dict[str, Position]:
        """获取当前持仓"""
        pass
    
    @abc.abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """获取单只股票持仓"""
        pass
    
    @abc.abstractmethod
    def submit_order(self, order: Order) -> Order:
        """提交订单，返回更新后的订单对象"""
        pass
    
    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        pass
    
    @abc.abstractmethod
    def get_order(self, order_id: str) -> Optional[Order]:
        """查询订单状态"""
        pass
    
    @abc.abstractmethod
    def get_open_orders(self) -> List[Order]:
        """获取所有未完成订单"""
        pass
    
    @abc.abstractmethod
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict]:
        """获取实时行情"""
        pass
    
    @abc.abstractmethod
    def get_history(self, symbol: str, start: str, end: str, 
                    freq: str = "1d") -> List[Dict]:
        """获取历史数据"""
        pass

    def record_external_trade(self, message: str) -> None:
        """记录由外部权威逻辑 (portfolio_core) 产生的成交。

        默认 no-op；LivePort 应覆写此方法把成交同步到券商日账。
        设计意图：持仓/现金的权威账本只有一份（portfolio_core state），
        执行端口不得自行重算，避免双账本口径分裂。
        """
        return None


class SimulationPort(ExecutionPort):
    """模拟盘执行端口 - 复用 portfolio_sim.py 逻辑"""
    
    def __init__(self, initial_capital: float = 100_000, 
                 commission: float = 0.0003, slippage: float = 0.001):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self._connected = False
        self._orders: Dict[str, Order] = {}
        self._positions: Dict[str, Position] = {}
        self._account = AccountInfo(
            total_equity=initial_capital,
            cash=initial_capital,
            market_value=0.0,
            buying_power=initial_capital
        )
        self._order_counter = 0
        self._price_cache: Dict[str, Dict] = {}
    
    def connect(self) -> bool:
        self._connected = True
        return True
    
    def disconnect(self) -> None:
        self._connected = False
    
    def is_connected(self) -> bool:
        return self._connected
    
    def get_account(self) -> AccountInfo:
        mv = sum(p.market_value for p in self._positions.values())
        self._account.market_value = mv
        self._account.total_equity = self._account.cash + mv
        self._account.buying_power = self._account.cash
        return self._account
    
    def get_positions(self) -> Dict[str, Position]:
        return self._positions.copy()
    
    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)
    
    def _get_price(self, symbol: str) -> float:
        """获取当前价格（模拟用最后收盘价）"""
        # 这里简化处理，实际应接入行情
        return self._price_cache.get(symbol, {}).get("close", 0)
    
    def submit_order(self, order: Order) -> Order:
        if not self._connected:
            order.status = OrderStatus.REJECTED
            order.metadata["reject_reason"] = "Not connected"
            return order
        
        self._order_counter += 1
        order_id = f"SIM_{datetime.now().strftime('%Y%m%d%H%M%S')}_{self._order_counter:04d}"
        order.order_id = order_id
        order.status = OrderStatus.SUBMITTED
        
        # 模拟撮合 - 市价单直接成交
        price = self._get_price(order.symbol)
        if price <= 0:
            order.status = OrderStatus.REJECTED
            order.metadata["reject_reason"] = "No price available"
            self._orders[order_id] = order
            return order
        
        # 计算可用资金/股数
        if order.side == OrderSide.BUY:
            available_cash = self._account.cash
            cost_per_share = price * (1 + self.slippage)
            fee_rate = self.commission
            max_shares = int(available_cash / (cost_per_share * (1 + fee_rate)) / 100) * 100
            qty = min(order.quantity, max_shares)
            if qty < 100:
                order.status = OrderStatus.REJECTED
                order.metadata["reject_reason"] = "Insufficient cash"
                self._orders[order_id] = order
                return order
            
            # 执行买入
            cost = qty * price * (1 + self.slippage)
            fee = cost * self.commission
            self._account.cash -= (cost + fee)
            
            # 更新持仓
            pos = self._positions.get(order.symbol)
            if pos is None:
                pos = Position(symbol=order.symbol, strategy=order.strategy)
                self._positions[order.symbol] = pos
            
            # 加权平均成本
            total_cost = pos.quantity * pos.avg_cost + qty * price * (1 + self.slippage)
            pos.quantity += qty
            pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0
            pos.strategy = order.strategy
            pos.bucket = order.metadata.get("bucket", "satellite")
            if pos.entry_date is None:
                pos.entry_date = datetime.now()
            
        else:  # SELL
            pos = self._positions.get(order.symbol)
            if pos is None or pos.quantity < order.quantity:
                order.status = OrderStatus.REJECTED
                order.metadata["reject_reason"] = "Insufficient position"
                self._orders[order_id] = order
                return order
            
            qty = min(order.quantity, pos.quantity)
            proceeds = qty * price * (1 - self.slippage)
            fee = proceeds * self.commission
            self._account.cash += (proceeds - fee)
            
            # 计算已实现盈亏
            cost_basis = qty * pos.avg_cost * (1 + self.slippage)
            realized_pnl = proceeds - fee - cost_basis
            pos.realized_pnl += realized_pnl
            
            pos.quantity -= qty
            if pos.quantity == 0:
                del self._positions[order.symbol]
        
        order.filled_qty = qty
        order.avg_fill_price = price
        order.status = OrderStatus.FILLED
        order.updated_at = datetime.now()
        
        self._orders[order_id] = order
        return order
    
    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order and order.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL_FILLED):
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now()
            return True
        return False
    
    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)
    
    def get_open_orders(self) -> List[Order]:
        return [o for o in self._orders.values() 
                if o.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL_FILLED)]
    
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict]:
        """模拟行情：返回缓存价格"""
        result = {}
        for sym in symbols:
            if sym in self._price_cache:
                result[sym] = self._price_cache[sym].copy()
        return result
    
    def get_history(self, symbol: str, start: str, end: str, freq: str = "1d") -> List[Dict]:
        """从数据层获取历史数据"""
        from analysis.data_layer import get_router
        router = get_router()
        df = router.get_history(symbol, start, end)
        if df is not None:
            return df.to_dict("records")
        return []
    
    def update_prices(self, price_data: Dict[str, Dict]) -> None:
        """更新价格缓存（供外部行情推送调用）"""
        for sym, data in price_data.items():
            self._price_cache[sym] = data
    
    def set_initial_state(self, positions: Dict[str, Position], cash: float) -> None:
        """设置初始状态（用于从 portfolio_sim_state.json 恢复）"""
        self._positions = positions
        self._account.cash = cash
        mv = sum(p.market_value for p in self._positions.values())
        self._account.market_value = mv
        self._account.total_equity = cash + mv


class LivePort(ExecutionPort):
    """实盘执行端口 - 预留券商接口"""
    
    def __init__(self, broker: str = "yigui", **credentials):
        self.broker = broker
        self.credentials = credentials
        self._connected = False
        self._client = None
    
    def connect(self) -> bool:
        """连接券商 - 需根据具体券商实现"""
        if self.broker == "yigui":
            # from broker.yigui import YiGuiClient
            # self._client = YiGuiClient(**self.credentials)
            pass
        elif self.broker == "futu":
            # from broker.futu import FutuClient
            # self._client = FutuClient(**self.credentials)
            pass
        self._connected = True  # 占位
        return self._connected
    
    def disconnect(self) -> None:
        self._connected = False
    
    def is_connected(self) -> bool:
        return self._connected
    
    def get_account(self) -> AccountInfo:
        raise NotImplementedError("需实现具体券商接口")
    
    def get_positions(self) -> Dict[str, Position]:
        raise NotImplementedError
    
    def get_position(self, symbol: str) -> Optional[Position]:
        raise NotImplementedError
    
    def submit_order(self, order: Order) -> Order:
        raise NotImplementedError
    
    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError
    
    def get_order(self, order_id: str) -> Optional[Order]:
        raise NotImplementedError
    
    def get_open_orders(self) -> List[Order]:
        raise NotImplementedError
    
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict]:
        raise NotImplementedError
    
    def get_history(self, symbol: str, start: str, end: str, freq: str = "1d") -> List[Dict]:
        raise NotImplementedError


class BacktestPort(ExecutionPort):
    """向量化回测执行端口 - 高性能回测"""
    
    def __init__(self, initial_capital: float = 100_000,
                 commission: float = 0.0003, slippage: float = 0.001,
                 price_data: Optional[Dict[str, Any]] = None):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.price_data = price_data or {}  # {symbol: DataFrame}
        self._positions: Dict[str, Position] = {}
        self._account = AccountInfo(
            total_equity=initial_capital,
            cash=initial_capital,
            market_value=0.0,
            buying_power=initial_capital
        )
        self._orders: List[Order] = []
        self._trades: List[Dict] = []
        self._current_idx = 0
        self._dates: List[datetime] = []
        self._initialized = False
    
    def _init_from_price_data(self):
        """从价格数据初始化时间轴"""
        all_dates = set()
        for sym, df in self.price_data.items():
            if hasattr(df, 'index'):
                all_dates.update(df.index)
            elif 'date' in df.columns:
                all_dates.update(pd.to_datetime(df['date']))
        self._dates = sorted(all_dates)
        self._initialized = True
    
    def connect(self) -> bool:
        if not self._initialized:
            self._init_from_price_data()
        return True
    
    def disconnect(self) -> None:
        pass
    
    def is_connected(self) -> bool:
        return True
    
    def get_account(self) -> AccountInfo:
        mv = sum(p.market_value for p in self._positions.values())
        self._account.market_value = mv
        self._account.total_equity = self._account.cash + mv
        return self._account
    
    def get_positions(self) -> Dict[str, Position]:
        return self._positions.copy()
    
    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)
    
    def submit_order(self, order: Order) -> Order:
        """向量化回测中，订单在下一根K线开盘按开盘价成交"""
        # 订单提交后，在下一交易日开盘撮合
        # 这里将订单直接加入下一个交易日的待撮合队列
        # 简化：假设当前是下一个交易日的前一交易日收盘后提交
        # 实际应用中，应根据当前时间确定下一个交易日
        if not self._initialized:
            self._init_from_price_data()
        
        # 确保 _pending_orders 存在
        if not hasattr(self, '_pending_orders'):
            self._pending_orders = {}
        
        if self._dates:
            # 默认提交到第一个交易日（或下一个交易日）
            # 实际应用中应根据订单创建时间确定
            target_date = self._dates[0] if self._dates else None
            if target_date:
                self._pending_orders.setdefault(target_date, []).append(order)
        
        order.status = OrderStatus.SUBMITTED
        return order
    
    def run(self) -> Dict:
        """运行完整回测 - 完整撮合逻辑
        
        撮合规则（实盘级别）：
        - T日收盘生成的订单，在 T+1 日按开盘价撮合（若开盘价在涨跌停范围内）
        - 市价单：按开盘价成交，若开盘涨停则买单不成，跌停则卖单不成
        - 限价单：开盘价在限价范围内成交，否则挂单等待
        - 涨跌停判断：±10%（ST股±5%），以昨收为基准
        - 成交量限制：单笔成交量不超过当日成交量的 25%
        - 佣金/滑点/印花税（卖出侧）全含
        """
        import pandas as pd
        import numpy as np
        
        if not self._initialized:
            self._init_from_price_data()
        
        results = []
        trades_log = []
        
        # 用于存放待撮合的订单：按日期索引
        # 使用实例属性 self._pending_orders，以便 submit_order 能访问
        if not hasattr(self, '_pending_orders'):
            self._pending_orders = {}
        
        # 将 submit_order 提交的订单加入待撮合队列
        for date, orders in getattr(self, '_pending_orders', {}).items():
            pass  # 已在 self._pending_orders 中
        
        for i, date in enumerate(self._dates):
            # 获取当日行情数据
            daily_prices = {}
            for sym, df in self.price_data.items():
                if date in df.index:
                    row = df.loc[date]
                    daily_prices[sym] = {
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "pre_close": float(df.loc[df.index < date, "close"].iloc[-1]) if len(df.loc[df.index < date]) > 0 else float(row.get("close", 0)),
                        "volume": float(row.get("volume", 0)),
                        "amount": float(row.get("amount", 0)),
                    }
            
            # ===== 1. 撮合前一日提交的订单 =====
            orders_to_match = self._pending_orders.pop(date, [])
            new_pending = []
            
            for order in orders_to_match:
                sym = order.symbol
                if sym not in daily_prices:
                    # 停牌或无数据，订单延期到下一交易日
                    next_idx = self._dates.index(date) + 1 if date in self._dates else len(self._dates)
                    if next_idx < len(self._dates):
                        next_date = self._dates[next_idx]
                        self._pending_orders.setdefault(next_date, []).append(order)
                    else:
                        order.status = OrderStatus.REJECTED
                        order.metadata["reject_reason"] = "No more trading days"
                        self._trades.append(self._order_to_trade(order, 0, 0, "REJECTED", trade_date=date))
                    continue
                
                md = daily_prices[sym]
                pre_close = md["pre_close"]
                open_price = md["open"]
                high = md["high"]
                low = md["low"]
                close = md["close"]
                volume = md["volume"]
                
                # 涨跌停价格
                is_st = "ST" in (self.price_data[sym].get("name", "") if hasattr(self.price_data[sym], "get") else "")
                limit_pct = 0.05 if is_st else 0.10
                up_limit = round(pre_close * (1 + limit_pct), 2)
                down_limit = round(pre_close * (1 - limit_pct), 2)
                
                # 检查开盘价是否涨跌停
                open_limit_up = abs(open_price - up_limit) < 0.01
                open_limit_down = abs(open_price - down_limit) < 0.01
                
                filled_qty = 0
                fill_price = 0.0
                status = OrderStatus.REJECTED
                reject_reason = ""
                
                if order.order_type == OrderType.MARKET:
                    # 市价单：按开盘价成交，但涨跌停时不成交
                    if order.side == OrderSide.BUY and open_limit_up:
                        reject_reason = f"开盘涨停({up_limit:.2f})，买单无法成交"
                    elif order.side == OrderSide.SELL and open_limit_down:
                        reject_reason = f"开盘跌停({down_limit:.2f})，卖单无法成交"
                    else:
                        fill_price = open_price
                        # 成交量限制：不超过当日成交量的25%
                        max_vol = int(volume * 0.25) if volume > 0 else order.quantity
                        max_vol = (max_vol // 100) * 100  # 整百股
                        filled_qty = min(order.quantity, max_vol)
                        if filled_qty >= 100:
                            status = OrderStatus.FILLED
                        else:
                            reject_reason = "可成交量不足100股"
                
                elif order.order_type == OrderType.LIMIT:
                    # 限价单：检查限价是否在开盘价有效范围内
                    if order.side == OrderSide.BUY:
                        if order.limit_price >= open_price and not open_limit_up:
                            fill_price = min(open_price, order.limit_price)
                            max_vol = int(volume * 0.25) if volume > 0 else order.quantity
                            max_vol = (max_vol // 100) * 100
                            filled_qty = min(order.quantity, max_vol)
                            if filled_qty >= 100:
                                status = OrderStatus.FILLED
                            else:
                                reject_reason = "可成交量不足100股"
                        elif open_limit_up:
                            # 涨停买不进，挂单等待
                            new_pending.append(order)
                            continue
                        else:
                            reject_reason = f"限价({order.limit_price:.2f}) < 开盘价({open_price:.2f})"
                    
                    else:  # SELL
                        if order.limit_price <= open_price and not open_limit_down:
                            fill_price = max(open_price, order.limit_price)
                            max_vol = int(volume * 0.25) if volume > 0 else order.quantity
                            max_vol = (max_vol // 100) * 100
                            filled_qty = min(order.quantity, max_vol)
                            if filled_qty >= 100:
                                status = OrderStatus.FILLED
                            else:
                                reject_reason = "可成交量不足100股"
                        elif open_limit_down:
                            new_pending.append(order)
                            continue
                        else:
                            reject_reason = f"限价({order.limit_price:.2f}) > 开盘价({open_price:.2f})"
                
                # 执行成交
                if status == OrderStatus.FILLED and filled_qty > 0:
                    slippage_cost = 1 + self.slippage if order.side == OrderSide.BUY else 1 - self.slippage
                    exec_price = fill_price * slippage_cost
                    fee_rate = self.commission
                    stamp_tax = 0.001 if order.side == OrderSide.SELL else 0  # 印花税，仅卖出
                    
                    cost = filled_qty * exec_price
                    fee = cost * fee_rate
                    tax = cost * stamp_tax
                    
                    if order.side == OrderSide.BUY:
                        total_cost = cost + fee + tax
                        if self._account.cash >= total_cost:
                            self._account.cash -= total_cost
                            
                            # 更新持仓
                            pos = self._positions.get(order.symbol)
                            if pos is None:
                                pos = Position(symbol=order.symbol, strategy=order.strategy)
                                self._positions[order.symbol] = pos
                            
                            total_cost_basis = pos.quantity * pos.avg_cost + filled_qty * exec_price
                            pos.quantity += filled_qty
                            pos.avg_cost = total_cost_basis / pos.quantity if pos.quantity > 0 else 0
                            pos.strategy = order.strategy
                            pos.bucket = order.metadata.get("bucket", "satellite")
                            if pos.entry_date is None:
                                pos.entry_date = date
                        
                        else:  # SELL
                            pos = self._positions.get(order.symbol)
                            if pos and pos.quantity >= filled_qty:
                                proceeds = filled_qty * exec_price
                                fee = proceeds * fee_rate
                                tax = proceeds * stamp_tax
                                net_proceeds = proceeds - fee - tax
                                self._account.cash += net_proceeds
                                
                                # 已实现盈亏
                                cost_basis = filled_qty * pos.avg_cost
                                realized_pnl = net_proceeds - cost_basis
                                pos.realized_pnl += realized_pnl
                                
                                pos.quantity -= filled_qty
                                if pos.quantity == 0:
                                    del self._positions[order.symbol]
                        
                        order.filled_qty = filled_qty
                        order.avg_fill_price = exec_price
                        order.status = OrderStatus.FILLED
                        order.updated_at = date
                        
                        # 记录成交回报
                        trades_log.append(self._order_to_trade(order, filled_qty, exec_price, "FILLED", trade_date=date))
                    
                else:
                    order.status = OrderStatus.REJECTED
                    order.metadata["reject_reason"] = reject_reason or "Matching failed"
                    trades_log.append(self._order_to_trade(order, 0, 0, "REJECTED", trade_date=date))
                
                self._trades.append(trades_log[-1]) if trades_log else None
            
            # 将未成交的限价单挂单到下一交易日
            for order in new_pending:
                next_idx = self._dates.index(date) + 1
                if next_idx < len(self._dates):
                    self._pending_orders.setdefault(self._dates[next_idx], []).append(order)
                else:
                    order.status = OrderStatus.CANCELLED
                    order.metadata["cancel_reason"] = "Backtest ended"
                    trades_log.append(self._order_to_trade(order, 0, 0, "CANCELLED", trade_date=date))
            
            # ===== 2. 处理当日新提交的订单（收盘后生成，次日撮合）=====
            # 这里不处理，假设外部系统在日内生成订单加入 self._orders
            # run() 结束后外部调用 submit_order() 产生的订单会在第二天撮合
            
            # ===== 3. 更新持仓市值（用收盘价）=====
            for sym, pos in self._positions.items():
                if sym in daily_prices:
                    close_price = daily_prices[sym]["close"]
                    pos.market_value = pos.quantity * close_price
                    pos.unrealized_pnl = pos.quantity * (close_price - pos.avg_cost)
            
            # ===== 4. 记录每日权益=====
            mv = sum(p.market_value for p in self._positions.values())
            cash = self._account.cash
            total = cash + mv
            ret = (total - self.initial_capital) / self.initial_capital * 100
            
            results.append({
                "date": date,
                "total_value": total,
                "cash": cash,
                "market_value": mv,
                "return_pct": ret,
            })
        
        # 处理遗留挂单
        for date, orders in self._pending_orders.items():
            for order in orders:
                order.status = OrderStatus.CANCELLED
                order.metadata["cancel_reason"] = "Backtest ended"
                trades_log.append(self._order_to_trade(order, 0, 0, "CANCELLED", trade_date=date))
        
        final_equity = self._account.cash + sum(p.market_value for p in self._positions.values())
        
        return {
            "daily_results": results,
            "trades": trades_log,
            "final_equity": final_equity,
            "total_return": (final_equity - self.initial_capital) / self.initial_capital * 100,
            "total_trades": len(trades_log),
            "win_trades": len([t for t in trades_log if t.get("pnl", 0) > 0]),
            "max_drawdown": self._calc_max_drawdown(results),
        }
    
    def _calc_max_drawdown(self, results: List[Dict]) -> float:
        """计算最大回撤"""
        if not results:
            return 0.0
        peak = 0
        max_dd = 0
        for r in results:
            val = r.get("total_value", 0)
            if val > peak:
                peak = val
            if peak > 0:
                dd = (peak - val) / peak * 100
                if dd > max_dd:
                    max_dd = dd
        return round(max_dd, 2)
    
    def _order_to_trade(self, order: Order, filled_qty: int, fill_price: float, status: str, trade_date=None) -> Dict:
        """将订单转换为成交记录"""
        pnl = 0.0
        if status == "FILLED" and filled_qty > 0:
            if order.side == OrderSide.BUY:
                pnl = -filled_qty * fill_price  # 买入为负现金流
            else:
                # 卖出需要计算相对于持仓成本的盈亏
                pos = self._positions.get(order.symbol)
                if pos and pos.avg_cost > 0:
                    pnl = filled_qty * (fill_price - pos.avg_cost)
        
        return {
            "date": trade_date if trade_date else datetime.now(),
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": filled_qty,
            "price": fill_price,
            "status": status,
            "strategy": order.strategy,
            "pnl": pnl,
        }
    
    def cancel_order(self, order_id: str) -> bool:
        return False
    
    def get_order(self, order_id: str) -> Optional[Order]:
        return None
    
    def get_open_orders(self) -> List[Order]:
        return []
    
    def get_market_data(self, symbols: List[str]) -> Dict[str, Dict]:
        return {}
    
    def get_history(self, symbol: str, start: str, end: str, freq: str = "1d") -> List[Dict]:
        if symbol in self.price_data:
            df = self.price_data[symbol]
            mask = (df.index >= start) & (df.index <= end)
            return df.loc[mask].to_dict("records")
        return []


def create_port(port_type: str, **kwargs) -> ExecutionPort:
    """工厂函数：创建执行端口
    
    Args:
        port_type: "sim" | "live" | "backtest"
        **kwargs: 传给具体实现的参数
    
    Returns:
        ExecutionPort 实例
    """
    if port_type == "sim":
        return SimulationPort(**kwargs)
    elif port_type == "live":
        return LivePort(**kwargs)
    elif port_type == "backtest":
        return BacktestPort(**kwargs)
    else:
        raise ValueError(f"Unknown port type: {port_type}")


def load_price_data_for_backtest(symbols: List[str], start: str, end: str) -> Dict[str, Any]:
    """为回测加载价格数据（供 BacktestPort 使用）"""
    from analysis.data_layer import get_router
    import pandas as pd
    
    router = get_router()
    price_data = {}
    
    for sym in symbols:
        # get_daily 只有 start_date 参数，end_date 隐含为当前日期
        df = router.get_daily(sym, '', start_date=start)
        if df is not None and len(df) > 0:
            # 手动截断到 end 日期
            if end:
                end_dt = pd.to_datetime(end)
                df = df[df["date"] <= end_dt]
            df = df.set_index("date")
            price_data[sym] = df
    
    return price_data


# 导出
__all__ = [
    "ExecutionPort",
    "SimulationPort", 
    "LivePort",
    "BacktestPort",
    "Order",
    "Position",
    "AccountInfo",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "create_port",
    "load_price_data_for_backtest",
]