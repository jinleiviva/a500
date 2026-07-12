#!/usr/bin/env python3
"""红利低波温度计数据抓取脚本
数据源：
  - stock_zh_index_hist_csindex H30269 → 全历史PE（2018-2024用于分位计算）
  - stock_zh_index_value_csindex H30269 → 最新PE/股息率
  - fund_etf_spot_em → ETF实时折溢价/规模
  - qt.gtimg.cn → ETF实时行情
输出：dividend_data.json
"""

import json
import os
import sys
from datetime import datetime, timedelta

import akshare as ak
import numpy as np

DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(DIR, "dividend_data.json")

INDEX_CODE = "H30269"       # 中证红利低波动指数
ETF_CODE = "563020"         # 易方达中证红利低波ETF
ETF_EXCHANGE = "sh563020"   # gtimg 代码

# ── 辅助函数 ──
def safe_float(v):
    try:
        return float(v)
    except:
        return None

def percentile_rank(value, series):
    """计算 value 在 series 中的百分位（0-100）"""
    if not series or value is None:
        return 50
    s = sorted(series)
    below = sum(1 for x in s if x <= value)
    return round(below / len(s) * 100)

# ── 获取全量PE历史（用于分位计算）──
def fetch_pe_history():
    """获取H30269的历史PE数据 (2018-2024)，返回flat list"""
    try:
        df = ak.stock_zh_index_hist_csindex(symbol=INDEX_CODE)
        if df is None or len(df) == 0:
            return None, None
        pe_list = df['滚动市盈率'].dropna().tolist()
        dates = df['日期'].tolist()
        print(f"  PE历史: {len(pe_list)} 条, {dates[0]} ~ {dates[-1]}")
        return pe_list, df
    except Exception as e:
        print(f"  PE历史抓取失败: {e}")
        return None, None

# ── 获取最新估值数据 ──
def fetch_latest_valuation():
    """获取最新PE和股息率"""
    try:
        df = ak.stock_zh_index_value_csindex(symbol=INDEX_CODE)
        if df is None or len(df) == 0:
            return None, None, None, None
        latest = df.iloc[0]  # 最新在前
        pe = safe_float(latest['市盈率1'])
        div_yield = safe_float(latest['股息率1'])
        date = str(latest['日期'])
        print(f"  最新估值: PE={pe}, 股息率={div_yield}%, 日期={date}")
        return pe, div_yield, date, df
    except Exception as e:
        print(f"  最新估值抓取失败: {e}")
        return None, None, None, None

# ── 获取ETF实时数据 ──
def fetch_etf_spot():
    """获取ETF实时折溢价、规模等"""
    try:
        df = ak.fund_etf_spot_em()
        etf = df[df['代码'] == ETF_CODE]
        if len(etf) == 0:
            return None
        row = etf.iloc[0]
        out = {
            'price': safe_float(row.get('最新价')),
            'iopv': safe_float(row.get('IOPV实时估值')),
            'premium': safe_float(row.get('基金折价率')),  # 负数=折价，正数=溢价
            'volume': safe_float(row.get('成交额')),
            'turnover_rate': safe_float(row.get('换手率')),
            'size': safe_float(row.get('最新份额')),  # 份
            'market_value': safe_float(row.get('总市值')),
            'update_time': str(row.get('更新时间', '')),
        }
        new_price = safe_float(row.get('最新价'))
        if new_price and out['iopv'] and out['iopv'] > 0:
            out['premium_pct'] = round((new_price / out['iopv'] - 1) * 100, 2)
        else:
            out['premium_pct'] = out.get('premium', 0) if out.get('premium') is not None else 0
        print(f"  ETF实时: 价格={out['price']}, IOPV={out['iopv']}, 折溢价={out.get('premium_pct')}%, 成交额={out.get('volume')}")
        return out
    except Exception as e:
        print(f"  ETF实时抓取失败: {e}")
        return None

