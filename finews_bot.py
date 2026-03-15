#!/usr/bin/env python3
"""
财经新闻 AI 分析机器人 — 最终版
早上 07:00：完整决策简报（宏观日历+情绪+期权+新闻+关注雷达）
晚上 19:00：新闻简报（五层分析）
"""

import os
import re
import json
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf
from google import genai
from google.genai import types

# ════════════════════════════════════════════════════════
# ❶ 配置区
# ════════════════════════════════════════════════════════

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "your_gemini_api_key_here")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "https://discord.com/api/webhooks/xxx/yyy")
DISCORD_USER_ID = "917262120742158386"

DOWNLOADS_DIR  = Path.home() / "Downloads"
OUTPUT_PREFIX  = "财经新闻"
FETCH_HOURS    = 12
TZ             = ZoneInfo("Asia/Shanghai")

# 关注标的文字说明（写入 AI prompt）
POSITIONS_TEXT = """
美股科技：QQQ / NVDA / AAPL / MSFT / TSLA / GOOGL / PLTR / SOXL
中国大盘：YINN（3x做多中国大盘）
存储芯片：07709.HK（2x做多SK Hynix）
科创50：KSTR（中国科创50 ETF）
大宗商品：IAUM（黄金） / CLMAIN（原油）
外汇：USDCNH / USDMYR
加密货币：BTC / ETH
"""

# 关注标的 ticker 映射
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
    "KSTR":   {"name": "科创50ETF",  "ticker": "KSTR"},
    "IAUM":   {"name": "黄金ETF",    "ticker": "IAUM"},
    "07709":  {"name": "2xHynix",    "ticker": "7709.HK"},
    "CLMAIN": {"name": "原油",        "ticker": "CL=F"},
    "BTC":    {"name": "比特币",      "ticker": "BTC-USD"},
    "ETH":    {"name": "以太坊",      "ticker": "ETH-USD"},
    "USDCNH": {"name": "美元/人民币", "ticker": "USDCNH=X"},
    "USDMYR": {"name": "美元/马币",   "ticker": "USDMYR=X"},
}

# 宏观数据日历（每月手动更新日期）
MACRO_CALENDAR = [
    {"date": "2026-03-18", "event": "美联储利率决议 + 新闻发布会", "importance": "🔴极重要", "impact": "全市场"},
    {"date": "2026-03-19", "event": "日本央行利率决议",             "importance": "🟡重要",   "impact": "日元/USDJPY"},
    {"date": "2026-03-20", "event": "美国初请失业金人数",           "importance": "🟡重要",   "impact": "美元/美股"},
    {"date": "2026-03-28", "event": "美国PCE通胀数据",              "importance": "🔴极重要", "impact": "美联储预期"},
    {"date": "2026-03-31", "event": "中国官方PMI（制造业+服务业）", "importance": "🟡重要",   "impact": "YINN/KSTR"},
    {"date": "2026-04-02", "event": "美国非农就业数据",             "importance": "🔴极重要", "impact": "全市场"},
    {"date": "2026-04-10", "event": "美国CPI通胀数据",              "importance": "🔴极重要", "impact": "美联储预期"},
]

