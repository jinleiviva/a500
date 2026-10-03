#!/usr/bin/env python3
"""
中国经济「健康度温度计」· 数据抓取与打分脚本 v0.5
============================================
4线22指标框架：内需与物价(30%) / 就业与收入(15%) / 货币与信用(25%) / 转型与开放(30%)

v0.5 升级要点（解决"数据不更新"问题）：
1. 东方财富数据中心 API 直连作为首选源（带重试），akshare 降级为备用
   —— 旧版 akshare 的部分统计局接口在 Actions 环境间歇性失败，导致 CPI/PPI/PMI/M2
      长期停留在手动回退值（旧数据）。
2. 自愈机制：每次成功自动抓取后，把最新值写回 transition_config.json 的
   last_known_value / manual_fallback，回退值永远新鲜，不再"定格在旧数据"。
3. 新增 6 个指标（非制造业PMI / 固定资产投资 / 消费者信心 / M1 / 社融增量 / 进口同比），
   全部走可自动化的数据源。

输出：transition_data.json（并回写 transition_config.json）
"""

import os, sys, json, re, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 清代理 ──
for k in list(os.environ.keys()):
    if k.lower().endswith('_proxy') or k.lower() == 'no_proxy':
        os.environ.pop(k, None)

import numpy as np
import requests

try:
    import akshare as ak
    HAS_AK = True
except Exception:
    ak = None
    HAS_AK = False
    print("⚠️  akshare 不可用，仅使用直连数据源 + 回退值")

CUR_YEAR = datetime.now().year

DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(DIR, "transition_config.json")
OUTPUT_JSON = os.path.join(DIR, "transition_data.json")

with open(CONFIG_PATH, encoding='utf-8') as f:
    CONFIG = json.load(f)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}
EM_HEADERS = dict(HEADERS, Referer='https://data.eastmoney.com/')

# ══════════════════════════════════════════════════════
# 通用工具
# ══════════════════════════════════════════════════════

def score_value(value: float, thresholds: list) -> tuple:
    for t in thresholds:
        if t['min'] <= value < t['max']:
            return t['score'], t['label']
    return 0, "未知"

def safe_float(val, default=None):
    try:
        v = float(str(val).replace(',', '').replace('%', ''))
        return None if (np.isnan(v) or np.isinf(v)) else v
    except Exception:
        return default

def fetch_url(url, timeout=20, headers=None):
    try:
        r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

# ══════════════════════════════════════════════════════
# 东方财富数据中心直连（首选源，带重试）
# ══════════════════════════════════════════════════════

def em_get(report, pagesize=5, retries=3):
    """从东财数据中心取最近 pagesize 条记录，失败重试。返回 list[dict] 或 None"""
    url = (f"https://datacenter-web.eastmoney.com/api/data/v1/get"
           f"?sortColumns=REPORT_DATE&sortTypes=-1&pageSize={pagesize}&pageNumber=1"
           f"&reportName={report}&columns=ALL")
    for i in range(retries):
        try:
            r = requests.get(url, headers=EM_HEADERS, timeout=25)
            if r.status_code == 200:
                j = r.json()
                if j.get('result') and j['result'].get('data'):
                    return j['result']['data']
        except Exception as e:
            if i == retries - 1:
                print(f"  ⚠️ 东财 {report} 失败: {e}")
        time.sleep(1.5 * (i + 1))
    return None

def em_field(report, field, pagesize=5):
    """取东财某报表最新一条的某字段。返回 (value, 'YYYY-MM') 或 (None, None)"""
    rows = em_get(report, pagesize)
    if not rows:
        return None, None
    for row in rows:  # 最新一条字段可能为空，向前找
        v = safe_float(row.get(field))
        if v is not None:
            m = re.search(r'(\d{4})年(\d{1,2})月', row.get('TIME', ''))
            date = f"{m.group(1)}-{int(m.group(2)):02d}" if m else row.get('REPORT_DATE', '')[:7]
            return v, date
    return None, None

# ══════════════════════════════════════════════════════
# Web 抓取（Trading Economics / CFETS / OpenRouter）
# ══════════════════════════════════════════════════════

