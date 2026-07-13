#!/usr/bin/env python3
"""
A500 温度计 · 盘中实时脚本
================================
由自动化任务在交易时段每 5 分钟调用一次。
用 akshare 实时行情接口拿到中证 A500 最新点位（盘中=实时，午休/收盘=最新收盘），
基于 fetch_a500_data.py 落盘的 realtime_baseline.json（PE 历史 + 全量收盘价），
按与日频相同的公式（温度 = PE分位×60% + 价格分位×40%）重算温度，
写入 realtime_data.js（window.__RT），供前端轮询展示。

容错：akshare 偶发连接中断时，复用「今日最近一次成功行情」计算（避免退回旧基线）；
连续失败且无今日缓存时，才回退日频基线。

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
LASTGOOD = os.path.join(DIR, "realtime_last.json")
OUT_LOCAL = os.path.join(DIR, "realtime_data.js")
OUT_MAIN = os.path.join(os.path.dirname(DIR), "realtime_data.js")   # 主目录（与 A500温度计.html 同层）
OUT_MAIN_HTML = os.path.join(os.path.dirname(DIR), "A500温度计.html")  # 主目录 HTML


def load_json(path: str):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_json(path: str, data: dict):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def inline_into_html(html_path, payload):
    """把实时数据内联进 HTML 的 REALTIME_INLINE 锚点（双保险，避免依赖外部文件加载成败）。"""
    import re
    if not os.path.exists(html_path):
        return
    with open(html_path, encoding='utf-8') as f:
        h = f.read()
    marker = "<!-- REALTIME_INLINE -->"
    inline = marker + "\n<script>" + payload + "</script>"
    if marker in h:
        h2 = re.sub(r"<!-- REALTIME_INLINE -->\s*<script>.*?</script>", inline, h, count=1, flags=re.S)
        if h2 == h:
            h2 = h.replace(marker, inline, 1)
    else:
        h2 = h.replace("</body>", inline + "\n</body>", 1)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(h2)
    print(f"   ✅ 内联实时数据 → {html_path}")


def is_market_open() -> bool:
    n = datetime.datetime.now()
    if n.weekday() >= 5:
        return False
    t = n.hour * 100 + n.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


def fetch_spot(retries: int = 5):
    """返回 (最新价, 涨跌幅%)，失败返回 (None, None)。带重试。"""
    last_err = None
    for i in range(retries):
        try:
            df = ak.stock_zh_index_spot_em(symbol='沪深重要指数')
            row = df[df['名称'].astype(str).str.contains('A500|中证A500', na=False)]
            if not row.empty:
                return float(row.iloc[0]['最新价']), float(row.iloc[0]['涨跌幅'])
        except Exception as e:
            last_err = e
            time.sleep(3)
    if last_err:
        print(f"   ⚠️ 实时行情获取失败（{retries}次重试）: {last_err}")
    return None, None


def compute(spot, chg, base, market, ts, fresh, live, delayed=False):
    price0 = float(base["price"])
    pe0 = float(base["pe"])
    r_pe = round(pe0 * spot / price0, 2)

    pe_hist = base.get("peHistory", [])
    if pe_hist:
        cnt = sum(1 for h in pe_hist if h["pe"] <= r_pe)
        r_pe_pctl = round(cnt / len(pe_hist) * 100, 1)
    else:
        r_pe_pctl = base.get("pePercentile")

    closes = base.get("closesAll", [])
    if closes:
        cnt = sum(1 for c in closes if c <= spot)
        r_price_pctl = round(cnt / len(closes) * 100, 1)
    else:
        r_price_pctl = base.get("pricePercentile")

    temp = int(round(r_pe_pctl * 0.6 + r_price_pctl * 0.4, 0))
    return {
        "price": round(spot, 1),
        "change": round(chg, 2),
        "pe": r_pe,
        "pePercentile": r_pe_pctl,
        "pricePercentile": r_price_pctl,
        "temperature": temp,
        "live": live,
        "fresh": fresh,
        "delayed": delayed,
        "market": market,
        "ts": ts,
    }


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

    if spot is not None:
        out = compute(spot, chg, base, market, ts, fresh=True, live=market)
        save_json(LASTGOOD, {"price": spot, "chg": chg, "ts": ts})
        phase = '盘中' if market else '最新收盘'
        print(f"   📈 最新点位: {spot:.1f} ({chg:+.2f}%) [{phase}]")
        print(f"   📊 估算PE: {out['pe']} (分位 {out['pePercentile']}%) | 价格分位 {out['pricePercentile']}%")
        print(f"   🌡️  温度: {out['temperature']}°C")
    else:
        # 拉取失败 → 复用今日最近一次成功行情（避免退回旧基线）
        lg = load_json(LASTGOOD)
        if lg and lg.get("ts", "").startswith(now.strftime('%Y-%m-%d')):
            sp, cg = lg["price"], lg.get("chg", 0)
            out = compute(sp, cg, base, market, lg.get("ts"), fresh=True, live=False, delayed=True)
            print(f"   🔄 行情拉取失败，复用今日最近成功数据 (ts={lg.get('ts')})")
            print(f"   🌡️  温度: {out['temperature']}°C (数据略有延迟)")
        else:
            out = {
                "price": base.get("price"),
                "change": base.get("priceChange"),
                "pe": base.get("pe"),
                "pePercentile": base.get("pePercentile"),
                "pricePercentile": base.get("pricePercentile"),
                "temperature": base.get("temperature"),
                "live": False,
                "fresh": False,
                "delayed": False,
                "market": market,
                "ts": ts,
            }
            print(f"   ⚠️ 无今日缓存，回退日频基线温度 {base.get('temperature')}°C")

    payload = "window.__RT = " + json.dumps(out, ensure_ascii=False) + ";"
    for path in (OUT_LOCAL, OUT_MAIN):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(payload)
            print(f"   ✅ 写入 {path}")
        except Exception as e:
            print(f"   ⚠️ 写入失败 {path}: {e}")

    # 内联到 HTML（双保险：即使外部 realtime_data.js 加载失败，页面打开即显示正确温度）
    inline_into_html(os.path.join(DIR, "a500_dashboard.html"), payload)
    # 同步主目录（覆盖任何残留，确保 HTML 结构完整 + 内联最新数据）
    import shutil
    try:
        shutil.copy(os.path.join(DIR, "a500_dashboard.html"), OUT_MAIN_HTML)
        print(f"   ✅ 同步 → {OUT_MAIN_HTML}")
    except Exception as e:
        print(f"   ⚠️ 同步失败 {OUT_MAIN_HTML}: {e}")


if __name__ == '__main__':
    main()