# RSS 订阅源
RSS_FEEDS = {
    "Yahoo/NVDA":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
    "Yahoo/AAPL":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US",
    "Yahoo/MSFT":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MSFT&region=US&lang=en-US",
    "Yahoo/TSLA":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSLA&region=US&lang=en-US",
    "Yahoo/GOOGL":        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GOOGL&region=US&lang=en-US",
    "Yahoo/PLTR":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=PLTR&region=US&lang=en-US",
    "Yahoo/SOXL":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SOXL&region=US&lang=en-US",
    "KED Global":         "https://www.kedglobal.com/rss",
    "Korea Herald":       "https://www.koreaherald.com/rss/Herald_TopNews.xml",
    "Yahoo/Micron":       "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MU&region=US&lang=en-US",
    "Yahoo/KSTR":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=KSTR&region=US&lang=en-US",
    "SCMP Tech":          "https://www.scmp.com/rss/5/feed",
    "Yahoo/YINN":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=YINN&region=US&lang=en-US",
    "SCMP China Economy": "https://www.scmp.com/rss/4/feed",
    "FT China":           "https://www.ft.com/world/asia-pacific/china?format=rss",
    "Economist China":    "https://www.economist.com/china/rss.xml",
    "Bloomberg Markets":  "https://feeds.bloomberg.com/markets/news.rss",
    "FT Markets":         "https://www.ft.com/markets?format=rss",
    "CNBC Economy":       "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "MarketWatch":        "https://feeds.marketwatch.com/marketwatch/topstories/",
    "ForexLive":          "https://www.forexlive.com/feed/news",
    "FXStreet":           "https://www.fxstreet.com/rss",
    "Kitco Gold":         "https://www.kitco.com/rss/news.rss",
    "OilPrice":           "https://oilprice.com/rss/main",
    "Yahoo/USDCNH":       "https://feeds.finance.yahoo.com/rss/2.0/headline?s=USDCNH%3DX&region=US&lang=en-US",
    "CoinTelegraph":      "https://cointelegraph.com/rss",
    "The Block":          "https://www.theblock.co/rss.xml",
    "Decrypt":            "https://decrypt.co/feed",
    "Bitcoin Magazine":   "https://bitcoinmagazine.com/feed",
    "CryptoSlate":        "https://cryptoslate.com/feed/",
    "Yahoo/BTC-USD":      "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US",
    "Yahoo/ETH-USD":      "https://feeds.finance.yahoo.com/rss/2.0/headline?s=ETH-USD&region=US&lang=en-US",
}

# ════════════════════════════════════════════════════════
# ❷ 日志
# ════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════
# ❸ 判断早晚模式
# ════════════════════════════════════════════════════════

def is_morning_run() -> bool:
    mode = os.getenv("RUN_MODE", "morning")
    return mode != "evening"

# ════════════════════════════════════════════════════════
# ❹ RSS 抓取
# ════════════════════════════════════════════════════════

def fetch_articles() -> list[dict]:
    cutoff    = datetime.now(tz=timezone.utc) - timedelta(hours=FETCH_HOURS)
    articles  = []
    seen_urls: set = set()

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            log.info(f"📡 {source_name}")
            feed = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})
            for entry in feed.entries[:15]:
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
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:300]
                articles.append({
                    "source":  source_name,
                    "title":   title,
                    "url":     url,
                    "summary": summary,
                    "pub":     pub.astimezone(TZ).strftime("%m-%d %H:%M") if pub else "??",
                })
        except Exception as e:
            log.warning(f"  ⚠️ {source_name} 抓取失败: {e}")

    log.info(f"✅ 共抓取 {len(articles)} 篇")
    return articles

# ════════════════════════════════════════════════════════
# ❺ 市场数据抓取（早报专用）
# ════════════════════════════════════════════════════════

def fetch_market_data() -> dict:
    log.info("📈 抓取市场数据...")
    data = {}
    for symbol, info in TICKERS.items():
        try:
            tk        = yf.Ticker(info["ticker"])
            hist      = tk.history(period="1y")
            fast_info = tk.fast_info
            if hist.empty:
                continue
            current  = fast_info.last_price
            high_52w = hist["Close"].max()
            low_52w  = hist["Close"].min()
            position = int((current - low_52w) / (high_52w - low_52w) * 100) if high_52w != low_52w else 50

            if position <= 30:
                pos_label = f"低位 {position}%"
            elif position <= 70:
                pos_label = f"中位 {position}%"
            else:
                pos_label = f"高位 {position}%"

            if position <= 20:   stars = "⭐⭐⭐⭐⭐"
            elif position <= 35: stars = "⭐⭐⭐⭐"
            elif position <= 55: stars = "⭐⭐⭐"
            elif position <= 75: stars = "⭐⭐"
            else:                stars = "⭐"

            try:
                cal      = tk.calendar
                earnings = cal.get("Earnings Date", [None])[0] if isinstance(cal, dict) else None
                if earnings and hasattr(earnings, 'strftime'):
                    days_to      = (earnings.date() - datetime.now(tz=TZ).date()).days
                    earnings_str = f"{earnings.strftime('%m月%d日')}（{days_to}天后）"
                else:
                    earnings_str = "—"
            except Exception:
                earnings_str = "—"

            if current >= 1000:   price_str = f"${current:,.0f}"
            elif current >= 10:   price_str = f"${current:.2f}"
            elif current >= 1:    price_str = f"${current:.3f}"
            else:                 price_str = f"${current:.4f}"

            data[symbol] = {
                "name":         info["name"],
                "price":        price_str,
                "position":     pos_label,
                "stars":        stars,
                "earnings":     earnings_str,
                "position_pct": position,
            }
            log.info(f"  ✅ {symbol}: {price_str} | {pos_label} | {stars}")
        except Exception as e:
            log.warning(f"  ⚠️ {symbol} 数据抓取失败: {e}")
    return data


