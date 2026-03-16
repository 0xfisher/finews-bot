#!/usr/bin/env python3
"""
财经数据抓取与分析 — 看板 JSON 输出版
严格输出看板渲染所需格式：
{
  "updated", "fear_greed", "bond", "macro_events",
  "tickers"(含l_long/l_short/signal), "options"(含hot合约),
  "layers"(l1-l5完整分析)
}
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import email.utils
import xml.etree.ElementTree as ET
import requests
import yfinance as yf

# ══════════════════════════════════════════════
# ❶ 配置
# ══════════════════════════════════════════════

TZ            = ZoneInfo("Asia/Shanghai")
FETCH_HOURS   = 12
NEWS_PER_FEED = 10
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

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

# 期权 P/C 只抓这几个有流动性的
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
    if price >= 1000:  return f"${price:,.0f}"
    if price >= 10:    return f"${price:.2f}"
    if price >= 1:     return f"${price:.3f}"
    return f"${price:.4f}"


def _stars(position_pct: int) -> int:
    if position_pct <= 20: return 5
    if position_pct <= 35: return 4
    if position_pct <= 55: return 3
    if position_pct <= 75: return 2
    return 1


def _hot_contract(chain, expiry: str) -> str:
    """从期权链里找成交量最大的合约，格式 MMDD.C.STRIKE 或 MMDD.P.STRIKE"""
    try:
        exp_label = datetime.strptime(expiry, "%Y-%m-%d").strftime("%m%d")
        best_call = chain.calls.loc[chain.calls["volume"].idxmax()]
        best_put  = chain.puts.loc[chain.puts["volume"].idxmax()]
        if best_call["volume"] >= best_put["volume"]:
            return f"{exp_label}.C.{int(best_call['strike'])}"
        else:
            return f"{exp_label}.P.{int(best_put['strike'])}"
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
                "_raw_price": current,
                "_change_pct": change_pct,
                "_pos_pct": pos_pct,
                # dashboard fields (partial — AI will fill signal/l_long/l_short)
                "name":    info["name"],
                "price":   _fmt_price(current),
                "pos_pct": pos_pct,
                "stars":   _stars(pos_pct),
            }
            log.info(f"  ✅ {symbol}: {_fmt_price(current)} ({change_pct:+.2f}%) pos={pos_pct}%")
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 失败: {e}")
    return result


def _parse_rss_date(date_str: str):
    """解析 RFC 2822 或 ISO 8601 日期字符串，返回 UTC datetime 或 None"""
    if not date_str:
        return None
    # RFC 2822 (Mon, 16 Mar 2026 07:00:00 +0000)
    try:
        t = email.utils.parsedate_to_datetime(date_str)
        return t.astimezone(timezone.utc)
    except Exception:
        pass
    # ISO 8601 subset
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

    # RSS/Atom 命名空间
    NS = {
        "atom":    "http://www.w3.org/2005/Atom",
        "content": "http://purl.org/rss/1.0/modules/content/",
        "media":   "http://search.yahoo.com/mrss/",
    }

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
            root = ET.fromstring(resp.content)

            # 判断是 RSS 还是 Atom
            is_atom = "atom" in root.tag.lower() or root.tag.startswith("{http://www.w3.org/2005/Atom}")
            if is_atom:
                items = root.findall("{http://www.w3.org/2005/Atom}entry")
            else:
                channel = root.find("channel")
                items   = (channel or root).findall("item")

            count = 0
            for item in items:
                if count >= NEWS_PER_FEED:
                    break

                if is_atom:
                    title = _text(item.find("{http://www.w3.org/2005/Atom}title"))
                    link_el = item.find("{http://www.w3.org/2005/Atom}link")
                    url = (link_el.get("href") or "").strip() if link_el is not None else ""
                    date_str = _text(item.find("{http://www.w3.org/2005/Atom}updated")) or \
                               _text(item.find("{http://www.w3.org/2005/Atom}published"))
                    summary = _text(item.find("{http://www.w3.org/2005/Atom}summary")) or \
                              _text(item.find("{http://www.w3.org/2005/Atom}content"))
                else:
                    title    = _find_text(item, "title")
                    url      = _find_text(item, "link")
                    date_str = _find_text(item, "pubDate", "dc:date", "updated")
                    summary  = _find_text(item, "description", "summary",
                                          f"{{{NS['content']}}}encoded")

                title = title.strip()
                url   = url.strip()
                if not title or not url or url in seen_urls:
                    continue

                pub = _parse_rss_date(date_str)
                if pub and pub < cutoff:
                    continue

                seen_urls.add(url)
                summary_clean = re.sub(r"<[^>]+>", "", summary)[:400].strip()
                articles.append({
                    "source":    source_name,
                    "title":     title,
                    "url":       url,
                    "summary":   summary_clean,
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
        score = int(item["value"])
        label = {
            "Extreme Fear": "极度恐惧", "Fear": "恐惧",
            "Neutral": "中性", "Greed": "贪婪", "Extreme Greed": "极度贪婪",
        }.get(item["value_classification"], item["value_classification"])
        return {"score": score, "label": label}
    except Exception as e:
        log.warning(f"  ⚠️ 恐惧贪婪指数失败: {e}")
        return {"score": 50, "label": "中性"}


def fetch_bond() -> dict:
    try:
        t10    = round(float(yf.Ticker("^TNX").fast_info.last_price), 3)
        t2_raw = float(yf.Ticker("^IRX").fast_info.last_price)
        spread = round(t10 - t2_raw / 10, 3)
        return {"t10": t10, "spread": spread}
    except Exception as e:
        log.warning(f"  ⚠️ 美债利差失败: {e}")
        return {"t10": 0.0, "spread": 0.0}


def fetch_macro_events() -> list[dict]:
    today    = datetime.now(tz=TZ).date()
    end_date = today + timedelta(days=14)
    events   = []
    for item in MACRO_CALENDAR:
        d = datetime.strptime(item["date"], "%Y-%m-%d").date()
        if today <= d <= end_date:
            events.append({
                "date":       d.strftime("%m/%d"),
                "event":      item["event"],
                "importance": item["importance"],
                "days_to":    (d - today).days,
            })
    return events


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
            ratio    = round(put_vol / call_vol, 2) if call_vol > 0 else None
            hot      = _hot_contract(chain, exps[0])
            result[symbol] = {"ratio": ratio, "hot": hot}
            log.info(f"  ✅ {symbol}: P/C={ratio}  hot={hot}")
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 期权失败: {e}")
            result[symbol] = {"ratio": None, "hot": ""}
    return result

# ══════════════════════════════════════════════
# ❹ Gemini AI 分析（两阶段）
# ══════════════════════════════════════════════

SYSTEM_INSTRUCTION = (
    "你是专属投资顾问，风格如Ray Dalio与对冲基金交易员。"
    "说话像朋友，直接说人话，信息密度高，每句有用。"
    "禁止输出Markdown加粗/分隔线，纯文字。"
    "按要求严格输出JSON，不加任何额外说明。"
)


def _flash_summarize(articles: list[dict]) -> str:
    if not articles or not GEMINI_API_KEY:
        return "\n".join(f"[{a['source']}] {a['title']}" for a in articles)

    log.info(f"⚡ Flash 浓缩 {len(articles)} 条新闻...")
    time.sleep(3)
    news_block = "".join(
        f"{i}. [{a['source']}] {a['title']}\n   {a['summary']}\n"
        for i, a in enumerate(articles, 1)
    )
    prompt = (
        "以下财经新闻，提炼最有投资价值的内容。\n"
        "要求：过滤无关内容，每条1-2句概括，保留关键数字，按板块分组（宏观/科技/中国港股/大宗商品/加密货币）。\n\n"
        f"{news_block}"
    )
    try:
        from google import genai
        from google.genai import types
        client   = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=3000),
        )
        return response.text.strip()
    except Exception as e:
        log.warning(f"  ⚠️ Flash 失败，使用原始标题: {e}")
        return "\n".join(f"[{a['source']}] {a['title']}" for a in articles)


def _call_gemini_pro(prompt: str) -> str:
    if not GEMINI_API_KEY:
        return ""
    log.info("🧠 Gemini Pro 深度分析...")
    time.sleep(10)
    for attempt in range(3):
        try:
            from google import genai
            from google.genai import types
            client   = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-2.5-pro-preview-03-25",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.1,
                    max_output_tokens=8192,
                ),
            )
            return response.text.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                wait = 60 * (attempt + 2)
                log.warning(f"  ⚠️ 频率限制，等待 {wait}s（第{attempt+1}次）")
                time.sleep(wait)
            else:
                log.error(f"  ❌ Gemini 错误: {e}")
                return ""
    return ""


def _build_ai_prompt(news_summary: str, market_data: dict) -> str:
    now_str = datetime.now(tz=TZ).strftime("%Y年%m月%d日 %H:%M")
    price_block = "\n".join(
        f"{sym}({d['name']}): {d['price']} | 52周位置{d['pos_pct']}%"
        for sym, d in market_data.items()
    )
    ticker_list = " / ".join(TICKERS.keys())

    return f"""当前时间：{now_str}（北京时间）

