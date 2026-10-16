#!/usr/bin/env python3
"""预警 levels 每日自动派生：从收盘扫描 json 刷新 intraday_alert_config.json
================================================================================
背景（2026-10-16 16:4x 缺陷发现 + 用户批准的待批提案）：
data/intraday_alert_config.json 的 levels 停在 09-13 无自动更新机制，
14:30 推送 29 条预警中约 13 条为常真噪音（平高 break_below=30.0 vs 现价 20.7），
翰宇 10-12 入池后缺位=开关位警报永不触发。增量修复只止了 2 只血，本脚本根除漂移。

派生口径（沿用 16:50 已在配置 note 中声明的 close_scan 口径，不造第二套）：
- break_below  = stop_long   （ATR 纪律位，跌破=纪律止损警报）
- buy_zone_low = support     （近期支撑，低吸区）
- break_above  = resistance  （近期阻力，突破警报；突破需收盘站稳确认）
- drop_pct / surge_pct / action / type / note 原样保留
- pinned=true 的股票跳过（人工策略开关位，如翰宇 26.38/24/24.69 条件开关）

数据源：analysis/daily/{date}_close_scan.json（18:30 daily_push.persist_artifacts 落盘），
本脚本同时被 daily_push 每日调用 + 可独立运行（--scan 指定 / 默认取最新）。
"""

import os
import sys

WORKSPACE = os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace")
# 必须同时插入：工作区根 + analysis/（TOOLS.md 强制样板）
for _p in (WORKSPACE, os.path.join(WORKSPACE, "analysis")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import glob
import json
import logging

log = logging.getLogger("refresh_alert_levels")

CONFIG_PATH = os.path.join(WORKSPACE, "data", "intraday_alert_config.json")
DAILY_DIR = os.path.join(WORKSPACE, "analysis", "daily")


def latest_scan_path() -> str:
    """按 mtime 取最新 close_scan json（analysis/daily/ 下）"""
    paths = sorted(glob.glob(os.path.join(DAILY_DIR, "*_close_scan.json")),
                   key=os.path.getmtime)
    if not paths:
        raise FileNotFoundError("analysis/daily/ 下无 *_close_scan.json")
    return paths[-1]


def _valid(v) -> bool:
    try:
        return v is not None and float(v) > 0
    except (TypeError, ValueError):
        return False


def update_config_from_scan(scan: dict, config_path: str = CONFIG_PATH,
                            dry_run: bool = False):
    """用 slim close_scan dict 刷新配置 levels，返回写入路径或 None。

    scan 形如 persist_artifacts 的 slim：
    {data_date, stocks: [{symbol, name, price, stop_long, support, resistance}]}
    """
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    stocks = config.get("stocks", {})
    data_date = scan.get("data_date") or "unknown"
    rows = {r.get("symbol"): r for r in (scan.get("stocks") or [])
            if r.get("symbol")}

    changes, kept_pinned, no_row, skip_bad = [], [], [], []
    for code, entry in stocks.items():
        if entry.get("pinned"):
            kept_pinned.append(code)
            continue
        row = rows.get(code)
        if row is None:
            no_row.append(code)
            log.warning("扫描缺行，保留旧 levels: %s(%s)",
                        entry.get("name", code), code)
            continue
        price = row.get("price")
        stop_long = row.get("stop_long")
        support = row.get("support")
        resistance = row.get("resistance")
        if not (_valid(price) and _valid(stop_long) and float(stop_long) < float(price)):
            skip_bad.append(code)
            log.warning("stop_long 无效(>=现价或缺失)，保留旧 levels: %s(%s) "
                        "price=%s stop_long=%s", entry.get("name", code), code,
                        price, stop_long)
            continue
        levels = entry.setdefault("levels", {})
        old_bb = levels.get("break_below")
        levels["break_below"] = round(float(stop_long), 2)
        if _valid(support):
            levels["buy_zone_low"] = round(float(support), 2)
        if _valid(resistance) and float(resistance) > float(price):
            levels["break_above"] = round(float(resistance), 2)
        elif "break_above" in levels:
            # 阻力已失守：保留旧值会在盘中立刻误报，本次不更新该字段并留痕
            log.warning("resistance<=price，break_above 保留旧值 %s: %s(%s)",
                        levels.get("break_above"), entry.get("name", code), code)
        levels["_derived_from"] = f"close_scan {data_date}"
        entry["name"] = entry.get("name") or row.get("name")
        changes.append((code, entry.get("name"), old_bb, levels["break_below"]))

    if not dry_run:
        meta = config.setdefault("_meta", {})
        meta["updated"] = data_date
        meta["levels_source"] = (
            f"close_scan 每日自动派生 (analysis/refresh_alert_levels.py, {data_date})；"
            "跌破=ATR纪律位、低吸=近期支撑、突破=近期阻力(需收盘站稳)；pinned 股票除外")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=1)

    log.info("levels 派生完成: 更新 %d / pinned保留 %d / 缺行 %d / 无效跳过 %d (data_date=%s, dry_run=%s)",
             len(changes), len(kept_pinned), len(no_row), len(skip_bad),
             data_date, dry_run)
    for c, name, old, new in changes:
        log.info("  %s(%s) break_below %s -> %s", name, c, old, new)
    return None if dry_run else (config_path if changes or not dry_run else None)


def update_config_from_file(scan_path: str, dry_run: bool = False):
    with open(scan_path, encoding="utf-8") as f:
        scan = json.load(f)
    return update_config_from_scan(scan, dry_run=dry_run)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="预警 levels 自动派生自 close_scan json")
    ap.add_argument("--scan", default=None, help="close_scan json 路径，默认取最新")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    path = args.scan or latest_scan_path()
    log.info("扫描源: %s", path)
    result = update_config_from_file(path, dry_run=args.dry_run)
    print(f"config: {result or '(dry-run 未写入)'}")


if __name__ == "__main__":
    main()
