#!/usr/bin/env python3
"""
Daily Market News Telegram Bot — Enhanced Edition
Config file format (config.json):
{
    "telegram_chat_id": "YOUR_CHAT_ID",
    "telegram_bot_token": "YOUR_BOT_TOKEN"
}
Usage:  python market_news_bot.py
"""

import os
import json
import argparse
import datetime
import pytz
import requests
import yfinance as yf


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    cfg = {}
    if os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)

    # Environment variables override / supplement the config file
    # (used in GitHub Actions via repo secrets)
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    env_chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if env_token:
        cfg["telegram_bot_token"] = env_token
    if env_chat:
        cfg["telegram_chat_id"] = env_chat

    missing = {"telegram_chat_id", "telegram_bot_token"} - cfg.keys()
    if missing:
        raise ValueError(f"Missing keys in config: {missing}")
    return cfg


# ─── Escape ───────────────────────────────────────────────────────────────────

def esc(text: str) -> str:
    for ch in ['\\', '_', '*', '[', ']', '(', ')', '~', '`', '>',
               '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, '\\' + ch)
    return text


# ─── Tickers ──────────────────────────────────────────────────────────────────

NSE_TICKERS = {
    "NIFTY 50":   "^NSEI",
    "SENSEX":     "^BSESN",
    "NIFTY BANK": "^NSEBANK",
}
NYSE_TICKERS = {
    "Dow Jones": "^DJI",
    "S&P 500":   "^GSPC",
    "Nasdaq":    "^IXIC",
    "VIX":       "^VIX",
}
NSE_SECTORS = {
    "IT":      "^CNXIT",
    "Bank":    "^NSEBANK",
    "Pharma":  "^CNXPHARMA",
    "Auto":    "^CNXAUTO",
    "FMCG":    "^CNXFMCG",
    "Energy":  "^CNXENERGY",
}
NYSE_SECTORS = {
    "Tech":       "XLK",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Energy":     "XLE",
    "Industrials":"XLI",
    "Consumer":   "XLY",
}
GLOBAL_CUES = {
    "SGX Nifty":    "^NSEI",       # Nifty as SGX proxy
    "Dow Futures":  "YM=F",
    "US 10Y":       "^TNX",
    "Crude Oil":    "CL=F",        # WTI Crude (USD)
    "Gold (USD)":   "GC=F",        # Comex Gold
    "Silver (USD)": "SI=F",        # Comex Silver
    "USDINR":       "INR=X",       # USD/INR rate
    "Gold India":   "GOLDBEES.NS", # NSE Gold ETF (INR)
    "Silver India": "SILVERBEES.NS",# NSE Silver ETF (INR)
}
# Global Market Pulse — Asia + Europe
ASIA_EUROPE_CUES = {
    "Nikkei 225": "^N225",
    "Hang Seng":  "^HSI",
    "Shanghai":   "000001.SS",
    "DAX":        "^GDAXI",
    "FTSE 100":   "^FTSE",
    "CAC 40":     "^FCHI",
    "Nasdaq Fut": "NQ=F",
    "S&P Fut":    "ES=F",
}
TOP_NSE_STOCKS  = [
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS",
    "WIPRO.NS","HCLTECH.NS","SBIN.NS","BAJFINANCE.NS","ADANIENT.NS",
]
TOP_NYSE_STOCKS = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL",
    "META","TSLA","JPM","V","UNH",
]
INDIA_VIX = "^INDIAVIX"
US_VIX    = "^VIX"


# ─── Data fetchers ────────────────────────────────────────────────────────────

def get_ticker(symbol: str, period: str = "2d") -> dict | None:
    try:
        hist = yf.Ticker(symbol).history(period=period)
        if len(hist) < 1:
            return None
        close = hist["Close"].iloc[-1]
        prev  = hist["Close"].iloc[-2] if len(hist) >= 2 else close
        change = close - prev
        pct    = (change / prev * 100) if prev else 0
        return {"close": close, "change": change, "pct": pct}
    except Exception:
        return None


def get_index_data(tickers: dict) -> list:
    rows = []
    for name, symbol in tickers.items():
        d = get_ticker(symbol)
        if d:
            rows.append({"name": name, **d})
        else:
            rows.append({"name": name, "error": True})
    return rows


def calc_rsi(symbol: str, period: int = 14) -> float | None:
    """Calculate RSI using last 30 days of closes."""
    try:
        hist = yf.Ticker(symbol).history(period="30d")
        closes = hist["Close"].tolist()
        if len(closes) < period + 1:
            return None
        deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains  = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs  = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 1)
    except Exception:
        return None


def rsi_tag(rsi: float | None) -> str:
    if rsi is None:
        return ""
    if rsi >= 70:
        return "⚠️OB"   # Overbought
    elif rsi <= 30:
        return "💡OS"   # Oversold
    return ""


def get_sector_data(sectors: dict) -> list:
    rows = []
    for name, symbol in sectors.items():
        d = get_ticker(symbol)
        if d:
            rsi = calc_rsi(symbol)
            rows.append({"name": name, "pct": d["pct"], "rsi": rsi})
    return rows


