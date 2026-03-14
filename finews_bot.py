#!/usr/bin/env python3
"""
财经新闻 AI 分析机器人
数据源：Yahoo Finance RSS + 多来源财经 RSS
AI分析：Google Gemini API（gemini-3.1-pro-preview）
推送：Discord Webhook
存储：~/Downloads/财经新闻_日期时间.txt
去重：~/Downloads/.news_seen.json
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
from google import genai
from google.genai import types

# ════════════════════════════════════════════════════════
# ❶ 配置区 — 只需修改这里（或通过环境变量注入）
# ════════════════════════════════════════════════════════

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "your_gemini_api_key_here")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "https://discord.com/api/webhooks/xxx/yyy")

DOWNLOADS_DIR   = Path.home() / "Downloads"
SEEN_FILE       = DOWNLOADS_DIR / ".news_seen.json"
OUTPUT_PREFIX   = "财经新闻"

FETCH_HOURS     = 24   # 只抓取最近 N 小时的文章
MAX_ARTICLES_AI = 80   # 送给 AI 分析的最大条数
TZ              = ZoneInfo("Asia/Shanghai")

# ── 持仓标的（写入 AI prompt）──────────────────────────
POSITIONS = """
美股科技：QQQ / NVDA / AAPL / MSFT / TSLA / GOOGL / PLTR / SOXL
中国大盘：YINN（3x做多中国大盘）
存储芯片：07709.HK（2x做多SK Hynix）
科创50：KSTR（中国科创50 ETF）
大宗商品：IAUM（黄金） / CLMAIN（原油）
外汇：USDCNH / USDMYR
加密货币：BTC / ETH
"""

# ── RSS 订阅源 ─────────────────────────────────────────
RSS_FEEDS = {
    # ── 美股科技个股（Yahoo Finance）──────────────────
    "Yahoo/NVDA":  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US",
    "Yahoo/AAPL":  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US",
    "Yahoo/MSFT":  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MSFT&region=US&lang=en-US",
    "Yahoo/TSLA":  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSLA&region=US&lang=en-US",
    "Yahoo/GOOGL": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GOOGL&region=US&lang=en-US",
    "Yahoo/PLTR":  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=PLTR&region=US&lang=en-US",
    "Yahoo/SOXL":  "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SOXL&region=US&lang=en-US",
    # ── 存储/韩国/科创 ────────────────────────────────
    "KED Global":         "https://www.kedglobal.com/rss",
    "Korea Herald":       "https://www.koreaherald.com/rss/Herald_TopNews.xml",
    "Yahoo/SK Hynix":     "https://feeds.finance.yahoo.com/rss/2.0/headline?s=000660.KS&region=US&lang=en-US",
    "Yahoo/Micron":       "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MU&region=US&lang=en-US",
    "Yahoo/KSTR":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=KSTR&region=US&lang=en-US",
    "SCMP Tech":          "https://www.scmp.com/rss/5/feed",
    # ── 中国/港股 ─────────────────────────────────────
    "Yahoo/YINN":         "https://feeds.finance.yahoo.com/rss/2.0/headline?s=YINN&region=US&lang=en-US",
    "SCMP China Economy": "https://www.scmp.com/rss/4/feed",
    "FT China":           "https://www.ft.com/world/asia-pacific/china?format=rss",
    "Economist China":    "https://www.economist.com/china/rss.xml",
    # ── 宏观/美联储 ───────────────────────────────────
    "Bloomberg Markets":  "https://feeds.bloomberg.com/markets/news.rss",
    "FT Markets":         "https://www.ft.com/markets?format=rss",
    "CNBC Economy":       "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "MarketWatch":        "https://feeds.marketwatch.com/marketwatch/topstories/",
    # ── 外汇/大宗 ─────────────────────────────────────
    "ForexLive":          "https://www.forexlive.com/feed/news",
    "FXStreet":           "https://www.fxstreet.com/rss",
    "Kitco Gold":         "https://www.kitco.com/rss/news.rss",
    "OilPrice":           "https://oilprice.com/rss/main",
    "Yahoo/USDCNH":       "https://feeds.finance.yahoo.com/rss/2.0/headline?s=USDCNH%3DX&region=US&lang=en-US",
    # ── 加密货币 ──────────────────────────────────────
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
# ❸ 去重缓存
# ════════════════════════════════════════════════════════

def load_seen() -> set:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f)


def make_id(url: str, title: str) -> str:
    return hashlib.md5(f"{url}{title}".encode()).hexdigest()

# ════════════════════════════════════════════════════════
# ❹ RSS 抓取
# ════════════════════════════════════════════════════════

def fetch_articles(seen: set) -> tuple[list[dict], set]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=FETCH_HOURS)
    articles = []
    new_ids: set = set()

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            log.info(f"📡 {source_name}")
            feed = feedparser.parse(feed_url, request_headers={"User-Agent": "Mozilla/5.0"})

            for entry in feed.entries[:15]:
                title = entry.get("title", "").strip()
                url   = entry.get("link", "").strip()
                if not title or not url:
                    continue

                # 时间解析
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

                aid    = make_id(url, title)
                is_new = aid not in seen
                new_ids.add(aid)

                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:300]

                articles.append({
                    "id":      aid,
                    "source":  source_name,
                    "title":   title,
                    "url":     url,
                    "summary": summary,
                    "pub":     pub.astimezone(TZ).strftime("%m-%d %H:%M") if pub else "??",
                    "is_new":  is_new,
                })
        except Exception as e:
            log.warning(f"  ⚠️ {source_name} 抓取失败: {e}")

    log.info(f"✅ 共抓取 {len(articles)} 篇，其中 🆕 新增 {sum(a['is_new'] for a in articles)} 篇")
    return articles, new_ids

# ════════════════════════════════════════════════════════
# ❺ Gemini AI 分析
# ════════════════════════════════════════════════════════

SYSTEM_INSTRUCTION = (
    "你现在是一位拥有20年华尔街经验的宏观分析师，专门服务于追求FIRE（提前退休）的极简长线投资者。"
    "请阅读以下新闻，进行极简降噪处理。"
    "强制要求：1. 只提取对美元流动性、美联储政策预期、以及北美科技龙头有实质性影响的核心数据。"
    "2. 彻底剔除市场噪音和短线博弈废话。"
    "3. 宁缺毋滥，如果新闻没有实质性宏观价值，请直接返回'今日无核心异动信号'。"
)


def build_prompt(articles: list[dict]) -> str:
    now_str    = datetime.now(tz=TZ).strftime("%Y年%m月%d日 %H:%M")
    news_block = ""
    for i, a in enumerate(articles[:MAX_ARTICLES_AI], 1):
        tag = "🆕" if a["is_new"] else "  "
        news_block += f"{i}. {tag}[{a['source']}] {a['title']}\n"
        if a["summary"]:
            news_block += f"   {a['summary']}\n"
        news_block += "\n"

    return f"""当前时间：{now_str}（北京时间）

