#!/usr/bin/env bash
cd /Users/duguke/.openclaw/workspace
echo '=== 运行全部核心门禁验证 ==='
for f in rm_guard.py strategy_map_guard.py script_path_guard.py data_freshness_guard.py equity_guard.py wire_net_guard.py skill_supply_scan.py; do
  echo "--- $f ---"
  python3 scripts/$f 2>&1 | head -8
  echo "exit_code=${PIPESTATUS[0]}"
done