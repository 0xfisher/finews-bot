#!/usr/bin/env python3
"""
财经数据抓取 — 看板 JSON 输出版
输出格式供 Claude Code 读取后做深度分析。
不依赖任何外部 AI API。
"""

import email.utils
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

# ══════════════════════════════════════════════
# ❶ 配置
# ══════════════════════════════════════════════

TZ            = ZoneInfo("Asia/Shanghai")
FETCH_HOURS   = 12
NEWS_PER_FEED = 10

TICKERS = {
    "QQQ":    {"name": "纳指ETF",    "ticker": "QQQ"},
    "NVDA":   {"name": "英伟达",      "ticker": "NVDA"},
    "AAPL":   {"name": "苹果",        "ticker": "AAPL"},
    "MSFT":   {"name": "微软",        "ticker": "MSFT"},
    "TSLA":   {"name": "特斯拉",      "ticker": "TSLA"},
    "GOOGL":  {"name": "谷歌",        "ticker": "GOOGL"},
    "PLTR":   {"name": "Palantir",   "ticker": "PLTR"},
    "SOXL":   {"name": "3x芯片ETF",  "ticker": "SOXL"},
    "YINN":   {"name": "3xA50ETF",   "ticker": "YINN"},
    "07709":  {"name": "2xHynix",    "ticker": "7709.HK"},
    "KSTR":   {"name": "科创50ETF",  "ticker": "KSTR"},
    "IAUM":   {"name": "黄金ETF",    "ticker": "IAUM"},
    "CLMAIN": {"name": "原油",        "ticker": "CL=F"},
    "BTC":    {"name": "比特币",      "ticker": "BTC-USD"},
    "ETH":    {"name": "以太坊",      "ticker": "ETH-USD"},
    "USDCNH": {"name": "美元/人民币", "ticker": "USDCNH=X"},
    "USDMYR": {"name": "美元/马币",   "ticker": "USDMYR=X"},
}