【我的持仓标的】
{POSITIONS.strip()}

【今日财经新闻（{len(articles[:MAX_ARTICLES_AI])} 条）】
{news_block}
---
请严格按照以下四层结构输出分析报告，使用中文，风格极简、直接、无废话：

## 第一层：宏观框架
（今日最重要的 1-2 个市场主题，每个主题不超过 3 句话）

## 第二层：逐标的影响
（对每个持仓标的给出：方向判断 偏多/偏空/中性 + 核心理由，无相关新闻则略去）

## 第三层：跨资产联动
（描述今日最显著的跨资产传导链，例如：美债收益率→科技股→BTC→黄金）

## 第四层：今日最需关注的一件事
（只说一件，给出具体的观察指标或时间节点）"""


def call_gemini(articles: list[dict]) -> str:
    if not articles:
        return "今日无新文章可分析。"

    log.info("🤖 调用 Gemini API 进行深度分析...")
    log.info("⏳ 等待 15 秒...")
    time.sleep(15)

    for attempt in range(3):
        try:
            client   = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=build_prompt(articles),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.1,
                    max_output_tokens=2048,
                ),
            )
            log.info("✅ Gemini 分析完成")
            return response.text

        except Exception as e:
            err_str = str(e)

            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if attempt < 2:
                    wait = 60 * (attempt + 2)   # 第1次重试等120s，第2次180s
                    log.warning(f"⚠️ 触发频率限制，{wait} 秒后重试（第 {attempt + 1} 次）...")
                    time.sleep(wait)
                else:
                    log.error("❌ 三次重试均失败")
                    return (
                        "⚠️ **Gemini API 频率超限**\n"
                        "已自动重试 3 次仍失败，请稍后手动重新运行。\n"
                        "或前往 https://aistudio.google.com/app/apikey 检查配额状态。"
                    )

            elif "API_KEY_INVALID" in err_str or "403" in err_str:
                log.error("❌ API Key 无效")
                return (
                    "⚠️ **Gemini API Key 无效**\n"
                    "请前往 https://aistudio.google.com/app/apikey 重新生成 Key，"
                    "并更新环境变量 GEMINI_API_KEY。"
                )

            else:
                log.error(f"❌ Gemini 未知错误: {e}")
                return f"⚠️ **AI 分析失败**：{err_str[:120]}"

    return "⚠️ 多次重试后仍失败，请稍后再运行。"

# ════════════════════════════════════════════════════════
# ❻ Discord 推送（自动分段，绕过 2000 字符限制）
# ════════════════════════════════════════════════════════

def send_discord(content: str, title_line: str):
    if not DISCORD_WEBHOOK or "webhooks/xxx" in DISCORD_WEBHOOK:
        log.warning("⚠️ 未配置 DISCORD_WEBHOOK，跳过推送")
        return

    header = (
        f"# 📊 {title_line}\n"
        f"> 🤖 Powered by Gemini 3.1 Pro · 持仓分析 · 自动去重\n"
        f"{'─' * 40}"
    )
    _discord_send(header)

    for chunk in _split_discord(content, max_len=1900):
        _discord_send(chunk)

    log.info("🚀 Discord 推送完成")


def _discord_send(text: str):
    resp = requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=10)
    if resp.status_code not in (200, 204):
        log.warning(f"Discord 返回 {resp.status_code}: {resp.text[:200]}")


def _split_discord(text: str, max_len: int = 1900) -> list[str]:
    lines = text.splitlines(keepends=True)
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
# ❼ 保存到本地文件
# ════════════════════════════════════════════════════════

def save_report(analysis: str, articles: list[dict]) -> Path:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    now_str  = datetime.now(tz=TZ).strftime("%Y%m%d_%H%M")
    filepath = DOWNLOADS_DIR / f"{OUTPUT_PREFIX}_{now_str}.txt"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("财经新闻AI分析报告\n")
        f.write(f"生成时间：{datetime.now(tz=TZ).strftime('%Y年%m月%d日 %H:%M')}（北京时间）\n")
        f.write("=" * 60 + "\n\n")
        f.write("【AI 深度分析】\n\n")
        f.write(analysis)
        f.write("\n\n" + "=" * 60 + "\n\n")
        f.write(f"【原始新闻列表（共 {len(articles)} 条）】\n\n")
        for a in articles:
            tag = "🆕" if a["is_new"] else "  "
            f.write(f"{tag} [{a['source']}] {a['pub']}  {a['title']}\n")
            f.write(f"   {a['url']}\n\n")

    log.info(f"💾 报告已保存：{filepath}")
    return filepath

# ════════════════════════════════════════════════════════
# ❽ 主流程
# ════════════════════════════════════════════════════════

def main():
    now_label = datetime.now(tz=TZ).strftime("%Y-%m-%d %H:%M")
    log.info("=" * 50)
    log.info(f"🚀 财经新闻机器人启动  {now_label}")

    seen = load_seen()

    # 抓取
    articles, new_ids = fetch_articles(seen)

    if not articles:
        log.info("📭 没有符合时间窗口的文章")
        return

    # 终端展示新文章
    new_articles = [a for a in articles if a["is_new"]]
    if new_articles:
        print(f"\n{'─'*50}")
        print(f"🆕 本次新增 {len(new_articles)} 篇：")
        for a in new_articles[:20]:
            print(f"  [{a['source']}] {a['title'][:60]}")
        print(f"{'─'*50}\n")

    # AI 分析
    analysis = call_gemini(articles)

    # 终端输出
    print("\n" + "═" * 60)
    print("📊 AI 深度分析报告")
    print("═" * 60)
    print(analysis)
    print("═" * 60 + "\n")

    # 保存文件
    save_report(analysis, articles)

    # Discord 推送
    title_line = f"财经日报  {now_label}  🆕{len(new_articles)}条新闻"
    send_discord(analysis, title_line)

    # 更新已读记录
    seen.update(new_ids)
    save_seen(seen)

    log.info("✅ 本次运行完成")


if __name__ == "__main__":
    main()
