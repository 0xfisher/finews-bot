#!/usr/bin/env python3
"""
财经数据抓取与分析 — JSON 输出版
抓取市场行情、RSS 新闻、恐惧贪婪指数、美债利差，汇总输出 JSON
"""

import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf

# ══════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════

TZ           = ZoneInfo("Asia/Shanghai")
FETCH_HOURS  = 12   # 抓取过去 N 小时内的新闻
NEWS_PER_FEED = 10  # 每个 RSS 源最多取条数

TICKERS = {
    "QQQ":    {"name": "纳指ETF",      "ticker": "QQQ"},
    "NVDA":   {"name": "英伟达",        "ticker": "NVDA"},
    "AAPL":   {"name": "苹果",          "ticker": "AAPL"},
    "MSFT":   {"name": "微软",          "ticker": "MSFT"},
    "TSLA":   {"name": "特斯拉",        "ticker": "TSLA"},
    "GOOGL":  {"name": "谷歌",          "ticker": "GOOGL"},
    "PLTR":   {"name": "Palantir",     "ticker": "PLTR"},
    "SOXL":   {"name": "3x芯片ETF",    "ticker": "SOXL"},
    "YINN":   {"name": "3xA50ETF",     "ticker": "YINN"},
    "KSTR":   {"name": "科创50ETF",    "ticker": "KSTR"},
    "IAUM":   {"name": "黄金ETF",      "ticker": "IAUM"},
    "07709":  {"name": "2xHynix",      "ticker": "7709.HK"},
    "CLMAIN": {"name": "原油",          "ticker": "CL=F"},
    "BTC":    {"name": "比特币",        "ticker": "BTC-USD"},
    "ETH":    {"name": "以太坊",        "ticker": "ETH-USD"},
    "USDCNH": {"name": "美元/人民币",   "ticker": "USDCNH=X"},
    "USDMYR": {"name": "美元/马币",     "ticker": "USDMYR=X"},
}

RSS_FEEDS = {
    # 美股科技
    "Yahoo/NVDA":   "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
    "Yahoo/AAPL":   "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US",
    "Yahoo/MSFT":   "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MSFT&region=US&lang=en-US",
    "Yahoo/TSLA":   "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSLA&region=US&lang=en-US",
    "Yahoo/GOOGL":  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GOOGL&region=US&lang=en-US",
    "Yahoo/PLTR":   "https://feeds.finance.yahoo.com/rss/2.0/headline?s=PLTR&region=US&lang=en-US",
    # 宏观财经
    "Bloomberg Markets": "https://feeds.bloomberg.com/markets/news.rss",
    "FT Markets":        "https://www.ft.com/markets?format=rss",
    "CNBC Economy":      "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "MarketWatch":       "https://feeds.marketwatch.com/marketwatch/topstories/",
    # 中国/港股
    "SCMP China Economy": "https://www.scmp.com/rss/4/feed",
    "FT China":           "https://www.ft.com/world/asia-pacific/china?format=rss",
    # 外汇
    "ForexLive": "https://www.forexlive.com/feed/news",
    "FXStreet":  "https://www.fxstreet.com/rss",
    # 大宗商品
    "Kitco Gold": "https://www.kitco.com/rss/news.rss",
    "OilPrice":   "https://oilprice.com/rss/main",
    # 加密货币
    "CoinTelegraph":  "https://cointelegraph.com/rss",
    "Decrypt":        "https://decrypt.co/feed",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/feed",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
# 数据抓取函数
# ══════════════════════════════════════════════

def fetch_market_data() -> dict:
    """抓取每个标的的行情与52周位置"""
    log.info("📈 抓取行情数据...")
    result = {}
    for symbol, info in TICKERS.items():
        try:
            tk        = yf.Ticker(info["ticker"])
            hist      = tk.history(period="1y")
            fast_info = tk.fast_info
            if hist.empty:
                log.warning(f"  ⚠️ {symbol} 无历史数据，跳过")
                continue

            current  = round(float(fast_info.last_price), 6)
            high_52w = round(float(hist["Close"].max()), 6)
            low_52w  = round(float(hist["Close"].min()), 6)
            prev_close = round(float(hist["Close"].iloc[-2]), 6) if len(hist) >= 2 else current
            change_pct = round((current - prev_close) / prev_close * 100, 2) if prev_close else 0

            position_pct = (
                int((current - low_52w) / (high_52w - low_52w) * 100)
                if high_52w != low_52w else 50
            )
            if position_pct <= 30:
                position_label = "低位"
                attractiveness = min(5, 5 - position_pct // 10)
            elif position_pct <= 70:
                position_label = "中位"
                attractiveness = 3
            else:
                position_label = "高位"
                attractiveness = max(1, 5 - (position_pct - 70) // 10)

            # 财报日期
            try:
                cal      = tk.calendar
                earnings = cal.get("Earnings Date", [None])[0] if isinstance(cal, dict) else None
                from datetime import date as _date
                if earnings and hasattr(earnings, "strftime"):
                    days_to      = (earnings.date() - datetime.now(tz=TZ).date()).days
                    earnings_str = earnings.strftime("%Y-%m-%d")
                    earnings_days = days_to
                else:
                    earnings_str  = None
                    earnings_days = None
            except Exception:
                earnings_str  = None
                earnings_days = None

            result[symbol] = {
                "name":           info["name"],
                "ticker":         info["ticker"],
                "price":          current,
                "prev_close":     prev_close,
                "change_pct":     change_pct,
                "high_52w":       high_52w,
                "low_52w":        low_52w,
                "position_pct":   position_pct,
                "position_label": position_label,
                "attractiveness": attractiveness,
                "earnings_date":  earnings_str,
                "earnings_days":  earnings_days,
            }
            log.info(f"  ✅ {symbol}: {current} ({change_pct:+.2f}%) | {position_label} {position_pct}%")
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 失败: {e}")
            result[symbol] = {"name": info["name"], "ticker": info["ticker"], "error": str(e)}

    return result


def fetch_news() -> list[dict]:
    """从 RSS 源抓取最近 FETCH_HOURS 小时内的新闻"""
    log.info("📡 抓取 RSS 新闻...")
    cutoff    = datetime.now(tz=timezone.utc) - timedelta(hours=FETCH_HOURS)
    articles  = []
    seen_urls : set = set()

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})
            count = 0
            for entry in feed.entries:
                if count >= NEWS_PER_FEED:
                    break
                title = entry.get("title", "").strip()
                url   = entry.get("link", "").strip()
                if not title or not url or url in seen_urls:
                    continue

                pub = None
                for attr in ("published_parsed", "updated_parsed"):
                    if hasattr(entry, attr) and getattr(entry, attr):
                        try:
                            pub = datetime(*getattr(entry, attr)[:6], tzinfo=timezone.utc)
                            break
                        except Exception:
                            pass
                if pub and pub < cutoff:
                    continue

                seen_urls.add(url)
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:400].strip()
                articles.append({
                    "source":     source_name,
                    "title":      title,
                    "url":        url,
                    "summary":    summary,
                    "published":  pub.astimezone(TZ).strftime("%Y-%m-%d %H:%M") if pub else None,
                })
                count += 1
        except Exception as e:
            log.warning(f"  ⚠️ {source_name} 失败: {e}")

    log.info(f"  ✅ 共抓取 {len(articles)} 条新闻")
    return articles


