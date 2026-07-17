#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取台積電(2330)與臻鼎-KY(4958)過去一年的真實交易資料，並更新 public/granville.html。

整合兩種 Yahoo Finance 取得方式：

1. yfinance 歷史日 K（主要來源）::

       import yfinance as yf
       df = yf.download(stock_id, start=start, auto_adjust=False, multi_level_index=False)

2. tw.stock.yahoo.com 即時報價爬蟲（輔助：歷史資料落後時補上今日這根 K 棒）——
   即 requests + BeautifulSoup 解析 `qsp-overview-realtime-info` 區塊的作法。

若執行環境連不上 Yahoo（例如沙箱網路政策擋 CONNECT），會自動退回開源存檔
voidful/tw_stocker（raw.githubusercontent.com，Yahoo Finance 實際成交之 5 分 K），
聚合為日 K 後使用，並在網頁註記資料來源與截止日。

用法::

    pip install yfinance requests beautifulsoup4 pandas
    python scripts/update_granville_data.py                 # 自動：先 Yahoo，失敗退回存檔
    python scripts/update_granville_data.py --source yahoo  # 只用 yfinance＋即時報價
    python scripts/update_granville_data.py --source archive
    python scripts/update_granville_data.py --csv-dir data  # 額外輸出各股一年日 K 的 CSV

腳本會就地改寫 public/granville.html 中以 /*DATA:xxxx*/、/*SIG:xxxx*/、/*ADV:xxxx*/
與 <!--SRC--> 標記包住的區塊；「實戰解讀」註解與參考價位階梯為人工撰寫的教學內容，
更新資料後請人工複核（腳本結尾會印出新的關鍵價位供對照）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")
REPO_ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = REPO_ROOT / "public" / "granville.html"
ARCHIVE_URL = "https://raw.githubusercontent.com/voidful/tw_stocker/main/data/{sym}.csv"

STOCKS = {
    "2330": {"name": "台積電", "dev_hi": 0.10, "dev_lo": -0.10},
    "4958": {"name": "臻鼎-KY", "dev_hi": 0.13, "dev_lo": -0.13},
}


# ---------------------------------------------------------------- yfinance --
def fetch_yfinance(sym: str, start: str):
    """使用者提供之寫法：yf.download(stock_id, start=start, auto_adjust=False,
    multi_level_index=False)。回傳 [{d,o,h,l,c,v}, ...]（v 單位：股）。"""
    import yfinance as yf

    df = yf.download(f"{sym}.TW", start=start, auto_adjust=False,
                     multi_level_index=False, progress=False)
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance 對 {sym}.TW 回傳空資料（多半是網路被擋）")
    rows = []
    for dt, r in df.iterrows():
        if any(r.get(k) is None or r.get(k) != r.get(k) for k in ("Open", "High", "Low", "Close")):
            continue  # 跳過 NaN 列
        rows.append({
            "d": dt.strftime("%Y-%m-%d"),
            "o": float(r["Open"]), "h": float(r["High"]),
            "l": float(r["Low"]), "c": float(r["Close"]),
            "v": int(r["Volume"] or 0),
        })
    return rows


