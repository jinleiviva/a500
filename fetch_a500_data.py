#!/usr/bin/env python3
"""
A500 温度计 · 每日数据更新脚本
================================
全自动方案：自己累积 PE 历史数据，无需外部干预。
价格数据来自 akshare(新浪)，国债收益率自动实时拉取，
PE 估算基于价格变动。每天自动累积温度历史。

使用:  python3 fetch_a500_data.py
输出:  a500_dashboard.html
"""

import os, sys, json, re
from datetime import datetime, timedelta

# ── 清代理 ──
for k in list(os.environ.keys()):
    if k.lower().endswith('_proxy') or k.lower() == 'no_proxy':
        os.environ.pop(k, None)

import akshare as ak
import numpy as np
import pandas as pd

DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(DIR, "a500_template.html")
OUTPUT   = os.path.join(DIR, "a500_dashboard.html")
PE_HIST  = os.path.join(DIR, "pe_history.json")
TEMP_HIST = os.path.join(DIR, "temp_history.json")
DATA_CACHE = os.path.join(DIR, "data_cache.json")

# PE 最后校准日期（手动更新）
PE_LAST_CALIBRATED = "2026-07-07"

# ══════════════════════════════════════════════════════
# 历史数据读写
# ══════════════════════════════════════════════════════

def load_json(path: str) -> list:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def save_json(path: str, data: list):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ══════════════════════════════════════════════════════
# 国债收益率（自动拉取）
# ══════════════════════════════════════════════════════

def fetch_bond_yield() -> float:
    """获取中国10年期国债收益率"""
    try:
        df = ak.bond_zh_us_rate()
        return round(float(df.iloc[-1]['中国国债收益率10年']), 2)
    except Exception as e:
        print(f"   ⚠️ 国债收益率获取失败: {e}，使用 1.74%")
        return 1.74


def fetch_dividend_yield() -> float | None:
    """获取中证A500股息率（中证指数官方数据）"""
    try:
        df = ak.stock_zh_index_value_csindex(symbol="000510")
        return round(float(df.iloc[0]['股息率1']), 2)
    except Exception as e:
        print(f"   ⚠️ 股息率获取失败: {e}")
        return None


# ══════════════════════════════════════════════════════
# 核心逻辑
# ══════════════════════════════════════════════════════

