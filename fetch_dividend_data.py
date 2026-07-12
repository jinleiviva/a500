#!/usr/bin/env python3
"""红利低波温度计数据抓取脚本 v2
数据源：
  - stock_zh_index_hist_csindex H30269 → 全历史PE（2018-2024用于分位计算）
  - stock_zh_index_value_csindex H30269 → 最新PE/股息率
  - fund_etf_spot_em → ETF实时折溢价/规模
输出：dividend_data.json
""" + """
""" + """
变化：
  - 连续打分（消除断点跳跃）
  - 权重 6:3:1（股息率60% PE30% 折溢价10%）
  - 趋势保护开关
"""

import json, os
from datetime import datetime
import akshare as ak

DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(DIR, "dividend_data.json")
INDEX_CODE = "H30269"
ETF_CODE = "563020"

def safe_float(v):
    try: return float(v)
    except: return None

def percentile_rank(value, series):
    if not series or value is None: return 50
    s = sorted(series)
    below = sum(1 for x in s if x <= value)
    return round(below / len(s) * 100)

def continuous_score(pct, base_pts):
    """线性插值打分 [(分位,得分)]"""
    if pct is None: return 50
    pts = sorted(base_pts, key=lambda x: x[0])
    if pct <= pts[0][0]: return pts[0][1]
    if pct >= pts[-1][0]: return pts[-1][1]
    for i in range(len(pts)-1):
        x1, y1 = pts[i]; x2, y2 = pts[i+1]
        if x1 <= pct <= x2:
            return round(y1 + (y2-y1) * (pct-x1)/(x2-x1))
    return 50

def fetch_pe_history():
    try:
        df = ak.stock_zh_index_hist_csindex(symbol=INDEX_CODE)
        if df is None or len(df) == 0: return None, None
        pe_list = df['滚动市盈率'].dropna().tolist()
        dates = df['日期'].tolist()
        print(f"  PE历史: {len(pe_list)} 条, {dates[0]} ~ {dates[-1]}")
        return pe_list, df
    except Exception as e:
        print(f"  PE历史抓取失败: {e}")
        return None, None

def fetch_latest_valuation():
    try:
        df = ak.stock_zh_index_value_csindex(symbol=INDEX_CODE)
        if df is None or len(df) == 0: return None, None, None, None
        latest = df.iloc[0]
        pe = safe_float(latest['市盈率1'])
        div_yield = safe_float(latest['股息率1'])
        date = str(latest['日期'])
        print(f"  最新估值: PE={pe}, 股息率={div_yield}%, 日期={date}")
        return pe, div_yield, date, df
    except Exception as e:
        print(f"  最新估值抓取失败: {e}")
        return None, None, None, None

def fetch_etf_spot():
    try:
        df = ak.fund_etf_spot_em()
        etf = df[df['代码'] == ETF_CODE]
        if len(etf) == 0: return None
        row = etf.iloc[0]
        out = {
            'price': safe_float(row.get('最新价')),
            'iopv': safe_float(row.get('IOPV实时估值')),
            'premium': safe_float(row.get('基金折价率')),
            'volume': safe_float(row.get('成交额')),
            'market_value': safe_float(row.get('总市值')),
            'update_time': str(row.get('更新时间', '')),
        }
        new_price = safe_float(row.get('最新价'))
        if new_price and out['iopv'] and out['iopv'] > 0:
            out['premium_pct'] = round((new_price / out['iopv'] - 1) * 100, 2)
        else:
            out['premium_pct'] = out.get('premium', 0) if out.get('premium') is not None else 0
        print(f"  ETF实时: 价格={out['price']}, 折溢价={out.get('premium_pct')}%, 成交额={out.get('volume')}")
        return out
    except Exception as e:
        print(f"  ETF实时抓取失败: {e}")
        return None

def calc_traffic_light(div_pct, pe_pct, premium_pct, trend_blocked=False):
    """
    综合得分 = 股息率分位得分×0.6 + PE分位(反向)得分×0.3 + 折溢价得分×0.1
    连续打分，无断点跳跃
    ≥70 → 🟢, 30~69 → 🟡, <30 → 🔴
    trend_blocked=True 时强制总分≤50（不高于🟡）
    """
    base = [(0,0), (20,25), (40,50), (60,75), (80,100)]

    div_score = continuous_score(div_pct, base)

    pe_rev = 100 - pe_pct if pe_pct is not None else 50
    pe_score = continuous_score(pe_rev, base)

    if premium_pct is None:
        prem_score = 50
    elif premium_pct > 0:
        prem_score = max(0, 100 - premium_pct * 100)  # 溢价扣分
    else:
        prem_score = min(100, 100 + premium_pct * 50)  # 折价加分

    total = div_score * 0.6 + pe_score * 0.3 + prem_score * 0.1
    if trend_blocked:
        total = min(total, 50)

    if total >= 70:
        light, label = 'green', '适合买入'
    elif total >= 30:
        light, label = 'yellow', '适合持有'
    else:
        light, label = 'red', '观望/减仓'

    return round(total), light, label, {'div': div_score, 'pe': pe_score, 'premium': prem_score}

