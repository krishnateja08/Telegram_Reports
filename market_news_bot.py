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


def get_sector_5d_pct(symbol: str) -> float | None:
    """Return 5-trading-day percentage change for a sector."""
    try:
        hist = yf.Ticker(symbol).history(period="7d")
        closes = list(hist["Close"])
        if len(closes) < 2:
            return None
        return (closes[-1] - closes[0]) / closes[0] * 100
    except Exception:
        return None


def sector_strength_score(pct_1d: float, pct_5d: float | None, rsi: float | None) -> int:
    """
    Simple 0-100 composite strength score.
    40% weight on 1-day move, 40% on 5-day trend, 20% on RSI proximity to 50.
    Returns integer 0-100.
    """
    score = 50.0  # neutral baseline
    score += pct_1d * 4          # ±1% daily = ±4 pts
    if pct_5d is not None:
        score += pct_5d * 2      # ±1% weekly = ±2 pts
    if rsi is not None:
        score += (rsi - 50) * 0.2  # RSI 70 → +4, RSI 30 → -4
    return max(0, min(100, round(score)))


def get_sector_data(sectors: dict) -> list:
    rows = []
    for name, symbol in sectors.items():
        d = get_ticker(symbol)
        if d:
            rsi    = calc_rsi(symbol)
            pct_5d = get_sector_5d_pct(symbol)
            score  = sector_strength_score(d["pct"], pct_5d, rsi)
            rows.append({"name": name, "pct": d["pct"], "rsi": rsi,
                         "pct_5d": pct_5d, "score": score})
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
    seen_syms: set = set()  # FIX #1 — deduplicate tickers before sorting
    for sym in symbols:
        if sym in seen_syms:
            continue
        try:
            hist = yf.Ticker(sym).history(period="25d")
            if len(hist) < 2:
                continue
            close    = hist["Close"].iloc[-1]
            prev     = hist["Close"].iloc[-2]
            pct      = ((close - prev) / prev * 100) if prev else 0
            # Volume vs 20-day average
            avg_vol  = hist["Volume"].iloc[:-1].mean() if len(hist) > 1 else 0
            today_vol = hist["Volume"].iloc[-1]
            vol_x    = round(today_vol / avg_vol, 1) if avg_vol > 0 else None
            info     = yf.Ticker(sym).info
            name     = info.get("shortName") or sym
            movers.append({"name": name, "symbol": sym, "pct": pct, "vol_x": vol_x})
            seen_syms.add(sym)
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


# ─── Impact scoring ───────────────────────────────────────────────────────────

_HIGH_IMPACT_KW = [
    "fed", "rbi", "sebi", "rate hike", "rate cut", "emergency", "crash", "halt",
    "gdp", "inflation", "recession", "default", "collapse", "war", "sanctions",
    "quarterly results", "earnings beat", "earnings miss", "ipo listing",
]
_MEDIUM_IMPACT_KW = [
    "merger", "acquisition", "stake sale", "buyback", "guidance", "outlook",
    "crude", "gold", "opec", "tariff", "export", "import", "budget", "fiscal",
    "fii", "dii", "bulk deal", "block deal", "insider",
]


def news_impact_score(headline: str) -> str:
    """Return 🔥 High / ⚠️ Medium / 💤 Low impact tag for a headline."""
    h = headline.lower()
    if any(kw in h for kw in _HIGH_IMPACT_KW):
        return "🔥 High"
    if any(kw in h for kw in _MEDIUM_IMPACT_KW):
        return "⚠️ Medium"
    return "💤 Low"


# ─── "Why it moved" one-liner ────────────────────────────────────────────────

