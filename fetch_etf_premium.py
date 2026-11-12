#!/usr/bin/env python3
"""ETF 溢价历史分位。

纳指ETF国泰（513100）与标普500ETF博时（513500）：
  溢价率 = 收盘价 / 单位净值 − 1
  收盘价用腾讯证券不复权日 K（失败时回退东方财富不复权日 K）
  单位净值用东方财富基金净值；申购状态用东方财富 f10/lsjz 的 SGZT

513100 在 2022-01-13 拆分。当天没有成交，前后交易日的收盘价和单位净值同比例变化，
按日期对齐后溢价率仍连续。只保留收盘价和单位净值都有的交易日。

输出 etf_premium.json。任何失败都保留上一份文件并返回 0，避免打断 A500 收盘更新。
关闭面板：环境变量 A500_ETF_PREMIUM=0，或把 JSON 里的 enabled 设为 false。
"""

import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

for _k in list(os.environ.keys()):
    if "proxy" in _k.lower():
        os.environ.pop(_k, None)

DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(DIR, "etf_premium.json")

SYMBOLS = (
    ("513100", "纳指ETF国泰"),
    ("513500", "标普500ETF博时"),
)
MIN_SAMPLES = 60
TENCENT_PAGE = 640
TENCENT_PAGES = 12
NAV_PAGE_SIZE = 20
NAV_PAGE_CAP = 400
PAUSE_NOTE = "暂停申购中，溢价存在结构性偏高，分位仅供参考"
LIMIT_NOTE = "限制申购中，溢价存在结构性偏高，分位仅供参考"
HISTORY_FIELDS = ["date", "close", "nav", "premium_pct"]
SHANGHAI = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def shanghai_now():
    return datetime.now(SHANGHAI)


def premium_pct(close, nav):
    try:
        close = float(close)
        nav = float(nav)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(close) or not math.isfinite(nav) or close <= 0 or nav <= 0:
        return None
    rate = (close / nav - 1.0) * 100.0
    if not math.isfinite(rate):
        return None
    return rate


def join_daily(navs, closes):
    """navs/closes: iterable of (date, value). Keep dates present in both."""
    nav_map = {}
    for date, nav in navs:
        if valid_date(date):
            value = _positive(nav)
            if value is not None:
                nav_map[date] = value
    rows = []
    seen = set()
    for date, close in closes:
        if not valid_date(date) or date in seen or date not in nav_map:
            continue
        px = _positive(close)
        if px is None:
            continue
        rate = premium_pct(px, nav_map[date])
        if rate is None:
            continue
        seen.add(date)
        rows.append((date, px, nav_map[date], rate))
    rows.sort(key=lambda row: row[0])
    return rows


def subscription_limited(symbol, status):
    text = (status or "").strip()
    if not text:
        return symbol == "513100"
    return ("暂停申购" in text) or ("限制申购" in text)


def pause_note(symbol, status):
    if not subscription_limited(symbol, status):
        return ""
    text = (status or "").strip()
    if "限制申购" in text and "暂停申购" not in text:
        return LIMIT_NOTE
    return PAUSE_NOTE


def percentile_phrase(pct):
    rounded = _round_half_away(pct)
    if rounded < 10:
        return "溢价处于历史低位"
    if rounded < 30:
        return "溢价较低"
    if rounded <= 70:
        return "溢价正常"
    if rounded <= 90:
        return "溢价较高"
    return "溢价处于历史高位"


def percentiles_for(rows, current, today):
    return {
        "y1": _window_value(rows, current, today, 1),
        "y3": _window_value(rows, current, today, 3),
        "all": _window_value(rows, current, today, 0),
    }


def parse_lsjz(text):
    value = json.loads(strip_jsonp(text))
    if value.get("ErrCode", -1) != 0:
        raise ValueError(f"东财净值错误码 {value.get('ErrCode')}")
    data = value.get("Data") or {}
    items = data.get("LSJZList") or []
    total = int(value.get("TotalCount") or len(items))
    points = []
    best = None
    for item in items:
        date = item.get("FSRQ") or ""
        if not valid_date(date):
            continue
        status = (item.get("SGZT") or "").strip()
        if status and (best is None or date > best[0]):
            best = (date, status)
        nav = _positive(item.get("DWJZ"))
        if nav is None:
            continue
        points.append((date, nav))
    return points, total, (best[1] if best else None)


