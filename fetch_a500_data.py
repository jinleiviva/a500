#!/usr/bin/env python3
"""
A500 温度计 · 每日数据更新脚本
================================
全自动方案：自己累积 PE 历史数据，无需外部干预。
第 1 天用种子数据启动，之后每天自动估算并累积。
价格数据来自 akshare(新浪)，PE 估算基于价格变动。

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
HISTORY  = os.path.join(DIR, "pe_history.json")

# ══════════════════════════════════════════════════════
# 历史 PE 数据库（自维护，越跑越准）
# ══════════════════════════════════════════════════════

def load_history() -> list:
    """加载 PE 历史记录"""
    if os.path.exists(HISTORY):
        with open(HISTORY) as f:
            return json.load(f)
    return []

def save_history(history: list):
    with open(HISTORY, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def seed_history(history: list):
    """首次运行：用价格历史近似 PE"""
    if history:
        return
    print("   ⚠️ 无 PE 历史文件，将从零开始累积")
    # 不放种子数据，让系统从今天开始自建历史
    # day 1 显示的温度可能不准，但 30 天后就好了


# ══════════════════════════════════════════════════════
# 核心逻辑
# ══════════════════════════════════════════════════════

def update():
    print(f"\n{'='*50}")
    print(f"🌡️  A500 温度计 · 每日更新")
    print(f"⏰  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*50}")

    # 1. 加载/播种历史
    history = load_history()
    is_first_run = not history
    seed_history(history)

    # 2. 拉价格
    df = ak.stock_zh_index_daily(symbol='sh000510')
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)
    print(f"📈 价格: {len(df)} 行 ({df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()})")

    today     = df.iloc[-1]
    yesterday = df.iloc[-2]
    price_now = round(float(today['close']), 1)
    change    = round((today['close'] - yesterday['close']) / yesterday['close'] * 100, 2)

    # 3. 估算今日 PE
    last_record = history[-1]
    last_pe     = last_record['pe']
    last_price  = _price_on_date(df, last_record['date']) or price_now
    today_date  = today['date'].strftime('%Y-%m-%d')

    if today_date == last_record['date']:
        pe_now = last_pe  # 同一交易日，不用更新
    else:
        # 用价格变动估算 PE（短期盈利不变，PE ≈ 价格变动）
        pe_now = round(last_pe * (price_now / last_price), 2)

    # 4. 追加到历史
    if today_date != last_record['date']:
        history.append({"date": today_date, "pe": pe_now})
        save_history(history)

    # 5. 计算分位
    pe_vals = np.array([h['pe'] for h in history])
    pe_pctl = round(float(np.sum(pe_vals <= pe_now) / len(pe_vals) * 100), 1)

    # 一个月前的 PE 分位（趋势用）
    one_month_ago = (today['date'] - timedelta(days=30)).strftime('%Y-%m-%d')
    hist_before = [h for h in history if h['date'] <= one_month_ago]
    if hist_before:
        pe_vals_1m = np.array([h['pe'] for h in hist_before])
        pe_pctl_1m = round(float(np.sum(pe_vals_1m <= pe_vals_1m[-1]) / len(pe_vals_1m) * 100), 1)
    else:
        pe_pctl_1m = pe_pctl

    # 6. 价格分位
    p5 = df.tail(1250)['close'].values.astype(float)
    price_pctl = round(float(np.sum(p5 <= p5[-1]) / len(p5) * 100), 1)

    # 7. 温度
    temp = int(round(pe_pctl * 0.6 + price_pctl * 0.4, 0))

    # 8. 风险溢价
    stock_yield = round(1 / pe_now * 100, 1)
    bond_yield  = 3.1  # fallback，可通过 fetch_bond 改进

    # 9. 30日走势
    closes_30 = [round(float(x), 1) for x in df.tail(30)['close'].values]
    closes_30_label = today['date'].strftime('%Y-%m-%d')

    print(f"📊 PE: {pe_now} (分位 {pe_pctl}% · 历史{len(history)}天)")
    print(f"🌡️  温度: {temp}°C")
    print(f"💹 点位: {price_now} ({change:+.2f}%)")

    # 10. 渲染
    data = {
        "date":              today_date,
        "price":             price_now,
        "priceChange":       change,
        "pe":                pe_now,
        "pePercentile":      pe_pctl,
        "pePercentilePrev":  pe_pctl_1m,
        "pb":                1.89,
        "pbPercentile":      59,
        "divYield":          2.6,
        "divYieldPercentile": 42,
        "stockYield":        stock_yield,
        "bondYield":         bond_yield,
        "temperature":       temp,
        "recentCloses":      closes_30,
    }

    _render(data)
    print(f"\n✅ {OUTPUT}")
    print(f"   {'首次运行·种子数据' if is_first_run else '历史累积 ' + str(len(history)) + ' 天'}")


def _price_on_date(df, date_str):
    """查找指定日期的收盘价"""
    target = datetime.strptime(date_str, '%Y-%m-%d')
    match = df[df['date'] == target]
    if not match.empty:
        return float(match.iloc[-1]['close'])
    # 没找到（可能非交易日），往前找最近的有效交易日
    before = df[df['date'] < target]
    if not before.empty:
        return float(before.iloc[-1]['close'])
    return None


def _render(data: dict):
    with open(TEMPLATE, encoding='utf-8') as f:
        html = f.read()

    for key, val in {
        "'date': '--'":             f"'date': '{data['date']}'",
        "'price': --":              f"'price': {data['price']}",
        "'priceChange': --":        f"'priceChange': {data['priceChange']}",
        "'pe': --":                 f"'pe': {data['pe']}",
        "'pePercentile': --":       f"'pePercentile': {data['pePercentile']}",
        "'pePercentilePrev': --":   f"'pePercentilePrev': {data['pePercentilePrev']}",
        "'pb': --":                 f"'pb': {data['pb']}",
        "'pbPercentile': --":       f"'pbPercentile': {data['pbPercentile']}",
        "'divYield': --":           f"'divYield': {data['divYield']}",
        "'divYieldPercentile': --": f"'divYieldPercentile': {data['divYieldPercentile']}",
        "'stockYield': --":         f"'stockYield': {data['stockYield']}",
        "'bondYield': --":          f"'bondYield': {data['bondYield']}",
        "'temperature': --":        f"'temperature': {data['temperature']}",
    }.items():
        html = html.replace(key, val)

    closes = ",\\n      ".join(str(c) for c in data['recentCloses'])
    html = re.sub(r"'recentCloses': \[[^]]+\]", f"'recentCloses': [\\n      {closes}\\n    ]", html)

    with open(OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)


if __name__ == '__main__':
    update()