def get_global_cues() -> list:
    rows = []
    for name, symbol in GLOBAL_CUES.items():
        try:
            d = get_ticker(symbol)
            if d and d["close"] and d["close"] > 0:
                rows.append({"name": name, "close": d["close"], "pct": d["pct"]})
        except Exception:
            pass  # silently skip any unavailable symbol
    return rows


def get_gainers_losers(symbols: list) -> tuple:
    movers = []
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period="2d")
            if len(hist) < 2:
                continue
            close = hist["Close"].iloc[-1]
            prev  = hist["Close"].iloc[-2]
            pct   = ((close - prev) / prev * 100) if prev else 0
            info  = yf.Ticker(sym).info
            name  = info.get("shortName") or sym
            movers.append({"name": name, "symbol": sym, "pct": pct})
        except Exception:
            pass
    movers.sort(key=lambda x: x.get("pct", 0), reverse=True)
    gainers = movers[:3]
    losers  = movers[-3:][::-1]
    return gainers, losers


def get_vix_level(symbol: str) -> float | None:
    d = get_ticker(symbol)
    return d["close"] if d else None


def get_sparkline(symbol: str, days: int = 7) -> str:
    """Return a mini trend string from recent closes using arrows."""
    try:
        hist = yf.Ticker(symbol).history(period=f"{days}d")
        closes = list(hist["Close"])
        if len(closes) < 2:
            return ""
        recent = closes[-6:]
        trend = ""
        for i in range(1, len(recent)):
            diff = recent[i] - recent[i - 1]
            if diff > 0:
                trend += "↑"
            elif diff < 0:
                trend += "↓"
            else:
                trend += "→"
        return trend
    except Exception:
        return ""


# ─── News ─────────────────────────────────────────────────────────────────────

def fetch_headlines(url: str, max_items: int = 25) -> list:
    """Parse RSS feed properly — extracts <item> titles, handles CDATA."""
    try:
        import re
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })
        if resp.status_code != 200:
            return []

        # Extract only <item> blocks — avoids picking up the feed-level <title>
        items = re.findall(r"<item[^>]*>(.*?)</item>", resp.text, re.DOTALL)
        out = []
        for item in items[:max_items]:
            m = re.search(r"<title[^>]*>(.*?)</title>", item, re.DOTALL)
            if not m:
                continue
            t = m.group(1).strip()
            # Unwrap CDATA
            cdata = re.match(r"<!\[CDATA\[(.*?)\]\]>", t, re.DOTALL)
            if cdata:
                t = cdata.group(1).strip()
            # Decode HTML entities
            t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            t = t.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
            t = re.sub(r"&#\d+;", "", t)
            # Strip any remaining tags
            t = re.sub(r"<[^>]+>", "", t)
            t = re.sub(r"\s+", " ", t).strip()
            # Skip feed-level titles or empty
            if t and len(t) > 15:
                out.append(t)
        return out
    except Exception:
        return []


MARKET_RELEVANT_KEYWORDS = [
    "stock", "stocks", "share", "shares", "market", "markets", "sensex", "nifty",
    "nasdaq", "dow", "s&p", "sec ", "ipo", "earnings", "profit", "revenue",
    "results", "quarterly", "q1", "q2", "q3", "q4", "rbi", "fed", "rate",
    "inflation", "gdp", "economy", "fiscal", "budget", "rupee", "dollar",
    "investor", "investors", "trading", "bse", "nse", "wall street", "rally",
    "selloff", "sell-off", "fii", "dii", "mutual fund", "bond", "yield",
    "merger", "acquisition", "stake", "listing", "delisted", "valuation",
    "tariff", "export", "import", "crude", "gold", "rbi", "bank", "banks",
    "rbi", "sebi", "fund", "funds", "index", "indices", "futures", "treasury"
]


US_ONLY_KEYWORDS = [
    "us stocks", "u.s. stocks", "wall street", "nasdaq", "dow jones", "dow soars",
    "dow falls", "s&p 500", "fed ", "federal reserve", "white house",
    "treasury", "nyse", "spacex", "trump", "biden",
]

INDIA_ONLY_KEYWORDS = [
    "sensex", "nifty", "rbi", "rupee", "bse", "nse ", "sebi", "fii", "dii",
    "india", "indian", "mumbai", "delhi", "crore", "lakh",
]


def is_market_relevant(h: str, region: str | None = None) -> bool:
    h_lower = h.lower()
    if not any(kw in h_lower for kw in MARKET_RELEVANT_KEYWORDS):
        return False
    if region == "india" and any(kw in h_lower for kw in US_ONLY_KEYWORDS):
        return False
    if region == "us" and any(kw in h_lower for kw in INDIA_ONLY_KEYWORDS):
        return False
    return True


def get_headlines(feeds: list, max_total: int = 10, region: str | None = None) -> list:
    seen, result = set(), []
    for feed in feeds:
        for h in fetch_headlines(feed):
            if h not in seen and is_market_relevant(h, region) and len(result) < max_total:
                seen.add(h)
                result.append(h)
    return result


