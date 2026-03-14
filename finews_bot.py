#!/usr/bin/env python3
"""
财经新闻 AI 分析机器人 v5
早上 08:00：完整决策简报（宏观日历+情绪+资金流+期权+新闻+持仓雷达）
晚上 20:00：新闻简报（四层分析）
"""

import os
import re
import json
import time
import hashlib
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

DOWNLOADS_DIR   = Path.home() / "Downloads"
OUTPUT_PREFIX   = "财经新闻"
FETCH_HOURS     = 12
TZ              = ZoneInfo("Asia/Shanghai")

# 持仓标的
POSITIONS_TEXT = """
美股科技：QQQ / NVDA / AAPL / MSFT / TSLA / GOOGL / PLTR / SOXL
中国大盘：YINN（3x做多中国大盘）
存储芯片：07709.HK（2x做多SK Hynix）
科创50：KSTR（中国科创50 ETF）
大宗商品：IAUM（黄金） / CLMAIN（原油）
外汇：USDCNH / USDMYR
加密货币：BTC / ETH
"""

# 持仓标的 ticker 映射（用于拉价格数据）
TICKERS = {
    "QQQ":    {"name": "纳指ETF",       "ticker": "QQQ"},
    "NVDA":   {"name": "英伟达",         "ticker": "NVDA"},
    "AAPL":   {"name": "苹果",           "ticker": "AAPL"},
    "MSFT":   {"name": "微软",           "ticker": "MSFT"},
    "TSLA":   {"name": "特斯拉",         "ticker": "TSLA"},
    "GOOGL":  {"name": "谷歌",           "ticker": "GOOGL"},
    "PLTR":   {"name": "Palantir",      "ticker": "PLTR"},
    "SOXL":   {"name": "芯片3x多",      "ticker": "SOXL"},
    "YINN":   {"name": "中国大盘3x多",  "ticker": "YINN"},
    "KSTR":   {"name": "科创50ETF",     "ticker": "KSTR"},
    "IAUM":   {"name": "黄金ETF",       "ticker": "IAUM"},
    "BTC":    {"name": "比特币",         "ticker": "BTC-USD"},
    "ETH":    {"name": "以太坊",         "ticker": "ETH-USD"},
    "USDCNH": {"name": "美元/人民币",   "ticker": "USDCNH=X"},
    "USDMYR": {"name": "美元/马币",     "ticker": "USDMYR=X"},
}

# ETF资金流向监控
ETF_FLOW_TICKERS = ["QQQ", "SOXL", "YINN", "IAUM"]

# 宏观数据日历（固定重要节点，每月更新）
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
    mode = os.getenv("RUN_MODE", "")
    if mode == "morning":
        return True
    if mode == "evening":
        return False
    # 手动触发时默认走早报（方便测试）
    return True

# ════════════════════════════════════════════════════════
# ❹ RSS 抓取
# ════════════════════════════════════════════════════════