MONTH_MAP = {'January':'01','February':'02','March':'03','April':'04','May':'05','June':'06',
             'July':'07','August':'08','September':'09','October':'10','November':'11','December':'12'}

def web_scrape_te(url):
    """从 Trading Economics meta description 抓取指标数值和日期"""
    html = fetch_url(url)
    if not html:
        return None, None
    m = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]+)"', html)
    if not m:
        return None, None
    desc = m.group(1)
    val = None
    for p in [r'(?:increased|decreased|rose|fell)\s+(?:to\s+)?([\d.]+)\s*percent',
              r'([\d.]+)\s*percent']:
        vm = re.search(p, desc, re.IGNORECASE)
        if vm:
            val = safe_float(vm.group(1))
            if val is not None:
                break
    month_pat = r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    explicit_dates = re.findall(r'in ' + month_pat + r'\s+of\s+(\d{4})', desc)
    implicit_months = re.findall(r'in ' + month_pat + r'(?!\s+of\s)', desc)
    date_str = None
    if explicit_dates:
        year = explicit_dates[-1][1]
        latest_month = implicit_months[0] if implicit_months else explicit_dates[-1][0]
        date_str = f"{year}-{MONTH_MAP[latest_month]}"
    elif implicit_months:
        yr_m = re.search(r'(20\d{2})', desc)
        year = yr_m.group(1) if yr_m else str(datetime.now().year)
        date_str = f"{year}-{MONTH_MAP[implicit_months[0]]}"
    return val, date_str

def web_scrape_cfets():
    html = fetch_url("https://chl.cn/huilv/?cny-zhishu")
    if not html:
        return None, None
    m = re.search(r'CFETS\s*=\s*([\d.]+)', html)
    if m:
        val = safe_float(m.group(1))
        dates = re.findall(r'(\d{4}-\d{1,2}-\d{1,2})', html)
        return val, (dates[0] if dates else None)
    return None, None

def web_scrape_openrouter():
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

# ══════════════════════════════════════════════════════
# 回退值工具
# ══════════════════════════════════════════════════════

def cfg_fallback(key):
    c = CONFIG['indicators'][key]
    return c.get('manual_fallback'), c.get('last_known_date')

def is_cur_year(date_str):
    return bool(date_str) and str(CUR_YEAR) in str(date_str)

# ══════════════════════════════════════════════════════
# 指标抓取
# ══════════════════════════════════════════════════════

