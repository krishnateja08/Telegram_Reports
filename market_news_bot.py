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
    "DXY":          "DX-Y.NYB",    # US Dollar Index
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


def sector_strength_score(pct: float, rsi: float | None, vol_ratio: float | None = None) -> int:
    """
    Compute a 0–10 Strength Score for a sector.
    Based on: price momentum, RSI zone, volume vs average.
    """
    score = 5  # neutral baseline

    # Price momentum contribution (±3 pts)
    if pct > 1.5:
        score += 3
    elif pct > 0.5:
        score += 2
    elif pct > 0.1:
        score += 1
    elif pct < -1.5:
        score -= 3
    elif pct < -0.5:
        score -= 2
    elif pct < -0.1:
        score -= 1

    # RSI contribution (±2 pts)
    if rsi is not None:
        if rsi > 65:
            score += 2
        elif rsi > 55:
            score += 1
        elif rsi < 35:
            score -= 2
        elif rsi < 45:
            score -= 1

    # Volume ratio contribution (±2 pts)
    if vol_ratio is not None:
        if vol_ratio >= 1.5:
            score += 1
        elif vol_ratio <= 0.6:
            score -= 1

    return max(0, min(10, score))


def sector_trend_tag(pct: float, rsi: float | None) -> str:
    """Return a Trend Tag: Uptrend / Weakening / Reversal / Downtrend."""
    if pct > 0.5 and (rsi is None or rsi < 70):
        return "Uptrend"
    elif pct > 0 and rsi is not None and rsi > 65:
        return "OB — Weakening"
    elif pct < 0 and rsi is not None and rsi < 35:
        return "Reversal Attempt"
    elif pct < -0.5:
        return "Downtrend"
    else:
        return "Sideways"


def get_sector_data(sectors: dict) -> list:
    rows = []
    for name, symbol in sectors.items():
        d = get_ticker(symbol)
        if d:
            rsi = calc_rsi(symbol)
            strength = sector_strength_score(d["pct"], rsi)
            trend = sector_trend_tag(d["pct"], rsi)
            rows.append({
                "name": name,
                "pct": d["pct"],
                "rsi": rsi,
                "strength": strength,
                "trend": trend,
            })
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
            close = hist["Close"].iloc[-1]
            prev  = hist["Close"].iloc[-2]
            pct   = ((close - prev) / prev * 100) if prev else 0
            # Volume context vs 20-day average
            vol_x = None
            if len(hist) >= 21:
                avg_vol   = hist["Volume"].iloc[:-1].mean()
                today_vol = hist["Volume"].iloc[-1]
                if avg_vol > 0:
                    vol_x = round(today_vol / avg_vol, 1)
            info  = yf.Ticker(sym).info
            name  = info.get("shortName") or sym
            sector = info.get("sector", "")
            movers.append({
                "name":   name,
                "symbol": sym,
                "pct":    pct,
                "vol_x":  vol_x,
                "sector": sector,
            })
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
            pct_chg = abs(diff / recent[i - 1] * 100) if recent[i - 1] else 0
            if diff > 0:
                trend += "↑"
            elif diff < 0:
                trend += "↓"
            else:
                trend += "─"
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


def calc_market_bias_score(
    avg_pct: float,
    vix: float | None,
    global_pulse: list,
    sector_rows: list,
    usdinr_pct: float | None = None,
) -> tuple[int, str]:
    """
    Market Bias Score 0–100 (0 = extreme bearish, 100 = extreme bullish).
    Inputs: index avg %, VIX, global cues breadth, sector breadth, USDINR.
    Returns (score, label).
    """
    score = 50  # neutral baseline

    # 1. Index momentum (±15 pts)
    score += min(15, max(-15, avg_pct * 6))

    # 2. VIX (±10 pts) — lower VIX → more bullish
    if vix is not None:
        if vix < 13:
            score += 10
        elif vix < 17:
            score += 5
        elif vix < 22:
            score += 0
        elif vix < 27:
            score -= 8
        else:
            score -= 15

    # 3. Global cues breadth (±10 pts)
    if global_pulse:
        pos = sum(1 for g in global_pulse if g.get("pct", 0) >= 0)
        ratio = pos / len(global_pulse)
        score += (ratio - 0.5) * 20  # ±10

    # 4. Sector breadth (±10 pts)
    if sector_rows:
        pos_sec = sum(1 for s in sector_rows if s.get("pct", 0) >= 0)
        ratio_s = pos_sec / len(sector_rows)
        score += (ratio_s - 0.5) * 20  # ±10

    # 5. USDINR — rupee weakness pressures market (±5 pts)
    if usdinr_pct is not None:
        score -= min(5, max(-5, usdinr_pct * 5))  # rising USDINR = bearish

    score = max(0, min(100, round(score)))

    if score >= 70:
        label = "Strongly Bullish"
    elif score >= 58:
        label = "Mildly Bullish"
    elif score >= 43:
        label = "Neutral"
    elif score >= 30:
        label = "Mild Bearish"
    else:
        label = "Strongly Bearish"

    return score, label