def update():
    print(f"\n{'='*50}")
    print(f"🌡️  A500 温度计 · 每日更新")
    print(f"⏰  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*50}")

    # ── 1. 拉价格（带缓存降级） ──
    price_cache = load_json(DATA_CACHE) or {}
    price_stale = False
    try:
        df = ak.stock_zh_index_daily(symbol='sh000510')
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        print(f"📈 价格: {len(df)} 行 ({df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()})")
        today     = df.iloc[-1]
        yesterday = df.iloc[-2]
        price_now = round(float(today['close']), 1)
        change    = round((today['close'] - yesterday['close']) / yesterday['close'] * 100, 2)
        today_date = today['date'].strftime('%Y-%m-%d')
        # 缓存成功的数据
        price_cache['price'] = price_now
        price_cache['date']  = today_date
        price_cache['change'] = change
        price_cache['price_df_rows'] = len(df)
        save_json(DATA_CACHE, price_cache)
    except Exception as e:
        print(f"   ⚠️ 价格获取失败: {e}")
        if 'price' in price_cache:
            price_now   = price_cache['price']
            change      = price_cache.get('change', 0)
            today_date  = price_cache['date']
            price_stale = True
            print(f"   ↪ 使用缓存数据: {price_now} (日期: {today_date})")
            # 用缓存日期构建一个只有一天的 df
            df = pd.DataFrame({'date': [pd.to_datetime(today_date)], 'close': [price_now]})
        else:
            print("   ❌ 无缓存可用，脚本无法继续")
            sys.exit(1)

    # ── 2. 拉国债收益率 ──
    bond_yield = fetch_bond_yield()
    print(f"🏦 国债: 10Y = {bond_yield}%")

    # ── 2b. 拉股息率 ──
    dividend_yield = fetch_dividend_yield()
    if dividend_yield:
        print(f"💵 股息率: {dividend_yield}%")
    else:
        print("   ⚠️ 股息率获取失败")
        dividend_yield = 0

    # ── 3. 加载并更新 PE ──
    pe_history = load_json(PE_HIST)
    if not pe_history:
        print("   ⚠️ 无 PE 历史，从今天开始积累")
        pe_now = 16.0  # 默认初始值
        pe_history.append({"date": today_date, "pe": pe_now})
        save_json(PE_HIST, pe_history)
    else:
        last_record = pe_history[-1]
        last_pe     = last_record['pe']
        last_price  = _price_on_date(df, last_record['date']) or price_now

        if today_date == last_record['date']:
            pe_now = last_pe
        else:
            pe_now = round(last_pe * (price_now / last_price), 2)
            pe_history.append({"date": today_date, "pe": pe_now})
            save_json(PE_HIST, pe_history)

    # ── 4. 计算 PE 分位 ──
    pe_vals = np.array([h['pe'] for h in pe_history])
    pe_pctl = round(float(np.sum(pe_vals <= pe_now) / len(pe_vals) * 100), 1)

    # 一个月前的 PE 分位（趋势用）
    one_month_ago = (today['date'] - timedelta(days=30)).strftime('%Y-%m-%d')
    hist_before = [h for h in pe_history if h['date'] <= one_month_ago]
    if hist_before and len(hist_before) >= 2:
        pe_vals_1m = np.array([h['pe'] for h in hist_before])
        pe_pctl_1m = round(float(np.sum(pe_vals_1m <= pe_vals_1m[-1]) / len(pe_vals_1m) * 100), 1)
    else:
        pe_pctl_1m = pe_pctl

    # ── 5. 价格分位 ──
    p5 = df.tail(1250)['close'].values.astype(float)
    price_pctl = round(float(np.sum(p5 <= p5[-1]) / len(p5) * 100), 1)

    # ── 6. 温度 ──
    temp = int(round(pe_pctl * 0.6 + price_pctl * 0.4, 0))

    # ── 7. 温度历史维护 ──
    temp_history = load_json(TEMP_HIST)
    if not temp_history or temp_history[-1]['date'] != today_date:
        temp_history.append({"date": today_date, "temp": temp})
        # 只保留最近 180 天
        if len(temp_history) > 180:
            temp_history = temp_history[-180:]
        save_json(TEMP_HIST, temp_history)

    # ── 8. 风险溢价 ──
    stock_yield = round(1 / pe_now * 100, 1)
    premium = round(stock_yield - bond_yield, 1)

    # ── 9. 定投参考 ──
    dca_pct, dca_label = _dca_suggestion(temp)

    # ── 10. 走势数据 ──
    closes_30 = [round(float(x), 1) for x in df.tail(30)['close'].values]
    temp_90 = temp_history[-90:] if len(temp_history) >= 90 else temp_history

    # ── 打印摘要 ──
    print(f"📊 PE: {pe_now} (分位 {pe_pctl}% · {len(pe_history)}天)")
    print(f"🌡️  温度: {temp}°C")
    print(f"💹 点位: {price_now} ({change:+.2f}%)")
    print(f"🏦 国债: {bond_yield}% | 股债差: {premium}%")
    print(f"📋 定投建议: {dca_label} ({dca_pct}%)")

    # ── 11. 渲染 ──
    data = {
        "date":              today_date,
        "price":             price_now,
        "priceChange":       change,
        "pe":                pe_now,
        "pePercentile":      pe_pctl,
        "pePercentilePrev":  pe_pctl_1m,
        "stockYield":        stock_yield,
        "bondYield":         bond_yield,
        "dividendYield":     dividend_yield,
        "premium":           premium,
        "temperature":       temp,
        "dcaPct":            dca_pct,
        "dcaLabel":          dca_label,
        "recentCloses":      closes_30,
        "tempHistory":       [{"d": r["date"][5:], "t": r["temp"]} for r in temp_90],
        # 数据新鲜度
        "priceStale":        price_stale,
        "peLastCalibrated":  PE_LAST_CALIBRATED,
        # 实时相关
        "pageCreatedAt":     datetime.now().strftime('%Y-%m-%d %H:%M'),
        "pricePercentile":   price_pctl,
        "peHistory":         [{"d": r["date"][5:], "pe": r["pe"]} for r in pe_history],
    }

    # ── 实时基线（供 realtime.py 使用）──
    baseline = {
        "date":             today_date,
        "price":            price_now,
        "priceChange":      change,
        "pe":               pe_now,
        "pePercentile":     pe_pctl,
        "pricePercentile":  price_pctl,
        "bondYield":        bond_yield,
        "dividendYield":    dividend_yield,
        "premium":          premium,
        "temperature":      temp,
        "dcaPct":           dca_pct,
        "dcaLabel":         dca_label,
        "peLastCalibrated": PE_LAST_CALIBRATED,
        "peHistory":        pe_history,                                   # 全量 PE 历史（用于实时 PE 分位）
        "closesAll":        [round(float(x), 1) for x in df['close'].values],  # 全量收盘价（用于实时价格分位）
    }
    save_json(os.path.join(DIR, "realtime_baseline.json"), baseline)

    _render(data)
    print(f"\n✅ {OUTPUT}")
    print(f"   温度历史: {len(temp_history)} 天")