_MOVE_REASONS: list[tuple[list[str], str]] = [
    (["result", "earnings", "profit", "pat", "revenue", "q1","q2","q3","q4"], "Earnings catalyst"),
    (["buy", "target", "upgrade", "outperform", "overweight"],                "Analyst upgrade"),
    (["sell", "downgrade", "underperform", "underweight", "cut"],             "Analyst downgrade"),
    (["deal", "merger", "acqui", "stake", "takeover"],                        "M&A activity"),
    (["ipo", "listing", "allot"],                                              "IPO / listing"),
    (["fii", "dii", "foreign buy", "institutional buy"],                      "Institutional buying"),
    (["bonus", "split", "dividend", "buyback"],                                "Corporate action"),
    (["block deal", "bulk deal"],                                              "Block/Bulk deal"),
    (["sebi", "rbi", "regulation", "compliance", "norms"],                    "Regulatory news"),
    (["crude", "oil", "energy", "opec"],                                       "Commodity move"),
    (["dollar", "rupee", "forex", "currency"],                                 "Currency move"),
    (["macro", "gdp", "inflation", "rate"],                                    "Macro data"),
]


def why_moved(name: str, symbol: str, pct: float) -> str:
    """
    Try to fetch recent news for the ticker and derive a one-line reason.
    Falls back to price-action language if nothing is found.
    """
    try:
        ticker = yf.Ticker(symbol)
        news_items = ticker.news or []
        for item in news_items[:5]:
            title = (item.get("title") or item.get("content", {}).get("title", "") or "").lower()
            for keywords, reason in _MOVE_REASONS:
                if any(kw in title for kw in keywords):
                    return reason
    except Exception:
        pass
    # Fallback: pure price-action label
    if abs(pct) >= 5:
        return "Sharp price move — check news"
    if pct > 0:
        return "Broad market strength / momentum"
    return "Broad market weakness / selling"


def trend_tag(pct: float) -> str:
    """Bullish / Bearish / Reversal label for movers."""
    if pct >= 3:
        return "📈 Bullish"
    if pct <= -3:
        return "📉 Bearish"
    if pct >= 1:
        return "↗️ Mildly Bullish"
    if pct <= -1:
        return "↘️ Mildly Bearish"
    return "↔️ Neutral"


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
            tag    = get_context_tag(h, cat)
            impact = news_impact_score(h)
            lines.append(f"• {esc(h)}  \\|  {esc(tag)}  \\|  {esc(impact)}")
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
                pct_str = f'{sign}{g["pct"]:.1f}%'
                parts.append(f"{dot} {esc(n.split()[0])}: {esc(pct_str)}")
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

    # FIX #1 — Deduplicate: same ticker can appear multiple times if it triggers
    # multiple signals. Keep the entry with the highest vol_x per symbol.
    seen: dict = {}
    for s in spikes:
        sym = s["symbol"]
        if sym not in seen or s["vol_x"] > seen[sym]["vol_x"]:
            seen[sym] = s

    for s in seen.values():
        sign  = "+" if s["pct"] >= 0 else ""
        a     = arrow(s["pct"])
        name  = esc(s["name"])
        pct   = esc(f"{sign}{s['pct']:.2f}%")
        vol_x = esc(f"{s['vol_x']}×")
        lines.append(f"{a} *{name}*: {pct}  \\|  Vol: {vol_x} avg")
    lines.append("")
    return lines


# ─── Actionable Insights builder ─────────────────────────────────────────────