def fmt_market_bias(score: int, label: str) -> list:
    """Format the Market Bias Score block."""
    if score >= 60:
        bar = "🟢"
    elif score >= 40:
        bar = "🟡"
    else:
        bar = "🔴"
    score_str = esc(f"{score}/100")
    label_str = esc(label)
    return [
        f"*🎯 Market Bias Score:* {bar} {score_str} — {label_str}",
        esc("  Inputs: VIX · Global cues · Sector breadth · Index momentum · USDINR"),
        "",
    ]


def mood_explanation(
    avg_pct: float,
    vix: float | None,
    sector_rows: list,
    global_pulse: list,
    losers: list,
) -> str:
    """
    Generate a 1-line 'why' for the current market mood.
    e.g. 'Bearish due to Tech-led selloff and weak Nasdaq breadth.'
    """
    reasons = []

    # Worst sector — primary driver when clearly negative
    if sector_rows:
        worst = min(sector_rows, key=lambda s: s.get("pct", 0))
        if worst.get("pct", 0) < -0.5:
            reasons.append(f"{worst['name']}\\-led selloff")

    # Top losers contribute named drivers
    if losers:
        big = [s["name"] for s in losers if abs(s.get("pct", 0)) >= 1.5]
        if big:
            reasons.append(f"heavy losses in {', '.join(big[:2])}")

    # VIX context
    if vix is not None:
        if vix > 25:
            reasons.append("VIX spiking")
        elif vix < 13 and avg_pct > 0:
            reasons.append("VIX subdued — calm rally")

    # Global cues
    if global_pulse:
        neg = [g["name"] for g in global_pulse if g.get("pct", 0) < -0.5]
        if len(neg) >= 3:
            reasons.append(f"weak global breadth \\({', '.join(neg[:2])}\\)")
        elif not neg and avg_pct > 0:
            reasons.append("supportive global cues")

    if not reasons:
        if avg_pct > 0.3:
            return "Broad buying across indices"
        elif avg_pct < -0.3:
            return "Broad selling pressure"
        return "No dominant catalyst — range\\-bound action"

    return "Due to: " + "; ".join(reasons)


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

US_FUTURES_TICKERS = {
    "Dow Fut":    "YM=F",
    "S&P Fut":    "ES=F",
    "Nasdaq Fut": "NQ=F",
    "Russell Fut":"RTY=F",
}


def get_us_futures() -> list:
    """Fetch US equity index futures."""
    rows = []
    for name, symbol in US_FUTURES_TICKERS.items():
        try:
            d = get_ticker(symbol)
            if d and d["close"] and d["close"] > 0:
                rows.append({"name": name, "close": d["close"], "pct": d["pct"]})
        except Exception:
            pass
    return rows


def fmt_us_futures(futures: list) -> list:
    """Format the US Futures Snapshot block."""
    if not futures:
        return []
    lines = ["*📡 US FUTURES SNAPSHOT*"]
    for f in futures:
        a    = arrow(f["pct"])
        sign = "+" if f["pct"] >= 0 else ""
        name = esc(f["name"])
        pct  = esc(f"{sign}{f['pct']:.2f}%")
        lines.append(f"{a} {name}: {pct}")
    lines.append("")
    return lines


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


def get_key_events(region: str = "india") -> list:
    """
    Fetch today's key market events from RSS / specialist feeds.
    Returns list of event dicts: {time, description}.
    """
    events = []
    ist = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
    today_str = ist.strftime("%d %b")

    # Static recurring events (day-of-week based) — always relevant
    weekday = ist.weekday()  # 0=Mon … 6=Sun
    if region == "india":
        if weekday == 3:  # Thursday
            events.append({"time": "All Day", "description": "F&O Expiry Week — watch options volatility"})
        events.append({"time": "3:30 PM IST", "description": "NSE / BSE market close"})
        events.append({"time": "6:00 PM IST", "description": "FII/DII provisional flow data"})
    else:
        events.append({"time": "9:30 AM EST",  "description": "NYSE open"})
        events.append({"time": "4:00 PM EST",  "description": "NYSE close"})
        events.append({"time": "8:30 AM EST",  "description": "US macro data window (check calendar)"})

    # Pull macro-event headlines from regulatory/macro feeds
    macro_keywords = [
        "rbi", "fed", "fomc", "gdp", "cpi", "inflation", "rate decision",
        "consumer confidence", "crude inventory", "bank credit", "pmi",
        "earnings today", "ipo listing", "f&o expiry",
    ]
    for feed in (NSE_FEEDS if region == "india" else NYSE_FEEDS):
        for h in fetch_headlines(feed, max_items=30):
            h_low = h.lower()
            if any(kw in h_low for kw in macro_keywords):
                events.append({"time": "Today", "description": h[:90]})
            if len(events) >= 8:
                break
        if len(events) >= 8:
            break

    return events[:8]


