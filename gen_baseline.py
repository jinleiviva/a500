#!/usr/bin/env python3
"""
A500 温度计 · 前端自愈基线生成
================================
把 realtime_baseline.json 中「计算温度所需的全部历史数据」打包成前端静态 JS
（window.__A500_BASE），供 index.html 在前端用腾讯 gtimg 实时价本地算温度/PE分位/价格分位。

这样 A500 温度不再依赖 GitHub Actions 每日推送；Actions 仅低频（日频）更新此文件，
即使挂了，前端用已加载的基线 + 实时价依然能算（只是 PE 历史不增长，不影响当日温度）。

使用:  python3 gen_baseline.py
输出:  a500_baseline.js
"""
import os, json

DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(DIR, "realtime_baseline.json")
OUT = os.path.join(DIR, "a500_baseline.js")


def main():
    if not os.path.exists(BASELINE):
        print("   ❌ 找不到 realtime_baseline.json，请先运行 fetch_a500_data.py")
        return
    base = json.load(open(BASELINE, encoding="utf-8"))

    out = {
        # ── 锚点（PE 估算用：当前 PE = 基线PE × 实时价/基线价）──
        "date":             base.get("date"),
        "price":            base.get("price"),          # 最近收盘（基线价）
        "pe":               base.get("pe"),            # 基线 PE
        # ── 历史序列（分位计算用）──
        "peHistory":        base.get("peHistory"),     # 全量 PE 历史 [{date, pe}]
        "closesAll":        base.get("closesAll"),     # 全量收盘价（用于价格分位）
        # ── 低频指标（日频，供股债差/股息率展示；前端直接取用）──
        "bondYield":        base.get("bondYield"),
        "dividendYield":    base.get("dividendYield"),
        "peLastCalibrated": base.get("peLastCalibrated"),
        # ── 完整快照（兜底：万一前端计算失败，仍可显示最后已知温度）──
        "temperature":      base.get("temperature"),
        "pePercentile":     base.get("pePercentile"),
        "pricePercentile":  base.get("pricePercentile"),
    }

    payload = "window.__A500_BASE = " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(payload)

    closes = out["closesAll"] or []
    ph = out["peHistory"] or []
    print(f"   ✅ a500_baseline.js 生成完成 ({len(payload)} bytes)")
    print(f"      closesAll: {len(closes)} 点 | peHistory: {len(ph)} 条 | 基线价 {out['price']} / PE {out['pe']}")


if __name__ == "__main__":
    main()