def fetch_fear_greed() -> dict:
    """加密市场恐惧贪婪指数"""
    try:
        resp  = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        item  = resp.json()["data"][0]
        score = int(item["value"])
        label = item["value_classification"]
        label_zh = {
            "Extreme Fear": "极度恐惧", "Fear": "恐惧",
            "Neutral": "中性", "Greed": "贪婪", "Extreme Greed": "极度贪婪",
        }.get(label, label)
        return {"score": score, "label_en": label, "label_zh": label_zh}
    except Exception as e:
        log.warning(f"  ⚠️ 恐惧贪婪指数失败: {e}")
        return {"error": str(e)}


def fetch_bond_yields() -> dict:
    """美债10年期 & 短端收益率，计算利差"""
    try:
        t10    = round(float(yf.Ticker("^TNX").fast_info.last_price), 3)
        t2_raw = yf.Ticker("^IRX").fast_info.last_price   # 13周，值 ~= 百分比*10
        t2     = round(float(t2_raw) / 10, 3)
        spread = round(t10 - t2, 3)

        if spread > 0.5:
            signal = "正常"
            interpretation = "经济预期乐观"
        elif spread > 0:
            signal = "收窄"
            interpretation = "注意风险"
        else:
            signal = "倒挂"
            interpretation = "衰退信号"

        return {
            "t10y":          t10,
            "t2y_approx":    t2,
            "spread":        spread,
            "signal":        signal,
            "interpretation": interpretation,
        }
    except Exception as e:
        log.warning(f"  ⚠️ 美债利差失败: {e}")
        return {"error": str(e)}


def fetch_put_call_ratios() -> dict:
    """各标的期权 Put/Call 成交量比率"""
    log.info("📊 抓取期权 P/C 比率...")
    result = {}
    symbols = ["QQQ", "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "PLTR", "SOXL"]
    for symbol in symbols:
        try:
            tk   = yf.Ticker(symbol)
            exps = tk.options
            if not exps:
                continue
            chain    = tk.option_chain(exps[0])
            put_vol  = int(chain.puts["volume"].sum())
            call_vol = int(chain.calls["volume"].sum())
            ratio    = round(put_vol / call_vol, 3) if call_vol > 0 else None

            if ratio is None:
                sentiment = "无数据"
            elif ratio > 1.2:
                sentiment = "偏悲观"
            elif ratio > 0.8:
                sentiment = "中性"
            else:
                sentiment = "偏乐观"

            result[symbol] = {
                "expiry":    exps[0],
                "put_vol":   put_vol,
                "call_vol":  call_vol,
                "ratio":     ratio,
                "sentiment": sentiment,
            }
            log.info(f"  ✅ {symbol}: P/C={ratio} ({sentiment})")
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} P/C 失败: {e}")
            result[symbol] = {"error": str(e)}
    return result