def fmt_key_events(events: list) -> list:
    """Format Today's Key Events block."""
    if not events:
        return []
    lines = ["*🗓 TODAY'S KEY EVENTS*"]
    for e in events:
        t   = esc(e.get("time", ""))
        desc = esc(e.get("description", ""))
        lines.append(f"• {t} — {desc}")
    lines.append("")
    return lines


def get_premarket_levels(idx_tickers: dict) -> list:
    """
    Calculate support/resistance for each index using recent price history.
    Uses last 10 days of data: pivot = (H+L+C)/3, S1/S2/R1/R2 from pivot points.
    """
    levels = []
    for name, symbol in idx_tickers.items():
        try:
            hist = yf.Ticker(symbol).history(period="5d")
            if len(hist) < 2:
                continue
            # Use previous day's candle for pivot calculation
            prev = hist.iloc[-2]
            H, L, C = prev["High"], prev["Low"], prev["Close"]
            pivot = (H + L + C) / 3
            r1 = 2 * pivot - L
            r2 = pivot + (H - L)
            s1 = 2 * pivot - H
            s2 = pivot - (H - L)
            today_close = hist["Close"].iloc[-1]
            # Expected range: S1 to R1
            levels.append({
                "name":  name,
                "close": today_close,
                "pivot": pivot,
                "r1":    r1,
                "r2":    r2,
                "s1":    s1,
                "s2":    s2,
            })
        except Exception:
            pass
    return levels


def fmt_premarket_levels(levels: list) -> list:
    """Format Pre-Market Support/Resistance block — lean, no emoji overload."""
    if not levels:
        return []
    lines = ["*📐 KEY LEVELS*"]
    for lv in levels:
        name = esc(lv["name"])
        s2   = esc(f"{lv['s2']:,.0f}")
        s1   = esc(f"{lv['s1']:,.0f}")
        r1   = esc(f"{lv['r1']:,.0f}")
        r2   = esc(f"{lv['r2']:,.0f}")
        lines.append(f"*{name}*  R1: {r1}  R2: {r2}  \\|  S1: {s1}  S2: {s2}")
        lines.append(f"  Range: {s1} — {r1}")
    lines.append("")
    return lines


def get_market_breadth(symbols: list) -> dict:
    """
    Calculate Advance/Decline/Unchanged from a list of symbols.
    Returns dict with advances, declines, unchanged, breadth_pct.
    """
    advances = declines = unchanged = 0
    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period="2d")
            if len(hist) < 2:
                continue
            pct = (hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2] * 100
            if pct > 0.05:
                advances += 1
            elif pct < -0.05:
                declines += 1
            else:
                unchanged += 1
        except Exception:
            pass
    total = advances + declines + unchanged
    breadth_pct = round((advances / total * 100) if total > 0 else 50, 1)
    return {
        "advances":    advances,
        "declines":    declines,
        "unchanged":   unchanged,
        "breadth_pct": breadth_pct,
    }


def fmt_market_breadth(breadth: dict) -> list:
    """Format Market Breadth block."""
    if not breadth or breadth.get("advances", 0) + breadth.get("declines", 0) == 0:
        return []
    bp = breadth["breadth_pct"]
    if bp >= 65:
        label = "Strongly Positive"
        dot = "🟢"
    elif bp >= 52:
        label = "Mildly Positive"
        dot = "🟢"
    elif bp >= 48:
        label = "Neutral"
        dot = "🟡"
    elif bp >= 35:
        label = "Mildly Negative"
        dot = "🔴"
    else:
        label = "Strongly Negative"
        dot = "🔴"

    adv = esc(str(breadth["advances"]))
    dec = esc(str(breadth["declines"]))
    unc = esc(str(breadth["unchanged"]))
    bp_str = esc(f"{bp}%")
    lbl_str = esc(label)
    return [
        "*📊 Market Breadth*",
        f"🟢 Advances: {adv}  \\|  🔴 Declines: {dec}  \\|  ⬜ Unchanged: {unc}",
        f"{dot} Breadth Score: {bp_str} — {lbl_str}",
        "",
    ]


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

def loser_reason(s: dict) -> str:
    """
    Generate a contextual reason for a loser based on pct magnitude and vol.
    Avoids the incorrect 'broad market strength' explanation for losers.
    """
    pct    = s.get("pct", 0)
    vol_x  = s.get("vol_x")          # present when from volume spikes
    symbol = s.get("symbol", "")

    # Use symbol suffix to infer market context
    is_nse = symbol.endswith(".NS")

    if vol_x and vol_x >= 2.0:
        return f"Heavy selling — volume {vol_x}× avg"
    if abs(pct) < 0.3:
        return "Mild profit booking; low volume drift"
    if abs(pct) < 0.8:
        if is_nse:
            return "Sector rotation or profit booking"
        return "Mild pullback; no fresh catalyst"
    if abs(pct) < 2.0:
        return "Technical pullback; watch support"
    return "Sharp sell-off — sector weakness or news-driven"


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


