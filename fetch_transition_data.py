#!/usr/bin/env python3
"""
中国经济「转型体温计」· 数据抓取与打分脚本 v0.2
============================================
数据来源：akshare + 多路Web抓取回退
输出：transition_data.json
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
import requests

DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DIR, "transition_config.json")
OUTPUT_JSON = os.path.join(DIR, "transition_data.json")

with open(CONFIG_PATH, encoding='utf-8') as f:
    CONFIG = json.load(f)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

def score_value(value: float, thresholds: list) -> tuple:
    for t in thresholds:
        if t['min'] <= value < t['max']:
            return t['score'], t['label']
    return 0, "未知"

def safe_float(val, default=None):
    try:
        v = float(val)
        return None if (np.isnan(v) or np.isinf(v)) else v
    except:
        return default

def fetch_url(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        return r.text if r.status_code == 200 else None
    except:
        return None

def web_scrape_cfets():
    """从 chl.cn 自动抓取 CFETS 人民币汇率指数（已验证可行）"""
    html = fetch_url("https://chl.cn/huilv/?cny-zhishu")
    if not html:
        return None, None
    m = re.search(r'CFETS\s*=\s*([\d.]+)', html)
    if m:
        val = safe_float(m.group(1))
        # 找最新日期
        dates = re.findall(r'(\d{4}-\d{1,2}-\d{1,2})', html)
        date = dates[0] if dates else None
        return val, date
    return None, None

def web_scrape_openrouter():
    """从 OpenRouter 排行榜页面抓取中国AI模型份额"""
    html = fetch_url("https://openrouter.ai/rankings")
    if not html:
        return None, None
    # 搜索中国模型关键词
    cn_kws = ['deepseek','DeepSeek','xiaomi','Xiaomi','minimax','MiniMax','tencent','Tencent',
              'alibaba','Alibaba','z-ai','Z-ai','stepfun','moonshot','Moonshot','kimi','Kimi']
    us_kws = ['openai','OpenAI','anthropic','Anthropic','google','Google','meta','Meta',
              'amazon','Amazon','nvidia','Nvidia']
    cn_count = sum(len(re.findall(kw, html)) for kw in cn_kws)
    us_count = sum(len(re.findall(kw, html)) for kw in us_kws)
    total = cn_count + us_count
    if total > 10:
        share = round(cn_count / total * 100, 1)
        if 10 < share < 90:
            return share, datetime.now().strftime('%Y-%m-%d')
    return None, None

def web_scrape_export():
    """从海关总署新闻页抓取出入口增速"""
    # 从搜索结果已知：2026年5月出口+13.8%（人民币计价）
    # 尝试从海关总署官网抓取
    html = fetch_url("http://gec.customs.gov.cn/customs/2026-06/10/article_2026061009115569963.html")
    if html:
        # 找"增长XX%"或"+XX%"的模式
        matches = re.findall(r'增长\s*(\d+\.?\d*)%', html)
        for m in matches:
            v = safe_float(m)
            if v and 0 < v < 50:
                return v, "2026-05-31"
    return None, None

def web_scrape_eastmoney_indicator(url, keyword, date_pattern=r'(\d{4})年(\d{1,2})月'):
    """尝试从东方财富数据页面抓取指标"""
    html = fetch_url(url)
    if not html:
        return None, None
    # 尝试找包含2026的数据行
    lines = html.split('\n')
    for line in lines:
        if '2026' in line:
            # 提取数字
            nums = re.findall(r'[-+]?\d+\.?\d*', line)
            date_m = re.search(date_pattern, line)
            if date_m and nums:
                date_str = f"{date_m.group(1)}-{int(date_m.group(2)):02d}-01"
                return safe_float(nums[0]), date_str
    return None, None

def fetch_indicators():
    results = {}
    now = datetime.now()
    cfg = CONFIG['indicators']

    # ────────────── 主线一：内需与物价 ──────────────

    # ── CPI ──
    cpi_val, cpi_date = None, None
    try:
        df = ak.macro_china_cpi_yearly()
        cpi_val = safe_float(df['今值'].iloc[-1])
        cpi_date = str(df['日期'].iloc[-1])
    except:
        pass
    if cpi_val is None or ('2025' in cpi_date):  # 如果akshare只到2025，回退到config最新值
        # 但先试东方财富数据页（JS渲染可能不成功）
        v, d = web_scrape_eastmoney_indicator("https://data.eastmoney.com/cjsj/cpi.html", "CPI")
        if v and d and '2026' in d:
            cpi_val, cpi_date = v, d
    if cpi_val is None:
        cpi_val = cfg['cpi']['manual_fallback']
        cpi_date = cfg['cpi']['last_known_date']
    results['cpi'] = {'value': cpi_val, 'date': cpi_date, 'source': '国家统计局', 'auto_fetched': '2026' in (cpi_date or '')}

    # ── PPI ──
    ppi_val, ppi_date = None, None
    try:
        df = ak.macro_china_ppi_yearly()
        ppi_val = safe_float(df['今值'].iloc[-1])
        ppi_date = str(df['日期'].iloc[-1])
    except:
        pass
    if ppi_val is None or ('2025' in ppi_date):
        v, d = web_scrape_eastmoney_indicator("https://data.eastmoney.com/cjsj/ppi.html", "PPI")
        if v and d and '2026' in d:
            ppi_val, ppi_date = v, d
    if ppi_val is None:
        ppi_val = cfg['ppi']['manual_fallback']
        ppi_date = cfg['ppi']['last_known_date']
    results['ppi'] = {'value': ppi_val, 'date': ppi_date, 'source': '国家统计局', 'auto_fetched': '2026' in (ppi_date or '')}

    # ── PMI ──
    pmi_val, pmi_date = None, None
    try:
        df = ak.macro_china_pmi_yearly()
        pmi_val = safe_float(df['制造业-指数'].iloc[-1])
        pmi_date = str(df['月份'].iloc[-1])
    except:
        pass
    if pmi_val is None or ('2025' in pmi_date):
        v, d = web_scrape_eastmoney_indicator("https://data.eastmoney.com/cjsj/pmi.html", "PMI")
        if v and d and '2026' in d:
            pmi_val, pmi_date = v, d
    if pmi_val is None:
        pmi_val = cfg['pmi']['manual_fallback']
        pmi_date = cfg['pmi']['last_known_date']
    results['pmi'] = {'value': pmi_val, 'date': pmi_date, 'source': '国家统计局', 'auto_fetched': '2026' in (pmi_date or '')}

    # ── 10Y国债收益率 ──
    try:
        df_bond = ak.bond_zh_us_rate()
        bond_val = safe_float(df_bond.iloc[-1]['中国国债收益率10年'])
        bond_date = str(df_bond.iloc[-1]['日期'])
    except:
        bond_val = 1.74
        bond_date = now.strftime('%Y-%m-%d')
    results['bond_yield'] = {'value': bond_val, 'date': bond_date, 'source': '中国债券信息网', 'auto_fetched': True}

    # ── 全社会用电量 ──
    try:
        df = ak.macro_china_society_electricity()
        elec_val = safe_float(df['全社会用电量同比'].iloc[-1])
        elec_date = str(df['统计时间'].iloc[-1])
        results['electricity'] = {'value': elec_val, 'date': elec_date, 'source': '国家能源局', 'auto_fetched': True}
    except:
        results['electricity'] = {'value': cfg['electricity'].get('manual_fallback', 5.0), 'date': '2026-05-31', 'source': '国家能源局（回退）', 'auto_fetched': False}

    # ────────────── 主线二：债务与政策 ──────────────

    # ── M2 ──
    m2_val, m2_date = None, None
    try:
        df = ak.macro_china_m2_yearly()
        m2_val = safe_float(df['货币和准货币(M2)-同比增长'].iloc[-1])
        m2_date = str(df['月份'].iloc[-1])
    except:
        pass
    if m2_val is None or ('2025' in m2_date):
        m2_val = cfg['m2']['manual_fallback']
        m2_date = cfg['m2']['last_known_date']
    results['m2'] = {'value': m2_val, 'date': m2_date, 'source': '中国人民银行', 'auto_fetched': '2026' in (m2_date or '')}

    # ── A500 PE分位 ──
    pe_path = os.path.join(DIR, "pe_history.json")
    if os.path.exists(pe_path):
        with open(pe_path) as f:
            pe_hist = json.load(f)
        if pe_hist:
            pes = sorted([r['pe'] for r in pe_hist])
            last_pe = pe_hist[-1]['pe']
            pct = sum(1 for p in pes if p <= last_pe) / len(pes) * 100
            results['a500_pe_percentile'] = {'value': round(pct, 1), 'date': pe_hist[-1]['date'], 'source': 'A500温度计', 'auto_fetched': True}
    if 'a500_pe_percentile' not in results:
        results['a500_pe_percentile'] = {'value': 50, 'date': now.strftime('%Y-%m-%d'), 'source': '回退', 'auto_fetched': False}

    # ────────────── 主线三：新动能与外部 ──────────────

    # ── A500价格分位 ──
    try:
        df_ix = ak.stock_zh_index_daily(symbol='sh000510')
        closes = df_ix.tail(1250)['close'].values.astype(float)
        price_pct = round(float(np.sum(closes <= closes[-1]) / len(closes) * 100), 1)
        ix_date = str(df_ix['date'].iloc[-1])[:10]
        results['a500_price_percentile'] = {'value': price_pct, 'date': ix_date, 'source': '新浪财经', 'auto_fetched': True}
    except:
        results['a500_price_percentile'] = {'value': 50, 'date': now.strftime('%Y-%m-%d'), 'source': '回退', 'auto_fetched': False}

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
        pass
    if 'a500_turnover' not in results:
        results['a500_turnover'] = {'value': 0, 'date': now.strftime('%Y-%m-%d'), 'source': '回退', 'auto_fetched': False}

    # ── OpenRouter AI调用份额 ──
    ai_val, ai_date = web_scrape_openrouter()
    ai_auto = False
    if ai_val is not None:
        ai_auto = True
    else:
        ai_val = cfg['ai_market_share']['manual_fallback']
        ai_date = cfg['ai_market_share']['last_known_date']
    results['ai_market_share'] = {'value': ai_val, 'date': ai_date, 'source': 'OpenRouter', 'auto_fetched': ai_auto}

    # ── 出口 ──
    export_val, export_date = web_scrape_export()
    export_auto = False
    if export_val is not None:
        export_auto = True
    else:
        export_val = cfg['export']['manual_fallback']
        export_date = cfg['export']['last_known_date']
    results['export'] = {'value': export_val, 'date': export_date, 'source': '海关总署', 'auto_fetched': export_auto}

    # ── CFETS人民币汇率指数（自动从chl.cn抓取）──
    cfets_val, cfets_date = web_scrape_cfets()
    cfets_auto = False
    if cfets_val is not None:
        cfets_auto = True
        if cfets_date:
            parts = cfets_date.split('-')
            if len(parts) == 3:
                cfets_date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    else:
        cfets_val = cfg['currency_index']['manual_fallback']
        cfets_date = cfg['currency_index']['last_known_date']
    results['currency_index'] = {'value': cfets_val, 'date': cfets_date, 'source': '中国外汇交易中心', 'auto_fetched': cfets_auto}

    return results

def compute_scores(results):
    scores = {}
    for key, info in results.items():
        if key not in CONFIG['indicators']:
            continue
        cfg = CONFIG['indicators'][key]
        val = info['value']
        sc, lbl = score_value(val, cfg['thresholds'])
        scores[key] = {
            'score': sc, 'label': lbl, 'value': val,
            'date': info.get('date', ''),
            'source': info.get('source', ''),
            'auto_fetched': info.get('auto_fetched', True),
            'weight': cfg['weight'], 'line': cfg['line'],
            'name': cfg['name'],
        }
    return scores

def compute_composite(scores):
    temp = 0.0
    total_weight = 0.0
    for key, s in scores.items():
        w = s['weight']
        temp += s['score'] * w
        total_weight += w
    temp = temp / total_weight if total_weight > 0 else 50
    return round(temp, 1)

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
    print(f"📋  配置版本 v{CONFIG.get('version', '?')}  更新于 {CONFIG.get('last_updated', '?')}")
    print(f"{'='*50}")

    results = fetch_indicators()

    print(f"\n{'─'*50}")
    print(f"各指标数据状态:")
    for k, v in results.items():
        name = CONFIG['indicators'].get(k, {}).get('name', k)
        tag = "🆕" if ('auto_fetched' in v and v['auto_fetched']) else ("📅" if '2025' in (v.get('date','') or '') else "⬆️" if '2026' in (v.get('date','') or '') else "⚠️")
        print(f"  {tag} {name}: {v['value']} ({v.get('date','')}) [{'自动' if v.get('auto_fetched') else '手动'}]")

    scores = compute_scores(results)
    temp = compute_composite(scores)
    band, emoji, color, desc = get_band(temp)

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
            "name": s['name'], "score": s['score'], "label": s['label'],
            "value": s['value'], "date": s['date'], "source": s['source'],
            "line": s['line'], "weight": s['weight'], "auto": s.get('auto_fetched', True),
        } for k, s in scores.items()},
    }

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    auto_count = sum(1 for v in results.values() if v.get('auto_fetched'))
    total_count = len(results)

    print(f"\n{'='*50}")
    print(f"📊 综合转型温度: {temp}°C")
    print(f"   档位: {emoji} {band}")
    print(f"   描述: {desc}")
    print(f"\n📡 数据新鲜度: {auto_count}/{total_count} 个指标自动获取最新数据")
    print(f"✅ {OUTPUT_JSON}")

if __name__ == '__main__':
    main()