def categorize_headline(h: str) -> str:
    h_lower = h.lower()
    if any(w in h_lower for w in ["fed", "rbi", "gdp", "inflation", "rate", "economy", "fiscal", "budget"]):
        return "Macro"
    if any(w in h_lower for w in ["earnings", "profit", "revenue", "results", "quarterly", "q1","q2","q3","q4"]):
        return "Earnings"
    if any(w in h_lower for w in ["ai", "tech", "software", "chip", "openai", "nvidia","cloud","cyber"]):
        return "Tech/AI"
    if any(w in h_lower for w in ["war", "iran", "china", "sanctions", "geopolit", "conflict", "israel","russia"]):
        return "Geopolitics"
    return "Market"


NSE_FEEDS = [
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/marketreports.xml",
]
NYSE_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
]
NSE_SPECIALIST_FEEDS = {
    "fii_dii":      [
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://www.moneycontrol.com/rss/fii-dii-activity.xml",
    ],
    "bulk_block":   [
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        "https://www.moneycontrol.com/rss/bulk-deal.xml",
    ],
    "insider":      [
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    ],
    "ipo":          [
        "https://economictimes.indiatimes.com/markets/ipos/rssfeeds/3624960.cms",
        "https://www.chittorgarh.com/rss/ipo.asp",
    ],
    "corp_actions": [
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    ],
    "earnings":     [
        "https://economictimes.indiatimes.com/markets/earnings/rssfeeds/2143429.cms",
        "https://www.moneycontrol.com/rss/results.xml",
    ],
    "regulatory":   [
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://www.moneycontrol.com/rss/marketreports.xml",
    ],
}


# ─── Analysis helpers ─────────────────────────────────────────────────────────

def sentiment_bar(vix: float | None, avg_pct: float) -> str:
    """Return bullish/neutral/bearish tag based on VIX + avg index move."""
    if vix is None:
        score = avg_pct
    else:
        score = avg_pct - (vix / 20)
    if score > 0.3:
        return "🟢 Bullish"
    elif score < -0.3:
        return "🔴 Bearish"
    else:
        return "🟡 Neutral"


def vix_tag(vix: float | None) -> str:
    if vix is None:
        return "N/A"
    if vix < 15:
        return "🟢 Low Volatility"
    elif vix < 25:
        return "🟡 Medium Volatility"
    else:
        return "🔴 High Risk"


def market_regime(avg_pct: float, vix: float | None) -> str:
    vix_val = vix or 20
    if avg_pct > 0.8 and vix_val < 20:
        return "Trending Up 📈"
    elif avg_pct < -0.8 and vix_val < 20:
        return "Trending Down 📉"
    elif vix_val > 25:
        return "High Volatility ⚡"
    elif abs(avg_pct) < 0.3:
        return "Range\\-Bound ↔️"
    else:
        return "Choppy 〰️"


def market_status(tz_name: str, open_h: int, open_m: int, close_h: int, close_m: int) -> str:
    now = datetime.datetime.now(pytz.timezone(tz_name))
    t   = now.hour * 60 + now.minute
    op  = open_h  * 60 + open_m
    cl  = close_h * 60 + close_m
    pre = op - 75
    if t < pre:
        return "Closed"
    elif t < op:
        return "Pre\\-Market"
    elif t <= cl:
        return "🟢 Open"
    else:
        return "Closed"




# ─── New category fetchers ────────────────────────────────────────────────────

def get_global_pulse() -> list:
    """Asia + Europe + US Futures for Global Market Pulse section."""
    rows = []
    for name, symbol in ASIA_EUROPE_CUES.items():
        try:
            d = get_ticker(symbol)
            if d and d["close"] and d["close"] > 0:
                rows.append({"name": name, "close": d["close"], "pct": d["pct"]})
        except Exception:
            pass
    return rows


def get_volume_spikes(symbols: list, threshold: float = 2.0) -> list:
    """Return stocks where today vol > threshold × 20-day average."""
    spikes = []
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period="25d")
            if len(hist) < 21:
                continue
            avg_vol   = hist["Volume"].iloc[:-1].mean()
            today_vol = hist["Volume"].iloc[-1]
            if avg_vol > 0 and today_vol / avg_vol >= threshold:
                close = hist["Close"].iloc[-1]
                pct   = hist["Close"].pct_change().iloc[-1] * 100
                info  = yf.Ticker(sym).info
                name  = info.get("shortName") or sym
                spikes.append({
                    "name":   name,
                    "symbol": sym,
                    "close":  close,
                    "pct":    pct,
                    "vol_x":  round(today_vol / avg_vol, 1),
                })
        except Exception:
            pass
    spikes.sort(key=lambda x: x["vol_x"], reverse=True)
    return spikes[:5]