def build_sentiment_summary(categorized: dict, avg_pct: float, vix: float | None,
                            sector_rows: list | None = None,
                            bias_score: int | None = None,
                            bias_label: str | None = None) -> list:
    """
    Rich one-liner sentiment summary:
    'Tech mixed, Pharma strong, VIX low, global cues flat — expect range-bound to mildly positive.'
    """
    parts = []

    # Sector highlights
    if sector_rows:
        top    = max(sector_rows, key=lambda s: s.get("pct", 0), default=None)
        bottom = min(sector_rows, key=lambda s: s.get("pct", 0), default=None)
        if top and top.get("pct", 0) > 0.3:
            parts.append(f"{top['name']} strong")
        if bottom and bottom.get("pct", 0) < -0.3:
            parts.append(f"{bottom['name']} weak")

    # VIX summary
    if vix is not None:
        if vix < 15:
            parts.append("VIX low")
        elif vix < 22:
            parts.append("VIX moderate")
        else:
            parts.append("VIX elevated")

    # Macro / news presence
    if "Macro" in categorized:
        parts.append("macro events active")
    if "Geopolitics" in categorized:
        parts.append("geo risk present")

    # Overall bias
    if bias_label:
        if avg_pct > 0.3:
            outlook = f"expect positive bias — {bias_label}"
        elif avg_pct < -0.3:
            outlook = f"expect cautious tone — {bias_label}"
        else:
            outlook = f"expect range\\-bound to sideways — {bias_label}"
    else:
        outlook = "mixed signals"

    summary = esc(", ".join(parts)) + esc(" — ") + esc(outlook) if parts else esc(outlook)
    return [
        "*💬 Sentiment Summary*",
        summary,
        "",
    ]



# ─── New section formatters ──────────────────────────────────────────────────

def build_macro_risk_meter(
    vix: float | None,
    global_pulse: list,
    global_cues: list,
    headlines: list,
) -> list:
    """
    3-item Macro Risk Meter: Inflation, Rates, Geo.
    All derived from live VIX, bond yield, cue moves, and headline keywords.
    Returns formatted lines.
    """
    # ── Inflation risk ────────────────────────────────────────────────────────
    crude = next((g for g in global_cues if g["name"] == "Crude Oil"), None)
    infl_kw = ["inflation", "cpi", "pce", "price index", "consumer price"]
    infl_headline = any(any(kw in h.lower() for kw in infl_kw) for h in headlines)
    crude_rising  = crude and crude.get("pct", 0) > 1.0
    if crude_rising and infl_headline:
        infl_level, infl_dot = "High",   "🔴"
    elif crude_rising or infl_headline:
        infl_level, infl_dot = "Medium", "🟡"
    else:
        infl_level, infl_dot = "Low",    "🟢"

    # ── Rates risk ────────────────────────────────────────────────────────────
    us10y = next((g for g in global_cues if g["name"] == "US 10Y"), None)
    rate_kw = ["fed", "rate hike", "rate cut", "fomc", "taper", "rbi", "boe", "ecb"]
    rate_headline = any(any(kw in h.lower() for kw in rate_kw) for h in headlines)
    yield_rising  = us10y and us10y.get("pct", 0) > 0.5
    if yield_rising and rate_headline:
        rate_level, rate_dot = "High",   "🔴"
    elif yield_rising or rate_headline:
        rate_level, rate_dot = "Medium", "🟡"
    else:
        rate_level, rate_dot = "Low",    "🟢"

    # ── Geo risk ─────────────────────────────────────────────────────────────
    geo_kw = ["war", "iran", "china", "russia", "sanction", "conflict",
              "israel", "north korea", "taiwan", "geopolit", "attack"]
    geo_count = sum(1 for h in headlines if any(kw in h.lower() for kw in geo_kw))
    neg_global = sum(1 for g in global_pulse if g.get("pct", 0) < -1.0)
    if geo_count >= 2 or neg_global >= 5:
        geo_level, geo_dot = "High",   "🔴"
    elif geo_count >= 1 or neg_global >= 3:
        geo_level, geo_dot = "Medium", "🟡"
    else:
        geo_level, geo_dot = "Low",    "🟢"

    return [
        "*🧭 MACRO RISK METER*",
        f"  Inflation: {infl_dot} {esc(infl_level)}  \\|  "
        f"Rates: {rate_dot} {esc(rate_level)}  \\|  "
        f"Geo: {geo_dot} {esc(geo_level)}",
        "",
    ]