def parse_pingzhong(text):
    marker = "Data_netWorthTrend"
    start = text.find(marker)
    if start < 0:
        raise ValueError("东财净值缺少 Data_netWorthTrend")
    bracket = text.find("[", start)
    if bracket < 0:
        raise ValueError("东财净值缺少序列")
    arr, _ = json.JSONDecoder().raw_decode(text[bracket:])
    points = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        raw = item.get("x")
        nav = _positive(item.get("y"))
        if raw is None or nav is None:
            continue
        date = _ms_to_date(raw)
        if valid_date(date):
            points.append((date, nav))
    if not points:
        raise ValueError("东财净值没有单位净值")
    return points


def parse_tencent_kline(text, symbol):
    value = json.loads(strip_jsonp(text))
    if value.get("code", -1) != 0:
        raise ValueError(f"腾讯K线错误码 {value.get('code')}")
    node = ((value.get("data") or {}).get(f"sh{symbol}")) or {}
    rows = node.get("day")
    if not isinstance(rows, list):
        raise ValueError("腾讯K线缺少未复权 day")
    points = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        date = str(row[0])
        close = _positive(row[2])
        if valid_date(date) and close is not None:
            points.append((date, close))
    return points


def parse_eastmoney_kline(text):
    value = json.loads(strip_jsonp(text))
    if value.get("rc", -1) != 0:
        raise ValueError(f"东财K线错误码 {value.get('rc')}")
    lines = ((value.get("data") or {}).get("klines")) or []
    if not isinstance(lines, list) or not lines:
        raise ValueError("东财K线没有收盘价")
    points = []
    for line in lines:
        parts = str(line).split(",")
        if len(parts) < 3:
            continue
        date, _open, close = parts[0], parts[1], parts[2]
        px = _positive(close)
        if valid_date(date) and px is not None:
            points.append((date, px))
    if not points:
        raise ValueError("东财K线没有收盘价")
    return points


def usable_closes(closes, now):
    """盘中不把当天未收盘的 K 线当成收盘价。15:05 之后视为当日已收盘。"""
    if now.hour * 60 + now.minute >= 15 * 60 + 5:
        return list(closes)
    today = now.strftime("%Y-%m-%d")
    return [(date, close) for date, close in closes if date != today]


def build_item(symbol, name, rows, status, close_source, today):
    if not rows:
        raise ValueError("没有可对齐的收盘价和单位净值")
    latest = rows[-1]
    current = latest[3]
    history = [
        [date, _round(close, 4), _round(nav, 4), _round(rate, 4)]
        for date, close, nav, rate in rows
    ]
    return {
        "symbol": symbol,
        "name": name,
        "subscription_status": status or "",
        "subscription_limited": subscription_limited(symbol, status),
        "pause_note": pause_note(symbol, status),
        "close_source": close_source,
        "nav_source": "eastmoney",
        "latest": {
            "date": latest[0],
            "close": _round(latest[1], 4),
            "nav": _round(latest[2], 4),
            "premium_pct": _round(latest[3], 4),
        },
        "percentiles": percentiles_for(rows, current, today),
        "history": history,
    }


def update(path=OUTPUT, now=None):
    now = now or shanghai_now()
    today = now.strftime("%Y-%m-%d")
    previous = _load(path)
    old_items = {item.get("symbol"): item for item in previous.get("items") or []}
    items = []
    ok = 0
    for symbol, name in SYMBOLS:
        try:
            item = _fetch_item(symbol, name, today, now)
            items.append(item)
            ok += 1
            latest = item["latest"]
            print(
                f"  {symbol} {name}: {latest['date']} 溢价 {latest['premium_pct']:.2f}%"
                f"（收盘 {latest['close']} / 净值 {latest['nav']}，{item['close_source']}，{len(item['history'])} 日）"
                f" 申购 {item['subscription_status'] or '未知'}"
            )
        except Exception as exc:
            print(f"  ⚠️ {symbol} 更新失败，沿用上一份：{exc}")
            if symbol in old_items:
                items.append(old_items[symbol])
    if ok == 0:
        print("⚠️ ETF溢价没有新数据，保留上一份文件")
        return False
    doc = {
        "enabled": True,
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "as_of": today,
        "note": "基于收盘价与单位净值对齐的日度历史，不含估算溢价。分位是当前溢价在该区间内小于等于它的交易日占比；样本少于 60 个交易日的区间不展示。",
        "history_fields": HISTORY_FIELDS,
        "items": items,
    }
    _write(path, doc)
    print(f"✅ {path}")
    return True