def fetch_fear_greed() -> str:
    try:
        resp  = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        data  = resp.json()["data"][0]
        score = int(data["value"])
        label = data["value_classification"]
        label_map = {
            "Extreme Fear": "极度恐惧", "Fear": "恐惧",
            "Neutral": "中性", "Greed": "贪婪", "Extreme Greed": "极度贪婪",
        }
        if score <= 25:   emoji = "😱"
        elif score <= 45: emoji = "😨"
        elif score <= 55: emoji = "😐"
        elif score <= 75: emoji = "😏"
        else:             emoji = "🤑"
        return f"{emoji} {score}/100（{label_map.get(label, label)}）"
    except Exception as e:
        log.warning(f"恐惧贪婪指数抓取失败: {e}")
        return "数据获取失败"


def fetch_bond_spread() -> str:
    try:
        t10    = yf.Ticker("^TNX").fast_info.last_price
        t2     = yf.Ticker("^IRX").fast_info.last_price
        spread = round(t10 - t2 / 10, 2)
        if spread > 0.5:   label, emoji = "正常（经济预期乐观）", "🟢"
        elif spread > 0:   label, emoji = "收窄（注意风险）", "🟡"
        else:              label, emoji = "倒挂（衰退信号）", "🔴"
        return f"{emoji} 10Y {t10:.2f}% | 利差 {spread:+.2f}% {label}"
    except Exception as e:
        log.warning(f"美债利差抓取失败: {e}")
        return "数据获取失败"


def fetch_put_call() -> str:
    lines = [
        "```",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{'标的':<10} {'P/C比率':<12} 市场情绪",
        "─────────────────────────────────────────────",
    ]
    for symbol in ["QQQ", "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "PLTR", "SOXL"]:
        try:
            tk       = yf.Ticker(symbol)
            exps     = tk.options
            if not exps:
                continue
            chain    = tk.option_chain(exps[0])
            put_vol  = chain.puts["volume"].sum()
            call_vol = chain.calls["volume"].sum()
            ratio    = put_vol / call_vol if call_vol > 0 else 1
            if ratio > 1.2:   sentiment = "🔴 偏悲观（看跌多）"
            elif ratio > 0.8: sentiment = "🟡 中性"
            else:             sentiment = "🟢 偏乐观（看涨多）"
            lines.append(f"{symbol:<10} P/C={ratio:.2f}{'':>4} {sentiment}")
            lines.append("")
        except Exception as e:
            log.warning(f"期权P/C {symbol} 失败: {e}")
    lines += ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "```"]
    return "\n".join(lines)


def build_macro_calendar() -> str:
    today    = datetime.now(tz=TZ).date()
    end_date = today + timedelta(days=7)
    lines    = []
    for item in MACRO_CALENDAR:
        event_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
        if today <= event_date <= end_date:
            days_to = (event_date - today).days
            when    = "今天" if days_to == 0 else "明天" if days_to == 1 else f"{days_to}天后"
            lines.append(
                f"{item['importance']} {event_date.strftime('%m/%d')}（{when}）"
                f"  {item['event']}  → 影响：{item['impact']}"
            )
    return "\n".join(lines) if lines else "未来7天无重大数据发布"

# ════════════════════════════════════════════════════════
# ❻ 构建关注雷达表格
# ════════════════════════════════════════════════════════