def build_risk_alerts(
    global_cues: list,
    vix: float | None,
    india_vix: float | None,
    sector_rows: list,
    global_pulse: list,
) -> list:
    """
    Generate contextual risk alerts based on live data.
    Never hardcoded — derived from actual values.
    """
    alerts = []

    # USDINR movement
    usdinr = next((g for g in global_cues if g["name"] == "USDINR"), None)
    if usdinr and usdinr.get("pct", 0) > 0.2:
        pct_s = esc(f"+{usdinr['pct']:.2f}%")
        alerts.append(f"USDINR rising \\({pct_s}\\) — may pressure IT & import\\-heavy sectors")
    elif usdinr and usdinr.get("pct", 0) < -0.2:
        pct_s = esc(f"{usdinr['pct']:.2f}%")
        alerts.append(f"Rupee strengthening \\({pct_s}\\) — supportive for importers")

    # Crude oil
    crude = next((g for g in global_cues if g["name"] == "Crude Oil"), None)
    if crude:
        if crude.get("pct", 0) > 1.5:
            pct_s = esc(f"+{crude['pct']:.2f}%")
            alerts.append(f"Crude surging \\({pct_s}\\) — negative for OMCs & aviation; watch inflation")
        elif crude.get("pct", 0) < -1.5:
            pct_s = esc(f"{crude['pct']:.2f}%")
            alerts.append(f"Crude falling \\({pct_s}\\) — supportive for OMCs & broader market")

    # VIX risk
    if vix and vix > 25:
        alerts.append(f"US VIX elevated at {esc(f'{vix:.1f}')} — options expensive; heightened uncertainty")
    elif vix and vix < 13:
        alerts.append(f"US VIX very low at {esc(f'{vix:.1f}')} — options cheap but prone to sudden spikes")
    if india_vix and india_vix > 18:
        alerts.append(f"India VIX at {esc(f'{india_vix:.1f}')} — elevated; avoid naked short positions")

    # Global cue weakness (Nasdaq-IT link)
    nasdaq_fut = next((g for g in global_pulse if "Nasdaq" in g.get("name", "")), None)
    if nasdaq_fut and nasdaq_fut.get("pct", 0) < -0.5:
        pct_s = esc(f"{nasdaq_fut['pct']:.2f}%")
        alerts.append(f"Nasdaq Fut weak \\({pct_s}\\) — watch IT sector intraday")

    # Broad global weakness
    neg_count = sum(1 for g in global_pulse if g.get("pct", 0) < -0.5)
    if neg_count >= 4:
        alerts.append(f"{neg_count} global indices weak — expect cautious opening; watch support levels")

    # Weak sector concentration
    weak_sectors = [s["name"] for s in sector_rows if s.get("pct", 0) < -0.8]
    if len(weak_sectors) >= 3:
        alerts.append(f"Multiple sectors under pressure: {esc(', '.join(weak_sectors))} — breadth deteriorating")

    return alerts


def fmt_risk_alerts(alerts: list) -> list:
    """Format Risk Alerts block."""
    if not alerts:
        return []
    lines = ["*⚠️ RISK ALERTS*"]
    for a in alerts:
        lines.append(f"• {a}")
    lines.append("")
    return lines


def fmt_premarket_cues(global_cues: list, global_pulse: list) -> list:
    """Pre-market cues block: SGX Nifty, Dow Fut, Crude, USD."""
    if not global_cues:
        return []
    priority = {"SGX Nifty", "Dow Futures", "Crude Oil", "USDINR", "US 10Y", "DXY"}
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


def sector_buzz_reason(s: dict, bullish: bool) -> str:
    """
    Generate a 1-line visible reason for Strong/Weak label.
    Resolves contradictions: green price + low RSI = 'weak despite gains'.
    """
    pct   = s.get("pct", 0)
    rsi   = s.get("rsi")
    trend = s.get("trend", "")
    str_  = s.get("strength", 5)

    if bullish:
        if pct > 1.0 and rsi and rsi > 55:
            return "price \\+ RSI momentum confirm strength"
        if pct > 0.5 and str_ >= 6:
            return "solid breadth \\+ sector momentum"
        if pct > 0 and rsi and rsi < 45:
            return "green but RSI lagging — cautious strength"
        if trend == "OB — Weakening":
            return "overbought — watch for reversal"
        return "leading gains; macro supportive"
    else:
        if pct < 0 and rsi and rsi < 40:
            return "price \\+ RSI both weak — confirmed downside"
        if pct >= 0 and str_ <= 4:
            return "green price but weak breadth \\& RSI — misleading"
        if trend == "Reversal Attempt":
            return "oversold bounce attempt; not confirmed"
        if pct < -0.5:
            return "sector rotation away; volume confirms"
        return "lagging peers; no catalyst"


def fmt_sector_buzz(buzz: dict) -> list:
    """Sector buzz: top strong and weak sectors with visible reasoning."""
    if not buzz.get("bullish") and not buzz.get("bearish"):
        return []
    lines = ["*🌡 SECTOR BUZZ*"]
    sep = "\\|"
    for s in buzz.get("bullish", []):
        sign   = "+" if s["pct"] >= 0 else ""
        pct_s  = esc(f"{sign}{s['pct']:.1f}%")
        reason = sector_buzz_reason(s, bullish=True)
        lines.append(f"🟢 *{esc(s['name'])}*: {pct_s}  {sep}  🔥 Strong — {reason}")
    for s in buzz.get("bearish", []):
        sign   = "+" if s["pct"] >= 0 else ""
        pct_s  = esc(f"{sign}{s['pct']:.1f}%")
        reason = sector_buzz_reason(s, bullish=False)
        lines.append(f"🔴 *{esc(s['name'])}*: {pct_s}  {sep}  🧊 Weak — {reason}")
    lines.append("")
    return lines


