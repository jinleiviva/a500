#!/usr/bin/env python3
"""
A500 温度计 · 盘中实时脚本
================================
由自动化任务在交易时段每 5 分钟调用一次。
用 akshare 实时行情接口拿到中证 A500 盘中最新点位，
基于 fetch_a500_data.py 落盘的 realtime_baseline.json（PE 历史 + 全量收盘价），
按与日频相同的公式（温度 = PE分位×60% + 价格分位×40%）重算盘中实时温度，
写入 realtime_data.js（window.__RT），供前端轮询展示。

使用:  python3 realtime.py
输出:  realtime_data.js  (+ 同步到主目录)
"""

import os, json, datetime, time

# ── 清代理（akshare 走直连）──
for k in list(os.environ.keys()):
    if k.lower().endswith('_proxy') or k.lower() == 'no_proxy':
        os.environ.pop(k, None)

import akshare as ak

DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(DIR, "realtime_baseline.json")
OUT_LOCAL = os.path.join(DIR, "realtime_data.js")
OUT_MAIN = os.path.join(os.path.dirname(DIR), "realtime_data.js")   # 主目录（与 A500温度计.html 同层）


def load_json(path: str):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


def is_market_open() -> bool:
    n = datetime.datetime.now()
    if n.weekday() >= 5:          # 周末
        return False
    t = n.hour * 100 + n.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


def fetch_spot() -> tuple:
    """返回 (最新价, 涨跌幅%) 或 (None, None)"""
    for _ in range(3):
        try:
            df = ak.stock_zh_index_spot_em(symbol='沪深重要指数')
            row = df[df['名称'].astype(str).str.contains('A500|中证A500', na=False)]
            if not row.empty:
                price = float(row.iloc[0]['最新价'])
                chg = float(row.iloc[0]['涨跌幅'])
                return price, chg
        except Exception as e:
            print(f"   ⚠️ 实时行情获取失败: {e}")
            time.sleep(2)
    return None, None


def main():
    print(f"\n{'='*50}")
    print(f"📡 A500 温度计 · 盘中实时")
    print(f"⏰  {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*50}")

    base = load_json(BASELINE)
    if not base:
        print("   ❌ 找不到 realtime_baseline.json，请先运行 fetch_a500_data.py")
        return

    now = datetime.datetime.now()
    ts = now.strftime('%Y-%m-%d %H:%M:%S')
    market = is_market_open()
    spot, chg = fetch_spot()

    if not market or spot is None:
        # 非交易时段 / 拉取失败 → 用日频基线（静态），标注非实时
        out = {
            "price": base.get("price"),
            "change": base.get("priceChange"),
            "pe": base.get("pe"),
            "pePercentile": base.get("pePercentile"),
            "pricePercentile": base.get("pricePercentile"),
            "temperature": base.get("temperature"),
            "live": False,
            "market": market,
            "ts": ts,
        }
        print(f"   💤 盘后/非交易时段，使用日频基线温度 {base.get('temperature')}°C")
    else:
        price0 = float(base["price"])
        pe0 = float(base["pe"])
        r_pe = round(pe0 * spot / price0, 2)

        # 实时 PE 分位（基于 PE 历史）
        pe_hist = base.get("peHistory", [])
        if pe_hist:
            cnt = sum(1 for h in pe_hist if h["pe"] <= r_pe)
            r_pe_pctl = round(cnt / len(pe_hist) * 100, 1)
        else:
            r_pe_pctl = base.get("pePercentile")

        # 实时价格分位（基于全量收盘价）
        closes = base.get("closesAll", [])
        if closes:
            cnt = sum(1 for c in closes if c <= spot)
            r_price_pctl = round(cnt / len(closes) * 100, 1)
        else:
            r_price_pctl = base.get("pricePercentile")

        temp = int(round(r_pe_pctl * 0.6 + r_price_pctl * 0.4, 0))
        out = {
            "price": round(spot, 1),
            "change": round(chg, 2),
            "pe": r_pe,
            "pePercentile": r_pe_pctl,
            "pricePercentile": r_price_pctl,
            "temperature": temp,
            "live": True,
            "market": True,
            "ts": ts,
        }
        print(f"   📈 实时点位: {spot:.1f} ({chg:+.2f}%)")
        print(f"   📊 估算PE: {r_pe} (分位 {r_pe_pctl}%) | 价格分位 {r_price_pctl}%")
        print(f"   🌡️  实时温度: {temp}°C")

    payload = "window.__RT = " + json.dumps(out, ensure_ascii=False) + ";"
    for path in (OUT_LOCAL, OUT_MAIN):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(payload)
            print(f"   ✅ 写入 {path}")
        except Exception as e:
            print(f"   ⚠️ 写入失败 {path}: {e}")


if __name__ == '__main__':
    main()