def fetch_articles() -> list[dict]:
    cutoff   = datetime.now(tz=timezone.utc) - timedelta(hours=FETCH_HOURS)
    articles = []
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
    """拉取所有持仓标的的价格、52周位置、财报日期、期权Put/Call"""
    log.info("📈 抓取市场数据...")
    data = {}

    for symbol, info in TICKERS.items():
        try:
            tk   = yf.Ticker(info["ticker"])
            hist = tk.history(period="1y")
            fast_info = tk.fast_info

            if hist.empty:
                continue

            current  = fast_info.last_price
            high_52w = hist["Close"].max()
            low_52w  = hist["Close"].min()
            position = int((current - low_52w) / (high_52w - low_52w) * 100) if high_52w != low_52w else 50

            # 位置描述
            if position <= 30:
                pos_label = f"低位 {position}%"
            elif position <= 70:
                pos_label = f"中位 {position}%"
            else:
                pos_label = f"高位 {position}%"

            # 买入吸引力星级（基于52周位置反向打分）
            if position <= 20:
                stars = "⭐⭐⭐⭐⭐"
            elif position <= 35:
                stars = "⭐⭐⭐⭐"
            elif position <= 55:
                stars = "⭐⭐⭐"
            elif position <= 75:
                stars = "⭐⭐"
            else:
                stars = "⭐"

            # 财报日期
            try:
                cal = tk.calendar
                earnings = cal.get("Earnings Date", [None])[0] if isinstance(cal, dict) else None
                if earnings:
                    if hasattr(earnings, 'strftime'):
                        days_to = (earnings.date() - datetime.now(tz=TZ).date()).days
                        earnings_str = f"{earnings.strftime('%m月%d日')}（{days_to}天后）"
                    else:
                        earnings_str = str(earnings)
                else:
                    earnings_str = "—"
            except Exception:
                earnings_str = "—"

            # 价格格式化
            if current >= 1000:
                price_str = f"${current:,.0f}"
            elif current >= 10:
                price_str = f"${current:.2f}"
            elif current >= 1:
                price_str = f"${current:.3f}"
            else:
                price_str = f"${current:.4f}"

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
        resp = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=8,
        )
        data = resp.json()["data"][0]
        score = int(data["value"])
        label = data["value_classification"]

        label_map = {
            "Extreme Fear": "极度恐惧",
            "Fear": "恐惧",
            "Neutral": "中性",
            "Greed": "贪婪",
            "Extreme Greed": "极度贪婪",
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
    """抓取美债10Y-2Y利差"""
    try:
        t10 = yf.Ticker("^TNX").fast_info.last_price
        t2  = yf.Ticker("^IRX").fast_info.last_price
        spread = round(t10 - t2 / 10, 2)  # IRX 是折算后的，除以10近似
        if spread > 0.5:
            label = "正常（经济预期乐观）"
            emoji = "🟢"
        elif spread > 0:
            label = "收窄（注意风险）"
            emoji = "🟡"
        else:
            label = "倒挂（衰退信号）"
            emoji = "🔴"
        return f"{emoji} 10Y {t10:.2f}% | 利差 {spread:+.2f}% {label}"
    except Exception as e:
        log.warning(f"美债利差抓取失败: {e}")
        return "数据获取失败"


def fetch_etf_flows() -> str:
    """抓取ETF近5日资金流向（用成交量变化近似）"""
    lines = []
    for symbol in ETF_FLOW_TICKERS:
        try:
            hist = yf.Ticker(symbol).history(period="10d")
            if len(hist) < 5:
                continue
            avg_vol    = hist["Volume"].iloc[:-5].mean()
            recent_vol = hist["Volume"].iloc[-5:].mean()
            ratio      = recent_vol / avg_vol if avg_vol > 0 else 1

            price      = hist["Close"].iloc[-1]
            price_chg  = (hist["Close"].iloc[-1] / hist["Close"].iloc[-5] - 1) * 100

            if ratio > 1.3 and price_chg > 0:
                flow = "🟢 资金流入"
            elif ratio > 1.3 and price_chg < 0:
                flow = "🔴 资金流出"
            elif ratio < 0.7:
                flow = "⚪ 成交清淡"
            else:
                flow = "🟡 正常波动"

            lines.append(f"{symbol:<6} ${price:.2f}  近5日{price_chg:+.1f}%  {flow}")
        except Exception as e:
            log.warning(f"ETF流向 {symbol} 失败: {e}")

    return "\n".join(lines) if lines else "数据获取失败"


def fetch_put_call() -> str:
    """抓取主要标的期权Put/Call比率"""
    lines = []
    for symbol in ["QQQ", "NVDA", "TSLA", "AAPL"]:
        try:
            tk   = yf.Ticker(symbol)
            exps = tk.options
            if not exps:
                continue
            chain     = tk.option_chain(exps[0])
            put_vol   = chain.puts["volume"].sum()
            call_vol  = chain.calls["volume"].sum()
            ratio     = put_vol / call_vol if call_vol > 0 else 1

            if ratio > 1.2:
                sentiment = "🔴 偏悲观（看跌多）"
            elif ratio > 0.8:
                sentiment = "🟡 中性"
            else:
                sentiment = "🟢 偏乐观（看涨多）"

            lines.append(f"{symbol:<6} P/C={ratio:.2f}  {sentiment}")
        except Exception as e:
            log.warning(f"期权P/C {symbol} 失败: {e}")

    return "\n".join(lines) if lines else "数据获取失败"


def build_macro_calendar() -> str:
    """生成未来7天宏观数据日历"""
    today    = datetime.now(tz=TZ).date()
    end_date = today + timedelta(days=7)
    lines    = []

    for item in MACRO_CALENDAR:
        event_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
        if today <= event_date <= end_date:
            days_to = (event_date - today).days
            if days_to == 0:
                when = "今天"
            elif days_to == 1:
                when = "明天"
            else:
                when = f"{days_to}天后"
            lines.append(
                f"{item['importance']} {event_date.strftime('%m/%d')}（{when}）"
                f"  {item['event']}  → 影响：{item['impact']}"
            )

    return "\n".join(lines) if lines else "未来7天无重大数据发布"

# ════════════════════════════════════════════════════════
# ❻ 构建持仓雷达表格
# ════════════════════════════════════════════════════════

def build_radar_table(market_data: dict, ai_signals: dict) -> str:
    """生成持仓雷达表格"""
    lines = [
        "```",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{'标的/现价':<14} {'52周位置':<12} {'买入吸引力':<12} {'AI信号':<10} {'下次财报'}",
        "─────────────────────────────────────────────────────",
    ]

    for symbol, info in market_data.items():
        signal = ai_signals.get(symbol, "🟡 中性")
        line = (
            f"{symbol+' '+info['price']:<14} "
            f"{info['position']:<12} "
            f"{info['stars']:<12} "
            f"{signal:<10} "
            f"{info['earnings']}"
        )
        lines.append(line)
        lines.append("")  # 空行间隔

    lines += [
        "─────────────────────────────────────────────────────",
        "图例: 🟢偏多  🟡中性  🔴偏空  |  ⭐越多越值得关注",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "```",
    ]

    return "\n".join(lines)

# ════════════════════════════════════════════════════════
# ❼ Gemini AI 分析（两阶段：Flash过滤 → Pro深度分析）
# ════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION_PRO = (
    "你现在是一位拥有20年华尔街经验的宏观分析师，专门服务于追求FIRE（提前退休）的极简长线投资者。"
    "强制要求：1. 只提取对美元流动性、美联储政策预期、以及资本市场有实质性影响的核心信息。"
    "2. 彻底剔除市场噪音和短线博弈废话。"
    "3. 宁缺毋滥，如果新闻没有实质性宏观价值，请直接返回今日无核心异动信号。"
    "4. 输出格式要求: 不要多余空行，每层标题后直接接内容，全文紧凑，控制在2000字以内。"
    "5. 禁止输出**加粗**、---分隔线等Markdown符号，直接用纯文字输出。"
    "6. 如有具体价格数据请标注，格式：标的名（当前价/涨跌幅）。"
)


