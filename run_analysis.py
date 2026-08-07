import akshare as ak
import pandas as pd
import traceback
import sys
import os

# Add the skills directory to path so we can import multi_dimension_strategy
skills_path = "/Users/duguke/.openclaw/workspace/skills"
if skills_path not in sys.path:
    sys.path.insert(0, skills_path)

# Import the strategy function
try:
    from multi_dimension_strategy import run_multi_dimension_strategy
except ImportError as e:
    print(f"错误: 无法导入 multi_dimension_strategy: {e}")
    sys.exit(1)

# Stock list: (code, name)
stocks = [
    ("600584", "长电科技"),
    ("688981", "中芯国际"),
    ("600160", "巨化股份"),
    ("300285", "国瓷材料"),
    ("000987", "越秀资本"),
    ("601066", "中信建投"),
    ("600030", "中信证券"),
    ("601995", "中金公司"),
    ("002335", "科华数据"),
    ("002466", "天齐锂业"),
    ("300014", "亿纬锂能"),
    ("300827", "上能电气"),
    ("600570", "恒生电子"),
    ("601865", "福莱特"),
    ("603308", "应流股份"),
    ("300748", "金力永磁"),
    ("601061", "中信金属"),
    ("601100", "恒立液压"),
    ("300124", "汇川技术"),
    ("600036", "招商银行"),
    ("000157", "中联重科"),
    ("600660", "福耀玻璃"),
    ("000708", "中信特钢"),
    ("002413", "雷科防务"),
    ("300719", "安达维尔"),
    ("600346", "恒力石化")
]

# Ensure output directory exists
output_dir = "/Users/duguke/.openclaw/workspace"
os.makedirs(output_dir, exist_ok=True)

success_count = 0
fail_count = 0

for code, name in stocks:
    print(f"\n=== 处理股票: {code} {name} ===")
    try:
        # 1. 读取已下载的CSV数据
        input_path = f"/Users/duguke/stock_data/{code}_{name}.csv"
        
        # 读取CSV文件，第一列是空的（索引列），我们将其作为索引读取
        df = pd.read_csv(input_path, index_col=0)
        
        print(f"  ✓ 成功读取数据，共 {len(df)} 条记录")
        print(f"  列名: {list(df.columns)}")
        
        # 确保日期列存在并转换为datetime
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        else:
            # 如果没有日期列，创建一个日期索引（虽然我们的CSV有日期列）
            df['date'] = pd.date_range(end=pd.Timestamp.today(), periods=len(df))
        
        # 按日期排序
        df = df.sort_values('date').reset_index(drop=True)
        
        # 2. 应用策略
        result_df = run_multi_dimension_strategy(df.copy())
        
        # 3. 添加股票信息
        result_df['股票代码'] = code
        result_df['股票名称'] = name
        
        # 4. 保存结果
        output_path = os.path.join(output_dir, f"analysis_result_{code}.csv")
        result_df.to_csv(output_path, index=False, encoding='utf_8_sig')
        print(f"  ✓ 结果已保存至: {output_path}")
        
        # 5. 信号统计
        signal_counts = result_df['Signal'].value_counts()
        buy_count = signal_counts.get(1, 0)
        sell_count = signal_counts.get(-1, 0)
        hold_count = signal_counts.get(0, 0)
        print(f"  信号统计: 买入(1)={buy_count}, 卖出(-1)={sell_count}, 观望(0)={hold_count}")
        
        success_count += 1
        
    except Exception as e:
        print(f"  ✗ 处理股票 {code} {name} 时出错: {e}")
        traceback.print_exc()
        fail_count += 1
        continue

# 7. 处理完所有股票后，打印总结
print("\n=== 处理完毕 ===")
print(f"成功分析的股票数量: {success_count}")
print(f"失败的股票数量: {fail_count}")