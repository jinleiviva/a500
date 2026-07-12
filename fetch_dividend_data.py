#!/usr/bin/env python3
"""红利低波温度计数据抓取脚本 v3
数据源：
  - stock_zh_index_hist_csindex H30269 → 全历史PE（2018-2024 用于分位计算）
  - stock_zh_index_value_csindex H30269 → 最新PE/股息率
  - fund_etf_spot_em → ETF实时折溢价/规模
  - index_stock_cons_weight_csindex → 成分股权重 → 加权PB/ROE
输出：dividend_data.json

v3 变化：
  - PB/ROE 从成分股加权计算（top20），不再硬编码
  - 利差（股息率-国债利率）动态抓取
  - 趋势保护窗口从20天改为60天
  - 连续打分 6:3:1 权重
"""

import json, os, time, sys
from datetime import datetime

# 清除代理，避免East Money API被代理限制
for k in list(os.environ.keys()):
    if 'proxy' in k.lower():
        os.environ.pop(k, None)

import akshare as ak

DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_JSON = os.path.join(DIR, "dividend_data.json")
INDEX_CODE = "H30269"
ETF_CODE = "563020"

# ── 辅助 ──
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

# ── A. 指数PE历史 ──
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

# ── B. 最新估值（PE/股息率） ──
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

# ── C. ETF实时行情 ──
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

# ── D. 10年国债利率（利差用） ──
def fetch_bond_yield():
    """用 bond_zh_us_rate 获取10年国债利率（同transition脚本）"""
    try:
        df = ak.bond_zh_us_rate()
        if df is None or len(df) == 0: return None
        val = safe_float(df.iloc[-1]['中国国债收益率10年'])
        print(f"  国债利率: {val}%")
        return val
    except Exception as e:
        print(f"  国债利率抓取失败: {e}, 回退手动值")
        return 1.74

# ── E. 加权PB/ROE（从成分股） ──
def fetch_pb_roe():
    """从成分股权重加权计算PB和ROE（top20，用gtimg获取个股价格）"""
    import requests, re
    def gtimg_price(code):
        mkt = 'sh' if code.startswith('6') else 'sz'
        try:
            r = requests.get(f'https://qt.gtimg.cn/q={mkt}{code}', timeout=10,
                             headers={'User-Agent': 'Mozilla/5.0'})
            txt = r.text
            # 格式: v_sz000001="51~名称~代码~价格~昨收~..."
            parts = txt.split('~')
            if len(parts) >= 4:
                return safe_float(parts[3])
        except:
            pass
        return None
    try:
        cons = ak.index_stock_cons_weight_csindex(symbol=INDEX_CODE)
        if cons is None or len(cons) == 0:
            return None, None
        top = cons.sort_values('权重', ascending=False).head(20)
        print(f"  成分股: {len(top)} 只, 合计权重 {top['权重'].sum():.1f}%")
        total_pb, total_roe, sum_w = 0.0, 0.0, 0.0
        count = 0
        for _, row in top.iterrows():
            code = str(int(row['成分券代码'])).zfill(6)
            w = row['权重']
            try:
                fin = ak.stock_financial_analysis_indicator(symbol=code, start_year='2024')
                # 找最近完整年度的数据（12-31），退而求其次取最新
                nav = safe_float(fin['每股净资产_调整前(元)'].dropna().iloc[-1]) if '每股净资产_调整前(元)' in fin.columns else None
                if '加权净资产收益率(%)' in fin.columns:
                    annual_roe = fin[fin['日期'].astype(str).str.contains('12-31')]
                    roe = safe_float(annual_roe['加权净资产收益率(%)'].dropna().iloc[-1]) if len(annual_roe) > 0 else None
                    if roe is None:
                        roe = safe_float(fin['加权净资产收益率(%)'].dropna().iloc[-1])  # fallback to latest
                else:
                    roe = None
                price = gtimg_price(code)
                pb = round(price / nav, 2) if (price and nav and nav > 0) else None
                if pb and 0.1 < pb < 10:
                    total_pb += pb * w
                    count += 1
                if roe and roe > 0:
                    total_roe += roe * w
                sum_w += w
                p_str = f"PB={pb}" if pb else "PB=?"
                print(f"    {row['成分券名称']}({code}): {p_str} ROE={roe}% w={w}%")
            except Exception as e:
                pass
            time.sleep(0.3)
        avg_pb = round(total_pb / sum_w, 2) if sum_w > 0 and count > 0 else None
        avg_roe = round(total_roe / sum_w, 1) if sum_w > 0 else None
        print(f"  加权PB={avg_pb}, ROE={avg_roe}% (覆盖{count}只股票)")
        return avg_pb, avg_roe
    except Exception as e:
        print(f"  PB/ROE计算失败: {e}")
        return None, None

