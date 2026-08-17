#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隔夜港股美股 → 今日A股盘前展望（纯命令任务，不调用大模型）
数据源: 新浪 hq.sinajs.cn（必须带 Referer header）
输出:   markdown 报告文本（由 cron --announce 推送）
"""
import sys, io, datetime, urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SINA = "https://hq.sinajs.cn/list="
HDR = {"Referer": "https://finance.sina.com.cn"}

def fetch(codes, is_gb=False):
    """codes: list of 完整 sina 代码。返回 {code: 值列表}"""
    out = {}
    if not codes:
        return out
    url = SINA + ",".join(codes)
    req = urllib.request.Request(url, headers=HDR)
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
    for line in raw.splitlines():
        if '="' not in line:
            continue
        key = line.split('hq_str_')[1].split('=')[0]
        val = line.split('"')[1].split(',')
        out[key] = val
    return out

def f(x):
    try:
        return float(x)
    except Exception:
        return 0.0

def main():
    today = datetime.date.today().strftime("%Y-%m-%d") + "（" + "一二三四五六日"[datetime.date.today().weekday()] + "）"
    lines = []
    lines.append(f"📊 A股盘前展望 · {today}")
    lines.append("")
    lines.append("一、隔夜外围数据")

    # 港股（rt_ 前缀, 前一交易日收盘）
    hk = fetch(["rt_hkHSI", "rt_hkHSTECH", "rt_hkHSCEI"])
    # 美股（gb_ 前缀）
    us = fetch(["gb_dji", "gb_ixic", "gb_inx"])
    # A股大盘（昨收）
    cn = fetch(["s_sh000001", "s_sz399001", "s_sz399006", "s_sh000688"])
    # 商品
    cm = fetch(["hf_GC", "hf_CL"])

    def pct(v):
        return f"{f(v):+.2f}%" if v else "—"

    rows = []
    # 港股 rt_ 格式: [0]=code [1]=中文名 [2]=最新 [3]=今开 [4]=最高 [5]=最低 [6]=昨收 [7]=涨跌额 [8]=涨跌幅
    if "rt_hkHSI" in hk and len(hk["rt_hkHSI"]) > 8:
        v = hk["rt_hkHSI"]; rows.append(("🇭🇰 港股(昨)", "恒生指数", f(v[2]), f(v[8])))
    if "rt_hkHSTECH" in hk and len(hk["rt_hkHSTECH"]) > 8:
        v = hk["rt_hkHSTECH"]; rows.append(("🇭🇰 港股(昨)", "恒生科技", f(v[2]), f(v[8])))
    if "rt_hkHSCEI" in hk and len(hk["rt_hkHSCEI"]) > 8:
        v = hk["rt_hkHSCEI"]; rows.append(("🇭🇰 港股(昨)", "国企指数", f(v[2]), f(v[8])))
    # 美股 gb_ 格式: [0]=名称 [1]=最新价 [2]=涨跌幅% [3]=时间
    if "gb_dji" in us and len(us["gb_dji"]) > 2:
        v = us["gb_dji"]; rows.append(("🇺🇸 美股(夜)", "道琼斯", f(v[1]), v[2]))
    if "gb_ixic" in us and len(us["gb_ixic"]) > 2:
        v = us["gb_ixic"]; rows.append(("🇺🇸 美股(夜)", "纳斯达克", f(v[1]), v[2]))
    if "gb_inx" in us and len(us["gb_inx"]) > 2:
        v = us["gb_inx"]; rows.append(("🇺🇸 美股(夜)", "标普500", f(v[1]), v[2]))
    # A股 s_ 格式: [0]=name [1]=最新 [2]=涨跌额 [3]=涨跌幅
    if "s_sh000001" in cn and len(cn["s_sh000001"]) > 3:
        v = cn["s_sh000001"]; rows.append(("🇨🇳 A股(昨收)", "上证指数", f(v[1]), f(v[3])))
    if "s_sz399001" in cn and len(cn["s_sz399001"]) > 3:
        v = cn["s_sz399001"]; rows.append(("🇨🇳 A股(昨收)", "深证成指", f(v[1]), f(v[3])))
    if "s_sz399006" in cn and len(cn["s_sz399006"]) > 3:
        v = cn["s_sz399006"]; rows.append(("🇨🇳 A股(昨收)", "创业板指", f(v[1]), f(v[3])))
    if "s_sh000688" in cn and len(cn["s_sh000688"]) > 3:
        v = cn["s_sh000688"]; rows.append(("🇨🇳 A股(昨收)", "科创50", f(v[1]), f(v[3])))
    if "hf_GC" in cm and len(cm["hf_GC"]) > 1:
        v = cm["hf_GC"]; rows.append(("🥇 商品", "纽约黄金", f(v[0]), "—"))
    if "hf_CL" in cm and len(cm["hf_CL"]) > 1:
        v = cm["hf_CL"]; rows.append(("🛢️ 商品", "NYMEX原油", f(v[0]), "—"))

    lines.append("| 市场 | 指标 | 收盘/最新 | 涨跌幅 |")
    lines.append("|------|------|----------|--------|")
    for cat, name, val, chg in rows:
        lines.append(f"| {cat} | {name} | {val} | {pct(chg)} |")

    lines.append("")
    lines.append("二、今日A股研判（纯数据快照，非模型观点）")
    lines.append("⚠️ 以上为隔夜/昨收行情快照。结合港股涨跌与美股、美债、商品方向，")
    lines.append("供盘前参考；具体板块判断与30只自选池研判请以15:30收盘推送验证为准。")
    lines.append("")
    lines.append("⚠️ 仅供参考，不构成投资建议")

    report = "\n".join(lines)
    print(report)

    # 落盘供 cron announce 推送读取
    import os
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily")
    os.makedirs(outdir, exist_ok=True)
    fname = os.path.join(outdir, datetime.date.today().strftime("%Y-%m-%d_outlook.md"))
    with open(fname, "w", encoding="utf-8") as fp:
        fp.write(report)
    print(f"\n[已保存] {fname}")

if __name__ == "__main__":
    main()