def get_sector_buzz(sector_rows: list, top_n: int = 2) -> dict:
    """Top N bullish and bearish sectors from already-fetched sector data."""
    sorted_s = sorted(sector_rows, key=lambda x: x.get("pct", 0), reverse=True)
    return {
        "bullish": sorted_s[:top_n],
        "bearish": sorted_s[-top_n:][::-1],
    }


def get_specialist_headlines(category: str, max_items: int = 5,
                              region: str | None = None) -> list:
    """Fetch specialist headlines for a named category."""
    feeds = NSE_SPECIALIST_FEEDS.get(category, [])
    kw_map = {
        "fii_dii":      ["fii", "dii", "foreign institutional", "domestic institutional",
                         "net buy", "net sell", "flows"],
        "bulk_block":   ["bulk deal", "block deal", "bulk", "block", "institution"],
        "insider":      ["promoter", "insider", "director", "management", "bought", "sold"],
        "ipo":          ["ipo", "gmp", "subscription", "listing", "grey market", "allotment"],
        "corp_actions": ["bonus", "split", "buyback", "dividend", "rights issue", "record date"],
        "earnings":     ["earnings", "profit", "revenue", "results", "q1", "q2", "q3", "q4",
                         "beat", "miss", "guidance", "ebitda", "pat"],
        "regulatory":   ["sebi", "rbi", "fed", "opec", "circular", "guideline", "regulation",
                         "penalty", "notice", "norms"],
    }
    keywords = kw_map.get(category, [])
    seen, result = set(), []
    for feed in feeds:
        for h in fetch_headlines(feed, max_items=30):
            h_low = h.lower()
            if h not in seen and any(kw in h_low for kw in keywords) and len(result) < max_items:
                seen.add(h)
                result.append(h)
    return result


def tag_earnings_headline(h: str) -> str:
    h_low = h.lower()
    if any(w in h_low for w in ["beat", "above estimate", "beats"]):
        return "✅ Beat"
    if any(w in h_low for w in ["miss", "below estimate", "misses", "disappoints"]):
        return "❌ Miss"
    if any(w in h_low for w in ["guidance", "outlook", "forecast", "projects"]):
        return "🔮 Guidance"
    return "📋 Result"

def arrow(pct: float) -> str:
    return "🟢" if pct >= 0 else "🔴"


def sector_dot(pct: float) -> str:
    if pct > 0.2:
        return "🟢"
    elif pct < -0.2:
        return "🔴"
    return "🟡"


# ─── News formatting helpers ──────────────────────────────────────────────────

# Maps keyword → (emoji, label) context tag
_CONTEXT_TAG_MAP = [
    # Regulation / legal
    (["sebi", "sec ", "regulation", "norms", "compliance", "penalty", "case"],        "⚖️ Regulation"),
    # Macro / central bank
    (["rbi", "fed", "rate", "inflation", "gdp", "budget", "fiscal", "repo"],          "🏦 Macro"),
    # FX / currency
    (["rupee", "dollar", "usdinr", "forex", "fx", "currency"],                        "💵 FX"),
    # Commodities
    (["crude", "oil", "gold", "silver", "commodity", "commodities"],                  "🛢 Commodity"),
    # Real estate / infra
    (["property", "real estate", "infra", "hotel", "housing", "realty", "reit"],      "🏗️ Real estate"),
    # IPO / listing
    (["ipo", "listing", "unlisted", "pre-ipo"],                                        "📋 IPO"),
    # Earnings / results
    (["earnings", "profit", "revenue", "results", "quarterly", "q1","q2","q3","q4"],  "📊 Earnings"),
    # Key events / AGM
    (["agm", "concall", "investor day", "merger", "acquisition", "stake"],             "🔍 Key event"),
    # Geopolitics
    (["iran", "china", "russia", "war", "sanctions", "israel", "deal", "geo"],        "🌍 Geo"),
    # Tech / AI
    (["ai", "chip", "cloud", "software", "nvidia", "openai", "cyber", "tech"],        "💡 Tech"),
    # Banking / financial sector
    (["bank", "banks", "nbfc", "npa", "credit", "loan", "deposit"],                   "🏦 Banking"),
    # Manufacturing / industrial
    (["auto", "ev", "electrical", "machinery", "manufacturing", "plant"],              "🏭 Industry"),
    # Stocks / picks
    (["stock ideas", "buy", "target price", "analyst", "picks", "watchlist"],          "📌 Stocks"),
    # Quote / wisdom
    (["quote", "said", "says", '"', "—"],                                              "💬 Quote"),
]

_CAT_DEFAULT_TAG = {
    "Macro":       "🏦 Macro",
    "Earnings":    "💰 Earnings",
    "Tech/AI":     "💡 Tech",
    "Geopolitics": "🌍 Geo",
    "Market":      "📌 Markets",
}

_CAT_HEADER = {
    "Tech/AI":     "🧠 *TECH \\& AI*",
    "Macro":       "📊 *MACRO*",
    "Earnings":    "💰 *EARNINGS*",
    "Geopolitics": "🌍 *GEOPOLITICS*",
    "Market":      "📈 *MARKET*",
}