# ------------------------------------------------- tw.stock.yahoo.com 即時 --
def yahoo_stock(stock_id: str):
    """使用者提供之即時報價爬蟲（requests + BeautifulSoup），欄位如
    成交/開盤/最高/最低/總量…；回傳 dict，失敗回傳 None。"""
    import requests
    from bs4 import BeautifulSoup

    url = f"https://tw.stock.yahoo.com/quote/{stock_id}.TW"
    response = requests.get(url, timeout=20,
                            headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    section = soup.find("section", {"id": "qsp-overview-realtime-info"})
    if section is None:
        return None
    time_element = section.find("time")
    fields, datas = [], []
    for li in section.find("ul").find_all("li"):
        for num, span in enumerate(li.find_all("span")):
            if span.text == "":
                continue
            (fields if num == 0 else datas).append(span.text)
    quote = dict(zip(fields, datas))
    # 頁面上的 time 屬性拼法歷來有 datatime / datetime 兩種
    when = None
    if time_element is not None:
        when = time_element.get("datatime") or time_element.get("datetime") \
            or time_element.text
    quote["_time"] = when
    return quote


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def patch_today_from_realtime(sym: str, rows: list) -> bool:
    """歷史序列若落後於今日，且即時頁有今日成交資料，補上今日 K 棒。"""
    try:
        q = yahoo_stock(sym)
    except Exception as e:  # 網路不通等情況：略過即可
        print(f"  [{sym}] 即時報價略過（{type(e).__name__}: {str(e)[:80]}）")
        return False
    if not q:
        return False
    today = datetime.now(TPE).strftime("%Y-%m-%d")
    if rows and rows[-1]["d"] >= today:
        return False
    o, h, l, c = (_num(q.get("開盤")), _num(q.get("最高")),
                  _num(q.get("最低")), _num(q.get("成交")))
    v = _num(q.get("總量"))  # 單位：張
    if None in (o, h, l, c):
        return False
    rows.append({"d": today, "o": o, "h": h, "l": l, "c": c,
                 "v": int((v or 0) * 1000)})
    print(f"  [{sym}] 已用即時報價補上 {today}（收 {c}）")
    return True


# ----------------------------------------------------------------- archive --
def fetch_archive(sym: str):
    """voidful/tw_stocker 的 5 分 K 存檔 → 聚合為日 K。"""
    req = urllib.request.Request(ARCHIVE_URL.format(sym=sym),
                                 headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=120).read().decode()
    days = {}
    for row in csv.DictReader(io.StringIO(raw)):
        ts = datetime.fromisoformat(row["Datetime"]).astimezone(TPE)
        hm = ts.hour * 60 + ts.minute
        if hm < 9 * 60 or hm > 13 * 60 + 30:  # 只留普通交易時段
            continue
        o, h, l, c = (float(row["Open"]), float(row["High"]),
                      float(row["Low"]), float(row["Close"]))
        v = int(float(row["Volume"]))
        d = ts.date().isoformat()
        if d not in days:
            days[d] = {"d": d, "o": o, "h": h, "l": l, "c": c, "v": v,
                       "first": hm, "last": hm, "bars": 1}
        else:
            dd = days[d]
            if hm < dd["first"]:
                dd["first"], dd["o"] = hm, o
            if hm >= dd["last"]:
                dd["last"], dd["c"] = hm, c
            dd["h"] = max(dd["h"], h)
            dd["l"] = min(dd["l"], l)
            dd["v"] += v
            dd["bars"] += 1
    rows = [days[k] for k in sorted(days) if days[k]["bars"] >= 30]
    for r in rows:
        for k in ("first", "last", "bars"):
            r.pop(k)
    return rows


# ------------------------------------------------------------ indicators --
def add_ma(rows, periods=(5, 20, 60)):
    closes = [r["c"] for r in rows]
    for p in periods:
        s = 0.0
        for i, r in enumerate(rows):
            s += closes[i]
            if i >= p:
                s -= closes[i - p]
            r[f"ma{p}"] = round(s / p, 2) if i >= p - 1 else None


def slope(rows, i, k=3):
    if i < k or rows[i]["ma20"] is None or rows[i - k]["ma20"] is None:
        return None
    return (rows[i]["ma20"] - rows[i - k]["ma20"]) / rows[i - k]["ma20"]


def detect_signals(rows, dev_hi, dev_lo):
    """葛蘭碧八法則訊號（與網頁既有標註同一套演算法，基準 20 日均線）。"""
    sig, n = [], len(rows)
    for i in range(1, n):
        r, p = rows[i], rows[i - 1]
        ma, pma = r["ma20"], p["ma20"]
        if ma is None or pma is None:
            continue
        sl = slope(rows, i)
        if sl is None:
            continue
        dev = (r["c"] - ma) / ma
        cross_up = p["c"] <= pma and r["c"] > ma
        cross_dn = p["c"] >= pma and r["c"] < ma
        if dev <= dev_lo:
            sig.append((i, "B4", dev)); continue
        if dev >= dev_hi:
            sig.append((i, "S4", dev)); continue
        if cross_up:
            below = 0
            for j in range(i - 1, max(i - 7, 0), -1):
                if rows[j]["c"] < rows[j]["ma20"]:
                    below += 1
                else:
                    break
            if 1 <= below <= 5 and sl > 0.001:
                sig.append((i, "B2", dev))
            elif sl > -0.002:
                sig.append((i, "B1", dev))
        elif cross_dn:
            above = 0
            for j in range(i - 1, max(i - 7, 0), -1):
                if rows[j]["c"] > rows[j]["ma20"]:
                    above += 1
                else:
                    break
            if 1 <= above <= 5 and sl < -0.001:
                sig.append((i, "S2", dev))
            elif sl < 0.002:
                sig.append((i, "S1", dev))
        else:
            if (r["c"] > ma and sl > 0.002 and r["l"] <= ma * 1.005
                    and p["c"] > pma and i + 1 < n and rows[i + 1]["c"] > r["c"]):
                sig.append((i, "B3", dev))
            elif (r["c"] < ma and sl < -0.002 and r["h"] >= ma * 0.995
                    and p["c"] < pma and i + 1 < n and rows[i + 1]["c"] < r["c"]):
                sig.append((i, "S3", dev))
    out, last = [], {}
    for i, t, dev in sig:  # 同型訊號 5 日內只留第一個
        if t in last and i - last[t] < 5:
            last[t] = i
            continue
        last[t] = i
        out.append({"i": i, "d": rows[i]["d"], "t": t, "px": rows[i]["c"],
                    "dev": round(dev * 100, 1)})
    return out


# ------------------------------------------------------------------ html --
def replace_between(html, pattern, replacement):
    new, cnt = re.subn(pattern, replacement, html, count=1, flags=re.S)
    if cnt != 1:
        raise RuntimeError(f"在 granville.html 找不到標記 {pattern!r}")
    return new


def inject(html, sym, win, signals):
    dj = json.dumps(win, ensure_ascii=False, separators=(",", ":"))
    sj = json.dumps(signals, ensure_ascii=False, separators=(",", ":"))
    last = win[-1]
    dev20 = round((last["c"] - last["ma20"]) / last["ma20"] * 100, 1)
    adv = (f'asof:"{last["d"].replace("-", "/")}", close:{last["c"]}, '
           f'ma20:{last["ma20"]}, ma60:{last["ma60"]}, dev20:{dev20}, '
           f'hi52:{max(r["h"] for r in win)}, lo52:{min(r["l"] for r in win)}')
    html = replace_between(html, rf"/\*DATA:{sym}\*/.*?/\*:DATA\*/",
                           f"/*DATA:{sym}*/{dj}/*:DATA*/")
    html = replace_between(html, rf"/\*SIG:{sym}\*/.*?/\*:SIG\*/",
                           f"/*SIG:{sym}*/{sj}/*:SIG*/")
    html = replace_between(html, rf"/\*ADV:{sym}\*/.*?/\*:ADV\*/",
                           f"/*ADV:{sym}*/{adv}/*:ADV*/")
    return html


def update_source_note(html, source_desc, first_d, last_d):
    note = (f"<!--SRC-->股價資料取自 {source_desc}，"
            f"區間 {first_d.replace('-', '/')} – {last_d.replace('-', '/')}，"
            f"為未還原除權息之原始價格；成交量單位為「張」（1 張 = 1,000 股）。"
            f"頁面資料更新於 {datetime.now(TPE).strftime('%Y/%m/%d')}。<!--/SRC-->")
    return replace_between(html, r"<!--SRC-->.*?<!--/SRC-->", note)


# ------------------------------------------------------------------ main --
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=["auto", "yahoo", "archive"], default="auto")
    ap.add_argument("--csv-dir", type=Path, default=None,
                    help="另存各股過去一年日 K 為 CSV 的目錄")
    ap.add_argument("--html", type=Path, default=HTML_PATH)
    args = ap.parse_args()

    today = datetime.now(TPE).date()
    # 多抓約半年，讓一年窗的第一天就有正確的 MA60
    start = (today - timedelta(days=365 + 180)).isoformat()
    html = args.html.read_text(encoding="utf-8")
    source_desc = None

    for sym, cfg in STOCKS.items():
        rows, used = None, None
        if args.source in ("auto", "yahoo"):
            try:
                rows = fetch_yfinance(sym, start)
                patch_today_from_realtime(sym, rows)
                used = "yahoo"
            except Exception as e:
                print(f"  [{sym}] yfinance 失敗：{type(e).__name__}: {str(e)[:120]}")
                if args.source == "yahoo":
                    sys.exit(f"--source yahoo 指定失敗（{sym}），中止。")
        if rows is None:
            print(f"  [{sym}] 退回 tw_stocker 存檔…")
            rows = fetch_archive(sym)
            used = "archive"

        add_ma(rows)
        for r in rows:
            for k in ("o", "h", "l", "c"):
                r[k] = round(r[k], 2)
        cut = (date.fromisoformat(rows[-1]["d"]) - timedelta(days=365)).isoformat()
        win = [r for r in rows if r["d"] >= cut]
        signals = detect_signals(win, cfg["dev_hi"], cfg["dev_lo"])
        html = inject(html, sym, win, signals)
        print(f"  [{sym}] {cfg['name']}: {win[0]['d']} ~ {win[-1]['d']}"
              f"（{len(win)} 個交易日，{len(signals)} 個訊號，來源 {used}）")
        last = win[-1]
        print(f"        收盤 {last['c']}  MA20 {last['ma20']}  MA60 {last['ma60']}"
              f"  52週高 {max(r['h'] for r in win)}  52週低 {min(r['l'] for r in win)}")

        if args.csv_dir:
            args.csv_dir.mkdir(parents=True, exist_ok=True)
            out = args.csv_dir / f"{sym}_daily_1y.csv"
            with out.open("w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=["d", "o", "h", "l", "c", "v",
                                                  "ma5", "ma20", "ma60"])
                w.writeheader()
                w.writerows(win)
            print(f"        CSV → {out}")

        if source_desc is None:
            source_desc = ("Yahoo Finance（yfinance 歷史日 K，必要時以"
                           "tw.stock.yahoo.com 即時報價補當日）" if used == "yahoo"
                           else "Yahoo Finance 實際成交紀錄（透過開源資料庫 "
                           '<a href="https://github.com/voidful/tw_stocker" '
                           'rel="noopener">voidful/tw_stocker</a> 之 5 分 K 存檔'
                           "聚合為日 K）")
        first_d, last_d = win[0]["d"], win[-1]["d"]

    html = update_source_note(html, source_desc, first_d, last_d)
    args.html.write_text(html, encoding="utf-8")
    print(f"已更新 {args.html}")
    print("提醒：K 線與訊號已自動更新；「實戰解讀」註解與 Section 03 的價位階梯／"
          "文字建議為人工教學內容，請依上方新關鍵價位複核調整。")


if __name__ == "__main__":
    main()