def flash_filter_news(articles: list[dict]) -> list[dict]:
    """
    第一阶段：用 Flash 快速过滤无关新闻
    250条 → 保留约 60-80 条真正有投资价值的
    """
    if not articles:
        return []

    log.info(f"⚡ Flash 预过滤：{len(articles)} 条新闻...")
    time.sleep(5)

    # 把所有新闻标题打包成一个列表送给 Flash
    titles_block = ""
    for i, a in enumerate(articles, 1):
        titles_block += f"{i}. [{a['source']}] {a['title']}\n"

    prompt = f"""以下是今日财经新闻标题列表，请快速筛选出有投资价值的新闻编号。

保留标准（满足任意一条即保留）：
- 宏观经济、美联储、利率、通胀、就业数据相关
- 科技行业、AI、芯片、半导体相关
- 中国经济、港股、A股、人民币相关
- 大宗商品（黄金、原油）相关
- 外汇市场相关
- 加密货币市场相关
- 地缘政治、贸易战、重大政策相关
- 市场异动、资金流向、机构动态相关

过滤掉：娱乐、体育、生活、软文广告、公司招聘、无关奖项

新闻列表：
{titles_block}

请只返回需要保留的新闻编号，用逗号分隔，例如：1,3,5,7,12,15
不要任何其他文字，只返回数字列表。"""

    try:
        client   = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=500,
            ),
        )
        raw = response.text.strip()
        # 解析编号列表
        indices = set()
        for part in re.split(r"[,\s]+", raw):
            part = part.strip()
            if part.isdigit():
                indices.add(int(part))

        filtered = [a for i, a in enumerate(articles, 1) if i in indices]
        log.info(f"⚡ Flash 过滤完成：{len(articles)} → {len(filtered)} 条")
        return filtered if filtered else articles[:60]  # 保底：过滤失败则取前60条

    except Exception as e:
        log.warning(f"⚠️ Flash 过滤失败，使用原始新闻: {e}")
        return articles[:80]