def get_context_tag(headline: str, category: str) -> str:
    """Return a short emoji context tag for a headline."""
    h = headline.lower()
    for keywords, tag in _CONTEXT_TAG_MAP:
        if any(kw in h for kw in keywords):
            return tag
    return _CAT_DEFAULT_TAG.get(category, "📌 Markets")


def format_news_section(categorized: dict) -> list:
    """
    Build punchy, trader-friendly news lines:
      🧠 *TECH & AI*
      • Headline text  | 🔍 Key event
    """
    lines = []
    # Preferred display order
    order = ["Tech/AI", "Macro", "Market", "Earnings", "Geopolitics"]
    sorted_cats = [c for c in order if c in categorized] + \
                  [c for c in categorized if c not in order]

    for cat in sorted_cats:
        items = categorized[cat]
        header = _CAT_HEADER.get(cat, f"📌 *{esc(cat.upper())}*")
        lines.append(header)
        for h in items:
            tag  = get_context_tag(h, cat)
            lines.append(f"• {esc(h)}  \\|  {esc(tag)}")
        lines.append("")
    return lines


def build_sentiment_summary(categorized: dict, avg_pct: float, vix: float | None) -> list:
    """Optional per-section sentiment tags."""
    lines = ["*📡 Section Sentiment*"]
    base = sentiment_bar(vix, avg_pct)
    for cat in categorized:
        # Simple heuristic: inherit market mood; could be refined per category
        lines.append(f"  {_CAT_DEFAULT_TAG.get(cat, cat)}: {base}")
    lines.append("")
    return lines



# ─── New section formatters ──────────────────────────────────────────────────

def fmt_premarket_cues(global_cues: list, global_pulse: list) -> list:
    """Pre-market cues block: SGX Nifty, Dow Fut, Crude, USD."""
    if not global_cues:
        return []
    priority = {"SGX Nifty", "Dow Futures", "Crude Oil", "USDINR", "US 10Y"}
    lines = ["*🌅 PRE\\-MARKET CUES*"]
    for g in global_cues:
        if g["name"] in priority:
            a    = arrow(g["pct"])
            sign = "+" if g["pct"] >= 0 else ""
            paren_open  = "\\("
            paren_close = "\\)"
            close_fmt = esc(f"{g['close']:,.2f}")
            pct_fmt   = esc(f"{sign}{g['pct']:.2f}%")
            lines.append(
                f"{a} *{esc(g['name'])}:* {close_fmt} "
                f"{paren_open}{pct_fmt}{paren_close}"
            )
    lines.append("")
    return lines


def fmt_global_pulse(global_pulse: list) -> list:
    """Global market pulse: Asia, Europe, US futures."""
    if not global_pulse:
        return []
    lines = ["*🌍 GLOBAL MARKET PULSE*"]
    groups = [
        ("🌏 Asia",    ["Nikkei 225", "Hang Seng", "Shanghai"]),
        ("🌍 Europe",  ["DAX", "FTSE 100", "CAC 40"]),
        ("🇺🇸 US Fut", ["Nasdaq Fut", "S&P Fut"]),
    ]
    pm = {g["name"]: g for g in global_pulse}
    for label, names in groups:
        parts = []
        for n in names:
            if n in pm:
                g    = pm[n]
                sign = "+" if g["pct"] >= 0 else ""
                dot  = "🟢" if g["pct"] >= 0 else "🔴"
                parts.append(f"{dot} {esc(n.split()[0])}: {esc(f'{sign}{g["pct"]:.1f}%')}")
        if parts:
            lines.append(f"{esc(label)}: {'  '.join(parts)}")
    lines.append("")
    return lines


def fmt_sector_buzz(buzz: dict) -> list:
    """Sector buzz: top strong and weak sectors."""
    if not buzz.get("bullish") and not buzz.get("bearish"):
        return []
    lines = ["*🌡 SECTOR BUZZ*"]
    sep = "\\|"
    for s in buzz.get("bullish", []):
        sign = "+" if s["pct"] >= 0 else ""
        pct_s = esc(f"{sign}{s['pct']:.1f}%")
        lines.append(f"🟢 *{esc(s['name'])}*: {pct_s}  {sep}  🔥 Strong")
    for s in buzz.get("bearish", []):
        sign = "+" if s["pct"] >= 0 else ""
        pct_s = esc(f"{sign}{s['pct']:.1f}%")
        lines.append(f"🔴 *{esc(s['name'])}*: {pct_s}  {sep}  🧊 Weak")
    lines.append("")
    return lines


def fmt_fii_dii(headlines: list) -> list:
    if not headlines:
        return []
    lines = ["*💸 FII \\/ DII FLOWS*"]
    for h in headlines:
        lines.append(f"• {esc(h)}")
    lines.append("")
    return lines


def fmt_bulk_block_deals(headlines: list) -> list:
    if not headlines:
        return []
    lines = ["*🏦 BULK \\& BLOCK DEALS*"]
    for h in headlines:
        lines.append(f"• {esc(h)}")
    lines.append("")
    return lines


