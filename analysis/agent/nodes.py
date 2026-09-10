#!/usr/bin/env python3
"""
LangGraph � 节点实现：每个节点都是纯�函数，输入 State、输出更新后的 State。
仅修改自己关心的字段，其余保持不变。
"""

import os, json, re, time, uuid
from datetime import datetime
from typing import List, Tuple, Dict, Any
from analysis.service import (
    is_trading_day, get_spot_scan, market_overview,
    get_daily_hist, calc_full_signal, calc_spot_signal,
    analyze_zhonglian, load_state, save_state, execute_zhonglian_trade,
    WATCHLIST
)
from analysis.llm_tool import (get_today_news, get_market_health, call_llm_with_tools, extract_llm_content)
from analysis.rag.experience_store import retrieve_top_k
from analysis.trader_stock_picks import get_trader_picks
from analysis.three_factor_helper import ResonanceGate, SENT_BUY, FUND_BUY

# ---------- � 辅助：生成唯一 run_id ----------
def _make_run_id() -> str:
    return f"{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}"

# ---------- Node 1: 初始化 ----------
def node_init(state: Dict[str, Any]) -> Dict[str, Any]:
    state = state.copy()
    state["run_id"] = _make_run_id()
    state["timestamp"] = datetime.now().isoformat(timespec='seconds')
    state["error_count"] = 0
    state["done"] = False
    state["date"] = datetime.now().strftime("%Y-%m-%d")
    return state

# ---------- Node 2: 判断交易日 ----------
def node_check_trading_day(state: Dict[str, Any]) -> Dict[str, Any]:
    state = state.copy()
    state["is_trading_day"] = is_trading_day()
    return state

# ---------- Node 3: �� 获取实时行情 ----------
def node_fetch_spot(state: Dict[str, Any]) -> Dict[str, Any]:
    state = state.copy()
    # 新浪实时接口偶发 RemoteDisconnected，做 3 次重试（每次间隔 1s）避免一次抖动清空整个报告
    import time as _t
    spot, data_date, last_err = [], None, ""
    for attempt in range(3):
        try:
            spot, data_date = get_spot_scan()
            if spot and len([s for s in spot if "error" not in s]) > 0:
                break
            last_err = "实时行情返回为空/全错误"
        except Exception as e:
            last_err = str(e)
        if attempt < 2:
            _t.sleep(1)
    state["spot_data"] = spot
    state["data_date"] = data_date or state["date"]
    try:
        state["market_overview"] = market_overview(spot)
    except Exception:
        state["market_overview"] = None
    if not spot or len([s for s in spot if "error" not in s]) == 0:
        state["error_count"] = state.get("error_count", 0) + 1
        state["spot_data"] = [{"error": last_err or "实时行情获取失败"}]
    return state

# ---------- Node 4: 计算技术面信号 ----------
def node_calc_signals(state: Dict[str, Any]) -> Dict[str, Any]:
    state = state.copy()
    signals = []
    # 先判断历史数据是否可用（以中联重科为试探）
    test_df = get_daily_hist("000157", "中联重科", start_date="20260725")
    use_full = test_df is not None and len(test_df) >= 2

    for item in state.get("spot_data", []):
        if "error" in item:
            signals.append(item)   # 保持错误记录
            continue
        sym = item["symbol"]
        name = item[0]["name"] if isinstance(item.get("name"), list) else item.get("name", "")
        if use_full:
            df = get_daily_hist(sym, name)
            if df is not None and len(df) >= 60:
                try:
                    sig = calc_full_signal(df)
                    sig.update({"symbol": sym, "name": name, "data_source": "hist"})
                except Exception:
                    sig = calc_spot_signal(item)
                    sig.update({"symbol": sym, "name": name, "data_source": "spot"})
            else:
                sig = calc_spot_signal(item)
                sig.update({"symbol": sym, "name": name, "data_source": "spot"})
        else:
            sig = calc_spot_signal(item)
            sig.update({"symbol": sym, "name": name, "data_source": "spot"})
        signals.append(sig)

    state["signals"] = signals
    # 统计简要分布（便于后续节点快速查看）
    strong_buy = sum(1 for s in signals if "强烈�买入" in s.get("signal", {}).get("level", ""))
    watch = sum(1 for s in signals if "关注" in s.get("signal", {}).get("level", ""))
    neutral = sum(1 for s in signals if "中性" in s.get("signal", {}).get("level", ""))
    caution = sum(1 for s in signals if "�谨�慎" in s.get("signal", {}).get("level", ""))
    strong_sell = sum(1 for s in signals if "强烈�卖出" in s.get("signal", {}).get("level", ""))
    state["signal_summary"] = {
        "强烈�买入": strong_buy, "关注": watch, "中性": neutral,
        "�谨�慎": caution, "强烈�卖出": strong_sell,
        "失败": sum(1 for s in signals if "error" in s)
    }
    return state

