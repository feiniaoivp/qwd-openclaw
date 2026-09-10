with open('analysis/backtest_strategies.py', 'r') as f:
    content = f.read()

# Find positions
idx = content.find('所有数据源均失败')
idx2 = content.find('"""生成详细对比报告', idx)

# Replace the section between them
before = content[:idx + len('所有数据源均失败")\n    return None')]
after = content[idx2:]

new_middle = '''

def main():
    today = datetime.now().strftime("%Y-%m-%d")

    # 登录 baostock
    lg = bs.login()
    if lg.error_code != "0":
        print(f"❌ baostock登录失败: {lg.error_msg}")
        return

    print(f"🚀 八策略全量回测开始 | {START_DATE} ~ {END_DATE} | {len(STOCKS)} 只股票")
    print("=" * 70)

    results = []
    for idx, (symbol, name) in enumerate(STOCKS):
        print(f"\\n[{idx+1}/{len(STOCKS)}] {name}({symbol}) — 获取数据...")
        df = fetch_data(symbol, name)
        if df is None or len(df) < 100:
            print(f"  ❌ 数据不足，跳过")
            results.append({"symbol": symbol, "name": name, "error": "数据不足"})
            continue

        stock_result = {"symbol": symbol, "name": name, "strategies": {}}
        for sname, sfunc in ALL_STRATEGIES:
            try:
                actions = sfunc(df)
                metrics = run_simulation(df, actions)
                stock_result["strategies"][sname] = metrics
            except Exception as e:
                stock_result["strategies"][sname] = {"error": str(e)}
        results.append(stock_result)

    bs.logout()

    # ── 生成报告 ──
    """生成详细对比报告"
'''

new_content = before + new_middle + after

with open('analysis/backtest_strategies.py', 'w') as f:
    f.write(new_content)
print('✅ Successfully replaced via index')