def fmt_insider_activity(headlines: list) -> list:
    if not headlines:
        return []
    lines = ["*👔 INSIDER ACTIVITY*"]
    for h in headlines:
        h_low = h.lower()
        tag = "🟢 Buy"  if any(w in h_low for w in ["bought","buy","purchase","acquired"]) else               "🔴 Sell" if any(w in h_low for w in ["sold","sell","offload","stake sale"])  else "📋"
        lines.append(f"{tag}  {esc(h)}")
    lines.append("")
    return lines


def fmt_ipo(headlines: list) -> list:
    if not headlines:
        return []
    lines = ["*📋 IPO \\/ LISTING UPDATES*"]
    for h in headlines:
        h_low = h.lower()
        tag = "🚀 Listing" if "listing" in h_low else               "📊 GMP"     if "gmp"     in h_low else               "📝 Sub"     if "subscri" in h_low else "📌"
        lines.append(f"{tag}  {esc(h)}")
    lines.append("")
    return lines


def fmt_corp_actions(headlines: list) -> list:
    if not headlines:
        return []
    lines = ["*🎁 CORPORATE ACTIONS*"]
    tag_map = {
        "bonus":    "🎁 Bonus",
        "split":    "✂️ Split",
        "buyback":  "🔄 Buyback",
        "dividend": "💵 Dividend",
        "rights":   "📋 Rights",
    }
    for h in headlines:
        h_low = h.lower()
        tag = next((v for k, v in tag_map.items() if k in h_low), "📌")
        lines.append(f"{tag}  {esc(h)}")
    lines.append("")
    return lines


def fmt_regulatory_alerts(headlines: list) -> list:
    if not headlines:
        return []
    lines = ["*⚖️ REGULATORY ALERTS*"]
    body_map = [
        ("sebi", "🇮🇳 SEBI"), ("rbi", "🇮🇳 RBI"),
        ("fed",  "🇺🇸 Fed"),  ("opec","🛢 OPEC"), ("sec ","🇺🇸 SEC"),
    ]
    for h in headlines:
        h_low = h.lower()
        body = next((v for k, v in body_map if k in h_low), "📌")
        lines.append(f"{body}  {esc(h)}")
    lines.append("")
    return lines


def fmt_earnings_snapshot(headlines: list) -> list:
    if not headlines:
        return []
    lines = ["*💰 EARNINGS SNAPSHOT*"]
    for h in headlines:
        tag = tag_earnings_headline(h)
        lines.append(f"{tag}  {esc(h)}")
    lines.append("")
    return lines


def fmt_volume_spikes(spikes: list) -> list:
    if not spikes:
        return []
    lines = ["*📡 UNUSUAL VOLUME*"]
    for s in spikes:
        sign  = "+" if s["pct"] >= 0 else ""
        a     = arrow(s["pct"])
        name  = esc(s["name"])
        pct   = esc(f"{sign}{s['pct']:.2f}%")
        vol_x = esc(f"{s['vol_x']}×")
        lines.append(f"{a} *{name}*: {pct}  \\|  Vol: {vol_x} avg")
    lines.append("")
    return lines


# ─── Message builder ─────────────────────────────────────────────────────────