def _price_on_date(df, date_str: str) -> float | None:
    target = datetime.strptime(date_str, '%Y-%m-%d')
    match = df[df['date'] == target]
    if not match.empty:
        return float(match.iloc[-1]['close'])
    before = df[df['date'] < target]
    return float(before.iloc[-1]['close']) if not before.empty else None


def _dca_suggestion(temp: int) -> tuple:
    """根据温度返回（建议定投比例，标签）"""
    if temp < 20:
        return (150, "🚀 加仓买入")
    elif temp < 30:
        return (100, "✅ 满额定投")
    elif temp < 45:
        return (75, "👍 正常定投")
    elif temp < 60:
        return (50, "✋ 半额定投")
    elif temp < 75:
        return (25, "⚠️ 减少定投")
    elif temp < 85:
        return (0, "🛑 暂停买入")
    else:
        return (-50, "🔴 考虑止盈")


def _render(data: dict):
    with open(TEMPLATE, encoding='utf-8') as f:
        html = f.read()

    replacements = {
        "'date': '--'":             f"'date': '{data['date']}'",
        "'price': --":              f"'price': {data['price']}",
        "'priceChange': --":        f"'priceChange': {data['priceChange']}",
        "'pe': --":                 f"'pe': {data['pe']}",
        "'pePercentile': --":       f"'pePercentile': {data['pePercentile']}",
        "'pePercentilePrev': --":   f"'pePercentilePrev': {data['pePercentilePrev']}",
        "'stockYield': --":         f"'stockYield': {data['stockYield']}",
        "'dividendYield': --":      f"'dividendYield': {data['dividendYield']}",
        "'bondYield': --":          f"'bondYield': {data['bondYield']}",
        "'premium': --":            f"'premium': {data['premium']}",
        "'temperature': --":        f"'temperature': {data['temperature']}",
        "'dcaPct': --":             f"'dcaPct': {data['dcaPct']}",
        "'dcaLabel': '--'":         f"'dcaLabel': '{data['dcaLabel']}'",
        "'priceStale': --":         f"'priceStale': {str(data['priceStale']).lower()}",
        "'peLastCalibrated': '--'": f"'peLastCalibrated': '{data['peLastCalibrated']}'",
        "'pageCreatedAt': '--'":     f"'pageCreatedAt': '{data['pageCreatedAt']}'",
        "'pricePercentile': --":      f"'pricePercentile': {data['pricePercentile']}",
    }
    for key, val in replacements.items():
        html = html.replace(key, val)

    # 替换收盘价数组
    closes = ",\\n      ".join(str(c) for c in data['recentCloses'])
    html = re.sub(r"'recentCloses': \[[^]]+\]", f"'recentCloses': [\\n      {closes}\\n    ]", html)

    # 替换温度历史数组
    temp_items = ",\\n      ".join(f'{{d:"{r["d"]}",t:{r["t"]}}}' for r in data['tempHistory'])
    html = re.sub(r"'tempHistory': \[[^]]*\]", f"'tempHistory': [\\n      {temp_items}\\n    ]", html)

    # 替换 PE 历史数组
    pe_items = ",\\n      ".join(f'{{d:"{r["d"]}",pe:{r["pe"]}}}' for r in data['peHistory'])
    html = re.sub(r"'peHistory': \[[^]]*\]", f"'peHistory': [\\n      {pe_items}\\n    ]", html)

    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    update()