# ---------- Node 5: 中联重科双策略 ----------
def node_zhonglian(state: Dict[str, Any]) -> Dict[str, Any]:
    state = state.copy()
    try:
        # � 若历史数据可用则用完整历史，否则回退到仅用 spot
        df = get_daily_hist("000157", "中联重科", start_date="20240101")
        if df is None or len(df) < 120:
            df = get_daily_hist("000157", "中联重科", start_date="20260725")
        zhonglian = analyze_zhonglian(df) if df is not None else {"error": "无历史数据"}
        state["zhonglian"] = zhonglian

        if "error" not in zhonglian:
            state["zhonglian"]["date"] = zhonglian.get("date", state["date"])
            # 只有在日期变化时才�执行交易（�避免重复下单）
            st = load_state()
            if zhonglian.get("date") != st.get("last_signal_date"):
                st["last_signal_date"] = zhonglian.get("date")
                actions, msgs, st, total_val, total_ret = execute_zhonglian_trade(st, zhonglian)
                save_state(st)
                state["zhonglian_actions"] = actions
                state["zhonglian_messages"] = msgs
                state["zhonglian"]["portfolio"] = {
                    "total_value": round(total_val, 2),
                    "total_return_pct": total_ret,
                }
            else:
                # 日期未变，仅给出当前持�仓市值
                st = load_state()
                price = zhonglian["price"]
                s1 = st["strategy1"]; s2 = st["strategy2"]
                total_val = (s1["capital"] + (s1["shares"]*price if s1["position"] else 0) +
                             s2["capital"] + (s2["shares"]*price if s2["position"] else 0))
                total_ret = round((total_val - 2*100_000)/(2*100_000)*100, 2)
                state["zhonglian"]["portfolio"] = {
                    "total_value": round(total_val, 2),
                    "total_return_pct": total_ret,
                }
    except Exception as e:
        state["error_count"] = state.get("error_count", 0) + 1
        state["zhonglian"] = {"error": str(e)}
    return state

# ---------- Node 6: �� 获取外部知识（新闻 + 030 �� 健康度） ----------
def node_fetch_external(state: Dict[str, Any]) -> Dict[str, Any]:
    state = state.copy()
    try:
        # 1) 今日热点新闻（标题+简要摘要）
        news_list = get_today_news(limit=5)   # 返回 [{title, url, snippet}]
        news_summary = "\n".join([f"- {n['title']}: {n['snippet']}" for n in news_list])
        state["news_summary"] = news_summary
    except Exception as e:
        state["news_summary"] = f"新闻获取失败: {e}"
        state["error_count"] = state.get("error_count", 0) + 1

    try:
        # 2) 030 � 市场健康度（0‑10 分数）
        health = get_market_health()   # � 已经内部调用 service 里的�函数，返回 int 0‑10
        state["health_score"] = health
        # � 再让 LLM �� 做一次简短解读（可选）；LLM 不可用时用规则兜底
        prompt = f"今日030市场健康度为 {health} / 10，请用一句中文说明市场情�绪（�积极/正常/降�仓/空�仓）并给出简要操作建议。"
        health_comment = call_llm_with_tools([{"role":"user","content":prompt}])
        txt = extract_llm_content(health_comment)
        if isinstance(health_comment, dict) and "error" in health_comment:
            # LLM 不可用 -> 规则化解读
            state["health_comment"] = rule_health_comment(health)
        elif txt:
            state["health_comment"] = txt
        else:
            state["health_comment"] = rule_health_comment(health)
    except Exception as e:
        state["health_score"] = get_market_health()
        state["health_comment"] = rule_health_comment(state["health_score"])
    return state


