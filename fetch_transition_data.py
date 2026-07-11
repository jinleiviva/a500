#!/usr/bin/env python3
"""
中国经济「转型体温计」· 数据抓取与打分脚本
========================================
生成 transition_data.json，供 A500 温度计页面中的「转型」TAB 读取。

用法:  python3 fetch_transition_data.py
输出:  transition_data.json  transition_dashboard.html（独立预览）
"""

import os, sys, json, re
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 清代理 ──
for k in list(os.environ.keys()):
    if k.lower().endswith('_proxy') or k.lower() == 'no_proxy':
        os.environ.pop(k, None)

import akshare as ak
import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DIR, "transition_config.json")
OUTPUT_JSON = os.path.join(DIR, "transition_data.json")
TEMPLATE_PATH = os.path.join(DIR, "a500_template.html")

# ── 加载配置 ──
with open(CONFIG_PATH, encoding='utf-8') as f:
    CONFIG = json.load(f)

def score_value(value: float, thresholds: list) -> tuple:
    """将原始值映射到 0-100 分数和标签"""
    for t in thresholds:
        if t['min'] <= value < t['max']:
            return t['score'], t['label']
    return 0, "未知"

def safe_float(val, default=None) -> float | None:
    try:
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
    except:
        return default

def fetch_indicators():
    """抓取所有指标数据"""
    results = {}
    now = datetime.now()
    cfg = CONFIG['indicators']

    # ── CPI ──
    cpi_val, cpi_date = None, None
    try:
        df = ak.macro_china_cpi_yearly()
        cpi_val = safe_float(df['今值'].iloc[-1])
        cpi_date = str(df['日期'].iloc[-1])
    except:
        pass
    if cpi_val is None:
        cpi_val = cfg['cpi']['manual_fallback']
        cpi_date = cfg['cpi']['last_known_date']
    results['cpi'] = {'value': cpi_val, 'date': cpi_date, 'source': '国家统计局'}

    # ── PPI ──
    ppi_val, ppi_date = None, None
    try:
        df = ak.macro_china_ppi_yearly()
        ppi_val = safe_float(df['今值'].iloc[-1])
        ppi_date = str(df['日期'].iloc[-1])
    except:
        pass
    if ppi_val is None:
        ppi_val = cfg['ppi']['manual_fallback']
        ppi_date = cfg['ppi']['last_known_date']
    results['ppi'] = {'value': ppi_val, 'date': ppi_date, 'source': '国家统计局'}

    # ── PMI ──
    pmi_val, pmi_date = None, None
    try:
        df = ak.macro_china_pmi_yearly()
        pmi_val = safe_float(df['制造业-指数'].iloc[-1])
        pmi_date = str(df['月份'].iloc[-1])
    except:
        pass
    if pmi_val is None:
        pmi_val = cfg['pmi']['manual_fallback']
        pmi_date = cfg['pmi']['last_known_date']
    results['pmi'] = {'value': pmi_val, 'date': pmi_date, 'source': '国家统计局'}

    # ── 10Y国债收益率（从已有数据读取） ──
    try:
        df_bond = ak.bond_zh_us_rate()
        bond_val = safe_float(df_bond.iloc[-1]['中国国债收益率10年'])
        bond_date = str(df_bond.iloc[-1]['日期'])
    except:
        bond_val = 1.74
        bond_date = now.strftime('%Y-%m-%d')
    results['bond_yield'] = {'value': bond_val, 'date': bond_date, 'source': '中国债券信息网'}

    # ── M2 ──
    m2_val, m2_date = None, None
    try:
        df = ak.macro_china_m2_yearly()
        m2_val = safe_float(df['货币和准货币(M2)-同比增长'].iloc[-1])
        m2_date = str(df['月份'].iloc[-1])
    except:
        pass
    if m2_val is None:
        m2_val = cfg['m2']['manual_fallback']
        m2_date = cfg['m2']['last_known_date']
    results['m2'] = {'value': m2_val, 'date': m2_date, 'source': '中国人民银行'}

    # ── A500 PE分位（从已有pe_history读） ──
    pe_path = os.path.join(DIR, "pe_history.json")
    if os.path.exists(pe_path):
        import json as j2
        with open(pe_path) as f:
            pe_hist = j2.load(f)
        if pe_hist:
            pes = sorted([r['pe'] for r in pe_hist])
            last_pe = pe_hist[-1]['pe']
            pct = sum(1 for p in pes if p <= last_pe) / len(pes) * 100
            results['a500_pe_percentile'] = {'value': round(pct, 1), 'date': pe_hist[-1]['date'], 'source': 'A500温度计'}
    if 'a500_pe_percentile' not in results:
        results['a500_pe_percentile'] = {'value': 50, 'date': now.strftime('%Y-%m-%d'), 'source': 'A500温度计（默认）'}

    # ── A500价格分位 ──
    try:
        df_ix = ak.stock_zh_index_daily(symbol='sh000510')
        closes = df_ix.tail(1250)['close'].values.astype(float)
        price_pct = round(float(np.sum(closes <= closes[-1]) / len(closes) * 100), 1)
        ix_date = str(df_ix['date'].iloc[-1])[:10]
        results['a500_price_percentile'] = {'value': price_pct, 'date': ix_date, 'source': '新浪财经'}
    except:
        results['a500_price_percentile'] = {'value': 50, 'date': now.strftime('%Y-%m-%d'), 'source': 'A500温度计（默认）'}

    # ── OpenRouter AI调用份额（改进抓取） ──
    ai_share = cfg['ai_market_share']['manual_fallback']  # 默认35%
    ai_date = cfg['ai_market_share']['last_known_date']
    ai_auto = False
    try:
        import requests
        # 尝试从多个来源获取
        urls_to_try = [
            "https://openrouter.ai/rankings",
            "https://openrouter.ai/stats",
        ]
        for rank_url in urls_to_try:
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                          'Accept': 'text/html,application/xhtml+xml'}
                resp = requests.get(rank_url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    text = resp.text
                    # 尝试多种模式提取中国模型份额
                    # 模式1: DeepSeek出现频率和占比
                    import re as re2
                    # 提取所有包含"tokens"的数据行
                    deepseek_count = len(re2.findall(r'deepseek', text, re2.I))
                    cn_count = len(re2.findall(r'(deepseek|xiaomi|minimax|tencent|alibaba|z-ai|stepfun|moonshot|kimi)', text, re2.I))
                    us_count = len(re2.findall(r'(openai|anthropic|google|meta|amazon|nvidia)', text, re2.I))
                    total = cn_count + us_count
                    if total > 10:  # 至少抓到10+个模型
                        share = round(cn_count / total * 100, 1)
                        if 20 < share < 80:  # 合理范围校验
                            ai_share = share
                            ai_date = now.strftime('%Y-%m-%d')
                            ai_auto = True
                            break
            except:
                continue
    except:
        pass
    results['ai_market_share'] = {'value': ai_share, 'date': ai_date, 'source': 'OpenRouter排行榜', 'auto_fetched': ai_auto}

    # ── 出口 ──
    results['export'] = {'value': cfg['export']['manual_fallback'], 'date': cfg['export']['last_known_date'], 'source': '海关总署（手动）', 'auto_fetched': False}

    # ── 汇率指数 ──
    results['currency_index'] = {'value': cfg['currency_index']['manual_fallback'], 'date': cfg['currency_index']['last_known_date'], 'source': '外汇交易中心（手动）', 'auto_fetched': False}

    # ── 全社会用电量同比（2026年最新！） ──
    try:
        df = ak.macro_china_society_electricity()
        # 最新行
        elec_val = safe_float(df['全社会用电量同比'].iloc[-1])
        elec_date = str(df['统计时间'].iloc[-1])
        if elec_val is not None:
            results['electricity'] = {'value': elec_val, 'date': elec_date, 'source': '国家能源局', 'auto_fetched': True}
    except:
        results['electricity'] = {'value': 5.0, 'date': '2026-05-31', 'source': '国家能源局（回退）', 'auto_fetched': False}

    # ── A500成交额变化 ──
    try:
        df_ix = ak.stock_zh_index_daily(symbol='sh000510')
        vols = df_ix.tail(15)['volume'].values.astype(float)
        if len(vols) >= 10:
            recent_wk = vols[-5:].mean()
            prev_wk = vols[-10:-5].mean()
            if prev_wk > 0:
                chg = round((recent_wk / prev_wk - 1) * 100, 1)
                ix_date2 = str(df_ix['date'].iloc[-1])[:10]
                results['a500_turnover'] = {'value': chg, 'date': ix_date2, 'source': '新浪财经', 'auto_fetched': True}
    except:
        results['a500_turnover'] = {'value': 0, 'date': now.strftime('%Y-%m-%d'), 'source': 'A500温度计（回退）', 'auto_fetched': False}

    return results

def compute_scores(results):
    """根据配置的阈值计算每个指标的得分"""
    scores = {}
    for key, info in results.items():
        if key not in CONFIG['indicators']:
            continue
        cfg = CONFIG['indicators'][key]
        val = info['value']
        sc, lbl = score_value(val, cfg['thresholds'])
        scores[key] = {
            'score': sc,
            'label': lbl,
            'value': val,
            'date': info.get('date', ''),
            'source': info.get('source', ''),
            'auto_fetched': info.get('auto_fetched', True),
            'weight': cfg['weight'],
            'line': cfg['line'],
            'name': cfg['name'],
        }
    return scores

def compute_composite(scores):
    """计算综合温度"""
    temp = 0.0
    total_weight_used = 0.0
    missing = []
    for key, s in scores.items():
        is_auto = s.get('auto_fetched', True)
        cfg = CONFIG['indicators'][key]
        # 如果是手动且用回退值，扣分但不跳过
        w = s['weight']
        temp += s['score'] * w
        total_weight_used += w
    
    if total_weight_used > 0:
        # 重整权重
        temp = temp / total_weight_used
    else:
        temp = 50  # 默认
    
    return round(temp, 1), missing

def get_band(temp):
    if temp < 40:
        return "低温区", "❄️", "#97C459", "内需与物价偏弱、转型动能尚未接续，处于承压阶段"
    elif temp < 70:
        return "温和区", "🌤️", "#EF9F27", "新旧动能交替中，经济温和运行、局部结构性改善"
    else:
        return "升温区", "🔥", "#E24B4A", "内需回暖、新动能放量、转型进度明显加快"

def main():
    print(f"\n{'='*50}")
    print(f"🌡️  中国经济「转型体温计」")
    print(f"⏰  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*50}")

    # 1. 抓取
    results = fetch_indicators()
    for k, v in results.items():
        status = "✅" if v.get('auto_fetched', True) else "⚠️"
        print(f"  {status} {CONFIG['indicators'][k]['name']}: {v['value']} ({v['date']})")

    # 2. 打分
    scores = compute_scores(results)
    
    # 3. 综合
    temp, missing = compute_composite(scores)
    band, emoji, color, desc = get_band(temp)

    # 4. 构建输出
    line_scores = {1: [], 2: [], 3: []}
    for k, s in scores.items():
        line_scores[s['line']].append(s)

    line_temps = {}
    for line_id, items in line_scores.items():
        if items:
            t = sum(s['score'] * s['weight'] for s in items) / sum(s['weight'] for s in items)
            line_temps[f'line{line_id}'] = round(t, 1)
    
    line_names = {1: "内需与物价", 2: "债务与政策", 3: "新动能与外部"}
    wkeys = {1: "line1_internal_demand", 2: "line2_debt_policy", 3: "line3_new_momentum"}

    output = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "temperature": temp,
        "band": band,
        "emoji": emoji,
        "color": color,
        "description": desc,
        "lineScores": {f"line{k}": {"name": line_names[k], "score": line_temps.get(f"line{k}", 50), "weight": round(CONFIG['weights'][wkeys[k]] * 100, 0)} for k in [1,2,3]},
        "indicators": {k: {
            "name": s['name'],
            "score": s['score'],
            "label": s['label'],
            "value": s['value'],
            "date": s['date'],
            "source": s['source'],
            "line": s['line'],
            "weight": s['weight'],
            "auto": s.get('auto_fetched', True),
        } for k, s in scores.items()},
    }

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n📊 综合转型温度: {temp}")
    print(f"   档位: {emoji} {band}")
    print(f"   描述: {desc}")
    print(f"\n✅ {OUTPUT_JSON}")

if __name__ == '__main__':
    main()