def parse_fii_dii_from_headlines(headlines: list) -> dict | None:
    """
    Try to extract structured FII/DII flow numbers from news headlines.
    Falls back to None if numbers can't be parsed.
    """
    import re
    fii_net = dii_net = None
    for h in headlines:
        h_low = h.lower()
        # Look for patterns like "FII net buy ₹1,240 cr" or "FII sold 820 crore"
        if "fii" in h_low or "foreign" in h_low:
            m = re.search(r"[\+\-]?\s*[₹]?\s*([\d,]+(?:\.\d+)?)\s*(?:cr|crore)", h_low)
            if m:
                amt = float(m.group(1).replace(",", ""))
                fii_net = -amt if any(w in h_low for w in ["sell", "sold", "net sell", "outflow"]) else amt
        if "dii" in h_low or "domestic" in h_low:
            m = re.search(r"[\+\-]?\s*[₹]?\s*([\d,]+(?:\.\d+)?)\s*(?:cr|crore)", h_low)
            if m:
                amt = float(m.group(1).replace(",", ""))
                dii_net = -amt if any(w in h_low for w in ["sell", "sold", "net sell", "outflow"]) else amt
    if fii_net is not None or dii_net is not None:
        net = (fii_net or 0) + (dii_net or 0)
        return {"fii": fii_net, "dii": dii_net, "net": net}
    return None


def fmt_fii_dii(headlines: list) -> list:
    """
    Show structured FII/DII flows if numbers are parseable,
    otherwise fall back to news headlines — but never mix oil news as FII/DII.
    """
    if not headlines:
        return []

    # Filter: only keep headlines that are genuinely FII/DII related
    fii_kw = ["fii", "dii", "foreign institutional", "domestic institutional",
               "net buy", "net sell", "flows", "foreign investor"]
    relevant = [h for h in headlines if any(kw in h.lower() for kw in fii_kw)]
    if not relevant:
        return []

    lines = ["*💸 FII \\/ DII FLOWS*"]

    # Try structured extraction first
    structured = parse_fii_dii_from_headlines(relevant)
    if structured:
        def fmt_flow(val):
            if val is None:
                return esc("N/A")
            sign = "+" if val >= 0 else ""
            arrow_e = "🟢" if val >= 0 else "🔴"
            return f"{arrow_e} {esc(f'{sign}₹{abs(val):,.0f} Cr')}"

        net = structured.get("net", 0) or 0
        net_tag = esc("Mildly Bullish") if net > 0 else esc("Mildly Bearish") if net < 0 else esc("Neutral")
        lines.append(f"  FII: {fmt_flow(structured.get('fii'))}  \\|  DII: {fmt_flow(structured.get('dii'))}")
        net_arrow = "🟢" if net >= 0 else "🔴"
        net_str = esc(f"{'+'if net>=0 else ''}₹{abs(net):,.0f} Cr")
        lines.append(f"  Net: {net_arrow} {net_str} — {net_tag}")
    else:
        for h in relevant[:4]:
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


# ─── Message builder ─────────────────────────────────────────────────────────

def build_watchlist(
    gainers: list,
    losers: list,
    sector_rows: list,
    vol_spikes: list,
) -> list:
    """
    Build a 'Next Session Watchlist' from:
    - Big movers (gainers/losers with high vol or large pct)
    - Leading/lagging sectors (top ETFs)
    - Unusual volume spikes
    Deduplicates and limits to 5 items.
    """
    items = []
    seen_names: set = set()

    def add(name: str, note: str):
        key = name.lower().strip()
        if key not in seen_names and len(items) < 5:
            seen_names.add(key)
            items.append((name, note))

    # Big losers first (more actionable for next session)
    for s in losers:
        pct   = s.get("pct", 0)
        vol_x = s.get("vol_x")
        if abs(pct) >= 1.5:
            vol_note = f" \\({esc(f'{vol_x}× vol')}\\)" if vol_x else ""
            add(s["name"], f"heavy selloff{vol_note} — watch for continuation or reversal")
        elif abs(pct) >= 0.8:
            add(s["name"], f"technical pullback — check support levels")

    # Strong gainers on high volume
    for s in gainers:
        pct   = s.get("pct", 0)
        vol_x = s.get("vol_x")
        if pct >= 1.5 and vol_x and vol_x >= 1.5:
            add(s["name"], f"strong breakout \\({esc(f'{vol_x}× vol')}\\) — momentum play")
        elif pct >= 0.8:
            add(s["name"], "leading sector — watch for follow-through")

    # Unusual volume spikes (even if not top mover)
    for s in vol_spikes:
        vol_x = s.get("vol_x", 1)
        pct   = s.get("pct", 0)
        if vol_x >= 2.5:
            direction = "upside" if pct >= 0 else "downside"
            add(s["name"], f"volume spike {esc(f'{vol_x}×')} — watch {direction} follow-through")

    # Strongest and weakest sector (by ETF name)
    if sector_rows:
        best  = max(sector_rows, key=lambda s: s.get("pct", 0))
        worst = min(sector_rows, key=lambda s: s.get("pct", 0))
        if best.get("pct", 0) > 0.8:
            add(best["name"] + " sector", "leadership — look for sector ETF or top names")
        if worst.get("pct", 0) < -0.8:
            add(worst["name"] + " sector", "lagging — avoid long exposure next session")

    if not items:
        return []

    lines = ["*👀 NEXT SESSION WATCHLIST*"]
    for name, note in items:
        lines.append(f"• *{esc(name)}* — {note}")
    lines.append("")
    return lines