def main():
    print("=" * 50)
    print("📊 红利低波温度计 v2")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   指数: {INDEX_CODE} · ETF: {ETF_CODE}")
    print("=" * 50)

    print("\n📡 1. 获取PE历史...")
    pe_history, _ = fetch_pe_history()

    print("\n📡 2. 获取最新估值...")
    cur_pe, cur_div, cur_date, val_df = fetch_latest_valuation()

    print("\n📡 3. 获取ETF实时...")
    etf = fetch_etf_spot()

    print("\n📊 4. 计算分位...")
    pe_hist_list = [safe_float(x) for x in pe_history] if pe_history else []
    pe_pct = percentile_rank(cur_pe, pe_hist_list) if cur_pe and pe_hist_list else 50
    print(f"  PE分位: {pe_pct}%")

    div_hist = [safe_float(x) for x in val_df['股息率1'].dropna().tolist()] if val_df is not None and cur_div is not None else []
    div_pct = percentile_rank(cur_div, div_hist) if div_hist else 50
    print(f"  股息率分位: {div_pct}%")

    print("\n📈 5. 趋势判断（基于PE趋势推断）...")
    trend_blocked = False
    trend_reason = ''
    if val_df is not None and len(val_df) >= 10:
        rows = val_df[::-1]  # 旧→新
        pe_vals = [v for v in (safe_float(r['市盈率1']) for _, r in rows.iterrows()) if v is not None]
        if len(pe_vals) >= 10:
            pe_current = pe_vals[-1]; pe_high = max(pe_vals)
            drawdown = (pe_high - pe_current) / pe_high * 100
            print(f"  PE 20日高点: {pe_high:.2f}, 当前: {pe_current:.2f}, 回撤: {drawdown:.1f}%")
            if drawdown > 10:
                trend_blocked = True
                trend_reason = f'PE从20日高点回撤{drawdown:.0f}%，触发趋势保护'
                print(f"  ⚠️ {trend_reason}")
            elif drawdown > 5:
                trend_reason = f'PE偏弱（回撤{drawdown:.0f}%）'
                print(f"  🟡 {trend_reason}")
            else:
                trend_reason = f'PE趋势平稳'
                print(f"  ✅ {trend_reason}")
        else:
            print("  PE数据不足，跳过趋势判断")
    else:
        print("  数据不足，跳过趋势判断")

    print("\n🚦 6. 计算红绿灯...")
    premium = etf.get('premium_pct') if etf else 0
    total_score, light, light_label, scores = calc_traffic_light(div_pct, pe_pct, premium, trend_blocked)
    emoji = '🟢' if light == 'green' else '🟡' if light == 'yellow' else '🔴'
    print(f"  综合: {total_score}分 → {emoji} {light_label}")
    print(f"  分项: 股息率{scores['div']} + PE{scores['pe']} + 折溢价{scores['premium']}")
    if trend_blocked:
        print(f"  🔒 趋势保护生效")

    print("\n📈 7. 走势数据...")
    temp_history, div_history = [], []
    if val_df is not None:
        for _, row in val_df[::-1].iterrows():
            d = str(row['日期'])
            pe = safe_float(row['市盈率1'])
            dv = safe_float(row['股息率1'])
            if pe and pe_hist_list:
                temp_history.append({'d': d, 't': percentile_rank(pe, pe_hist_list)})
            if dv:
                div_history.append({'d': d, 'v': dv})

    results = {
        'pe': cur_pe, 'pe_date': cur_date, 'pe_percentile': pe_pct,
        'dividend_yield': cur_div, 'dividend_yield_date': cur_date, 'dividend_yield_percentile': div_pct,
        'etf_price': etf.get('price') if etf else None,
        'etf_premium': premium,
        'etf_volume': etf.get('volume') if etf else None,
        'etf_size': round(etf.get('market_value', 0) / 1e8, 2) if etf else None,
        'etf_update_time': etf.get('update_time') if etf else None,
        'light': light, 'light_score': total_score, 'light_label': light_label,
        'light_weights': '6:3:1',
        'trend_blocked': trend_blocked, 'trend_reason': trend_reason,
        'temp_history': temp_history, 'dividend_history': div_history,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n✅ {OUTPUT_JSON}")
    print(f"   🚦 {emoji} {light_label}{' 🔒' if trend_blocked else ''}")

if __name__ == '__main__':
    main()