def fetch_indicators():
    results = {}
    now = datetime.now()
    cfg = CONFIG['indicators']

    # ══════ 主线一：内需与物价 ══════

    # ── CPI：东财直连 → akshare → 回退 ──
    v, d = em_field('RPT_ECONOMY_CPI', 'NATIONAL_SAME')
    auto = v is not None
    if not auto and HAS_AK:
        try:
            df = ak.macro_china_cpi_yearly()
            v = safe_float(df['今值'].iloc[-1])
            d = str(df['日期'].iloc[-1])
            auto = v is not None and is_cur_year(d)
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('cpi')
    results['cpi'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ── PPI ──
    v, d = em_field('RPT_ECONOMY_PPI', 'BASE_SAME')
    auto = v is not None
    if not auto and HAS_AK:
        try:
            df = ak.macro_china_ppi_yearly()
            v = safe_float(df['今值'].iloc[-1])
            d = str(df['日期'].iloc[-1])
            auto = v is not None and is_cur_year(d)
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('ppi')
    results['ppi'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ── 制造业PMI ──
    v, d = em_field('RPT_ECONOMY_PMI', 'MAKE_INDEX')
    auto = v is not None
    if not auto and HAS_AK:
        try:
            df = ak.macro_china_pmi_yearly()
            v = safe_float(df['制造业-指数'].iloc[-1])
            d = str(df['月份'].iloc[-1])
            auto = v is not None and is_cur_year(d)
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('pmi')
    results['pmi'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ── 非制造业PMI（新）──
    v, d = em_field('RPT_ECONOMY_PMI', 'NMAKE_INDEX')
    auto = v is not None
    if not auto:
        v, d = cfg_fallback('pmi_nonmfg')
    results['pmi_nonmfg'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ── 社零 ──
    v, d = em_field('RPT_ECONOMY_TOTAL_RETAIL', 'RETAIL_TOTAL_SAME')
    auto = v is not None
    if not auto and HAS_AK:
        try:
            df = ak.macro_china_consumer_goods_retail()
            df_cy = df[df['月份'].str.contains(str(CUR_YEAR), na=False)]
            if len(df_cy) > 0:
                v = safe_float(df_cy['同比增长'].iloc[0])
                d = str(df_cy['月份'].iloc[0]).replace('年', '-').replace('月', '-01')[:10][:-3]
                auto = v is not None
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('retail_sales')
    results['retail_sales'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ── 全社会用电量（akshare 国家能源局源，此前工作正常）──
    v, d, auto = None, None, False
    if HAS_AK:
        try:
            df = ak.macro_china_society_electricity()
            v = safe_float(df['全社会用电量同比'].iloc[-1])
            d = str(df['统计时间'].iloc[-1]).replace('.', '-')
            parts = d.split('-')
            if len(parts) == 2:
                d = f"{parts[0]}-{int(parts[1]):02d}"
            auto = v is not None
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('electricity')
    results['electricity'] = {'value': v, 'date': d, 'source': '国家能源局', 'auto_fetched': auto}

    # ── 固定资产投资累计同比（新）：东财累计值自行计算同比 ──
    v, d, auto = None, None, False
    rows = em_get('RPT_ECONOMY_ASSET_INVEST', pagesize=24)
    if rows:
        by_month = {r['REPORT_DATE'][:7]: r for r in rows}
        latest = rows[0]
        cur_key = latest['REPORT_DATE'][:7]
        try:
            y, mth = int(cur_key[:4]), int(cur_key[5:7])
            ly = by_month.get(f"{y-1}-{mth:02d}")
            cur_acc = safe_float(latest.get('BASE_ACCUMULATE'))
            ly_acc = safe_float(ly.get('BASE_ACCUMULATE')) if ly else None
            if cur_acc and ly_acc and ly_acc > 0:
                v = round((cur_acc / ly_acc - 1) * 100, 1)
                d = cur_key
                auto = True
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('fixed_asset_investment')
    results['fixed_asset_investment'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ══════ 主线二：就业与收入 ══════

    # ── 城镇调查失业率（Trading Economics）──
    v, d = web_scrape_te("https://tradingeconomics.com/china/unemployment-rate")
    auto = bool(v is not None and d and is_cur_year(d))
    if not auto:
        v, d = cfg_fallback('unemployment')
    results['unemployment'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ── 消费者信心指数（新，东财）──
    v, d = em_field('RPT_ECONOMY_FAITH_INDEX', 'CONSUMERS_FAITH_INDEX')
    auto = v is not None
    if not auto:
        v, d = cfg_fallback('consumer_confidence')
    results['consumer_confidence'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ── 居民可支配收入累计同比（Trading Economics 年度）──
    v, d, auto = None, None, False
    html = fetch_url("https://tradingeconomics.com/china/disposable-personal-income")
    if html:
        m = re.search(r'increased to ([\d,.]+)\s*CNY in (\d{4}) from ([\d,.]+)\s*CNY in (\d{4})', html)
        if m:
            cur = safe_float(m.group(1))
            prev = safe_float(m.group(3))
            if cur and prev and prev > 0:
                v = round((cur / prev - 1) * 100, 1)
                d = f"{m.group(2)}-12-31"
                auto = True
    if not auto:
        v, d = cfg_fallback('disposable_income')
    results['disposable_income'] = {'value': v, 'date': d, 'source': '国家统计局（年度）', 'auto_fetched': auto}

    # ══════ 主线三：货币与信用 ══════

    # ── M2：东财直连 → akshare → 回退 ──
    v, d = em_field('RPT_ECONOMY_CURRENCY_SUPPLY', 'BASIC_CURRENCY_SAME')
    auto = v is not None
    if not auto and HAS_AK:
        try:
            df = ak.macro_china_m2_yearly()
            v = safe_float(df['货币和准货币(M2)-同比增长'].iloc[-1])
            d = str(df['月份'].iloc[-1])
            auto = v is not None and is_cur_year(d)
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('m2')
    results['m2'] = {'value': v, 'date': d, 'source': '中国人民银行', 'auto_fetched': auto}

    # ── M1（新，东财）──
    v, d = em_field('RPT_ECONOMY_CURRENCY_SUPPLY', 'CURRENCY_SAME')
    auto = v is not None
    if not auto:
        v, d = cfg_fallback('m1')
    results['m1'] = {'value': v, 'date': d, 'source': '中国人民银行', 'auto_fetched': auto}

    # ── 人民币贷款余额同比（akshare 此前工作正常）──
    v, d, auto = None, None, False
    if HAS_AK:
        try:
            df = ak.macro_rmb_loan()
            latest = df.iloc[-1]
            g = re.search(r'([\d.]+)', str(latest['累计人民币贷款-同比']))
            if g:
                v = safe_float(g.group(1))
            d = str(latest['月份']).replace('年', '-').replace('月', '')[:7]
            auto = v is not None
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('rmb_loan')
    results['rmb_loan'] = {'value': v, 'date': d, 'source': '中国人民银行', 'auto_fetched': auto}

    # ── 社融增量同比（新，滚动12个月增量同比，akshare）──
    v, d, auto = None, None, False
    if HAS_AK:
        try:
            df = ak.macro_china_shrzgm()
            # 嗅探列名：月份列 + 社融增量列
            date_col = next((c for c in df.columns if '月' in c or '日期' in c), df.columns[0])
            val_col = next((c for c in df.columns if '社会融资规模' in c and '其中' not in c), None)
            if val_col:
                df = df.copy()
                df['_v'] = df[val_col].apply(safe_float)
                df = df.dropna(subset=['_v'])
                if len(df) >= 24:
                    recent12 = df['_v'].iloc[-12:].sum()
                    prev12 = df['_v'].iloc[-24:-12].sum()
                    if prev12 > 0:
                        v = round((recent12 / prev12 - 1) * 100, 1)
                        dm = re.search(r'(20\d{2})\D{0,2}(\d{1,2})', str(df[date_col].iloc[-1]))
                        d = f"{dm.group(1)}-{int(dm.group(2)):02d}" if dm else None
                        auto = True
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('social_financing')
    results['social_financing'] = {'value': v, 'date': d, 'source': '中国人民银行（滚动12月）', 'auto_fetched': auto}

    # ── 10Y国债收益率 ──
    v, d, auto = None, None, False
    if HAS_AK:
        try:
            df_bond = ak.bond_zh_us_rate()
            v = safe_float(df_bond.iloc[-1]['中国国债收益率10年'])
            d = str(df_bond.iloc[-1]['日期'])
            auto = v is not None
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('bond_yield')
    results['bond_yield'] = {'value': v, 'date': d, 'source': '中国债券信息网', 'auto_fetched': auto}

    # ── 1年期LPR ──
    v, d, auto = None, None, False
    if HAS_AK:
        try:
            df = ak.macro_china_lpr()
            latest = df.iloc[-1]
            v = safe_float(latest['LPR1Y'])
            d = str(latest['TRADE_DATE'])
            auto = v is not None
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('lpr')
    results['lpr'] = {'value': v, 'date': d, 'source': '全国银行间同业拆借中心', 'auto_fetched': auto}

    # ══════ 主线四：转型与开放 ══════

    # ── 工业增加值同比：东财直连 → TE → 回退 ──
    v, d = em_field('RPT_ECONOMY_INDUS_GROW', 'BASE_SAME')
    auto = v is not None
    if not auto:
        v, d = web_scrape_te("https://tradingeconomics.com/china/industrial-production")
        auto = bool(v is not None and d and is_cur_year(d))
    if not auto:
        v, d = cfg_fallback('industrial_output')
    results['industrial_output'] = {'value': v, 'date': d, 'source': '国家统计局', 'auto_fetched': auto}

    # ── 出口同比：东财（海关总署数据）→ TE → 回退 ──
    v, d = em_field('RPT_ECONOMY_CUSTOMS', 'EXIT_BASE_SAME')
    auto = v is not None
    if not auto:
        v, d = web_scrape_te("https://tradingeconomics.com/china/exports-yoy")
        auto = bool(v is not None and d and is_cur_year(d))
    if not auto:
        v, d = cfg_fallback('export')
    results['export'] = {'value': v, 'date': d, 'source': '海关总署', 'auto_fetched': auto}

    # ── 进口同比（新，东财海关数据）──
    v, d = em_field('RPT_ECONOMY_CUSTOMS', 'IMPORT_BASE_SAME')
    auto = v is not None
    if not auto:
        v, d = cfg_fallback('import_yoy')
    results['import_yoy'] = {'value': v, 'date': d, 'source': '海关总署', 'auto_fetched': auto}

    # ── CFETS ──
    v, d = web_scrape_cfets()
    auto = v is not None
    if auto and d:
        parts = d.split('-')
        if len(parts) == 3:
            d = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    if not auto:
        v, d = cfg_fallback('currency_index')
    results['currency_index'] = {'value': v, 'date': d, 'source': '中国外汇交易中心', 'auto_fetched': auto}

    # ── AI调用份额 ──
    v, d = web_scrape_openrouter()
    auto = v is not None
    if not auto:
        v, d = cfg_fallback('ai_market_share')
    results['ai_market_share'] = {'value': v, 'date': d, 'source': 'OpenRouter', 'auto_fetched': auto}

    # ── 新能源汽车渗透率（akshare 乘联会，此前工作正常）──
    v, d, auto = None, None, False
    if HAS_AK:
        try:
            df_fuel = ak.car_market_fuel_cpca()
            df_total = ak.car_market_total_cpca()
            col = f'{CUR_YEAR}年'
            if col in df_fuel.columns and col in df_total.columns:
                fuel_vals = df_fuel[col].dropna()
                for i in range(len(fuel_vals) - 1, -1, -1):
                    month = df_fuel.iloc[i]['月份']
                    total_row = df_total[df_total['月份'] == month]
                    if len(total_row) > 0:
                        total_v = safe_float(total_row[col].iloc[0])
                        fuel_v = safe_float(fuel_vals.iloc[i])
                        if total_v and fuel_v and total_v > 0:
                            v = round(fuel_v / total_v * 100, 1)
                            mm = re.search(r'(\d+)', month)
                            d = f"{CUR_YEAR}-{int(mm.group(1)):02d}" if mm else None
                            auto = True
                            break
        except Exception:
            pass
    if not auto:
        v, d = cfg_fallback('new_energy_penetration')
    results['new_energy_penetration'] = {'value': v, 'date': d, 'source': '中国汽车工业协会', 'auto_fetched': auto}

    return results

# ══════════════════════════════════════════════════════
# 打分 / 过期判断 / 合成
# ══════════════════════════════════════════════════════

def compute_stale(date_str: str, frequency: str) -> dict:
    if not date_str:
        return {'stale': True, 'stale_days': 999}
    freq_days = {'日': 7, '周': 21, '月': 60, '季': 120, '年': 365}
    max_days = freq_days.get(frequency, 60)
    try:
        d_clean = str(date_str).replace('年', '-').replace('月', '-01').replace('.', '-').strip()
        for q, md in [('Q1', '-02-01'), ('Q2', '-05-01'), ('Q3', '-08-01'), ('Q4', '-11-01')]:
            d_clean = d_clean.replace(q, md)
        parts = d_clean.split('-')
        if len(parts) >= 3 and parts[2].isdigit():
            d = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
        elif len(parts) >= 2 and parts[1].isdigit():
            d = datetime(int(parts[0]), int(parts[1]), 15)
        else:
            return {'stale': True, 'stale_days': 999}
        delta = (datetime.now() - d).days
        return {'stale': delta > max_days, 'stale_days': delta}
    except Exception:
        return {'stale': True, 'stale_days': 999}

def compute_scores(results):
    scores = {}
    for key, info in results.items():
        if key not in CONFIG['indicators']:
            continue
        c = CONFIG['indicators'][key]
        if info.get('value') is None:
            # 无值且回退值也缺失：中性分占位并标记过期，不拖垮整体
            scores[key] = {
                'score': 50, 'label': '暂无数据', 'value': '—',
                'date': info.get('date', '') or '',
                'source': info.get('source', ''),
                'auto_fetched': False,
                'weight': c['weight'], 'line': c['line'],
                'name': c['name'], 'stale': True, 'stale_days': 999,
            }
            continue
        sc, lbl = score_value(info['value'], c['thresholds'])
        stale_info = compute_stale(info.get('date', ''), c.get('frequency', '月'))
        scores[key] = {
            'score': sc, 'label': lbl, 'value': info['value'],
            'date': info.get('date', ''),
            'source': info.get('source', ''),
            'auto_fetched': info.get('auto_fetched', True),
            'weight': c['weight'], 'line': c['line'],
            'name': c['name'],
            'stale': stale_info['stale'],
            'stale_days': stale_info['stale_days'],
        }
    return scores

def compute_line_scores(scores):
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
    total_weight = sum(s['weight'] for s in scores.values())
    if total_weight <= 0:
        return 50
    return round(sum(s['score'] * s['weight'] for s in scores.values()) / total_weight, 1)

def get_band(temp):
    if temp < 40:
        return "低温区", "❄️", "#97C459", "整体偏冷：内需偏弱、就业承压，经济运行面临较多挑战"
    elif temp < 70:
        return "温和区", "🌤️", "#EF9F27", "正常运转：内需与信贷温和运行，转型环境总体正常"
    else:
        return "升温区", "🔥", "#E24B4A", "整体向好：内需回暖、转型放量，增长与转型形成良性互动"

# ══════════════════════════════════════════════════════
# 自愈：成功抓取的值回写 config，回退值永不定格
# ══════════════════════════════════════════════════════

def heal_config(results):
    changed = 0
    for key, info in results.items():
        if key not in CONFIG['indicators']:
            continue
        c = CONFIG['indicators'][key]
        v, d = info.get('value'), info.get('date')
        if info.get('auto_fetched') and v is not None and d and str(d) != str(c.get('last_known_date')):
            c['last_known_value'] = round(v, 4) if isinstance(v, float) else v
            c['last_known_date'] = d
            c['manual_fallback'] = c['last_known_value']
            changed += 1
    if changed:
        CONFIG['last_updated'] = datetime.now().strftime('%Y-%m-%d')
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
        print(f"💾 已回写 {changed} 个指标的最新值到 config（自愈回退值）")
    else:
        print("💾 config 无需回写")

def main():
    print(f"\n{'='*50}")
    print(f"📊 中国经济健康度温度计 v{CONFIG.get('version', '?')}")
    print(f"⏰  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*50}")

    results = fetch_indicators()

    print(f"\n{'─'*50}")
    print("各指标数据状态:")
    for k, v in results.items():
        name = CONFIG['indicators'].get(k, {}).get('name', k)
        tag = "🆕" if v.get('auto_fetched') else "⚠️"
        print(f"  {tag} {name}: {v['value']} ({v.get('date')}) [{'自动' if v.get('auto_fetched') else '回退'}]")

    heal_config(results)

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
            "stale": s.get('stale', False), "stale_days": s.get('stale_days', 0),
        } for k, s in scores.items()},
    }

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    stale_count = sum(1 for s in scores.values() if s.get('stale'))
    auto_count = sum(1 for v in results.values() if v.get('auto_fetched'))
    total_count = len(results)
    print(f"\n┌────────────────────────────────────────────────")
    print(f"│  📊 综合健康度温度: {temp}°C  {emoji} {band}")
    print(f"│  📡 {auto_count}/{total_count} 自动获取 · {total_count - stale_count}/{total_count} 未过期")
    if stale_count > 0:
        stale_names = [s['name'] for s in scores.values() if s.get('stale')]
        print(f"│  ⚠️  过期指标({stale_count}): {'·'.join(stale_names)}")
    print(f"└────────────────────────────────────────────────")

if __name__ == '__main__':
    main()