def build_news_prompt(articles: list[dict], market_data: dict = None) -> str:
    now_str    = datetime.now(tz=TZ).strftime("%Y年%m月%d日 %H:%M")
    news_block = ""
    for i, a in enumerate(articles, 1):
        news_block += f"{i}. [{a['source']}] {a['title']}\n"
        if a["summary"]:
            news_block += f"   {a['summary']}\n"
        news_block += "\n"

    market_block = ""
    if market_data:
        market_block = "\n【当前持仓价格快照】\n"
        for symbol, info in market_data.items():
            market_block += f"{symbol}({info['name']}): {info['price']} | {info['position']}\n"

    return f"""当前时间：{now_str}（北京时间）

【我的持仓标的】
{POSITIONS_TEXT.strip()}
{market_block}
【今日精选财经新闻（{len(articles)} 条，已由AI预筛选）】
{news_block}
---
请严格按照以下五层结构输出分析报告，使用中文，风格极简、直接、无废话：

第一层：宏观框架
（今日最重要的 1-2 个市场主题，每个主题不超过 3 句话）

第二层：逐标的影响
（对每个持仓标的给出：方向判断 偏多/偏空/中性 + 核心理由。
请在每个标的后用括号标注信号，格式：[偏多] [偏空] [中性]
无相关新闻则略去。）

第三层：跨资产联动
（描述今日最显著的跨资产传导链，例如：美债收益率上升→科技股承压→BTC回调→黄金受益）

第四层：今日最需关注的一件事
（只说一件，给出具体的观察指标或时间节点）

第五层：场外值得关注的机会与风险
（突破你现有持仓视角，从今日新闻发现：
1. 有没有你目前没持有但值得考虑的标的或板块？给出具体名称和理由
2. 有没有你持仓里尚未充分反映的潜在风险？
3. 有没有正在形成的宏观趋势，未来30-90天可能影响你的仓位？
每条给出具体标的或指标名称，不说废话，没有则直接说无。）"""


def extract_ai_signals(analysis_text: str) -> dict:
    """从AI分析文本中提取每个标的的信号"""
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
    """第二阶段：Pro 深度分析（五层结构）"""
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
# ❽ Discord 推送
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
    time.sleep(0.5)  # 避免Discord限流


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
# ❾ 保存文件
# ════════════════════════════════════════════════════════

def save_report(content: str, mode: str):
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    now_str  = datetime.now(tz=TZ).strftime("%Y%m%d_%H%M")
    filepath = DOWNLOADS_DIR / f"{OUTPUT_PREFIX}_{mode}_{now_str}.txt"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    log.info(f"💾 报告已保存：{filepath}")

# ════════════════════════════════════════════════════════
# ❿ 主流程
# ════════════════════════════════════════════════════════

def run_morning():
    """早报：完整决策简报"""
    now_label = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info("🌅 运行早报模式")

    # 1. 抓取新闻
    articles = fetch_articles()

    # 2. 抓取市场数据
    market_data = fetch_market_data()

    # 3. 抓取情绪/资金数据
    fear_greed  = fetch_fear_greed()
    bond_spread = fetch_bond_spread()
    etf_flows   = fetch_etf_flows()
    put_call    = fetch_put_call()
    macro_cal   = build_macro_calendar()

    # 4. 两阶段 AI 分析
    filtered_articles = flash_filter_news(articles)                          # Flash 预过滤
    analysis          = call_gemini_pro(build_news_prompt(filtered_articles, market_data))  # Pro 深度分析

    # 5. 提取AI信号，生成雷达表格
    ai_signals  = extract_ai_signals(analysis)
    radar_table = build_radar_table(market_data, ai_signals)

    # 6. 组装完整早报
    report = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 宏观数据日历（未来7天）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{macro_cal}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌡️ 市场情绪温度计
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
恐惧贪婪指数: {fear_greed}
美债收益率:   {bond_spread}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 ETF 资金流向（近5日）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{etf_flows}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 期权市场情绪（Put/Call 比率）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{put_call}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📰 AI 深度新闻分析
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{analysis}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 持仓雷达快照
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{radar_table}
"""

    # 7. 推送 Discord
    header = (
        f"# 🌅 财经早报  {now_label}  |  {len(articles)}条新闻\n"
        f"> 🤖 Gemini 3.1 Pro · 宏观日历 + 情绪 + 资金流 + 持仓雷达\n"
        f"{'━' * 40}"
    )
    send_discord(report, header)

    # 8. 保存
    save_report(header + "\n" + report, "早报")
    log.info("✅ 早报完成")


def run_evening():
    """晚报：新闻简报"""
    now_label = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info("🌙 运行晚报模式")

    articles          = fetch_articles()
    filtered_articles = flash_filter_news(articles)
    analysis          = call_gemini_pro(build_news_prompt(filtered_articles))

    header = (
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