def build_takeaway(
    avg_pct: float,
    sector_rows: list,
    bias_label: str,
    breadth: dict,
    vix: float | None,
    losers: list,
    gainers: list,
) -> list:
    """
    Generate a punchy 1–2 sentence 🎯 Takeaway for any market.
    Derived from live data — never hardcoded.
    """
    parts = []

    # Dominant sector drag or leadership
    if sector_rows:
        worst = min(sector_rows, key=lambda s: s.get("pct", 0))
        best  = max(sector_rows, key=lambda s: s.get("pct", 0))
        if worst.get("pct", 0) < -0.5:
            parts.append(f"{esc(worst['name'])} weakness dragging indices")
        if best.get("pct", 0) > 0.5:
            parts.append(f"{esc(best['name'])} showing strength")

    # Breadth context
    bp = breadth.get("breadth_pct", 50) if breadth else 50
    if bp < 40:
        parts.append(esc("breadth poor"))
    elif bp > 65:
        parts.append(esc("broad participation"))

    # VIX
    if vix:
        if vix > 22:
            parts.append(esc("VIX elevated — expect choppy tape"))
        elif vix < 14:
            parts.append(esc("VIX low — calm conditions"))

    # Closing outlook driven by bias
    if avg_pct > 0.5:
        outlook = esc(f"expect continued upside bias — {bias_label}")
    elif avg_pct < -0.5:
        outlook = esc(f"expect cautious tone — {bias_label}")
    else:
        outlook = esc(f"range\\-bound session likely — {bias_label}")

    body = (esc("; ").join(parts) + esc(". ") + outlook) if parts else outlook
    return [
        "*🎯 TAKEAWAY*",
        body,
        "",
    ]