def build_message(
    market: str,
    index_rows: list,
    sector_rows: list,
    gainers: list,
    losers: list,
    headlines: list,
    vix_val: float | None,
    india_vix_val: float | None,
    idx_tickers: dict,
    global_cues: list | None = None,
    global_pulse: list | None = None,
    vol_spikes: list | None = None,
    fii_dii_lines: list | None = None,
    bulk_block_lines: list | None = None,
    insider_lines: list | None = None,
    ipo_lines: list | None = None,
    corp_action_lines: list | None = None,
    regulatory_lines: list | None = None,
    earnings_lines: list | None = None,
) -> str:
    global_cues        = global_cues        or []
    global_pulse       = global_pulse       or []
    vol_spikes         = vol_spikes         or []
    fii_dii_lines      = fii_dii_lines      or []
    bulk_block_lines   = bulk_block_lines   or []
    insider_lines      = insider_lines      or []
    ipo_lines          = ipo_lines          or []
    corp_action_lines  = corp_action_lines  or []
    regulatory_lines   = regulatory_lines   or []
    earnings_lines     = earnings_lines     or []
    today = esc(datetime.date.today().strftime("%d %b %Y"))
    is_nse = "NSE" in market

    # Avg index pct (excluding VIX)
    pcts = [r["pct"] for r in index_rows if "pct" in r and r["name"] != "VIX"]
    avg_pct = sum(pcts) / len(pcts) if pcts else 0

    # VIX for this market
    active_vix = india_vix_val if is_nse else vix_val

    # Time zones
    ist = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
    est = datetime.datetime.now(pytz.timezone("America/New_York"))
    ist_str = esc(ist.strftime("%I:%M %p IST"))
    est_str = esc(est.strftime("%I:%M %p EST"))

    nse_status  = market_status("Asia/Kolkata",     9, 15, 15, 30)
    nyse_status = market_status("America/New_York", 9, 30, 16,  0)

    lines = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        f"📊 *{esc(market)} Market Summary*",
        f"🗓 {today}  \\|  🕐 {ist_str} \\| {est_str}",
        "",
    ]

    # ── Pre-Market Cues ──────────────────────────────────────────────────────────
    lines += fmt_premarket_cues(global_cues, global_pulse)

    # ── Global Market Pulse ───────────────────────────────────────────────────────
    lines += fmt_global_pulse(global_pulse)

    # ── Market status ─────────────────────────────────────────────────────────
    lines += [
        "*🌐 Market Status*",
        f"🇮🇳 NSE:  {nse_status}",
        f"🇺🇸 NYSE: {nyse_status}",
        "",
    ]

    # ── Sentiment + Regime ────────────────────────────────────────────────────
    sentiment = sentiment_bar(active_vix, avg_pct)
    regime    = market_regime(avg_pct, active_vix)
    lines += [
        f"*📡 Market Mood:* {sentiment}",
        f"*🏷 Regime:* {esc(regime)}",
        "",
    ]

    # ── Indices + Sparklines ──────────────────────────────────────────────────
    lines.append("*📈 Indices*")
    ticker_map = idx_tickers
    for row in index_rows:
        if "error" in row:
            lines.append(f"• {esc(row['name'])}: N/A")
            continue
        a    = arrow(row["pct"])
        sign = "+" if row["change"] >= 0 else ""
        sym  = ticker_map.get(row["name"], "")
        spark = esc(get_sparkline(sym)) if sym else ""
        name  = esc(row["name"])
        close = esc(f"{row['close']:,.2f}")
        chg   = esc(f"{sign}{row['change']:,.2f}")
        pct   = esc(f"{sign}{row['pct']:.2f}%")
        lines.append(f"{a} *{name}* {spark}  {close} \\({chg} \\| {pct}\\)")
    lines.append("")

    # ── Volatility ────────────────────────────────────────────────────────────
    lines.append("*⚡ Volatility*")
    if india_vix_val:
        lines.append(f"🇮🇳 India VIX: {esc(f'{india_vix_val:.2f}')}  {vix_tag(india_vix_val)}")
    if vix_val:
        lines.append(f"🇺🇸 US VIX:    {esc(f'{vix_val:.2f}')}  {vix_tag(vix_val)}")
    lines.append("")

    # ── Sector Heat ───────────────────────────────────────────────────────────
    if sector_rows:
        lines.append("*🌡 Sector Heat \\| RSI*")
        for s in sector_rows:
            dot   = sector_dot(s["pct"])
            sign  = "+" if s["pct"] >= 0 else ""
            spct  = esc(f"{sign}{s['pct']:.1f}%")
            sname = esc(s["name"])
            rsi   = s.get("rsi")
            tag   = rsi_tag(rsi)
            rsi_str = esc(f"{rsi}") if rsi is not None else "N/A"
            lines.append(f"{dot} {sname}: {spct}  \\|  RSI: {rsi_str} {tag}")
        lines.append("")

    # ── Sector Buzz ──────────────────────────────────────────────────────────────
    if sector_rows:
        buzz = get_sector_buzz(sector_rows)
        lines += fmt_sector_buzz(buzz)

    # ── Gainers & Losers ──────────────────────────────────────────────────────
    if gainers:
        lines.append("*🚀 Top Gainers*")
        for s in gainers:
            gpct  = esc(f"+{s['pct']:.2f}%")
            gname = esc(s["name"])
            gsym  = esc(s["symbol"])
            lines.append(f"🟢 {gname} \\({gsym}\\): {gpct}")
        lines.append("")
    if losers:
        lines.append("*🔻 Top Losers*")
        for s in losers:
            lpct  = esc(f"{s['pct']:.2f}%")
            lname = esc(s["name"])
            lsym  = esc(s["symbol"])
            lines.append(f"🔴 {lname} \\({lsym}\\): {lpct}")
        lines.append("")

    # ── Unusual Volume ───────────────────────────────────────────────────────────
    lines += fmt_volume_spikes(vol_spikes)

    # ── FII / DII Flows ───────────────────────────────────────────────────────────
    lines += fmt_fii_dii(fii_dii_lines)

    # ── Bulk & Block Deals ────────────────────────────────────────────────────────
    lines += fmt_bulk_block_deals(bulk_block_lines)

    # ── Insider Activity ──────────────────────────────────────────────────────────
    lines += fmt_insider_activity(insider_lines)

    # ── IPO / Listing ─────────────────────────────────────────────────────────────
    lines += fmt_ipo(ipo_lines)

    # ── Corporate Actions ─────────────────────────────────────────────────────────
    lines += fmt_corp_actions(corp_action_lines)

    # ── Regulatory Alerts ─────────────────────────────────────────────────────────
    lines += fmt_regulatory_alerts(regulatory_lines)

    # ── Earnings Snapshot ─────────────────────────────────────────────────────────
    lines += fmt_earnings_snapshot(earnings_lines)

    # ── News by Category ──────────────────────────────────────────────────────
    if headlines:
        categorized: dict = {}
        for h in headlines:
            cat = categorize_headline(h)
            categorized.setdefault(cat, []).append(h)

        # Header + timestamp block
        lines += [
            "*📰 TOP HEADLINES*",
            f"🕒 Updated: {ist_str}",
            "",
        ]

        # Per-section sentiment summary (remove block to disable)
        lines += build_sentiment_summary(categorized, avg_pct, active_vix)

        # Punchy categorised news with context tags
        lines += format_news_section(categorized)

    lines.append(esc("━" * 22))
    return "\n".join(lines)


