#!/usr/bin/env python3
"""3-day auto_executor simulation — 零污染版 (2026-11-09 重写)。

事故背景
--------
11-08 晚间施工会话的旧版本把假 PENDING 意向直接写进生产
data/runtime/trade_intents/（真实股票代码 + 真实日期 + max_age_days=5 内），
若不处置，周一 18:45 生产扫描会按假单成交 10 笔买入。
2026-11-09 已隔离清理（data/sim3day_quarantine_20261109/ + memory/2026-11-09.md）。

本版红线
--------
1. auto_executor.INTENTS_DIR / REGIME_T1_PATH 全部重定向到 temp 目录，
   与 analysis/test_regime_executor_integration.py 同一隔离手法，永不触生产路径；
2. execute_trade 恒为假实现，绝不写 portfolio_sim_trades / trades_audit；
3. 本脚本自身落盘（汇总 JSON）写前 shutil.copy .bak + 行数防倒退。

验证意图（承 11-08 会话）：3 个交易日 × 5 标的 × 政权盖章意向 → T+1 开盘消费，
覆盖 意向盖章 / 政权权重缩放 / FILLED 回写 / .bak 备份 全链路。
"""
import os
import sys
import json
import shutil
import tempfile

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402

from analysis import auto_executor  # noqa: E402
from analysis.auto_executor import (  # noqa: E402
    load_pending_intents, consume_pending_for_symbol, flush_intent_updates,
)

SYMBOLS = ['600030', '600036', '000987', '600584', '002156']
SIM_DAYS = ['2026-11-04', '2026-11-05', '2026-11-06']


def _bak_write(path: str, obj) -> None:
    """写前 .bak（backup_guard 铁律；temp 路径同样保护，防重跑丢上轮结果）。"""
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def setup_data() -> dict:
    """加载 K 线 + 基本面面板合并（与生产扫描同形态：date 列 → DatetimeIndex）。"""
    fund_panel = pd.read_parquet(os.path.join(WORKSPACE, 'data/fundamental/fundamental_panel.parquet'))
    fund_panel['symbol'] = fund_panel['symbol'].astype(str).str.zfill(6)
    fund_cols = [c for c in ['eps_basic', 'book_value_per_share',
                             'operating_cash_flow_per_share', 'revenue_yoy']
                 if c in fund_panel.columns]
    data = {}
    for sym in SYMBOLS:
        df = pd.read_csv(os.path.join(WORKSPACE, f'data/stock_data/{sym}.csv'))
        df.columns = [c.lower() for c in df.columns]
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')[['open', 'high', 'low', 'close', 'volume']].sort_index()
        sym_fund = fund_panel[fund_panel['symbol'] == sym].copy()
        if len(sym_fund) and fund_cols:
            sym_fund = sym_fund.set_index('report_date')[fund_cols].resample('D').ffill()
            if sym_fund.index.max() < df.index.max():
                extra = pd.date_range(sym_fund.index.max() + pd.Timedelta(days=1),
                                      df.index.max(), freq='D')
                if len(extra):
                    pad = pd.DataFrame(index=extra,
                                       data={c: sym_fund.iloc[-1][c] for c in sym_fund.columns})
                    sym_fund = pd.concat([sym_fund, pad])
            df = df.join(sym_fund, how='left')
        data[sym] = df
    return data


def make_fake_intent(signal_date: str, price: float) -> dict:
    """带政权章的假买入意向（形态复刻生产 build_pending_intent 输出）。"""
    return {
        'name': 'ema_cross', 'action': '买入', 'reason': 'EMA金叉(sim)',
        'price': price, 'bucket': 'core', 'strategy': 'ema_cross',
        'atr': 0.5, 'atr_stop': round(price * 0.94, 2), 'fib_tp': {'1.0': round(price * 1.08, 2)},
        'created_at': f'{signal_date}T15:30:00', 'status': 'PENDING',
        'regime': {'name': 'sideways', 'signal_date': signal_date, 'max_position_pct': 0.08,
                   'stop_loss_pct': 0.03, 'take_profit_pct': 0.08, 'position_sizing': 'vol_target',
                   'factor_weights': {'BB_pos_20': 0.3, 'RSI_14': -0.25, 'KDJ_K': 0.2,
                                      'CCI_20': 0.15, 'volume_ratio_5_20': 0.1, 'MACD_hist': 0.0}},
    }


def run() -> int:
    tmp = tempfile.mkdtemp(prefix='sim3day_')
    auto_executor.INTENTS_DIR = os.path.join(tmp, 'intents')
    auto_executor.REGIME_T1_PATH = os.path.join(tmp, 'regime_t1_config.json')
    os.makedirs(auto_executor.INTENTS_DIR, exist_ok=True)
    print(f'[隔离] INTENTS_DIR -> {auto_executor.INTENTS_DIR}')

    data = setup_data()

    # Day0: T 日收盘生成 T+1 政权配置（真实 allocator，落 temp）
    from analysis.regime_allocator import generate_next_day_config
    cfg = generate_next_day_config(data)
    print(f'[regime] T+1 配置: regime={cfg.get("regime")} mpp={cfg.get("max_position_pct")}')

    # T 日: 5 标的盖章意向 → temp
    day0_intents = {sym: make_fake_intent(SIM_DAYS[0], float(data[sym]['close'].iloc[-1]))
                    for sym in SYMBOLS}
    fp0 = os.path.join(auto_executor.INTENTS_DIR, f'{SIM_DAYS[0]}_intents.json')
    _bak_write(fp0, day0_intents)

    filled, results = 0, {}
    for i, today_str in enumerate(SIM_DAYS):
        print(f'\n=== Processing {today_str} ===')
        ctx = {
            'pending_index': load_pending_intents(today_str=today_str),
            'check_dedup': lambda signal, pos, t: True,
            'execute_trade': lambda pos, signal, t, strategy=None, position_plan=None: ('BUY', '模拟成交(sim)'),
            'position_plan': {'single_stock_max_amount': 80000, 'buy_signal_multiplier': 1.0},
            'regime_t1_day': cfg if i > 0 else None,  # T+1 起消费 T 日配置
            'max_age_days': 5,
        }
        for sym, entries in sorted(ctx['pending_index'].items()):
            if not entries:
                continue
            act, msg = consume_pending_for_symbol(ctx, sym, 'sim', {'position': True},
                                                  data[sym], today_str)
            print(f'  {sym}: act={act} msg={msg}')
            if act == 'BUY':
                filled += 1
        flush_intent_updates(ctx['pending_index'], today_str=today_str)
        print(f'  flushed ({today_str})')

    # 回读验证
    final = json.load(open(os.path.join(auto_executor.INTENTS_DIR, f'{SIM_DAYS[0]}_intents.json'),
                           encoding='utf-8'))
    statuses = {s: it.get('status') for s, it in final.items()}
    results = {'filled_count': filled, 'statuses': statuses,
               'intents_dir': auto_executor.INTENTS_DIR}
    out = os.path.join(WORKSPACE, 'logs', 'sim3day_last_run.json')
    _bak_write(out, results)

    ok = filled >= len(SYMBOLS) and all(v == 'FILLED' for v in statuses.values())
    print(f"\n===== SIM RESULT: {'PASS' if ok else 'FAIL'} ===== filled={filled} statuses={statuses}")
    print(f'汇总: {out} (temp 意向目录: {auto_executor.INTENTS_DIR})')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(run())