# ══════════════════════════════════════════════
# 简单规则分析（不依赖 AI）
# ══════════════════════════════════════════════

def analyze_ticker(info: dict, pc_data: dict) -> dict:
    """基于规则对单个标的给出初步判断"""
    if "error" in info:
        return {"signal": "无数据", "reasons": [info["error"]]}

    reasons = []
    score   = 0  # 正数偏多，负数偏空

    pct = info.get("position_pct", 50)
    if pct <= 20:
        score += 2
        reasons.append(f"52周低位（{pct}%），性价比高")
    elif pct >= 80:
        score -= 1
        reasons.append(f"52周高位（{pct}%），注意回调风险")

    chg = info.get("change_pct", 0)
    if chg <= -5:
        score -= 2
        reasons.append(f"今日下跌 {chg:.1f}%，短线偏弱")
    elif chg >= 5:
        score += 1
        reasons.append(f"今日上涨 {chg:.1f}%，短线动能强")

    symbol = next((k for k, v in TICKERS.items() if v["name"] == info.get("name")), None)
    if symbol and symbol in pc_data and "ratio" in pc_data[symbol]:
        ratio = pc_data[symbol]["ratio"]
        if ratio and ratio > 1.2:
            score -= 1
            reasons.append(f"P/C比率 {ratio:.2f}，市场偏悲观")
        elif ratio and ratio < 0.8:
            score += 1
            reasons.append(f"P/C比率 {ratio:.2f}，市场偏乐观")

    if score >= 2:
        signal = "偏多"
    elif score <= -2:
        signal = "偏空"
    else:
        signal = "中性"

    return {"signal": signal, "score": score, "reasons": reasons}


def build_analysis(market_data: dict, pc_data: dict, fear_greed: dict, bond: dict) -> dict:
    """汇总分析结论"""
    ticker_analysis = {}
    for symbol, info in market_data.items():
        if "error" not in info:
            ticker_analysis[symbol] = analyze_ticker(info, pc_data)

    # 整体市场情绪评分
    fg_score  = fear_greed.get("score", 50)
    spread    = bond.get("spread", 0)
    signals   = [v["signal"] for v in ticker_analysis.values()]
    bull_cnt  = signals.count("偏多")
    bear_cnt  = signals.count("偏空")

    if fg_score <= 25 and spread > 0:
        market_mood = "恐慌后反弹窗口"
    elif fg_score >= 75:
        market_mood = "贪婪过热，注意风险"
    elif bear_cnt > bull_cnt * 2:
        market_mood = "整体偏弱"
    elif bull_cnt > bear_cnt * 2:
        market_mood = "整体偏强"
    else:
        market_mood = "震荡分化"

    top_opportunities = [
        symbol for symbol, a in ticker_analysis.items()
        if a["signal"] == "偏多"
    ]
    top_risks = [
        symbol for symbol, a in ticker_analysis.items()
        if a["signal"] == "偏空"
    ]

    return {
        "market_mood":       market_mood,
        "bull_count":        bull_cnt,
        "bear_count":        bear_cnt,
        "top_opportunities": top_opportunities,
        "top_risks":         top_risks,
        "tickers":           ticker_analysis,
    }


# ══════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════

def scrape_and_analyze() -> dict:
    now_str = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info(f"🚀 开始抓取财经数据  {now_str}")

    market_data = fetch_market_data()
    news        = fetch_news()
    fear_greed  = fetch_fear_greed()
    bond        = fetch_bond_yields()
    pc_ratios   = fetch_put_call_ratios()
    analysis    = build_analysis(market_data, pc_ratios, fear_greed, bond)

    output = {
        "meta": {
            "generated_at": now_str,
            "timezone":     "Asia/Shanghai",
            "fetch_hours":  FETCH_HOURS,
            "news_count":   len(news),
        },
        "market_data":      market_data,
        "news":             news,
        "fear_greed_index": fear_greed,
        "bond_yields":      bond,
        "put_call_ratios":  pc_ratios,
        "analysis":         analysis,
    }

    log.info("✅ 数据抓取与分析完成")
    return output


if __name__ == "__main__":
    data = scrape_and_analyze()
    print(json.dumps(data, ensure_ascii=False, indent=2))