def _dedup_across_sections(*headline_lists: list) -> list[list]:
    """
    Remove headlines that appear in more than one section.
    Priority order: first list wins, subsequent lists drop duplicates.
    Also normalises whitespace for comparison.
    Returns one deduplicated list per input.
    """
    seen: set = set()
    result = []
    for lst in headline_lists:
        clean = []
        for h in lst:
            key = " ".join(h.lower().split())
            if key not in seen:
                seen.add(key)
                clean.append(h)
        result.append(clean)
    return result


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
    breadth: dict | None = None,
    key_events: list | None = None,
    premarket_levels: list | None = None,
    us_futures: list | None = None,
) -> str:
    us_futures         = us_futures         or []
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
    breadth            = breadth            or {}
    key_events         = key_events         or []
    premarket_levels   = premarket_levels   or []

    # ── Suggestion 1: De-duplicate headlines across sections ─────────────────
    # Priority: regulatory > earnings > key_events > fii_dii > headlines
    # Anything already shown in Regulatory is dropped from all other sections.
    key_event_descs = [e.get("description", "") for e in key_events]
    (
        regulatory_lines,
        earnings_lines,
        key_event_descs,
        fii_dii_lines,
        headlines,
    ) = _dedup_across_sections(
        regulatory_lines,
        earnings_lines,
        key_event_descs,
        fii_dii_lines,
        headlines,
    )
    key_events = [
        e for e in key_events
        if e.get("description", "") in key_event_descs
    ]

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
    # FIX #3 — Clear NSE vs NYSE differentiation with flag + relevant timezone only
    flag     = "🇮🇳" if is_nse else "🇺🇸"
    time_str = ist_str if is_nse else est_str
    tz_label = esc("IST") if is_nse else esc("EST")
    lines += [
        f"{flag} *{esc(market)} Market Summary*",
        f"🗓 {today}  \\|  🕐 {tz_label}: {time_str}",
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
    mood_why  = mood_explanation(avg_pct, active_vix, sector_rows, global_pulse, losers)
    lines += [
        f"*📡 Market Mood:* {sentiment}",
        f"  ↳ {mood_why}",
        f"*🏷 Regime:* {esc(regime)}",
        "",
    ]

    # ── Market Bias Score ────────────────────────────────────────────────────
    usdinr_data = next((g for g in global_cues if g["name"] == "USDINR"), None)
    usdinr_pct  = usdinr_data["pct"] if usdinr_data else None
    bias_score, bias_label = calc_market_bias_score(
        avg_pct, active_vix, global_pulse, sector_rows, usdinr_pct
    )
    lines += fmt_market_bias(bias_score, bias_label)

    # ── Market Breadth ────────────────────────────────────────────────────────
    lines += fmt_market_breadth(breadth)

    # ── Pre-Market Levels ─────────────────────────────────────────────────────
    lines += fmt_premarket_levels(premarket_levels)

    # ── Today's Key Events ────────────────────────────────────────────────────
    lines += fmt_key_events(key_events)

    # ── Risk Alerts ───────────────────────────────────────────────────────────
    risk_alerts = build_risk_alerts(global_cues, vix_val, india_vix_val, sector_rows, global_pulse)
    lines += fmt_risk_alerts(risk_alerts)

    # ── Macro Risk Meter ──────────────────────────────────────────────────────
    lines += build_macro_risk_meter(active_vix, global_pulse, global_cues, headlines)

    # ── US Futures Snapshot (US report only) ──────────────────────────────────
    if not is_nse:
        lines += fmt_us_futures(us_futures)

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
        lines.append("*🌡 Sector Heat \\| RSI \\| Strength*")
        for s in sector_rows:
            dot      = sector_dot(s["pct"])
            sign     = "+" if s["pct"] >= 0 else ""
            spct     = esc(f"{sign}{s['pct']:.1f}%")
            sname    = esc(s["name"])
            rsi      = s.get("rsi")
            tag      = rsi_tag(rsi)
            rsi_str  = esc(f"{int(round(rsi))}") if rsi is not None else esc("N/A")
            strength = s.get("strength")
            str_str  = esc(f"{strength}/10") if strength is not None else esc("N/A")
            trend    = esc(s.get("trend", ""))
            lines.append(
                f"{dot} {sname}: {spct}  \\|  RSI: {rsi_str} {tag}  \\|  "
                f"Str: {str_str}  \\|  {trend}"
            )
        lines.append("")

    # ── Sector Buzz ──────────────────────────────────────────────────────────────
    if sector_rows:
        buzz = get_sector_buzz(sector_rows)
        lines += fmt_sector_buzz(buzz)

    # ── Gainers & Losers ──────────────────────────────────────────────────────
    if gainers:
        lines.append("*🚀 TOP GAINERS*")
        for s in gainers:
            gpct   = esc(f"+{s['pct']:.2f}%")
            gname  = esc(s["name"])
            gsym   = esc(s["symbol"])
            vol_x  = s.get("vol_x")
            sector = s.get("sector", "")
            vol_str = f"  \\|  Vol: {esc(f'{vol_x}×')}" if vol_x else ""
            sec_str = f"  \\|  {esc(sector)}" if sector else ""
            lines.append(f"🟢 {gname} \\({gsym}\\): {gpct}{vol_str}{sec_str}")
        lines.append("")
    if losers:
        lines.append("*🔻 TOP LOSERS*")
        for s in losers:
            lpct   = esc(f"{s['pct']:.2f}%")
            lname  = esc(s["name"])
            lsym   = esc(s["symbol"])
            reason = esc(loser_reason(s))
            vol_x  = s.get("vol_x")
            vol_str = f" \\({esc(f'{vol_x}× vol')}\\)" if vol_x else ""
            lines.append(f"🔴 {lname} \\({lsym}\\): {lpct}{vol_str} — {reason}")
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
        lines += build_sentiment_summary(
            categorized, avg_pct, active_vix,
            sector_rows=sector_rows,
            bias_score=bias_score,
            bias_label=bias_label,
        )

        # Punchy categorised news with context tags
        lines += format_news_section(categorized)

    # ── Next Session Watchlist ────────────────────────────────────────────────
    lines += build_watchlist(gainers, losers, sector_rows, vol_spikes)

    # ── Takeaway ──────────────────────────────────────────────────────────────
    lines += build_takeaway(avg_pct, sector_rows, bias_label, breadth, active_vix, losers, gainers)

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

    if global_cues or global_pulse:
        ok = send_telegram_safe(token, chat_id, build_global_cues_message(global_cues))
        print(f"  Global Cues Telegram: {'sent ✓' if ok else 'FAILED ✗'}")

    # FIX #3 — Always process NSE before NYSE so messages arrive in the right order.
    # Global cues (pre-market context) are injected ONLY into the NSE message.
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

        # New: breadth, key events, pre-market levels, US futures
        region_str       = "india" if is_nse else "us"
        breadth_data     = get_market_breadth(stock_list)
        key_events_data  = get_key_events(region=region_str)
        pm_levels        = get_premarket_levels(idx_tickers)
        us_futures_data  = [] if is_nse else get_us_futures()

        print(f"  Vol spikes: {len(vol_spikes)} | FII/DII: {len(fii_dii_lines)} | "
              f"IPO: {len(ipo_lines)} | Earnings: {len(earn_lines)} | "
              f"Breadth: A{breadth_data['advances']}/D{breadth_data['declines']}")

        # FIX #3 — Global cues only go into the NSE message (pre-market context for India).
        # NYSE message gets clean US-only content with no Indian pre-market data.
        msg_global_cues  = global_cues  if is_nse else []
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
            breadth           = breadth_data,
            key_events        = key_events_data,
            premarket_levels  = pm_levels,
            us_futures        = us_futures_data,
        )
        # FIX #2 — Use safe sender that splits messages exceeding Telegram's 4096-char limit
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