# ── F. 红绿灯 ──
def calc_traffic_light(div_pct, pe_pct, premium_pct, trend_blocked=False):
    """
    综合 = 股息率分位×0.6 + PE分位(反向)×0.3 + 折溢价×0.1
    连续打分，≥70→🟢, 30~69→🟡, <30→🔴
    trend_blocked=True 时强制总分≤50
    """
    base = [(0,0), (20,25), (40,50), (60,75), (80,100)]
    div_score = continuous_score(div_pct, base)
    pe_rev = 100 - pe_pct if pe_pct is not None else 50
    pe_score = continuous_score(pe_rev, base)
    if premium_pct is None:
        prem_score = 50
    elif premium_pct > 0:
        prem_score = max(0, 100 - premium_pct * 100)
    else:
        prem_score = min(100, 100 + premium_pct * 50)
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

# ── 主流程 ──
def main():
    print("=" * 50)
    print("📊 红利低波温度计 v3")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   指数: {INDEX_CODE} · ETF: {ETF_CODE}")
    print("=" * 50)

    # 1. PE历史
    print("\n📡 1. 获取PE历史...")
    pe_history, _ = fetch_pe_history()

    # 2. 最新估值
    print("\n📡 2. 获取最新估值...")
    cur_pe, cur_div, cur_date, val_df = fetch_latest_valuation()

    # 3. ETF实时
    print("\n📡 3. 获取ETF实时...")
    etf = fetch_etf_spot()

    # 4. 国债利率
    print("\n📡 4. 获取10年国债利率（利差）...")
    bond_yield = fetch_bond_yield()
    spread = round(cur_div - bond_yield, 2) if cur_div and bond_yield else None
    print(f"  国债利率: {bond_yield}% → 利差: {spread}%")

    # 5. PB/ROE
    print("\n📡 5. 计算PB/ROE...")
    pb, roe = fetch_pb_roe()

    # 6. 分位
    pe_hist_list = [safe_float(x) for x in pe_history] if pe_history else []
    pe_pct = percentile_rank(cur_pe, pe_hist_list) if cur_pe and pe_hist_list else 50
    print(f"\n📊 6. PE分位: {pe_pct}%")

    div_hist = [safe_float(x) for x in val_df['股息率1'].dropna().tolist()] if val_df is not None and cur_div is not None else []
    div_pct = percentile_rank(cur_div, div_hist) if div_hist else 50
    print(f"  股息率分位: {div_pct}%")

    # 7. 趋势判断（60天窗口）
    print("\n📈 7. 趋势判断（60天窗口）...")
    trend_blocked = False
    trend_reason = ''
    if val_df is not None and len(val_df) >= 20:
        rows = val_df[::-1]
        pe_vals = [v for v in (safe_float(r['市盈率1']) for _, r in rows.iterrows()) if v is not None]
        window = min(60, len(pe_vals))
        if window >= 20:
            recent = pe_vals[-window:]
            pe_current = recent[-1]; pe_high = max(recent)
            drawdown = (pe_high - pe_current) / pe_high * 100 if pe_high > 0 else 0
            print(f"  PE {window}日高点: {pe_high:.2f}, 当前: {pe_current:.2f}, 回撤: {drawdown:.1f}%")
            if drawdown > 10:
                trend_blocked = True
                trend_reason = f'PE从{window}日高点回撤{drawdown:.0f}%，触发趋势保护'
                print(f"  ⚠️ {trend_reason}")
            elif drawdown > 5:
                trend_reason = f'PE偏弱（回撤{drawdown:.0f}%）'
                print(f"  🟡 {trend_reason}")
            else:
                trend_reason = 'PE趋势平稳'
                print(f"  ✅ {trend_reason}")
        else:
            print(f"  PE数据不足{window}条，跳过趋势判断")
    else:
        print("  估值数据不足，跳过趋势判断")

    # 8. 红绿灯
    print("\n🚦 8. 计算红绿灯...")
    premium = etf.get('premium_pct') if etf else 0
    total_score, light, light_label, scores = calc_traffic_light(div_pct, pe_pct, premium, trend_blocked)
    emoji = '🟢' if light == 'green' else '🟡' if light == 'yellow' else '🔴'
    print(f"  综合: {total_score}分 → {emoji} {light_label}")
    print(f"  分项: 股息率{scores['div']} + PE{scores['pe']} + 折溢价{scores['premium']}")
    if trend_blocked:
        print(f"  🔒 趋势保护生效")

    # 9. 走势数据
    print("\n📈 9. 走势数据...")
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

    # 10. 组装输出
    results = {
        'pe': cur_pe, 'pe_date': cur_date, 'pe_percentile': pe_pct,
        'dividend_yield': cur_div, 'dividend_yield_date': cur_date, 'dividend_yield_percentile': div_pct,
        'pb': pb,
        'roe': roe,
        'bond_yield': bond_yield,
        'spread': spread,
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