def rule_health_comment(score):
    """030 健康度无 LLM 时的规则化解读。"""
    if score is None:
        return "健康度数据获取失败，保守观望。"
    if score >= 8:
        return f"市场情绪积极(健康度{score}/10)：可正常参与，维持正常仓位。"
    if score >= 6:
        return f"市场情绪正常偏暖(健康度{score}/10)：关注量能是否持续，仓位适中。"
    if score >= 4:
        return f"市场情绪偏谨慎(健康度{score}/10)：控制仓位，等待企稳信号。"
    return f"市场情绪低迷(健康度{score}/10)：建议降仓或空仓，规避风险。"

# ---------- Node 7: �� 获取操�盘手选股 ----------
def node_fetch_trader_picks(state: Dict[str, Any]) -> Dict[str, Any]:
    state = state.copy()
    try:
        # We assume WATCHLIST is available from analysis.service
        from analysis.service import WATCHLIST
        trader_picks = get_trader_picks(WATCHLIST)
        state["trader_picks"] = trader_picks
    except Exception as e:
        state["error_count"] = state.get("error_count", 0) + 1
        state["trader_picks"] = []
    return state

# ---------- Node 8: � 风险规则自然语言化 ----------
def node_apply_risk(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    读取 prompts/risk_instruction.txt（包含致命风险、�仓位�矩�阵等），
    让 LLM 在已有信息（signals、news、health）基�础上判断是否�触发风险，
    并给出调整后的�仓位建议。
    """
    state = state.copy()
    try:
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "risk_instruction.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            risk_instruction = f.read()

        # ---------- � 构建基�础上下文 ----------
        ctx = {
            "date": state.get("date"),
            "is_trading_day": state.get("is_trading_day"),
            "market_overview": state.get("market_overview"),
            "signal_summary": state.get("signal_summary"),
            "news_summary": state.get("news_summary"),
            "health_score": state.get("health_score"),
            "health_comment": state.get("health_comment"),
        }

        # ---------- � 把每只股的信号压�缩成易读文本（只取关�键字段） ----------
        lines = []
        for s in state.get("signals", []):
            if "error" in s:
                continue
            lines.append(
                f"{s['symbol']}({s['name']}): {s['signal']['level']} "
                f"(score={s['signal']['score']}) "
                f"原因:{s['signal']['reasons']} � 风险:{s['signal']['risks']}"
            )
        ctx["signals_text"] = "\n".join(lines[:30])   # � 防止 prompt 过长

        # ---------- � 检索相关经验（few‑shot） ----------
        # 我们把新闻热点 + � 市场健康度 + � 风险规则作为查�询向量，
        # 目标是�找出过去类似情况下的经验教�训。
        query_parts = []
        if ctx["news_summary"]:
            query_parts.append(ctx["news_summary"][:200])   # 取前200字�避免过长
        if ctx["health_score"] is not None:
            query_parts.append(f"030健康度{ctx['health_score']}/10")
        # � 风险规则本身也可以作为查�询的一部分，但通常较长，这里取前200字
        if risk_instruction:
            query_parts.append(risk_instruction[:200])
        query_str = " 。 ".join(query_parts)
        if not query_str.strip():
            query_str = "今日市场情�绪与操作建议"
        top_experiences = retrieve_top_k(query_str, k=3)
        experience_block = "\n".join([f"- {exp}" for exp in top_experiences]) if top_experiences else "(�暂无相关经验)"

        # ---------- 组装最终 Prompt ----------
        final_prompt = f"""
你是一位资深的 A �� 股量化风险官。请根据以下信息，严格�遵守下面的风险规则，并给出对每只股票的操作建议（�买入/�卖出/观望），以及是否需要调整总�仓上限。

[风险规则]
{risk_instruction}

[历史经验（供参考）]
{experience_block}

[今日行情概�览]
市场上�涨家数: {ctx['market_overview'].get('up')}
市场下�跌家数: {ctx['market_overview'].get('down')}
总成交�额(亿): {ctx['market_overview'].get('total_amount_billion')}

[信号摘要（前30只）]
{ctx['signals_text']}

[新闻热点]
{ctx['news_summary']}

[030 � 市场健康度]
得分: {ctx['health_score']}/10
解读: {ctx['health_comment']}

请输出JSON格式，包含两个字段：
1. "risk_flag": � 若�触发致命风险则�填 "致命风险"，否则�填 "无"。
2. "advice_list": � 每只股票的建议列表，每项包含 symbol、action（BUY/SELL/HOLD）、reason（简要原因）。
"""
        # 始终先跑规则化风控（保证止损位、风险第一等硬性规则）——这是权威输出
        rule_flag, rule_advice = rule_risk_advice(state)
        state["risk_flag"] = rule_flag
        state["final_advice"] = json.dumps(rule_advice, ensure_ascii=False, indent=2)

        # LLM 增强：仅作日志记录/事后复盘参考，不覆盖规则输出
        try:
            llm_result = call_llm_with_tools([
                {"role":"system","content":"你是一个严格遵守风险规则的量化助手。"},
                {"role":"user","content":final_prompt}
            ])
            content_txt = extract_llm_content(llm_result)
            if content_txt:
                parsed = _parse_json_loose(content_txt)
                if parsed:
                    # 记录 LLM 视角供复盘参考，不改变最终输出
                    state["llm_risk_perspective"] = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 静默忽略，规则输出已就绪
    except Exception as e:
        # 异常时也走规则兜底，而不是直接置 error (避免 error_count 累积导致重试/报警)
        rule_flag, rule_advice = rule_risk_advice(state)
        state["risk_flag"] = rule_flag
        state["final_advice"] = json.dumps(rule_advice, ensure_ascii=False, indent=2)
    return state


def _parse_json_loose(text):
    """从 LLM 文本里尽力提取 JSON 对象（支持 ```json 代码块、前后多余文字）。提取失败返回 None。"""
    import re as _re
    if not text:
        return None
    s = text.strip()
    # 去掉 ```json ... ``` 围栏
    fenced = _re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
    if fenced:
        s = fenced[-1].strip()
    # 直接解析
    import json as _json
    try:
        return _json.loads(s)
    except Exception:
        pass
    # 提取第一个 {...}（含嵌套）
    try:
        start = s.find("{")
        if start < 0:
            return None
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{": depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    return _json.loads(s[start:i+1])
    except Exception:
        pass
    return None


def rule_risk_advice(state):
    """LLM 不可用时的规则化风险决策：根据已有信号与市场概览给出风险标记+操作建议。
    030 风控要点：
      - 无止损即无交易：BUY 必须给出参考止损/支撑位。
      - 风险第一：中联重科综合最优触发卖出时强制 SELL。
      - 避免模板化：reason 携带现价/止损位，减少连续数日完全相同理由。
      - 重复 BUY 钝化：同一股票连续买入信号仅首次执行，后续降级 HOLD。
    """
    signals = state.get("signals", [])
    # 三因素共振门控（情绪+基本面，技术维度由信号级承载）—— 2026-08-19 接入
    _res_gate = None
    try:
        _res_gate = ResonanceGate()
    except Exception:
        _res_gate = None
    
    # ── 重复 BUY 钝化：读取/初始化持久化计数器 ──
    buy_streak_path = os.path.join(os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace"),
                                    "data", "buy_streak.json")
    try:
        with open(buy_streak_path, "r", encoding="utf-8") as _f:
            buy_streaks = json.load(_f)
    except Exception:
        buy_streaks = {}
    
    serious_buy = serious_sell = fail = 0
    advice_list = []

    # ── 2026-08-18 修复#3：双链路口径一致性校验 ──
    # adaptive_dual.py 的 030 裁决（含减仓/卖出）已落盘 data/arbitration_YYYY-MM-DD.json。
    # run_agent 的规则建议若与仲裁方向冲突（本链判 BUY/关注，仲裁判 减仓/卖出），
    # 一律降级为 HOLD 并注明来源，避免同一股票同日给相反信号。
    arb_by_symbol = {}
    try:
        arb_path = os.path.join(os.getenv("WORKSPACE", "/Users/duguke/.openclaw/workspace"),
                                "data", f"arbitration_{datetime.now():%Y-%m-%d}.json")
        if os.path.exists(arb_path):
            with open(arb_path, "r", encoding="utf-8") as _f:
                _arb = json.load(_f)
            if isinstance(_arb, list):
                arb_by_symbol = {str(r.get("symbol")): r for r in _arb if isinstance(r, dict)}
    except Exception:
        arb_by_symbol = {}  # 仲裁文件缺失/损坏时不阻塞，退回原逻辑

    # 中联重科双策略综合最优是否触发卖出
    # 2026-08-19 修复：卖出建议必须结合实际持仓 —— 空仓时技术面卖出信号只是"不宜买入/持有"，
    # 不构成"强制SELL"建议（无仓可卖出，应归为 HOLD/观望），否则与 030 仲裁"持有"矛盾
    # （challenge_review 高危#1 复现）。
    zl_sell, zl_strat = False, None
    zl_holding = {}  # 记录各策略是否实际持仓
    try:
        zl = state.get("zhonglian") or {}
        st = load_state() if "load_state" in globals() else {}  # 持久化持仓
        for k, v in (zl.get("signals") or {}).items():
            if not isinstance(v, dict):
                continue
            # 判断该策略对应持久化 key 是否持仓；找不到则按空仓处理（保守）
            holding = False
            if isinstance(st, dict):
                for vkey in (k, str(k), "strategy1", "strategy2"):
                    _h = st.get(vkey)
                    if isinstance(_h, dict) and _h.get("position"):
                        holding = True
                        break
            zl_holding[k] = holding
            if v.get("sell") and holding:
                zl_sell, zl_strat = True, v.get("name") or k
    except Exception:
        pass
    for s in signals:
        if "error" in s:
            fail += 1
            continue
        sym = s.get("symbol")
        name = s.get("name")
        sig = s.get("signal", {})
        raw_level = sig.get("level", "中性")
        level = re.sub(r"[\ufffd\ufeff\s]", "", str(raw_level))
        score = sig.get("score", 0)
        close = s.get("price") or sig.get("price") or sig.get("close")
        # 风险优先：中联重科综合最优卖出 -> 强制 SELL
        if sym == "000157" and zl_sell:
            serious_sell += 1
            advice_list.append({
                "symbol": sym, "name": name, "action": "SELL",
                "reason": "中联重科综合最优策略(%s)触发卖出；现价%s，按030风险第一执行离场/减仓" % (zl_strat, close),
            })
            continue
        if "强烈卖出" in level:
            serious_sell += 1
            action = "SELL"
            reason = "信号级:%s 分:%s" % (level, score)
        elif "强烈买入" in level or "关注" in level:
            # ── 2026-08-19 修复#4：破位回落拦截 —— 收盘价已跌破 EMA26(中期趋势转空) 时，
            #    买入/关注信号强制降级为观望(HOLD)，禁止接飞刀。
            #    依据：2026-08-19 国瓷材料 5日-13%、盘中破止损仍被给"强烈买入"——BUY 评级明显失当；
            #    以及记忆中的 08-02"天量高开低走=出货"教训。
            _close_val = float(close) if close else None
            _ema26_val = None
            _ind = s.get("indicators") or {}
            if isinstance(_ind, dict):
                try: _ema26_val = float(_ind.get("EMA26")) if _ind.get("EMA26") is not None else None
                except Exception: _ema26_val = None
            if _close_val is not None and _ema26_val is not None and _close_val < _ema26_val:
                action = "HOLD"
                reason = ("破位回落拦截:现价¥%s已跌破EMA26(¥%s)，中期趋势转空，买入/关注降级为观望，禁追。"
                          % (close, round(_ema26_val,2)))
                advice_list.append({"symbol": sym, "name": name, "action": action,
                                    "reason": reason})
                continue
            # ── 2026-08-19 三因素共振门控：情绪+基本面两维未达标，买入降级为观望 ──
            if _res_gate is not None:
                try:
                    _ok, _ginfo = _res_gate.check_buy(str(sym))  # 技术维度已由信号级确认
                    if not _ok:
                        action = "HOLD"
                        reason = ("三因素共振未达标:%s；买入/关注降级为观望，不追。"
                                  % _ginfo.get("reason", ""))
                        advice_list.append({"symbol": sym, "name": name, "action": action,
                                            "reason": reason})
                        continue
                except Exception:
                    pass  # 门控异常时退回原 BUY 逻辑，不阻断
            # ── 2026-08-18 修复#3：与 adaptive_dual 030 仲裁做一致性校验 ──
            # 仲裁判 减仓/卖出，本链却判 BUY —— 方向冲突，降级为 HOLD 并注明来源，
            # 避免同一股票同日给相反信号（如中船汉光 08-18：本链关注/BUY vs 仲裁减仓）。
            _arb = arb_by_symbol.get(str(sym))
            if _arb:
                _fa = str(_arb.get("final_action", ""))
                _bearish_arb = ("减仓" in _fa) or ("卖出" in _fa)
                if _bearish_arb:
                    serious_buy -= 1 if serious_buy > 0 else 0
                    action = "HOLD"
                    _arb_reason = str(_arb.get("reason", ""))
                    reason = ("与030仲裁方向冲突(仲裁:%s，本链信号级%s)；降级为持有/观望，不追。"
                              % (_fa, level))
                    advice_list.append({"symbol": sym, "name": name, "action": action,
                                        "reason": reason})
                    # 仲裁冲突时重置买入钝化计数
                    buy_streaks[str(sym)] = 0
                    continue
            
            # ── 2026-09-02 重复 BUY 钝化：同一股票连续买入仅首次执行 ──
            current_streak = buy_streaks.get(str(sym), 0)
            if current_streak > 0:
                # 连续买入信号，钝化降级为 HOLD
                action = "HOLD"
                reason = ("重复BUY钝化(连续第%s日)：信号级%s 分%s｜现价¥%s｜原止损¥%s；已连续%s日给出买入，暂不追加"
                          % (current_streak, level, score, close, round(float(close)*0.95,2) if close else 0, current_streak))
                advice_list.append({"symbol": sym, "name": name, "action": action,
                                    "reason": reason})
                buy_streaks[str(sym)] = current_streak + 1
                continue
            
            serious_buy += 1
            action = "BUY"
            if close:
                stop_ref = round(float(close) * 0.95, 2)
                sup_ref = round(float(close) * 0.92, 2)
                # 2026-08-18 修复#2：塞入指标数值差异化理由，避免多股同款模板
                ind = s.get("indicators") or {}
                if isinstance(ind, dict):
                    _rsi = ind.get("RSI14"); _e12 = ind.get("EMA12"); _e26 = ind.get("EMA26")
                    _macd = ind.get("MACD")
                    diff = f"｜RSI{_rsi}｜EMA12/26:{_e12}/{_e26}｜MACD差{_macd}" if _rsi is not None else ""
                else:
                    diff = ""
                reason = ("信号级:%s 分:%s｜现价¥%s%s｜参考止损¥%s(-5%%)，参考支撑¥%s；跌破止损位无条件离场"
                          % (level, score, close, diff, stop_ref, sup_ref))
            else:
                reason = "信号级:%s 分:%s（现价缺失，无法给出精确止损，谨慎执行）" % (level, score)
            # 首次买入，记录钝化计数
            buy_streaks[str(sym)] = 1
        else:
            action = "HOLD"
            # 非买入信号，重置该股票的买入钝化计数
            buy_streaks[str(sym)] = 0
            # 2026-08-18 修复#2：持有/中性也携带指标数值差异化
            ind = s.get("indicators") or {}
            if isinstance(ind, dict) and ind.get("RSI14") is not None:
                reason = "信号级:%s 分:%s｜RSI%s｜EMA12/26:%s/%s" % (
                    level, score, ind.get("RSI14"), ind.get("EMA12"), ind.get("EMA26"))
            else:
                reason = "信号级:%s 分:%s" % (level, score)
        advice_list.append({
            "symbol": sym, "name": name, "action": action,
            "reason": reason,
        })
    # 保存买入钝化计数器
    try:
        with open(buy_streak_path, "w", encoding="utf-8") as _f:
            json.dump(buy_streaks, _f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    
    n = len(signals) or 1
    if fail >= n * 0.6:
        return "致命风险", [{"action":"SELL","reason":"数据大面积获取失败，无法确认安全，按 030 风控降仓"}]
    if serious_sell >= 10:
        return "致命风险", advice_list
    if serious_sell >= 7:
        return "高风险", advice_list
    if serious_sell >= 4:
        return "中风险", advice_list
    if serious_buy >= 5:
        return "无", advice_list
    return "无", advice_list

# ---------- Node 9: 生成最终报告（可选） ----------
def node_make_report(state: Dict[str, Any]) -> Dict[str, Any]:
    state = state.copy()
    # 这里只做一个简单的文本报告，实际推送由外�层脚本决定
    report = f"""=== AI 智能体每日�盯�盘报告 ({state.get('date')}) ===
交易日状态: {"是" if state.get('is_trading_day') else "否（使用最近交易日数据）"}
数据日期: {state.get('data_date')}
市场概�览: 上�涨{state.get('market_overview',{}).get('up')}只，
          下�跌{state.get('market_overview',{}).get('down')}只，
          成交�额{state.get('market_overview',{}).get('total_amount_billion')}亿。

信号分布: � 强烈�买入{state.get('signal_summary',{}).get('强烈�买入',0)}，
          关注{state.get('signal_summary',{}).get('关注',0)}，
          中性{state.get('signal_summary',{}).get('中性',0)}，
          �� 谨�慎{state.get('signal_summary',{}).get('�谨�慎',0)}，
          � 强烈�卖出{state.get('signal_summary',{}).get('强烈�卖出',0)}，
          失败{state.get('signal_summary',{}).get('失败',0)}。

030 �� 健康度: {state.get('health_score')}/10
健康度解读: {state.get('health_comment')}
今日热点新闻:
{state.get('news_summary')}

风险标记: {state.get('risk_flag')}
操作建议（JSON）:
{state.get('final_advice')}

中联重科双策略:
{json.dumps(state.get('zhonglian', {}), ensure_ascii=False, indent=2)}

操�盘手选股:
{json.dumps(state.get('trader_picks', []), ensure_ascii=False, indent=2)}
"""
    state["report_text"] = report
    return state

# ---------- Node 10: 判断完成定义（DoD） ----------
def node_check_done(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    DoD 定义为：
    1. 所有关注股的信号已经生成（signals � 长度等于 WATCHLIST � 长度，�忽略错误项）。
    2. � 已生成最终报告（report_text 非空）。
    3. 如若出现致命风险，则强制标记为 done（不再继续�循环）。
    """
    state = state.copy()
    expected = len(WATCHLIST)
    actual_signals = [s for s in state.get("signals", []) if "error" not in s]
    # 说明(2026-08-07修复)：原文 `risk_flag != "致命风险"` 导致当风险=致命风险时 done 恒为 False，
    # 图会空转 3 轮(触发 [ALERT])且不产出报告文本。按文档意图：风险已处理(致命风险也已处理)即视为完成。
    done = (
        len(actual_signals) >= expected and
        bool(state.get("report_text"))
    )
    state["done"] = done
    return state