def build_radar_table(market_data: dict, ai_signals: dict) -> str:
    signal_map = {"🟢 偏多": "🟢 偏多", "🔴 偏空": "🔴 偏空", "🟡 中性": "🟡 中性"}
    lines = [
        "```",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{'标的':<8} {'现价':<10} {'52周位置':<10} {'信号':<8} {'财报':<14} 吸引力",
        "────────────────────────────────────────────────────────",
    ]
    for symbol, info in market_data.items():
        raw_signal = ai_signals.get(symbol, "🟡 中性")
        signal     = signal_map.get(raw_signal, "🟡 中性")
        lines.append(
            f"{symbol:<8} "
            f"{info['price']:<10} "
            f"{info['position']:<10} "
            f"{signal:<8} "
            f"{info['earnings']:<14} "
            f"{info['stars']}"
        )
        lines.append("")
    lines += [
        "────────────────────────────────────────────────────────",
        "🟢偏多  🟡中性  🔴偏空  |  ⭐越多=越值得关注",
        "",
        "标的说明:",
        "QQQ纳指ETF  NVDA英伟达  AAPL苹果  MSFT微软  TSLA特斯拉",
        "GOOGL谷歌  PLTR Palantir  SOXL 3x芯片  YINN 3xA50",
        "KSTR科创50  IAUM黄金  07709 2xHynix  CLMAIN原油",
        "BTC比特币  ETH以太坊  USDCNH美元/人民币  USDMYR美元/马币",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "```",
    ]
    return "\n".join(lines)

# ════════════════════════════════════════════════════════
# ❼ 保存看板数据（用于 GitHub Pages）
# ════════════════════════════════════════════════════════

def save_dashboard_data(market_data: dict, ai_signals: dict, fear_greed: str,
                        bond_spread: str, put_call_raw: str, analysis: str):
    data = {
        "updated":          datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M"),
        "fear_greed":       fear_greed,
        "bond_spread":      bond_spread,
        "analysis_summary": analysis[:800],
        "tickers":          {}
    }
    for symbol, info in market_data.items():
        data["tickers"][symbol] = {
            "name":     info["name"],
            "price":    info["price"],
            "position": info["position"],
            "pos_pct":  info["position_pct"],
            "stars":    info["stars"].count("⭐"),
            "signal":   ai_signals.get(symbol, "🟡 中性"),
            "earnings": info["earnings"],
        }
    docs_dir = Path(__file__).parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    with open(docs_dir / "data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("📊 看板数据已更新 docs/data.json")

# ════════════════════════════════════════════════════════
# ❽ Gemini AI 分析（两阶段：Flash浓缩 → Pro深度分析）
# ════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION_PRO = (
    "你现在是我的专属投资顾问，风格介于桥水Ray Dalio和对冲基金交易员之间。"
    "我的投资策略是杠铃法则：一边是长线价值投资（持有优质资产穿越周期），"
    "一边是短线期权博弈（捕捉事件驱动的短期波动机会）。"
    "我关注的标的有些是已持有的，有些是观察池中待入场的，有些仅作宏观参考。"
    "说话风格要求：像朋友聊天一样直接，不要用学术腔，不要堆砌专业术语，"
    "要让一个普通人看得懂，但信息密度要高，每句话都要有用。"
    "内容要求："
    "1. 提取今日对市场有实质影响的核心信息，宏观和个股都要，不要只盯着美联储。"
    "2. 长线视角：这条新闻对关注的长期逻辑有没有影响？是加分还是减分？"
    "3. 短线视角：这条新闻有没有催生近期期权机会？比如财报前后、重大事件窗口。"
    "4. 内容要完整，不要因为追求简洁就删掉有价值的分析，宁可多说也不要漏掉。"
    "5. 如有具体价格数据请标注，格式: 标的名（当前价/涨跌幅）。"
    "6. 每层标题单独一行，标题后空一行再写内容，层与层之间空一行。"
    "7. 禁止输出**加粗**、---分隔线等Markdown符号，直接用纯文字。"
    "8. 字数不限，把该说的都说完，不要为了控制字数砍掉有价值的内容。"
)