def build_actionable_insights(
    sector_rows: list,
    gainers: list,
    losers: list,
    avg_pct: float,
    active_vix: float | None,
    is_nse: bool,
) -> list:
    """
    Generate 3-5 plain-English trade insights from live data.
    Each insight is one punchy line that a trader can act on.
    """
    insights = []

    # 1. Overall market bias
    regime = market_regime(avg_pct, active_vix)
    region = "NSE/India" if is_nse else "US markets"
    if avg_pct > 0.5:
        insights.append(f"📈 {region} leaning *bullish* \\({esc(f'+{avg_pct:.1f}%')} avg\\) — momentum favours longs\\.")
    elif avg_pct < -0.5:
        insights.append(f"📉 {region} under pressure \\({esc(f'{avg_pct:.1f}%')} avg\\) — stay cautious, tighten stops\\.")
    else:
        insights.append(f"↔️ {region} range\\-bound today — wait for a breakout before adding positions\\.")

    # 2. Strongest sector → opportunity
    if sector_rows:
        top = max(sector_rows, key=lambda x: x.get("pct", 0))
        weak = min(sector_rows, key=lambda x: x.get("pct", 0))
        sign_t = "+" if top["pct"] >= 0 else ""
        sign_w = "+" if weak["pct"] >= 0 else ""
        top_pct_str  = esc(f"{sign_t}{top['pct']:.1f}%")
        weak_pct_str = esc(f"{sign_w}{weak['pct']:.1f}%")
        insights.append(
            f"🔥 *{esc(top['name'])}* leading \\({top_pct_str}\\) — "
            f"consider sector ETF or top stocks in this space\\."
        )
        if weak["pct"] < -0.5:
            insights.append(
                f"🧊 *{esc(weak['name'])}* weakest \\({weak_pct_str}\\) — "
                f"avoid fresh longs; watch for reversal signals\\."
            )

    # 3. VIX-based risk call
    if active_vix is not None:
        if active_vix > 25:
            insights.append(
                f"⚡ VIX at {esc(f'{active_vix:.1f}')} — elevated fear\\. "
                f"Reduce position size, prefer hedged plays\\."
            )
        elif active_vix < 14:
            insights.append(
                f"😴 VIX at {esc(f'{active_vix:.1f}')} — very low volatility\\. "
                f"Options premium cheap; consider protective puts before events\\."
            )

    # 4. Top gainer momentum note
    if gainers:
        g = gainers[0]
        reason  = why_moved(g["name"], g["symbol"], g["pct"])
        g_pct_s = esc(f"+{g['pct']:.1f}%")
        insights.append(
            f"🚀 *{esc(g['name'])}* surging {g_pct_s} on *{esc(reason)}* — "
            f"momentum trade valid above today's open\\."
        )

    # 5. Top loser caution note
    if losers:
        l = losers[0]
        reason  = why_moved(l["name"], l["symbol"], l["pct"])
        l_pct_s = esc(f"{l['pct']:.1f}%")
        insights.append(
            f"⚠️ *{esc(l['name'])}* down {l_pct_s} \\({esc(reason)}\\) — "
            f"avoid catching the falling knife; wait for stabilisation\\."
        )

    if not insights:
        return []
    lines = ["*💡 ACTIONABLE INSIGHTS*"]
    for i, ins in enumerate(insights, 1):
        lines.append(f"{i}\\. {ins}")
    lines.append("")
    return lines


# ─── Alerts & Risks builder ──────────────────────────────────────────────────

def build_alerts_risks(
    regulatory_lines: list,
    earnings_lines: list,
    active_vix: float | None,
    is_nse: bool,
) -> list:
    """Compact Alerts & Risks block: regulatory, earnings calendar, VIX warning."""
    lines = ["*🚨 ALERTS \\& RISKS*"]
    added = 0

    # VIX extreme alert
    if active_vix is not None and active_vix > 22:
        lines.append(f"⚡ VIX Alert: {esc(f'{active_vix:.1f}')} — high volatility regime\\. Risk management critical\\.")
        added += 1

    # Today's regulatory alerts (top 2)
    for h in regulatory_lines[:2]:
        lines.append(f"⚖️ {esc(h)}")
        added += 1

    # Upcoming earnings (top 3)
    if earnings_lines:
        lines.append("📅 *Earnings on radar:*")
        for h in earnings_lines[:3]:
            tag = tag_earnings_headline(h)
            lines.append(f"  {tag}  {esc(h)}")
        added += 1

    # Generic macro risk reminder
    if is_nse:
        lines.append("🇮🇳 Watch: RBI policy calendar, SEBI circulars, F&O expiry dates\\.")
    else:
        lines.append("🇺🇸 Watch: Fed meeting schedule, CPI/PPI dates, earnings season\\.")
    added += 1

    if added == 0:
        return []
    lines.append("")
    return lines


# ─── One-line market snapshot ─────────────────────────────────────────────────

