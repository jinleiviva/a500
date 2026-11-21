#!/usr/bin/env python3
"""ETF 溢价分位的纯函数测试，不访问网络。"""

import json
import os
import tempfile
import unittest
from datetime import datetime

import fetch_etf_premium as etf


class PremiumMathTest(unittest.TestCase):
    def test_premium_uses_close_over_unit_nav(self):
        self.assertAlmostEqual(etf.premium_pct(1.26, 1.2), 5.0)
        self.assertAlmostEqual(etf.premium_pct(2.356, 1.9548), 20.523837, places=4)
        for value in (0, -1, float("nan"), float("inf")):
            self.assertIsNone(etf.premium_pct(1.1, value))
            self.assertIsNone(etf.premium_pct(value, 1.0))

    def test_join_keeps_shared_dates_and_survives_split(self):
        navs = [
            ("2022-01-12", 5.189),
            ("2022-01-13", 1.009),
            ("2022-01-14", 1.019),
        ]
        closes = [("2022-01-12", 5.192), ("2022-01-14", 1.015)]
        rows = etf.join_daily(navs, closes)
        self.assertEqual([row[0] for row in rows], ["2022-01-12", "2022-01-14"])
        self.assertAlmostEqual(rows[0][3], 0.0578, places=3)
        self.assertAlmostEqual(rows[1][3], -0.3925, places=3)
        self.assertTrue(all(abs(row[3]) < 1 for row in rows))

    def test_percentile_window_rank_and_bands(self):
        rows = [(f"2024-01-{(index % 28) + 1:02d}", 1, 1, float(index)) for index in range(100)]
        stats = etf.percentiles_for(rows, 29.0, "2026-10-19")["all"]
        self.assertEqual(stats["n"], 100)
        self.assertAlmostEqual(stats["pct"], 30.0)
        self.assertAlmostEqual(stats["min"], 0.0)
        self.assertAlmostEqual(stats["p50"], 49.5)
        self.assertAlmostEqual(stats["max"], 99.0)
        self.assertIsNone(etf.percentiles_for(rows[:59], 1.0, "2026-10-19")["all"])
        self.assertEqual(etf.percentile_phrase(9.4), "溢价处于历史低位")
        self.assertEqual(etf.percentile_phrase(9.5), "溢价较低")
        self.assertEqual(etf.percentile_phrase(29.5), "溢价正常")
        self.assertEqual(etf.percentile_phrase(70.0), "溢价正常")
        self.assertEqual(etf.percentile_phrase(87.4), "溢价较高")
        self.assertEqual(etf.percentile_phrase(90.4), "溢价较高")
        self.assertEqual(etf.percentile_phrase(90.5), "溢价处于历史高位")

    def test_one_year_window_drops_older_sessions(self):
        rows = []
        date = "2026-10-19"
        for premium in range(69, -1, -1):
            rows.append((date, 1, 1, float(premium)))
            date = etf.shift_days(date, -1)
        rows.append(("2024-01-02", 1, 1, 500.0))
        stats = etf.percentiles_for(rows, 9.0, "2026-10-19")["y1"]
        self.assertEqual(stats["n"], 70)
        self.assertLess(stats["max"], 100)
        self.assertEqual(stats["since"], etf.shift_days("2026-10-19", -69))

    def test_subscription_status(self):
        self.assertTrue(etf.subscription_limited("513100", None))
        self.assertFalse(etf.subscription_limited("513500", None))
        self.assertTrue(etf.subscription_limited("513500", "暂停申购"))
        self.assertTrue(etf.subscription_limited("513100", "限制申购"))
        self.assertFalse(etf.subscription_limited("513100", "场内买入"))
        self.assertIn("暂停申购", etf.pause_note("513100", "暂停申购"))
        self.assertIn("限制申购", etf.pause_note("513500", "限制申购"))
        self.assertEqual(etf.pause_note("513500", "场内买入"), "")

    def test_calendar_leap_day_and_intraday_bar(self):
        self.assertEqual(etf.shift_years("2024-02-29", 1), "2023-02-28")
        self.assertEqual(etf.shift_years("2026-10-19", 3), "2023-10-19")
        self.assertEqual(etf.shift_days("2024-03-01", -1), "2024-02-29")
        self.assertFalse(etf.valid_date("2022-02-31"))
        noon = datetime(2026, 10, 20, 14, 27, tzinfo=etf.SHANGHAI)
        after = datetime(2026, 10, 20, 15, 31, tzinfo=etf.SHANGHAI)
        closes = [("2026-10-19", 2.3), ("2026-10-20", 2.4)]
        self.assertEqual(etf.usable_closes(closes, noon), [("2026-10-19", 2.3)])
        self.assertEqual(etf.usable_closes(closes, after), closes)


class ParserTest(unittest.TestCase):
    def test_lsjz_tencent_and_eastmoney_kline(self):
        nav_text = """{"ErrCode":0,"TotalCount":3,"Data":{"LSJZList":[
            {"FSRQ":"2026-10-15","DWJZ":"2.0057","SGZT":"暂停申购"},
            {"FSRQ":"2026-10-16","DWJZ":"1.9548","SGZT":"场内买入"},
            {"FSRQ":"bad","DWJZ":"1.1","SGZT":"暂停申购"}
        ]}}"""
        points, total, status = etf.parse_lsjz(nav_text)
        self.assertEqual(total, 3)
        self.assertEqual(points, [("2026-10-15", 2.0057), ("2026-10-16", 1.9548)])
        self.assertEqual(status, "场内买入")

        ping = 'var Data_netWorthTrend = [{"x":1792080000000,"y":1.9548,"equityReturn":-2.54,"unitMoney":""}];'
        self.assertEqual(etf.parse_pingzhong(ping), [("2026-10-16", 1.9548)])

        tencent = """{"code":0,"data":{"sh513100":{
            "qfqday":[["2024-01-02","0.500","0.600","0.700","0.400","100"]],
            "day":[["2024-01-02","1.100","1.260","1.270","1.090","100"]]
        }}}"""
        self.assertEqual(etf.parse_tencent_kline(tencent, "513100"), [("2024-01-02", 1.26)])
        kline = '{"rc":0,"data":{"klines":["2024-01-02,1.100,1.260","2024-01-03,1.200,1.300"]}}'
        self.assertEqual(etf.parse_eastmoney_kline(kline)[1], ("2024-01-03", 1.3))
        with self.assertRaises(ValueError):
            etf.parse_lsjz('{"ErrCode":1}')
        with self.assertRaises(ValueError):
            etf.parse_tencent_kline('{"code":0,"data":{"sh513100":{"qfqday":[]}}}', "513100")

    def test_failure_keeps_previous_file_and_disable_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "etf_premium.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"enabled": True, "items": [{"symbol": "513100", "history": []}]}, handle)
            with open(path, encoding="utf-8") as handle:
                original = handle.read()

            def boom(*_args, **_kwargs):
                raise RuntimeError("网络不可用")

            saved = etf._fetch_item
            etf._fetch_item = boom
            try:
                self.assertFalse(etf.update(path, now=datetime(2026, 10, 20, 16, 0, tzinfo=etf.SHANGHAI)))
            finally:
                etf._fetch_item = saved
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), original)

            etf.mark_disabled(path)
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
            self.assertFalse(stored["enabled"])
            self.assertEqual(stored["items"][0]["symbol"], "513100")


if __name__ == "__main__":
    unittest.main()