def mark_disabled(path=OUTPUT):
    doc = _load(path)
    doc["enabled"] = False
    doc.setdefault("items", [])
    doc["updated"] = shanghai_now().strftime("%Y-%m-%d %H:%M")
    _write(path, doc)
    print(f"已关闭 ETF 溢价面板 → {path}")


def main():
    try:
        flag = os.environ.get("A500_ETF_PREMIUM", "1").strip().lower()
        if flag in ("0", "false", "off", "no"):
            mark_disabled()
            return 0
        update()
    except Exception as exc:
        print(f"⚠️ ETF溢价更新失败，沿用上一份数据：{exc}")
    return 0


def _fetch_item(symbol, name, today, now):
    navs = _fetch_nav(symbol)
    try:
        status = _fetch_status(symbol)
    except Exception as exc:
        print(f"  ⚠️ {symbol} 申购状态不可用：{exc}")
        status = None
    closes, source = _fetch_closes(symbol)
    closes = usable_closes(closes, now)
    rows = join_daily(navs, closes)
    return build_item(symbol, name, rows, status, source, today)


def _fetch_nav(symbol):
    try:
        text = http_get(
            f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js",
            "https://fund.eastmoney.com/",
        )
        points = parse_pingzhong(text)
        print(f"  {symbol} 净值 {len(points)} 条（东方财富）")
        return points
    except Exception as exc:
        print(f"  ⚠️ {symbol} 净值序列不可用，改为分页接口：{exc}")
        return _fetch_nav_pages(symbol)


def _fetch_nav_pages(symbol):
    all_points = []
    page = 1
    while page <= NAV_PAGE_CAP:
        url = (
            "https://api.fund.eastmoney.com/f10/lsjz"
            f"?fundCode={symbol}&pageIndex={page}&pageSize={NAV_PAGE_SIZE}"
        )
        points, total, _status = parse_lsjz(
            http_get(url, "https://fundf10.eastmoney.com/")
        )
        all_points.extend(points)
        if not points or page * NAV_PAGE_SIZE >= max(total, 0):
            break
        page += 1
    if not all_points:
        raise ValueError("东财净值没有单位净值")
    return all_points


def _fetch_status(symbol):
    url = (
        "https://api.fund.eastmoney.com/f10/lsjz"
        f"?fundCode={symbol}&pageIndex=1&pageSize=5"
    )
    _points, _total, status = parse_lsjz(http_get(url, "https://fundf10.eastmoney.com/"))
    return status


def _fetch_closes(symbol):
    try:
        points = _fetch_tencent(symbol)
        if points:
            return points, "tencent"
    except Exception as exc:
        print(f"  ⚠️ {symbol} 腾讯K线不可用，改用东财：{exc}")
    return _fetch_eastmoney_closes(symbol), "eastmoney"


def _fetch_tencent(symbol):
    merged = {}
    end = None
    oldest = None
    for _ in range(TENCENT_PAGES):
        if end is None:
            url = (
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param=sh{symbol},day,,,{TENCENT_PAGE},"
            )
        else:
            url = (
                "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param=sh{symbol},day,,{end},{TENCENT_PAGE},"
            )
        points = parse_tencent_kline(http_get(url, "https://gu.qq.com/"), symbol)
        if not points:
            break
        points.sort(key=lambda row: row[0])
        first = points[0][0]
        if oldest is not None and first >= oldest:
            break
        for date, close in points:
            merged[date] = close
        oldest = first
        if len(points) < TENCENT_PAGE:
            break
        prev = shift_days(first, -1)
        if not prev or end == prev:
            break
        end = prev
    if not merged:
        raise ValueError("腾讯K线没有收盘价")
    return sorted(merged.items())


def _fetch_eastmoney_closes(symbol):
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid=1.{symbol}&klt=101&fqt=0&fields1=f1,f2,f3&fields2=f51,f52,f53"
        "&beg=0&end=20500101"
    )
    return parse_eastmoney_kline(http_get(url, "https://quote.eastmoney.com/"))