def flash_summarize_news(articles: list[dict]) -> str:
    """用 Flash 把所有新闻浓缩成精华摘要，直接返回文字块给 Pro 分析"""
    if not articles:
        return ""

    log.info(f"⚡ Flash 浓缩：{len(articles)} 条新闻...")
    time.sleep(5)

    news_block = ""
    for i, a in enumerate(articles, 1):
        news_block += f"{i}. [{a['source']}] {a['title']}\n"
        if a["summary"]:
            news_block += f"   {a['summary']}\n"

    prompt = f"""以下是今日财经新闻，请帮我提炼出最有投资价值的内容。

要求：
1. 过滤掉纯娱乐、纯体育、软文广告、与投资完全无关的内容，其余全部保留
2. 每条用1-2句话概括核心信息，保留关键数据和数字
3. 按板块分组输出：宏观/美联储、科技股、中国/港股、大宗商品、加密货币、其他
4. 如果某个板块没有重要新闻就跳过
5. 直接输出内容，不要废话

新闻列表：
{news_block}"""

    try:
        client   = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=4000,
            ),
        )
        result = response.text.strip()
        log.info(f"⚡ Flash 浓缩完成，输出约 {len(result)} 字")
        return result
    except Exception as e:
        log.warning(f"⚠️ Flash 浓缩失败，使用原始标题: {e}")
        return "\n".join([f"[{a['source']}] {a['title']}" for a in articles])


def build_news_prompt(news_summary: str, market_data: dict = None) -> str:
    now_str = datetime.now(tz=TZ).strftime("%Y年%m月%d日 %H:%M")

    market_block = ""
    if market_data:
        market_block = "\n【当前价格快照】\n"
        for symbol, info in market_data.items():
            market_block += f"{symbol}({info['name']}): {info['price']} | {info['position']}\n"

    return f"""当前时间：{now_str}（北京时间）

【我的关注】
{POSITIONS_TEXT.strip()}
{market_block}
【今日财经新闻精华（已由Flash提炼）】
{news_summary}
---
请按以下五层结构输出分析，语气像朋友聊天，直接说人话：

第一层：今天市场在讲什么故事
（用2-3句话说清今天最重要的1-2个市场主题，要让外行人也听得懂）

第二层：关注标的表现
（以下每个标的都必须分析，一个都不能跳过，哪怕没有直接相关新闻也要给出判断）
美股科技：QQQ / NVDA / AAPL / MSFT / TSLA / GOOGL / PLTR / SOXL
中国大盘：YINN / KSTR
大宗商品：IAUM（黄金）/ CLMAIN（原油）
外汇：USDCNH / USDMYR
加密货币：BTC / ETH

每个标的格式如下：
标的名 [偏多/偏空/中性]
长线：一句话说对长期持有逻辑的影响
短线：一句话说近期期权机会或风险，没有就写暂无明显催化剂

第三层：各市场之间怎么联动的
（今天最明显的跨资产传导链）

第四层：今天最该盯住的一件事
（只说一件，给出具体要看的指标、时间节点或价格位置）

第五层：我目前没关注但值得看的机会和风险
（从新闻里发现我关注之外的机会和风险，没有就直接说无）"""


def extract_ai_signals(analysis_text: str) -> dict:
    signals = {}
    for symbol in TICKERS.keys():
        sym_pos = analysis_text.upper().find(symbol.upper())
        if sym_pos == -1:
            continue
        snippet = analysis_text[sym_pos:sym_pos + 60]
        if "[偏多]" in snippet or "偏多" in snippet:
            signals[symbol] = "🟢 偏多"
        elif "[偏空]" in snippet or "偏空" in snippet:
            signals[symbol] = "🔴 偏空"
        else:
            signals[symbol] = "🟡 中性"
    return signals


def call_gemini_pro(prompt: str) -> str:
    log.info("🧠 Pro 深度分析...")
    time.sleep(15)
    for attempt in range(3):
        try:
            client   = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION_PRO,
                    temperature=0.1,
                    max_output_tokens=8192,
                ),
            )
            log.info("✅ Pro 分析完成")
            return response.text
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if attempt < 2:
                    wait = 60 * (attempt + 2)
                    log.warning(f"⚠️ 频率限制，{wait}秒后重试（第{attempt+1}次）...")
                    time.sleep(wait)
                else:
                    return "⚠️ Gemini Pro API 频率超限，请稍后重新运行。"
            elif "404" in err_str or "NOT_FOUND" in err_str:
                return "⚠️ 模型不可用，请检查模型名称。"
            else:
                log.error(f"❌ Gemini 错误: {e}")
                return f"⚠️ AI 分析失败：{err_str[:120]}"
    return "⚠️ 多次重试失败，请稍后再运行。"