def build_one_liner(
    avg_pct: float,
    active_vix: float | None,
    global_cues: list,
    sector_rows: list,
    is_nse: bool,
) -> list:
    """
    Single-line market snapshot: global cues + VIX mood + expected open bias.
    Example: "Global cues flat, US tech weak, India VIX low — expect range-bound open."
    """
    parts = []

    # Global cue flavour
    cue_names = {g["name"]: g["pct"] for g in global_cues}
    if is_nse:
        sgx = cue_names.get("SGX Nifty")
        if sgx is not None:
            parts.append(f"SGX Nifty {'positive' if sgx >= 0 else 'negative'}")
        dow = cue_names.get("Dow Futures")
        if dow is not None:
            parts.append(f"Dow Fut {'up' if dow >= 0 else 'down'}")
    else:
        sp = cue_names.get("S&P Fut") or avg_pct
        parts.append(f"S&P Fut {'positive' if sp >= 0 else 'negative'}")

    # VIX flavour
    if active_vix is not None:
        if active_vix < 15:
            parts.append("VIX low")
        elif active_vix > 25:
            parts.append("VIX elevated")
        else:
            parts.append("VIX moderate")

    # Sector bias
    if sector_rows:
        top = max(sector_rows, key=lambda x: x.get("pct", 0))
        parts.append(f"{top['name']} leading")

    # Open bias
    if avg_pct > 0.5:
        bias = "expect bullish open"
    elif avg_pct < -0.5:
        bias = "expect cautious open"
    else:
        bias = "expect range-bound open"

    snapshot = ", ".join(parts) + f" — {bias}\\."
    return [f"_{esc(snapshot)}_", ""]


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
    global_cues       = global_cues       or []
    global_pulse      = global_pulse      or []
    regulatory_lines  = regulatory_lines  or []
    earnings_lines    = earnings_lines    or []
    fii_dii_lines     = fii_dii_lines     or []
    ipo_lines         = ipo_lines         or []
    corp_action_lines = corp_action_lines or []
    bulk_block_lines  = bulk_block_lines  or []
    insider_lines     = insider_lines     or []
    vol_spikes        = vol_spikes        or []

    is_nse = "NSE" in market

    # ── Core computed values ──────────────────────────────────────────────────
    pcts    = [r["pct"] for r in index_rows if "pct" in r and r["name"] != "VIX"]
    avg_pct = sum(pcts) / len(pcts) if pcts else 0
    active_vix = india_vix_val if is_nse else vix_val

    now_ist = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
    now_est = datetime.datetime.now(pytz.timezone("America/New_York"))
    now_local = now_ist if is_nse else now_est
    date_str = now_local.strftime("%-d %b")
    time_str = now_local.strftime("%-I:%M %p")

    sentiment = sentiment_bar(active_vix, avg_pct)
    regime    = market_regime(avg_pct, active_vix)

    lines: list[str] = []

    # ══════════════════════════════════════════════════════════════════════════
    # 1. HEADER  📊 Market Snapshot (23 Jun | 7:15 PM)
    # ══════════════════════════════════════════════════════════════════════════
    flag = "🇮🇳" if is_nse else "🇺🇸"
    mkt_label = esc("NSE India" if is_nse else "US Markets")
    lines += [
        f"📊 {flag} *{mkt_label} Snapshot* \\({esc(date_str)} \\| {esc(time_str)}\\)",
        "",
    ]

    # ══════════════════════════════════════════════════════════════════════════
    # 2. MOOD LINE  Mood: Bearish | Regime: Choppy
    #               India VIX: 12.97 (Low) | US VIX: 17.28 (Medium)
    # ══════════════════════════════════════════════════════════════════════════
    # Strip markdown arrows/emojis from sentiment/regime for the compact line
    mood_clean   = sentiment.split(" ", 1)[-1]   # e.g. "Bullish" from "🟢 Bullish"
    regime_clean = regime.replace("\\-", "-")

    vix_parts = []
    if is_nse and india_vix_val:
        vtag = vix_tag(india_vix_val).split(" ", 1)[-1]
        vtag_short = vtag.replace(" Volatility", "").replace(" Risk", "")
        vix_parts.append(esc(f"India VIX: {india_vix_val:.2f} ({vtag_short})"))
    if not is_nse and vix_val:
        vtag = vix_tag(vix_val).split(" ", 1)[-1]
        vtag_short = vtag.replace(" Volatility", "").replace(" Risk", "")
        vix_parts.append(esc(f"US VIX: {vix_val:.2f} ({vtag_short})"))
    # Always show both when both are meaningful (cross-market context)
    if is_nse and vix_val:
        vtag = vix_tag(vix_val).split(" ", 1)[-1]
        vtag_short = vtag.replace(" Volatility", "").replace(" Risk", "")
        vix_parts.append(esc(f"US VIX: {vix_val:.2f} ({vtag_short})"))

    lines.append(
        f"Mood: *{esc(mood_clean)}*  \\|  Regime: *{esc(regime_clean)}*"
    )
    if vix_parts:
        lines.append("  ".join(vix_parts))
    lines.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # 3. GLOBAL CUES  🌍 *Global Cues*
    #    SGX Nifty / Dow Fut / US10Y / Crude / Gold / Silver / USDINR
    # ══════════════════════════════════════════════════════════════════════════
    if global_cues:
        lines.append("🌍 *Global Cues*")
        cue_map = {g["name"]: g for g in global_cues}

        # Futures line (SGX Nifty or Dow Fut + S&P Fut)
        fut_parts = []
        for name in (["SGX Nifty", "Dow Futures"] if is_nse else ["Dow Futures", "S&P Fut"]):
            g = cue_map.get(name)
            if g:
                sign = "+" if g["pct"] >= 0 else ""
                label = name.replace(" Futures", " Fut")
                fut_parts.append(esc(f"{label}: {g['close']:,.0f} ({sign}{g['pct']:.1f}%)"))
        if fut_parts:
            lines.append("  ".join(fut_parts))

        # US 10Y yield
        g = cue_map.get("US 10Y")
        if g:
            lines.append(esc(f"US10Y: {g['close']:.2f}%"))

        # Commodities line
        comm_parts = []
        for name, fmt in [("Crude Oil", "{:.2f}"), ("Gold (USD)", "{:,.0f}"), ("Silver (USD)", "{:.2f}")]:
            g = cue_map.get(name)
            if g:
                label = name.replace(" (USD)", "").replace(" Oil", "")
                comm_parts.append(esc(f"{label}: {fmt.format(g['close'])}"))
        if comm_parts:
            lines.append("  \\|  ".join(comm_parts))

        # USDINR
        g = cue_map.get("USDINR")
        if g:
            sign = "+" if g["pct"] >= 0 else ""
            lines.append(esc(f"USDINR: {g['close']:.2f} ({sign}{g['pct']:.2f}%)"))

        lines.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # 4. INDICES  📈 *Indices*
    #    Dow: +0.29% | S&P 500: -0.37% | Nasdaq: -1.32%
    # ══════════════════════════════════════════════════════════════════════════
    valid_idx = [r for r in index_rows if "pct" in r and r["name"] != "VIX"]
    if valid_idx:
        lines.append("📈 *Indices*")
        for row in valid_idx:
            a    = arrow(row["pct"])
            sign = "+" if row["pct"] >= 0 else ""
            name = esc(row["name"])
            pct  = esc(f"{sign}{row['pct']:.2f}%")
            lines.append(f"{a} {name}: {pct}")
        lines.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # 5. SECTOR HEATMAP  🔥 *Sector Heatmap*
    #    Energy +0.5% (Strong, 5D +1.2%, Score 68)
    # ══════════════════════════════════════════════════════════════════════════
    if sector_rows:
        lines.append("🔥 *Sector Heatmap*")
        for s in sorted(sector_rows, key=lambda x: x.get("pct", 0), reverse=True):
            dot  = sector_dot(s["pct"])
            sign = "+" if s["pct"] >= 0 else ""
            pct  = esc(f"{sign}{s['pct']:.1f}%")
            name = esc(s["name"])

            # Strength label
            score = s.get("score", 50)
            if score >= 65:
                strength = "Strong"
            elif score <= 35:
                strength = "Weak"
            else:
                strength = "Neutral"

            # OB/OS tag
            rsi    = s.get("rsi")
            ob_tag = ""
            if rsi is not None:
                if rsi >= 70:
                    ob_tag = " ⚠️OB"
                elif rsi <= 30:
                    ob_tag = " 💡OS"

            # 5-day trend
            pct_5d = s.get("pct_5d")
            if pct_5d is not None:
                s5sign = "+" if pct_5d >= 0 else ""
                trend5 = esc(f", 5D {s5sign}{pct_5d:.1f}%")
            else:
                trend5 = ""

            lines.append(f"{dot} {name} {pct} \\({esc(strength)}{trend5}\\){ob_tag}")
        lines.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # 6. TOP GAINERS  🏆 *Top Gainers*
    #    JPM +1.92% — Strong earnings outlook  | 2.1× vol
    # ══════════════════════════════════════════════════════════════════════════
    if gainers:
        lines.append("🏆 *Top Gainers*")
        for s in gainers:
            pct    = esc(f"+{s['pct']:.2f}%")
            name   = esc(s["name"])
            reason = esc(why_moved(s["name"], s["symbol"], s["pct"]))
            vol_x  = s.get("vol_x")
            vol_s  = esc(f"  \\|  {vol_x}× vol") if vol_x else ""
            lines.append(f"🟢 *{name}* {pct} — {reason}{vol_s}")
        lines.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # 7. TOP LOSERS  🔻 *Top Losers*
    # ══════════════════════════════════════════════════════════════════════════
    if losers:
        lines.append("🔻 *Top Losers*")
        for s in losers:
            pct    = esc(f"{s['pct']:.2f}%")
            name   = esc(s["name"])
            reason = esc(why_moved(s["name"], s["symbol"], s["pct"]))
            vol_x  = s.get("vol_x")
            vol_s  = esc(f"  \\|  {vol_x}× vol") if vol_x else ""
            lines.append(f"🔴 *{name}* {pct} — {reason}{vol_s}")
        lines.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # 8. KEY NEWS  📰 *Key News (Impact Score)*
    #    🔥 Headline text
    #    ⚠️ ...
    #    💤 ...
    # ══════════════════════════════════════════════════════════════════════════
    if headlines:
        lines.append("📰 *Key News \\(Impact Score\\)*")
        # Sort headlines: High first, then Medium, then Low
        _order = {"🔥 High": 0, "⚠️ Medium": 1, "💤 Low": 2}
        scored = sorted(headlines, key=lambda h: _order.get(news_impact_score(h), 9))
        for h in scored[:8]:   # cap at 8 items to stay compact
            impact = news_impact_score(h)
            emoji  = impact.split()[0]          # just the emoji
            lines.append(f"{emoji} {esc(h)}")
        lines.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # 9. EXTRA: FII/DII, IPO, Corp Actions, Earnings, Regulatory — compact rows
    # ══════════════════════════════════════════════════════════════════════════
    extra_blocks = [
        ("💸 FII\\/DII",        fii_dii_lines[:2]),
        ("📋 IPO\\/Listing",    ipo_lines[:2]),
        ("🎁 Corp Actions",     corp_action_lines[:2]),
        ("💰 Earnings",         earnings_lines[:3]),
        ("⚖️ Regulatory",       regulatory_lines[:2]),
        ("🏦 Bulk\\/Block",     bulk_block_lines[:2]),
        ("👔 Insider",          insider_lines[:2]),
    ]
    for header, items in extra_blocks:
        if items:
            lines.append(f"*{header}*")
            for h in items:
                lines.append(f"• {esc(h)}")
            lines.append("")

    # ══════════════════════════════════════════════════════════════════════════
    # 10. TAKEAWAY  🎯 *Takeaway*
    #     One punchy sentence traders can act on immediately.
    # ══════════════════════════════════════════════════════════════════════════
    lines.append("🎯 *Takeaway*")

    # Build the single-sentence takeaway
    region = "India" if is_nse else "US"
    open_bias = (
        "expect bullish open" if avg_pct > 0.5
        else "expect cautious open" if avg_pct < -0.5
        else "expect range\\-bound open"
    )

    # Weakest + strongest sector for the sentence
    if sector_rows:
        top_s  = max(sector_rows, key=lambda x: x.get("pct", 0))
        weak_s = min(sector_rows, key=lambda x: x.get("pct", 0))
        sector_note = (
            f"watch *{esc(top_s['name'])}* strength"
            if top_s["pct"] > 0
            else f"watch *{esc(weak_s['name'])}* weakness"
        )
        if top_s["pct"] > 0 and weak_s["pct"] < -0.3:
            sector_note = (
                f"watch *{esc(weak_s['name'])}* weakness"
                f" & *{esc(top_s['name'])}* strength"
            )
    else:
        sector_note = "monitor key sectors"

    # VIX rider
    vix_rider = ""
    if active_vix is not None:
        if active_vix > 22:
            vix_rider = f"; VIX elevated at {esc(f'{active_vix:.1f}')} — reduce size"
        elif active_vix < 14:
            vix_rider = f"; VIX low at {esc(f'{active_vix:.1f}')} — options cheap"

    lines.append(
        f"{esc(open_bias).capitalize()}; {sector_note}{vix_rider}\\."
    )
    lines.append("")
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