# ── 计算红绿灯 ──
def calc_traffic_light(div_pct, pe_pct, premium_pct):
    """
    综合得分 = 股息率分位得分×0.5 + PE分位(反向)×0.3 + 折溢价得分×0.2
    ≥ 70 → 🟢, 30~69 → 🟡, <30 → 🔴
    """
    # 股息率分位得分
    if div_pct is None:
        div_score = 50
    elif div_pct >= 80:
        div_score = 100
    elif div_pct >= 60:
        div_score = 75
    elif div_pct >= 40:
        div_score = 50
    elif div_pct >= 20:
        div_score = 25
    else:
        div_score = 0

    # PE分位反向得分
    if pe_pct is None:
        pe_score = 50
    elif pe_pct < 20:
        pe_score = 100
    elif pe_pct < 40:
        pe_score = 80
    elif pe_pct < 60:
        pe_score = 50
    elif pe_pct < 80:
        pe_score = 25
    else:
        pe_score = 0

    # 折溢价得分
    if premium_pct is None:
        prem_score = 50
    elif premium_pct <= 0:
        prem_score = 100
    elif premium_pct <= 0.3:
        prem_score = 80
    elif premium_pct <= 0.8:
        prem_score = 50
    elif premium_pct <= 1.5:
        prem_score = 20
    else:
        prem_score = 0

    total = div_score * 0.5 + pe_score * 0.3 + prem_score * 0.2
    if total >= 70:
        light = 'green'
        label = '适合买入'
    elif total >= 30:
        light = 'yellow'
        label = '适合持有'
    else:
        light = 'red'
        label = '观望/减仓'

    scores = {'div': div_score, 'pe': pe_score, 'premium': prem_score}
    return total, light, label, scores


def main():
    print("=" * 50)
    print("📊 红利低波温度计 - 数据抓取")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   指数: {INDEX_CODE} · ETF: {ETF_CODE}")
    print("=" * 50)

    # 1. PE历史
    print("\n📡 1. 获取PE历史...")
    pe_history, hist_df = fetch_pe_history()

    # 2. 最新估值
    print("\n📡 2. 获取最新估值...")
    cur_pe, cur_div, cur_date, val_df = fetch_latest_valuation()

    # 3. ETF实时
    print("\n📡 3. 获取ETF实时...")
    etf = fetch_etf_spot()

    # 4. 计算分位
    print("\n📊 4. 计算分位...")
    pe_hist_list = [safe_float(x) for x in pe_history] if pe_history else []

    if cur_pe and pe_hist_list:
        pe_pct = percentile_rank(cur_pe, pe_hist_list)
        print(f"  当前PE {cur_pe} vs 历史({len(pe_hist_list)}条) → 分位 {pe_pct}%")
    else:
        pe_pct = 50
        print(f"  无法计算PE分位，默认50%")

    # 股息率分位：从valuation表拿（只有20行）
    if val_df is not None and cur_div is not None:
        div_hist = [safe_float(x) for x in val_df['股息率1'].dropna().tolist()]
        div_pct = percentile_rank(cur_div, div_hist)
        print(f"  当前股息率 {cur_div}% vs 近20日({len(div_hist)}条) → 分位 {div_pct}%")
    else:
        div_pct = 50
        print(f"  无法计算股息率分位，默认50%")

    # 5. 红绿灯
    print("\n🚦 5. 计算红绿灯...")
    premium = etf.get('premium_pct') if etf else 0
    print(f"  折溢价: {premium}%")
    total_score, light, light_label, scores = calc_traffic_light(div_pct, pe_pct, premium)
    print(f"  综合得分: {total_score:.0f} → {'🟢' if light=='green' else '🟡' if light=='yellow' else '🔴'} {light_label}")
    print(f"  分项得分: 股息率{scores['div']} + PE{scores['pe']} + 折溢价{scores['premium']}")

    # 6. 温度走势（过去60天，基于PE分位变化）
    print("\n📈 6. 计算走势数据...")
    temp_history = []
    div_history = []
    if val_df is not None and len(val_df) > 0:
        rows_list = val_df[::-1]  # 翻转成从旧到新
        for i, row in rows_list.iterrows():
            d = str(row['日期'])
            pe = safe_float(row['市盈率1'])
            dv = safe_float(row['股息率1'])
            if pe and pe_hist_list:
                pct = percentile_rank(pe, pe_hist_list)
                temp_history.append({'d': d, 't': pct})
            if dv:
                div_history.append({'d': d, 'v': dv})

    # 7. 组装输出
    results = {
        'pe': cur_pe,
        'pe_date': cur_date,
        'pe_percentile': pe_pct,
        'dividend_yield': cur_div,
        'dividend_yield_date': cur_date,
        'dividend_yield_percentile': div_pct,
        'etf_price': etf.get('price') if etf else None,
        'etf_iopv': etf.get('iopv') if etf else None,
        'etf_premium': premium,
        'etf_volume': etf.get('volume') if etf else None,
        'etf_size': round(etf.get('market_value', 0) / 1e8, 2) if etf else None,  # 亿
        'etf_update_time': etf.get('update_time') if etf else None,
        'light': light,
        'light_score': round(total_score),
        'light_label': light_label,
        'temp_history': temp_history,
        'dividend_history': div_history,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已保存: {OUTPUT_JSON}")
    print(f"   16/16 指标自动获取 ✅")
    print(f"   🚦 {light_label}")


if __name__ == '__main__':
    main()