def http_get(url, referer):
    last = "请求失败"
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json, text/plain, */*",
                    "Referer": referer,
                },
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                status = getattr(resp, "status", 200)
                raw = resp.read()
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                raise RuntimeError("空响应")
            return text
        except Exception as exc:
            last = str(exc)
            time.sleep(0.4 * (attempt + 1))
    raise RuntimeError(last)


def _window_value(rows, current, today, years):
    selected = _select_window(rows, today, years)
    if len(selected) < MIN_SAMPLES:
        return None
    values = sorted(row[3] for row in selected)
    since = min(row[0] for row in selected)
    pct = None
    if current is not None:
        try:
            current = float(current)
        except (TypeError, ValueError):
            current = None
    if current is not None and current == current:
        below = sum(1 for value in values if value <= current)
        pct = _round(below / len(values) * 100.0, 1)
    return {
        "pct": pct,
        "n": len(values),
        "since": since,
        "min": _round(values[0], 4),
        "p10": _round(_quantile(values, 10), 4),
        "p50": _round(_quantile(values, 50), 4),
        "p90": _round(_quantile(values, 90), 4),
        "max": _round(values[-1], 4),
        "phrase": percentile_phrase(pct) if pct is not None else "",
    }


def _select_window(rows, today, years):
    cutoff = None if years <= 0 else shift_years(today, years)
    if years > 0 and cutoff is None:
        return []
    picked = []
    for row in rows:
        date = row[0]
        rate = row[3]
        if not valid_date(date):
            continue
        if rate != rate or rate in (float("inf"), float("-inf")):
            continue
        if cutoff is not None and date < cutoff:
            continue
        picked.append(row)
    return picked


def _quantile(sorted_values, percentile):
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (percentile / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = low if rank == low else low + 1
    weight = rank - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, doc):
    items = []
    histories = []
    for item in doc.get("items") or []:
        copied = dict(item)
        histories.append(copied.pop("history", []))
        items.append(copied)
    body = dict(doc)
    body["items"] = items
    for index, _history in enumerate(histories):
        items[index]["history"] = f"__HISTORY_{index}__"
    text = json.dumps(body, ensure_ascii=False, indent=2)
    for index, history in enumerate(histories):
        token = f'"__HISTORY_{index}__"'
        lines = [
            json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            for row in history
        ]
        block = "[\n        " + ",\n        ".join(lines) + "\n      ]" if lines else "[]"
        if token not in text:
            raise RuntimeError("写出溢价历史失败")
        text = text.replace(token, block, 1)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.write("\n")
    os.replace(tmp, path)


def strip_jsonp(text):
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        return text
    open_at = text.find("(")
    close_at = text.rfind(")")
    if open_at >= 0 and close_at > open_at:
        return text[open_at + 1:close_at].strip()
    return text


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 0 and number == number and number != float("inf"):
        return number
    return None


def _round(value, digits):
    return round(float(value) + 0.0, digits)


def _round_half_away(value):
    number = float(value)
    if number >= 0:
        return int(number + 0.5)
    return int(number - 0.5)


def _ms_to_date(raw):
    seconds = float(raw) / 1000.0 + 8 * 3600
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d")


def shift_years(date, years):
    parsed = _parse_ymd(date)
    if not parsed:
        return None
    year, month, day = parsed
    year -= int(years)
    if month == 2 and day == 29 and not _is_leap(year):
        day = 28
    shifted = f"{year:04d}-{month:02d}-{day:02d}"
    return shifted if valid_date(shifted) else None


def shift_days(date, days):
    parsed = _parse_ymd(date)
    if not parsed:
        return None
    year, month, day = parsed
    base = datetime(year, month, day) + timedelta(days=int(days))
    return base.strftime("%Y-%m-%d")


def valid_date(text):
    parsed = _parse_ymd(text)
    if not parsed:
        return False
    year, month, day = parsed
    if not (1990 <= year <= 2100):
        return False
    try:
        datetime(year, month, day)
    except ValueError:
        return False
    return True


def _parse_ymd(text):
    if not isinstance(text, str) or len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return int(text[0:4]), int(text[5:7]), int(text[8:10])
    except ValueError:
        return None


def _is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


if __name__ == "__main__":
    sys.exit(main())