def send_telegram_safe(token: str, chat_id: str, text: str) -> bool:
    """
    FIX #2 — Avoid cut-off messages.
    Telegram's hard limit is 4096 chars per message.
    Split long messages at clean line boundaries so no word/sentence is ever cut mid-way.
    """
    MAX_LEN = 4000  # comfortable buffer below 4096
    if len(text) <= MAX_LEN:
        return send_telegram(token, chat_id, text)

    lines   = text.split("\n")
    chunk   = ""
    success = True

    for line in lines:
        # +1 accounts for the newline we'll re-add
        if len(chunk) + len(line) + 1 > MAX_LEN:
            if chunk.strip():
                ok = send_telegram(token, chat_id, chunk.rstrip())
                success = success and ok
            chunk = line + "\n"
        else:
            chunk += line + "\n"

    if chunk.strip():
        ok = send_telegram(token, chat_id, chunk.rstrip())
        success = success and ok

    return success


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

    # NSE before NYSE so messages arrive in the right order.
    # Global cues are embedded inline into each report (no separate message).
    markets = []
    if market in ("nse", "both"):
        markets.append(("NSE India", NSE_TICKERS, NSE_SECTORS, TOP_NSE_STOCKS, NSE_FEEDS, "india"))
    if market in ("nyse", "both"):
        markets.append(("NYSE/NASDAQ US", NYSE_TICKERS, NYSE_SECTORS, TOP_NYSE_STOCKS, NYSE_FEEDS, "us"))

    for label, idx_tickers, sec_tickers, stock_list, feeds, region in markets:
        print(f"Fetching data for {label} ...")
        is_nse          = "NSE" in label
        idx_data        = get_index_data(idx_tickers)
        sector_rows     = get_sector_data(sec_tickers)
        gainers, losers = get_gainers_losers(stock_list)
        headlines       = get_headlines(feeds, region=region)

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

        # Global cues injected into NSE report; NYSE gets US-only cues
        msg_global_cues  = global_cues  if is_nse else [
            g for g in global_cues
            if g["name"] in {"Dow Futures", "US 10Y", "Crude Oil", "Gold (USD)", "Silver (USD)"}
        ]
        msg_global_pulse = global_pulse if is_nse else []

        msg = build_message(
            label, idx_data, sector_rows,
            gainers, losers,
            headlines, vix_val, india_vix_val,
            idx_tickers,
            global_cues       = msg_global_cues,
            global_pulse      = msg_global_pulse,
            vol_spikes        = vol_spikes,
            fii_dii_lines     = fii_dii_lines,
            bulk_block_lines  = bulk_block_lines,
            insider_lines     = insider_lines,
            ipo_lines         = ipo_lines,
            corp_action_lines = corp_lines,
            regulatory_lines  = reg_lines,
            earnings_lines    = earn_lines,
        )
        ok = send_telegram_safe(token, chat_id, msg)
        print(f"  Telegram [{label}]: {'sent ✓' if ok else 'FAILED ✗'}")


def main():
    parser = argparse.ArgumentParser(description="Market news Telegram bot")
    parser.add_argument("--config", default="config.json", help="Optional config file (falls back to env vars)")
    parser.add_argument("--market", choices=["nse", "nyse", "both"], default="both")
    args = parser.parse_args()
    run(load_config(args.config), args.market)


if __name__ == "__main__":
    main()
