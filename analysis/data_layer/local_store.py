"""
本地 CSV 数据湖（Local Data Lake）
==================================
作为网络数据源的最后一级兜底：
- 增量同步：每日 07:00 cron 拉取近 60 日增量，追加写入 CSV（去重）
- 读取：按 symbol 读取 CSV，筛选日期范围，返回 DataFrame
- 元数据：每只股票维护 _meta.json 记录最后同步日期、行数、数据口径
- 兼容性：导出的 DataFrame 列与 router.get_daily 完全一致 (date, open, high, low, close, volume)
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import pandas as pd
import threading

log = logging.getLogger(__name__)

WORKSPACE = "/Users/duguke/.openclaw/workspace"
STOCK_DATA_DIR = os.path.join(WORKSPACE, "data", "stock_data")
META_DIR = os.path.join(STOCK_DATA_DIR, "_meta")

# 锁：防止同一文件并发读写
_locks: Dict[str, threading.Lock] = {}
_global_lock = threading.Lock()

def _get_lock(symbol: str) -> threading.Lock:
    with _global_lock:
        if symbol not in _locks:
            _locks[symbol] = threading.Lock()
        return _locks[symbol]

def _meta_path(symbol: str) -> str:
    return os.path.join(META_DIR, f"{symbol}.json")

def _csv_path(symbol: str) -> str:
    return os.path.join(STOCK_DATA_DIR, f"{symbol}.csv")

def _load_meta(symbol: str) -> Dict:
    """加载元数据"""
    path = _meta_path(symbol)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 元数据读取失败: {e}")
    return {"last_sync_date": None, "row_count": 0, "adjust": "qfq", "source": "baostock"}

def _save_meta(symbol: str, meta: Dict):
    """保存元数据（原子写入）"""
    os.makedirs(META_DIR, exist_ok=True)
    path = _meta_path(symbol)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        log.warning(f"  ⚠️ {symbol} 元数据写入失败: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def read_local_daily(symbol: str, start_date: str = "20240101", 
                     adjust: str = "qfq") -> Optional[pd.DataFrame]:
    """
    从本地 CSV 读取日线数据（网络数据源全部失败时的最后兜底）
    
    返回: DataFrame (date, open, high, low, close, volume) 或 None
    """
    csv_path = _csv_path(symbol)
    if not os.path.exists(csv_path):
        log.info(f"  📭 本地 CSV 不存在: {symbol}")
        return None
    
    lock = _get_lock(symbol)
    with lock:
        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                return None
            
            # 统一列名
            if "date" not in df.columns:
                log.warning(f"  ⚠️ {symbol} CSV 缺少 date 列")
                return None
            
            df["date"] = pd.to_datetime(df["date"])
            
            # 日期筛选
            if start_date:
                if "-" not in start_date:
                    start_date = f"{start_date[0:4]}-{start_date[4:6]}-{start_date[6:8]}"
                df = df[df["date"] >= pd.to_datetime(start_date)]
            
            if len(df) < 2:
                return None
            
            # 确保列顺序与 router 一致
            cols = ["date", "open", "high", "low", "close", "volume"]
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            df = df[cols].dropna()
            
            if len(df) < 2:
                return None
            
            log.info(f"  ✅ {symbol}: 本地 CSV {len(df)} 条日线 [fallback]")
            return df.sort_values("date").reset_index(drop=True)
            
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 本地 CSV 读取失败: {e}")
            return None

def write_local_daily(symbol: str, df: pd.DataFrame, adjust: str = "qfq", 
                      source: str = "baostock", incremental: bool = True):
    """
    写入/追加本地 CSV（增量去重）
    
    Args:
        symbol: 股票代码
        df: DataFrame (date, open, high, low, close, volume)
        adjust: 复权类型
        source: 数据来源
        incremental: True=增量追加（去重），False=全量覆盖
    """
    if df is None or df.empty:
        return
    
    lock = _get_lock(symbol)
    with lock:
        csv_path = _csv_path(symbol)
        os.makedirs(STOCK_DATA_DIR, exist_ok=True)
        
        # 标准化列
        df = df.copy()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        
        # 只保留标准列
        cols = ["date", "open", "high", "low", "close", "volume"]
        for c in cols:
            if c not in df.columns:
                df[c] = None
        df = df[cols].dropna()
        
        if df.empty:
            return
        
        # 去重：按日期保留最新
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.sort_values("date").reset_index(drop=True)
        
        if incremental and os.path.exists(csv_path):
            try:
                existing = pd.read_csv(csv_path)
                if not existing.empty:
                    existing["date"] = pd.to_datetime(existing["date"]).dt.strftime("%Y-%m-%d")
                    existing = existing.drop_duplicates(subset=["date"], keep="last")
                    # 合并去重
                    combined = pd.concat([existing, df], ignore_index=True)
                    combined = combined.drop_duplicates(subset=["date"], keep="last")
                    combined["date"] = pd.to_datetime(combined["date"])
                    combined = combined.sort_values("date").reset_index(drop=True)
                    combined["date"] = combined["date"].dt.strftime("%Y-%m-%d")
                    df = combined
            except Exception as e:
                log.warning(f"  ⚠️ {symbol} 合并现有 CSV 失败，全量覆盖: {e}")
        
        # 写入（原子替换）
        tmp_path = csv_path + ".tmp"
        try:
            df.to_csv(tmp_path, index=False)
            os.replace(tmp_path, csv_path)
            
            # 更新元数据
            meta = {
                "last_sync_date": df["date"].iloc[-1],
                "row_count": len(df),
                "adjust": adjust,
                "source": source,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            _save_meta(symbol, meta)
            log.info(f"  💾 {symbol}: 本地 CSV 已更新 {len(df)} 行 ({meta['last_sync_date']})")
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 本地 CSV 写入失败: {e}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

def sync_incremental(symbol: str, df: pd.DataFrame, adjust: str = "qfq", 
                     source: str = "baostock"):
    """
    增量同步单只股票到本地 CSV（供 cron 调用）
    仅追加 df 中比现有 CSV 更新的数据
    """
    write_local_daily(symbol, df, adjust, source, incremental=True)

def sync_batch(symbols: List[str], fetch_func, adjust: str = "qfq", 
               source: str = "baostock", lookback_days: int = 60) -> Dict[str, bool]:
    """
    批量增量同步（供 07:00 cron 调用）
    
    Args:
        symbols: 股票代码列表
        fetch_func: 获取数据的函数，签名 fetch_func(symbol, start_date, adjust) -> DataFrame
        adjust: 复权类型
        source: 数据来源
        lookback_days: 向前回溯天数（增量窗口）
    
    Returns:
        {symbol: success_bool}
    """
    results = {}
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    log.info(f"📦 本地数据湖增量同步开始: {len(symbols)} 只, 窗口 {start_date} ~ {today}")
    
    for symbol in symbols:
        try:
            df = fetch_func(symbol, start_date=start_date, adjust=adjust)
            if df is not None and len(df) >= 2:
                sync_incremental(symbol, df, adjust, source)
                results[symbol] = True
            else:
                log.warning(f"  ⚠️ {symbol} 同步失败: 数据为空")
                results[symbol] = False
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 同步异常: {e}")
            results[symbol] = False
    
    success_count = sum(1 for v in results.values() if v)
    log.info(f"📦 本地数据湖同步完成: {success_count}/{len(symbols)} 成功")
    return results

def get_last_sync_date(symbol: str) -> Optional[str]:
    """获取最后同步日期"""
    meta = _load_meta(symbol)
    return meta.get("last_sync_date")

def is_data_fresh(symbol: str, max_age_days: int = 2) -> bool:
    """检查本地数据是否新鲜（最近 max_age_days 内有同步）"""
    last_sync = get_last_sync_date(symbol)
    if not last_sync:
        return False
    try:
        last_date = datetime.strptime(last_sync, "%Y-%m-%d").date()
        today = datetime.now().date()
        return (today - last_date).days <= max_age_days
    except Exception:
        return False

def get_freshness_report(symbols: List[str]) -> Dict[str, Dict]:
    """获取多只股票的数据新鲜度报告"""
    report = {}
    for symbol in symbols:
        meta = _load_meta(symbol)
        report[symbol] = {
            "last_sync_date": meta.get("last_sync_date"),
            "row_count": meta.get("row_count", 0),
            "adjust": meta.get("adjust"),
            "source": meta.get("source"),
            "is_fresh": is_data_fresh(symbol)
        }
    return report