def build_global_cues_message(global_cues: list) -> str:
    today = esc(datetime.date.today().strftime("%d %b %Y"))
    ist = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
    ist_str = esc(ist.strftime("%I:%M %p IST"))

    lines = [
        "🌍 *Global Cues*",
        f"🗓 {today}  \\|  🕐 {ist_str}",
        "",
    ]
    for g in global_cues:
        a    = arrow(g["pct"])
        sign = "+" if g["pct"] >= 0 else ""
        gname  = esc(g["name"])
        gclose = esc(f"{g['close']:,.2f}")
        gpct   = esc(f"{sign}{g['pct']:.2f}%")
        lines.append(f"{a} {gname}: {gclose} \\({gpct}\\)")
    lines.append("")
    lines.append(esc("━" * 22))
    return "\n".join(lines)


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }, timeout=15)
    if not resp.ok:
        print(f"Telegram error: {resp.status_code} — {resp.text}")
    return resp.ok


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(config: dict, market: str = "both"):
    token   = config["telegram_bot_token"]
    chat_id = str(config["telegram_chat_id"])

    print("Fetching VIX data ...")
    vix_val       = get_vix_level(US_VIX)
    india_vix_val = get_vix_level(INDIA_VIX)

    print("Fetching global cues ...")
    global_cues  = get_global_cues()
    global_pulse = get_global_pulse()

    if global_cues or global_pulse:
        ok = send_telegram(token, chat_id, build_global_cues_message(global_cues))
        print(f"  Global Cues Telegram: {'sent ✓' if ok else 'FAILED ✗'}")

    markets = []
    if market in ("nse", "both"):
        markets.append(("NSE India", NSE_TICKERS, NSE_SECTORS, TOP_NSE_STOCKS, NSE_FEEDS, "india"))
    if market in ("nyse", "both"):
        markets.append(("NYSE/NASDAQ US", NYSE_TICKERS, NYSE_SECTORS, TOP_NYSE_STOCKS, NYSE_FEEDS, "us"))

    for label, idx_tickers, sec_tickers, stock_list, feeds, region in markets:
        print(f"Fetching data for {label} ...")
        idx_data        = get_index_data(idx_tickers)
        sector_rows     = get_sector_data(sec_tickers)
        gainers, losers = get_gainers_losers(stock_list)
        headlines       = get_headlines(feeds, region=region)

        is_nse           = "NSE" in label
        vol_spikes       = get_volume_spikes(stock_list)
        fii_dii_lines    = get_specialist_headlines("fii_dii")     if is_nse else []
        bulk_block_lines = get_specialist_headlines("bulk_block")   if is_nse else []
        insider_lines    = get_specialist_headlines("insider")      if is_nse else []
        ipo_lines        = get_specialist_headlines("ipo")          if is_nse else []
        corp_lines       = get_specialist_headlines("corp_actions") if is_nse else []
        reg_lines        = get_specialist_headlines("regulatory")
        earn_lines       = get_specialist_headlines("earnings")

        print(f"  Vol spikes: {len(vol_spikes)} | FII/DII: {len(fii_dii_lines)} | "
              f"IPO: {len(ipo_lines)} | Earnings: {len(earn_lines)}")

        msg = build_message(
            label, idx_data, sector_rows,
            gainers, losers,
            headlines, vix_val, india_vix_val,
            idx_tickers,
            global_cues       = global_cues,
            global_pulse      = global_pulse,
            vol_spikes        = vol_spikes,
            fii_dii_lines     = fii_dii_lines,
            bulk_block_lines  = bulk_block_lines,
            insider_lines     = insider_lines,
            ipo_lines         = ipo_lines,
            corp_action_lines = corp_lines,
            regulatory_lines  = reg_lines,
            earnings_lines    = earn_lines,
        )
        ok = send_telegram(token, chat_id, msg)
        print(f"  Telegram: {'sent ✓' if ok else 'FAILED ✗'}")


def main():
    parser = argparse.ArgumentParser(description="Market news Telegram bot")
    parser.add_argument("--config", default="config.json", help="Optional config file (falls back to env vars)")
    parser.add_argument("--market", choices=["nse", "nyse", "both"], default="both")
    args = parser.parse_args()
    run(load_config(args.config), args.market)


if __name__ == "__main__":
    main()