# ════════════════════════════════════════════════════════
# ❾ Discord 推送
# ════════════════════════════════════════════════════════

def send_discord(content: str, title: str = ""):
    if not DISCORD_WEBHOOK or "webhooks/xxx" in DISCORD_WEBHOOK:
        log.warning("⚠️ 未配置 DISCORD_WEBHOOK")
        return
    if title:
        _discord_send(title)
    for chunk in _split_discord(content):
        _discord_send(chunk)
    log.info("🚀 Discord 推送完成")


def _discord_send(text: str):
    resp = requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=10)
    if resp.status_code not in (200, 204):
        log.warning(f"Discord 返回 {resp.status_code}: {resp.text[:200]}")
    time.sleep(0.5)


def _split_discord(text: str, max_len: int = 1900) -> list[str]:
    lines  = text.splitlines(keepends=True)
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks

# ════════════════════════════════════════════════════════
# ❿ 保存文件
# ════════════════════════════════════════════════════════

def save_report(content: str, mode: str):
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    now_str  = datetime.now(tz=TZ).strftime("%Y%m%d_%H%M")
    filepath = DOWNLOADS_DIR / f"{OUTPUT_PREFIX}_{mode}_{now_str}.txt"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    log.info(f"💾 报告已保存：{filepath}")

# ════════════════════════════════════════════════════════
# ⓫ 主流程
# ════════════════════════════════════════════════════════

def run_morning():
    now_label = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info("🌅 运行早报模式")

    articles    = fetch_articles()
    market_data = fetch_market_data()
    fear_greed  = fetch_fear_greed()
    bond_spread = fetch_bond_spread()
    put_call    = fetch_put_call()
    macro_cal   = build_macro_calendar()

    news_summary = flash_summarize_news(articles)
    analysis     = call_gemini_pro(build_news_prompt(news_summary, market_data))
    ai_signals   = extract_ai_signals(analysis)
    radar_table  = build_radar_table(market_data, ai_signals)

    # 保存看板数据（用于 GitHub Pages）
    save_dashboard_data(market_data, ai_signals, fear_greed, bond_spread, put_call, analysis)

    report = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 宏观数据日历（未来7天）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{macro_cal}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌡️ 市场情绪温度计
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
美债收益率:   {bond_spread}
恐惧贪婪指数: {fear_greed}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 期权市场情绪（Put/Call 比率）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{put_call}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📰 AI 深度新闻分析
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{analysis}
"""

    header = (
        f"<@{DISCORD_USER_ID}>\n"
        f"# 🌅 财经早报  {now_label}  |  {len(articles)}条新闻\n"
        f"> 🤖 Gemini 3.1 Pro · 宏观日历 + 情绪 + 期权 + 标的追踪\n"
        f"{'━' * 40}"
    )
    send_discord(report, header)
    _discord_send(radar_table)  # 雷达表格单独发送确保渲染正确
    save_report(header + "\n" + report + "\n" + radar_table, "早报")
    log.info("✅ 早报完成")


def run_evening():
    now_label = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info("🌙 运行晚报模式")

    articles     = fetch_articles()
    news_summary = flash_summarize_news(articles)
    analysis     = call_gemini_pro(build_news_prompt(news_summary))

    header = (
        f"<@{DISCORD_USER_ID}>\n"
        f"# 🌙 财经晚报  {now_label}  |  {len(articles)}条新闻\n"
        f"> 🤖 Gemini 3.1 Pro · 新闻深度分析\n"
        f"{'━' * 40}"
    )
    send_discord(analysis, header)
    save_report(header + "\n" + analysis, "晚报")
    log.info("✅ 晚报完成")


def main():
    now_label = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info("=" * 50)
    log.info(f"🚀 财经机器人启动  {now_label}")
    if is_morning_run():
        run_morning()
    else:
        run_evening()


if __name__ == "__main__":
    main()