OPTIONS_SYMBOLS = ["QQQ", "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "PLTR", "SOXL"]

MACRO_CALENDAR = [
    {"date": "2026-03-18", "event": "美联储利率决议 + 新闻发布会", "importance": "极重要"},
    {"date": "2026-03-19", "event": "日本央行利率决议",             "importance": "重要"},
    {"date": "2026-03-20", "event": "美国初请失业金人数",           "importance": "重要"},
    {"date": "2026-03-28", "event": "美国PCE通胀数据",              "importance": "极重要"},
    {"date": "2026-03-31", "event": "中国官方PMI（制造业+服务业）", "importance": "重要"},
    {"date": "2026-04-02", "event": "美国非农就业数据",             "importance": "极重要"},
    {"date": "2026-04-10", "event": "美国CPI通胀数据",              "importance": "极重要"},
]

RSS_FEEDS = {
    "Yahoo/NVDA":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
    "Yahoo/AAPL":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US",
    "Yahoo/MSFT":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MSFT&region=US&lang=en-US",
    "Yahoo/TSLA":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSLA&region=US&lang=en-US",
    "Yahoo/GOOGL":        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GOOGL&region=US&lang=en-US",
    "Yahoo/PLTR":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=PLTR&region=US&lang=en-US",
    "Bloomberg Markets":  "https://feeds.bloomberg.com/markets/news.rss",
    "FT Markets":         "https://www.ft.com/markets?format=rss",
    "CNBC Economy":       "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "MarketWatch":        "https://feeds.marketwatch.com/marketwatch/topstories/",
    "SCMP China Economy": "https://www.scmp.com/rss/4/feed",
    "FT China":           "https://www.ft.com/world/asia-pacific/china?format=rss",
    "ForexLive":          "https://www.forexlive.com/feed/news",
    "FXStreet":           "https://www.fxstreet.com/rss",
    "Kitco Gold":         "https://www.kitco.com/rss/news.rss",
    "OilPrice":           "https://oilprice.com/rss/main",
    "CoinTelegraph":      "https://cointelegraph.com/rss",
    "Decrypt":            "https://decrypt.co/feed",
    "Bitcoin Magazine":   "https://bitcoinmagazine.com/feed",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
# ❷ 辅助函数
# ══════════════════════════════════════════════

def _fmt_price(price: float) -> str:
    if price >= 1000: return f"${price:,.0f}"
    if price >= 10:   return f"${price:.2f}"
    if price >= 1:    return f"${price:.3f}"
    return f"${price:.4f}"


def _stars(pos_pct: int) -> int:
    if pos_pct <= 20: return 5
    if pos_pct <= 35: return 4
    if pos_pct <= 55: return 3
    if pos_pct <= 75: return 2
    return 1


def _signal(pos_pct: int) -> str:
    if pos_pct > 70:  return "偏空"
    if pos_pct < 30:  return "偏多"
    return "中性"


def _hot_contract(chain, expiry: str) -> str:
    try:
        label     = datetime.strptime(expiry, "%Y-%m-%d").strftime("%m%d")
        best_call = chain.calls.loc[chain.calls["volume"].idxmax()]
        best_put  = chain.puts.loc[chain.puts["volume"].idxmax()]
        if best_call["volume"] >= best_put["volume"]:
            return f"{label}.C.{int(best_call['strike'])}"
        return f"{label}.P.{int(best_put['strike'])}"
    except Exception:
        return ""

# ══════════════════════════════════════════════
# ❸ 数据抓取
# ══════════════════════════════════════════════

def fetch_market_data() -> dict:
    log.info("📈 抓取行情数据...")
    result = {}
    for symbol, info in TICKERS.items():
        try:
            tk        = yf.Ticker(info["ticker"])
            hist      = tk.history(period="1y")
            fast_info = tk.fast_info
            if hist.empty:
                log.warning(f"  ⚠️ {symbol} 无历史数据")
                continue
            current    = float(fast_info.last_price)
            high_52w   = float(hist["Close"].max())
            low_52w    = float(hist["Close"].min())
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else current
            change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close else 0
            pos_pct    = int((current - low_52w) / (high_52w - low_52w) * 100) if high_52w != low_52w else 50
            result[symbol] = {
                "name":       info["name"],
                "price":      _fmt_price(current),
                "pos_pct":    pos_pct,
                "stars":      _stars(pos_pct),
                "signal":     _signal(pos_pct),
                "change_pct": change_pct,
                "l_long":     "",
                "l_short":    "",
            }
            log.info(f"  ✅ {symbol}: {_fmt_price(current)} ({change_pct:+.2f}%) pos={pos_pct}% → {_signal(pos_pct)}")
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 失败: {e}")
    return result


def _parse_rss_date(date_str: str):
    if not date_str:
        return None
    try:
        return email.utils.parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(date_str[:19], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def fetch_news() -> list[dict]:
    log.info("📡 抓取 RSS 新闻...")
    cutoff    = datetime.now(tz=timezone.utc) - timedelta(hours=FETCH_HOURS)
    articles  = []
    seen_urls: set = set()
    headers   = {"User-Agent": "Mozilla/5.0"}

    CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

    def _text(el):
        return (el.text or "").strip() if el is not None else ""

    def _find_text(item, *tags):
        for tag in tags:
            el = item.find(tag)
            if el is not None and el.text:
                return el.text.strip()
        return ""

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            resp = requests.get(feed_url, headers=headers, timeout=10)
            resp.raise_for_status()
            root     = ET.fromstring(resp.content)
            is_atom  = root.tag.startswith("{http://www.w3.org/2005/Atom}")
            ATOM     = "http://www.w3.org/2005/Atom"

            items = (
                root.findall(f"{{{ATOM}}}entry")
                if is_atom
                else (root.find("channel") or root).findall("item")
            )

            count = 0
            for item in items:
                if count >= NEWS_PER_FEED:
                    break
                if is_atom:
                    title    = _text(item.find(f"{{{ATOM}}}title"))
                    link_el  = item.find(f"{{{ATOM}}}link")
                    url      = (link_el.get("href") or "").strip() if link_el is not None else ""
                    date_str = _text(item.find(f"{{{ATOM}}}updated")) or _text(item.find(f"{{{ATOM}}}published"))
                    summary  = _text(item.find(f"{{{ATOM}}}summary")) or _text(item.find(f"{{{ATOM}}}content"))
                else:
                    title    = _find_text(item, "title")
                    url      = _find_text(item, "link")
                    date_str = _find_text(item, "pubDate", "updated")
                    summary  = _find_text(item, "description", "summary", f"{{{CONTENT_NS}}}encoded")

                title = title.strip()
                url   = url.strip()
                if not title or not url or url in seen_urls:
                    continue
                pub = _parse_rss_date(date_str)
                if pub and pub < cutoff:
                    continue
                seen_urls.add(url)
                articles.append({
                    "source":    source_name,
                    "title":     title,
                    "url":       url,
                    "summary":   re.sub(r"<[^>]+>", "", summary)[:400].strip(),
                    "published": pub.astimezone(TZ).strftime("%Y-%m-%d %H:%M") if pub else None,
                })
                count += 1
        except Exception as e:
            log.warning(f"  ⚠️ {source_name} 失败: {e}")

    log.info(f"  ✅ 共抓取 {len(articles)} 条新闻")
    return articles


def fetch_fear_greed() -> dict:
    try:
        item  = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8).json()["data"][0]
        label = {
            "Extreme Fear": "极度恐惧", "Fear": "恐惧",
            "Neutral": "中性", "Greed": "贪婪", "Extreme Greed": "极度贪婪",
        }.get(item["value_classification"], item["value_classification"])
        return {"score": int(item["value"]), "label": label}
    except Exception as e:
        log.warning(f"  ⚠️ 恐惧贪婪指数失败: {e}")
        return {"score": 50, "label": "中性"}


def fetch_bond() -> dict:
    try:
        t10    = round(float(yf.Ticker("^TNX").fast_info.last_price), 3)
        t2_raw = float(yf.Ticker("^IRX").fast_info.last_price)
        return {"t10": t10, "spread": round(t10 - t2_raw / 10, 3)}
    except Exception as e:
        log.warning(f"  ⚠️ 美债利差失败: {e}")
        return {"t10": 0.0, "spread": 0.0}


def fetch_macro_events() -> list[dict]:
    today    = datetime.now(tz=TZ).date()
    end_date = today + timedelta(days=14)
    return [
        {
            "date":       datetime.strptime(item["date"], "%Y-%m-%d").date().strftime("%m/%d"),
            "event":      item["event"],
            "importance": item["importance"],
            "days_to":    (datetime.strptime(item["date"], "%Y-%m-%d").date() - today).days,
        }
        for item in MACRO_CALENDAR
        if today <= datetime.strptime(item["date"], "%Y-%m-%d").date() <= end_date
    ]


def fetch_options() -> dict:
    log.info("📊 抓取期权数据...")
    result = {}
    for symbol in OPTIONS_SYMBOLS:
        try:
            tk   = yf.Ticker(symbol)
            exps = tk.options
            if not exps:
                continue
            chain    = tk.option_chain(exps[0])
            put_vol  = int(chain.puts["volume"].sum())
            call_vol = int(chain.calls["volume"].sum())
            result[symbol] = {
                "ratio": round(put_vol / call_vol, 2) if call_vol > 0 else None,
                "hot":   _hot_contract(chain, exps[0]),
            }
            log.info(f"  ✅ {symbol}: P/C={result[symbol]['ratio']}  hot={result[symbol]['hot']}")
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 期权失败: {e}")
            result[symbol] = {"ratio": None, "hot": ""}
    return result

# ══════════════════════════════════════════════
# ❹ 主入口
# ══════════════════════════════════════════════

def build_dashboard_json() -> dict:
    now_str = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info(f"🚀 开始抓取  {now_str}")

    market_data  = fetch_market_data()
    news         = fetch_news()
    fear_greed   = fetch_fear_greed()
    bond         = fetch_bond()
    macro_events = fetch_macro_events()
    options      = fetch_options()

    log.info("✅ 抓取完成")
    return {
        "updated":      now_str,
        "fear_greed":   fear_greed,
        "bond":         bond,
        "macro_events": macro_events,
        "tickers":      market_data,
        "options":      options,
        "news":         news,
        "layers":       {"l1": "", "l2": "", "l3": "", "l4": "", "l5": ""},
    }


if __name__ == "__main__":
    print(json.dumps(build_dashboard_json(), ensure_ascii=False, indent=2))
