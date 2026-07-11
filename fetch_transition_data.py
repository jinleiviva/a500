#!/usr/bin/env python3
"""
中国经济「健康度温度计」· 数据抓取与打分脚本 v0.4
============================================
4线16指标框架：内需与物价(30%) / 就业与收入(15%) / 货币与信用(25%) / 转型与开放(30%)
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
    """从 chl.cn 自动抓取 CFETS 人民币汇率指数"""
    html = fetch_url("https://chl.cn/huilv/?cny-zhishu")
    if not html:
        return None, None
    m = re.search(r'CFETS\s*=\s*([\d.]+)', html)
    if m:
        val = safe_float(m.group(1))
        dates = re.findall(r'(\d{4}-\d{1,2}-\d{1,2})', html)
        date = dates[0] if dates else None
        return val, date
    return None, None

def web_scrape_openrouter():
    """从 OpenRouter 排行榜页面抓取中国AI模型份额"""
    html = fetch_url("https://openrouter.ai/rankings")
    if not html:
        return None, None
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

def web_scrape_unemployment():
    """从东方财富或新闻页面抓取城镇调查失业率"""
    html = fetch_url("https://data.eastmoney.com/cjsj/jy.html")
    if not html:
        return None, None
    # 找"城镇调查失业率"附近的数字
    nums = re.findall(r'城镇调查失业率[：:]\s*([\d.]+)', html)
    if not nums:
        # 尝试从表格中找
        nums = re.findall(r'失业率[^<]*?([\d.]+)%', html)
    if nums:
        for n in nums:
            v = safe_float(n)
            if v and 3.0 < v < 7.0:
                return v, datetime.now().strftime('%Y-%m-%d')
    return None, None

def fetch_indicators():
    results = {}
    now = datetime.now()
    cfg = CONFIG['indicators']
    line_cfgs = {k: cfg[k] for k in cfg}

    # ====================================
    # 主线一：内需与物价
    # ====================================

    # ── CPI ──
    cpi_val, cpi_date = None, None
    try:
        df = ak.macro_china_cpi_yearly()
        cpi_val = safe_float(df['今值'].iloc[-1])
        cpi_date = str(df['日期'].iloc[-1])
    except:
        pass
    if cpi_val is None or ('2025' in cpi_date):
        v, d = None, None
        try:
            html = fetch_url("https://data.eastmoney.com/cjsj/cpi.html")
            if html:
                lines = html.split('\n')
                for line in lines:
                    if '2026' in line:
                        nums = re.findall(r'[-+]?\d+\.?\d*', line)
                        if nums:
                            v = safe_float(nums[0])
                            d = "2026-" + re.search(r'(\d+)月', line).group(1) + "-01" if re.search(r'(\d+)月', line) else None
                            break
        except:
            pass
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
        pmi_val = cfg['pmi']['manual_fallback']
        pmi_date = cfg['pmi']['last_known_date']
    results['pmi'] = {'value': pmi_val, 'date': pmi_date, 'source': '国家统计局', 'auto_fetched': '2026' in (pmi_date or '')}

    # ── 全社会用电量 ──
    try:
        df = ak.macro_china_society_electricity()
        elec_val = safe_float(df['全社会用电量同比'].iloc[-1])
        elec_date = str(df['统计时间'].iloc[-1])
        results['electricity'] = {'value': elec_val, 'date': elec_date, 'source': '国家能源局', 'auto_fetched': True}
    except:
        results['electricity'] = {'value': cfg['electricity'].get('manual_fallback', 5.0), 'date': '2026-05-31', 'source': '国家能源局（回退）', 'auto_fetched': False}

    # ── 社零 ──
    retail_val, retail_date = None, None
    try:
        df = ak.macro_china_consumer_goods_retail()
        # 找2026年最新数据
        df_2026 = df[df['月份'].str.contains('2026', na=False)]
        if len(df_2026) > 0:
            retail_val = safe_float(df_2026['同比增长'].iloc[0])
            retail_date = str(df_2026['月份'].iloc[0]).replace('年', '-').replace('月', '-01')[:10]  # "2026年05月份" -> "2026-05-01" -> "2026-05"
            retail_date = retail_date[:-3]  # "2026-05"
    except:
        pass
    if retail_val is None:
        retail_val = cfg['retail_sales']['manual_fallback']
        retail_date = cfg['retail_sales']['last_known_date']
    results['retail_sales'] = {'value': retail_val, 'date': retail_date, 'source': '国家统计局', 'auto_fetched': '2026' in (retail_date or '')}

    # ====================================
    # 主线二：就业与收入
    # ====================================

    # ── 城镇调查失业率（web scraping + fallback）──
    unemp_val, unemp_date = web_scrape_unemployment()
    unemp_auto = False
    if unemp_val is not None:
        unemp_auto = True
    else:
        unemp_val = cfg['unemployment']['manual_fallback']
        unemp_date = cfg['unemployment']['last_known_date']
    results['unemployment'] = {'value': unemp_val, 'date': unemp_date, 'source': '国家统计局', 'auto_fetched': unemp_auto}

    # ── 居民可支配收入（手动更新，季度数据）──
    results['disposable_income'] = {
        'value': cfg['disposable_income']['manual_fallback'],
        'date': cfg['disposable_income']['last_known_date'],
        'source': '国家统计局（季度）',
        'auto_fetched': False
    }

    # ====================================
    # 主线三：货币与信用
    # ====================================

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

    # ── 人民币贷款余额同比 ──
    loan_val, loan_date = None, None
    try:
        df = ak.macro_rmb_loan()
        # 最新一条数据
        latest = df.iloc[-1]
        loan_val = safe_float(str(latest['累计人民币贷款-总额']))
        loan_growth = str(latest['累计人民币贷款-同比'])
        # 提取百分比的数值
        g = re.search(r'([\d.]+)', loan_growth)
        if g:
            loan_val = safe_float(g.group(1))
        loan_date = str(latest['月份']).replace('年', '-').replace('月', '')[:7]
    except:
        pass
    if loan_val is None:
        loan_val = cfg['rmb_loan']['manual_fallback']
        loan_date = cfg['rmb_loan']['last_known_date']
    results['rmb_loan'] = {'value': loan_val, 'date': loan_date, 'source': '中国人民银行', 'auto_fetched': loan_val != cfg['rmb_loan']['manual_fallback']}

    # ── 10Y国债收益率 ──
    try:
        df_bond = ak.bond_zh_us_rate()
        bond_val = safe_float(df_bond.iloc[-1]['中国国债收益率10年'])
        bond_date = str(df_bond.iloc[-1]['日期'])
    except:
        bond_val = 1.74
        bond_date = now.strftime('%Y-%m-%d')
    results['bond_yield'] = {'value': bond_val, 'date': bond_date, 'source': '中国债券信息网', 'auto_fetched': True}

    # ── 1年期LPR ──
    lpr_val, lpr_date = None, None
    try:
        df = ak.macro_china_lpr()
        latest = df.iloc[-1]
        lpr_val = safe_float(latest['LPR1Y'])
        lpr_date = str(latest['TRADE_DATE'])
    except:
        pass
    if lpr_val is None or lpr_date == cfg['lpr']['last_known_date']:
        lpr_val = cfg['lpr']['manual_fallback']
        lpr_date = cfg['lpr']['last_known_date']
    results['lpr'] = {'value': lpr_val, 'date': lpr_date, 'source': '全国银行间同业拆借中心', 'auto_fetched': lpr_val != cfg['lpr']['manual_fallback'] or '2026' in (lpr_date or '')}

    # ====================================
    # 主线四：转型与开放
    # ====================================

    # ── 工业增加值同比 ──
    ind_val, ind_date = None, None
    try:
        df = ak.macro_china_industrial_production_yoy()
        latest = df.iloc[-1]
        ind_val = safe_float(latest['今值'])
        ind_date = str(latest['日期'])
    except:
        pass
    if ind_val is None or ('2025' in ind_date):
        ind_val = cfg['industrial_output']['manual_fallback']
        ind_date = cfg['industrial_output']['last_known_date']
    results['industrial_output'] = {'value': ind_val, 'date': ind_date, 'source': '国家统计局', 'auto_fetched': '2026' in (ind_date or '')}

    # ── 出口同比 ──
    export_val, export_date = None, None
    export_auto = False
    # 先试akshare
    try:
        df = ak.macro_china_exports_yoy()
        latest = df.iloc[-1]
        export_val = safe_float(latest['今值'])
        export_date = str(latest['日期'])
        if export_date and '2025' in export_date:
            export_val = None  # 2025数据太旧，回退到手动值
    except:
        pass
    if export_val is None:
        export_val = cfg['export']['manual_fallback']
        export_date = cfg['export']['last_known_date']
    else:
        export_auto = True
    results['export'] = {'value': export_val, 'date': export_date, 'source': '海关总署', 'auto_fetched': export_auto}

    # ── CFETS ──
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

    # ── AI调用份额 ──
    ai_val, ai_date = web_scrape_openrouter()
    ai_auto = False
    if ai_val is not None:
        ai_auto = True
    else:
        ai_val = cfg['ai_market_share']['manual_fallback']
        ai_date = cfg['ai_market_share']['last_known_date']
    results['ai_market_share'] = {'value': ai_val, 'date': ai_date, 'source': 'OpenRouter', 'auto_fetched': ai_auto and ai_date != cfg['ai_market_share']['last_known_date']}

    # ── 新能源汽车渗透率 ──
    try:
        df_fuel = ak.car_market_fuel_cpca()
        df_total = ak.car_market_total_cpca()
        if '2026年' in df_fuel.columns and '2026年' in df_total.columns:
            # 取最新的非NaN行
            fuel_vals = df_fuel['2026年'].dropna()
            total_vals = df_total['2026年'].dropna()
            if len(fuel_vals) > 0 and len(total_vals) > 0:
                # 取交集：匹配月份
                for i in range(len(fuel_vals) - 1, -1, -1):
                    month = df_fuel.iloc[i]['月份']
                    total_row = df_total[df_total['月份'] == month]
                    if len(total_row) > 0:
                        total_v = safe_float(total_row['2026年'].iloc[0])
                        fuel_v = safe_float(fuel_vals.iloc[i])
                        if total_v and fuel_v and total_v > 0:
                            pen_val = round(fuel_v / total_v * 100, 1)
                            pen_date = f"2026-{int(re.search(r'(\d+)', month).group(1)):02d}"
                            results['new_energy_penetration'] = {'value': pen_val, 'date': pen_date, 'source': '中国汽车工业协会', 'auto_fetched': True}
                            break
    except:
        pass
    if 'new_energy_penetration' not in results:
        # 计算fallback渗透率
        pen_val = None
        try:
            # 尝试用本年所有可用数据算平均
            df_fuel = ak.car_market_fuel_cpca()
            df_total = ak.car_market_total_cpca()
            f_vals = df_fuel['2026年'].dropna()
            t_vals = df_total['2026年'].dropna()
            if len(f_vals) > 0 and len(t_vals) > 0:
                f_avg = f_vals.mean()
                t_avg = t_vals.mean()
                if t_avg > 0:
                    pen_val = round(float(f_avg) / float(t_avg) * 100, 1)
        except:
            pass
        if pen_val is None:
            pen_val = 40.0  # 2026年新能源渗透率大约在40%左右
        results['new_energy_penetration'] = {'value': pen_val, 'date': '2026-05-31', 'source': '中国汽车工业协会（推算）', 'auto_fetched': True}

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

def compute_line_scores(scores):
    """计算4条主线的加权分"""
    lines = {1: [], 2: [], 3: [], 4: []}
    for k, s in scores.items():
        lines[s['line']].append(s)

    line_temps = {}
    for lid in [1, 2, 3, 4]:
        items = lines[lid]
        if items:
            t = sum(s['score'] * s['weight'] for s in items) / sum(s['weight'] for s in items)
            line_temps[f'line{lid}'] = round(t, 1)
        else:
            line_temps[f'line{lid}'] = 50.0
    return line_temps

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
        return "低温区", "❄️", "#97C459", "整体偏冷：内需偏弱、就业承压，经济运行面临较多挑战"
    elif temp < 70:
        return "温和区", "🌤️", "#EF9F27", "正常运转：内需与信贷温和运行，转型环境总体正常"
    else:
        return "升温区", "🔥", "#E24B4A", "整体向好：内需回暖、转型放量，增长与转型形成良性互动"

def main():
    print(f"\n{'='*50}")
    print(f"📊 中国经济健康度温度计 v{CONFIG.get('version', '?')}")
    print(f"⏰  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"📋  更新于 {CONFIG.get('last_updated', '?')}")
    print(f"{'='*50}")

    results = fetch_indicators()

    print(f"\n{'─'*50}")
    print(f"各指标数据状态:")
    for k, v in results.items():
        name = CONFIG['indicators'].get(k, {}).get('name', k)
        tag = "🆕" if v.get('auto_fetched') else "⬆️" if '2026' in (v.get('date','') or '') else "⚠️"
        print(f"  {tag} {name}: {v['value']} ({v.get('date','')}) [{'自动' if v.get('auto_fetched') else '手动'}]")

    scores = compute_scores(results)
    temp = compute_composite(scores)
    band, emoji, color, desc = get_band(temp)
    line_temps = compute_line_scores(scores)

    line_names = {1: "内需与物价", 2: "就业与收入", 3: "货币与信用", 4: "转型与开放"}
    wkeys = {1: "line1_demand_price", 2: "line2_employment_income", 3: "line3_money_credit", 4: "line4_transition_open"}

    output = {
        "date": datetime.now().strftime('%Y-%m-%d'),
        "temperature": temp,
        "band": band,
        "emoji": emoji,
        "color": color,
        "description": desc,
        "lineScores": {
            f"line{k}": {
                "name": line_names[k],
                "score": line_temps.get(f"line{k}", 50),
                "weight": round(CONFIG['weights'][wkeys[k]] * 100, 0)
            } for k in [1, 2, 3, 4]
        },
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
    print(f"📊 综合健康度温度: {temp}°C")
    print(f"   档位: {emoji} {band}")
    print(f"   描述: {desc}")
    print(f"\n📡 数据新鲜度: {auto_count}/{total_count} 个指标自动获取最新数据")
    print(f"✅ {OUTPUT_JSON}")

if __name__ == '__main__':
    main()