【价格快照】
{price_block}

【今日财经新闻精华】
{news_summary}

---
请严格按以下 JSON 格式输出，不加任何额外文字，不加 markdown 代码块：

{{
  "tickers": {{
    "QQQ":    {{"signal": "偏多|中性|偏空", "l_long": "长线一句话", "l_short": "短线一句话"}},
    "NVDA":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "AAPL":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "MSFT":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "TSLA":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "GOOGL":  {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "PLTR":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "SOXL":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "YINN":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "07709":  {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "KSTR":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "IAUM":   {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "CLMAIN": {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "BTC":    {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "ETH":    {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "USDCNH": {{"signal": "...", "l_long": "...", "l_short": "..."}},
    "USDMYR": {{"signal": "...", "l_long": "...", "l_short": "..."}}
  }},
  "layers": {{
    "l1": "第一层：今天市场在讲什么故事（2-3句，让外行也听得懂）",
    "l2": "第二层：各标的整体表现总结（一段话综合多空格局）",
    "l3": "第三层：跨资产联动（今天最明显的传导链）",
    "l4": "第四层：今天最该盯住的一件事（给具体指标/时间/价位）",
    "l5": "第五层：关注之外的机会和风险（没有就写无）"
  }}
}}"""


def _parse_ai_response(raw: str) -> dict:
    """提取 JSON，兼容 Gemini 偶尔返回 ```json ... ``` 包裹的情况"""
    if not raw:
        return {}
    # 去掉 markdown 代码块
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试只取第一个 { ... } 块
        m = re.search(r"\{[\s\S]+\}", cleaned)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    log.warning("  ⚠️ AI 响应 JSON 解析失败，使用规则兜底")
    return {}


def _rule_based_signals(market_data: dict, options: dict) -> dict:
    """无 AI 时的规则兜底：signal + 占位 l_long/l_short"""
    signals = {}
    for symbol, d in market_data.items():
        score = 0
        if d["pos_pct"] <= 20:
            score += 2
        elif d["pos_pct"] >= 80:
            score -= 1
        chg = d.get("_change_pct", 0)
        if chg <= -5:
            score -= 2
        elif chg >= 5:
            score += 1
        ratio = options.get(symbol, {}).get("ratio")
        if ratio and ratio > 1.2:
            score -= 1
        elif ratio and ratio < 0.8:
            score += 1
        signal = "偏多" if score >= 2 else "偏空" if score <= -2 else "中性"
        signals[symbol] = {
            "signal":  signal,
            "l_long":  "暂无 AI 分析（未配置 GEMINI_API_KEY）",
            "l_short": "暂无 AI 分析（未配置 GEMINI_API_KEY）",
        }
    return signals

# ══════════════════════════════════════════════
# ❺ 组装最终输出
# ══════════════════════════════════════════════

def build_dashboard_json() -> dict:
    now_str = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info(f"🚀 开始抓取  {now_str}")

    # —— 并行友好：先拉所有数据 ——
    market_data  = fetch_market_data()
    news         = fetch_news()
    fear_greed   = fetch_fear_greed()
    bond         = fetch_bond()
    macro_events = fetch_macro_events()
    options      = fetch_options()

    # —— AI 分析 ——
    if GEMINI_API_KEY:
        news_summary = _flash_summarize(news)
        ai_raw       = _call_gemini_pro(_build_ai_prompt(news_summary, market_data))
        ai_data      = _parse_ai_response(ai_raw)
    else:
        log.warning("⚠️ 未设置 GEMINI_API_KEY，使用规则兜底")
        ai_data = {}

    ai_tickers = ai_data.get("tickers", {})
    ai_layers  = ai_data.get("layers", {})

    # 无 AI 时规则兜底
    if not ai_tickers:
        ai_tickers = _rule_based_signals(market_data, options)

    # —— 组装 tickers ——
    tickers_out = {}
    for symbol, d in market_data.items():
        ai_t = ai_tickers.get(symbol, {})
        tickers_out[symbol] = {
            "name":    d["name"],
            "price":   d["price"],
            "pos_pct": d["pos_pct"],
            "stars":   d["stars"],
            "signal":  ai_t.get("signal", "中性"),
            "l_long":  ai_t.get("l_long", ""),
            "l_short": ai_t.get("l_short", ""),
        }

    # —— 组装 layers ——
    layers_out = {
        "l1": ai_layers.get("l1", ""),
        "l2": ai_layers.get("l2", ""),
        "l3": ai_layers.get("l3", ""),
        "l4": ai_layers.get("l4", ""),
        "l5": ai_layers.get("l5", ""),
    }

    return {
        "updated":     now_str,
        "fear_greed":  fear_greed,
        "bond":        bond,
        "macro_events": macro_events,
        "tickers":     tickers_out,
        "options":     options,
        "layers":      layers_out,
    }


if __name__ == "__main__":
    data = build_dashboard_json()
    print(json.dumps(data, ensure_ascii=False, indent=2))
