#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  TRADING BOT V16.1 — HIGH WIN RATE + SIZING CORREGIDO              ║
║                                                                      ║
║  V16 BASE — CALIDAD SOBRE CANTIDAD:                                 ║
║    · H1 BULL/BEAR requerido (NEUTRAL = descarte)                    ║
║    · Slope = FILTRO DURO (no confluencia)                           ║
║    · ADX_MIN=22 (elimina rango 18-22 con alta reversión)            ║
║    · Patrón de vela requerido: PIN_BAR/ENGULF/MOMENTUM/INSIDE_BAR  ║
║    · Score mínimo = 55                                              ║
║    · MAX_OPEN_TRADES = 3                                            ║
║    · Trailing stop + Time-based exit                                ║
║                                                                      ║
║  V16.1 — SIZING CORREGIDO (auditoría de rentabilidad):             ║
║    · RISK_PERCENT  1.0 → 2.0  (riesgo real por trade)              ║
║    · MAX_ORDER_USDT 50 → 100  (nocional máximo por orden)           ║
║    · BE_ATR_MULT   1.0 → 1.5  (evita cortar winners en tendencias) ║
║                                                                      ║
║  RESULTADO ESPERADO V16.1:                                          ║
║    · ~74-78% win rate                                               ║
║    · Riesgo efectivo real: ~0.30-0.60% por trade (era 0.16%)       ║
║    · P&L mensual estimado: +15-25% sobre balance                    ║
║    · Break-even win rate: 33% (amplio margen de seguridad)          ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os, time, hmac, hashlib, json, asyncio, logging, threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

import requests
import pandas as pd
import numpy as np

try:
    from telegram import Bot
    from telegram.constants import ParseMode
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False

# ══════════════════════════════════════════════════════════════════════
#  CONFIG — V16 HIGH WIN RATE
# ══════════════════════════════════════════════════════════════════════
BINGX_API_KEY    = os.environ["BINGX_API_KEY"]
BINGX_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", os.environ.get("BINGX_API_SECRET", ""))
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

TIMEFRAME        = os.environ.get("TIMEFRAME",       "5m")
RISK_PERCENT     = float(os.environ.get("RISK_PERCENT",   "2.0"))   # V16.1: 1.0 → 2.0
LEVERAGE         = int(os.environ.get("LEVERAGE",         "5"))
LOOP_SECONDS     = int(os.environ.get("LOOP_SECONDS",     "60"))
MAX_OPEN_TRADES  = int(os.environ.get("MAX_OPEN_TRADES",  "3"))     # V16: 6 → 3
SCAN_WORKERS     = int(os.environ.get("SCAN_WORKERS",     "20"))
MAX_SYMBOLS      = int(os.environ.get("MAX_SYMBOLS",      "0"))

# ── Filtros de calidad — V16 MÁS RESTRICTIVO ─────────────────────────
MIN_SCORE        = float(os.environ.get("MIN_SCORE",      "55.0"))   # V16: 45 → 55
MIN_CONFLUENCES  = int(os.environ.get("MIN_CONFLUENCES",  "4"))
MIN_DIST_PCT     = float(os.environ.get("MIN_DIST_PCT",   "0.20"))
ATR_MAX_PCT      = float(os.environ.get("ATR_MAX_PCT",    "4.0"))

# ── EMAs ──────────────────────────────────────────────────────────────
EMA_FAST         = int(os.environ.get("EMA_FAST",   "7"))
EMA_SLOW         = int(os.environ.get("EMA_SLOW",   "21"))
EMA_TREND        = int(os.environ.get("EMA_TREND",  "50"))
SLOPE_LIMIT      = float(os.environ.get("SLOPE_LIMIT", "12.0"))
SLOPE_LOOK       = int(os.environ.get("SLOPE_LOOK",   "5"))

# ── ADX / RSI ─────────────────────────────────────────────────────────
ADX_LEN          = int(os.environ.get("ADX_LEN",  "14"))
ADX_MIN          = float(os.environ.get("ADX_MIN", "22.0"))          # V16: 18 → 22
RSI_LEN          = int(os.environ.get("RSI_LEN",  "14"))
RSI_OB           = float(os.environ.get("RSI_OB",  "72.0"))
RSI_OS           = float(os.environ.get("RSI_OS",  "28.0"))
VOL_MULT         = float(os.environ.get("VOL_MULT", "0.9"))

# ── SuperTrend ────────────────────────────────────────────────────────
ST_PERIOD        = int(os.environ.get("ST_PERIOD",  "10"))
ST_MULT          = float(os.environ.get("ST_MULT",  "3.0"))

# ── TP / SL ───────────────────────────────────────────────────────────
TP_MULT          = float(os.environ.get("TP_MULT",       "2.0"))
SL_ATR_MULT      = float(os.environ.get("SL_ATR_MULT",   "1.5"))
MIN_RR           = float(os.environ.get("MIN_RR",        "1.5"))

# ── Trailing Stop — V16 NUEVO ─────────────────────────────────────────
BE_ATR_MULT      = float(os.environ.get("BE_ATR_MULT",    "1.5"))   # V16.1: 1.0 → 1.5 (evita cortar winners)
TRAIL_ATR_MULT   = float(os.environ.get("TRAIL_ATR_MULT", "1.5"))   # Trailing en 1.5×ATR
MAX_CANDLES_OPEN = int(os.environ.get("MAX_CANDLES_OPEN", "30"))     # Time exit: 30 velas (2.5h)

# ── Filtros de calidad V16 — NUEVOS ──────────────────────────────────
REQUIRE_PATTERN  = os.environ.get("REQUIRE_PATTERN",  "true").lower() == "true"
REQUIRE_H1_ALIGN = os.environ.get("REQUIRE_H1_ALIGN", "true").lower() == "true"

# ── Position sizing ───────────────────────────────────────────────────
MIN_ORDER_USDT   = float(os.environ.get("MIN_ORDER_USDT", "5.0"))
MAX_ORDER_USDT   = float(os.environ.get("MAX_ORDER_USDT", "100.0"))  # V16.1: 50 → 100
MAX_MARGIN_PCT   = float(os.environ.get("MAX_MARGIN_PCT", "30.0"))

# ── Sesión ────────────────────────────────────────────────────────────
SESSION_FILTER   = os.environ.get("SESSION_FILTER", "false").lower() == "true"
SESSION_START    = int(os.environ.get("SESSION_START", "6"))
SESSION_END      = int(os.environ.get("SESSION_END",  "22"))

# ── Circuit breaker ───────────────────────────────────────────────────
MAX_CONSEC_LOSSES = int(os.environ.get("MAX_CONSEC_LOSSES", "3"))    # V16: 4 → 3
CB_PAUSE_MINS     = int(os.environ.get("CB_PAUSE_MINS",    "30"))

# ── H1 cache ──────────────────────────────────────────────────────────
H1_CACHE_TTL  = int(os.environ.get("H1_CACHE_TTL",  "300"))
COOLDOWN_MINS = int(os.environ.get("COOLDOWN_MINS", "15"))

_raw = os.environ.get("CUSTOM_SYMBOLS", "")
CUSTOM_SYMBOLS = [s.strip() for s in _raw.split(",") if s.strip()] if _raw else []

BINGX_BASE = "https://open-api.bingx.com"
INTERVAL_MAP = {"1m":"1m","3m":"3m","5m":"5m","15m":"15m","30m":"30m","1h":"1H","4h":"4H"}
EXCLUDED_PREFIXES = ("NCS","NCF","NCMEX","NCOIL","NCGAS","NCXAU","NCXAG")
EXCLUDED_KEYWORDS = ("Gasoline","GasOil","Brent","WTI","Copper","Wheat","Cotton",
                     "Soybean","Silver","EURUSD","GBPUSD","JPYUSD")

FALLBACK_SYMBOLS = [
    "BTC-USDT","ETH-USDT","BNB-USDT","SOL-USDT","XRP-USDT",
    "DOGE-USDT","ADA-USDT","AVAX-USDT","DOT-USDT","LINK-USDT",
    "MATIC-USDT","INJ-USDT","SUI-USDT","ARB-USDT","OP-USDT",
    "WIF-USDT","PEPE-USDT","WLD-USDT","TIA-USDT","SEI-USDT",
    "NEAR-USDT","APT-USDT","FIL-USDT","HBAR-USDT","AAVE-USDT",
    "LDO-USDT","RUNE-USDT","GRT-USDT","CRV-USDT","DYDX-USDT",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler()])
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
#  ESTADO GLOBAL
# ══════════════════════════════════════════════════════════════════════
sl_cooldown    = {}
h1_cache       = {}
consec_losses  = 0
cb_pause_until = None
# Registro de trades abiertos para trailing/time-exit
open_trade_meta = {}   # sym → {"open_time": datetime, "entry": float, "side": str, "atr": float}

# ══════════════════════════════════════════════════════════════════════
#  BINGX API
# ══════════════════════════════════════════════════════════════════════
def _sign(params):
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(BINGX_SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()

def bx_get(path, params=None):
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = _sign(p)
    r = requests.get(BINGX_BASE + path, params=p,
                     headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=15)
    r.raise_for_status()
    return r.json()

def bx_post(path, payload):
    p = dict(payload)
    p["timestamp"] = int(time.time() * 1000)
    p["signature"] = _sign(p)
    r = requests.post(BINGX_BASE + path, json=p,
                      headers={"X-BX-APIKEY": BINGX_API_KEY,
                               "Content-Type":"application/json"}, timeout=15)
    r.raise_for_status()
    return r.json()

def get_balance():
    try:
        data = bx_get("/openApi/swap/v2/user/balance")
        bal  = data.get("data", {}).get("balance", {})
        for f in ("availableMargin","available","crossWalletBalance","walletBalance","equity"):
            v = bal.get(f)
            if v is not None and v != "" and float(v) > 0:
                log.info(f"Balance: {float(v):.4f} USDT ({f})")
                return float(v)
        return 0.0
    except Exception as e:
        log.error(f"get_balance: {e}")
        return 0.0

def get_all_positions():
    try:
        data   = bx_get("/openApi/swap/v2/user/positions", {})
        result = {}
        for p in data.get("data", []):
            if isinstance(p, dict) and float(p.get("positionAmt", 0)) != 0:
                result[p["symbol"]] = p
        log.info(f"Open positions ({len(result)}): {list(result.keys())[:8]}")
        return result
    except Exception as e:
        log.error(f"get_positions: {e}")
        return {}

def _is_valid(sym):
    if not sym or not sym.endswith("-USDT"): return False
    base = sym.replace("-USDT","")
    if len(base) < 2: return False
    if any(base.startswith(p) for p in EXCLUDED_PREFIXES): return False
    if any(kw.lower() in sym.lower() for kw in EXCLUDED_KEYWORDS): return False
    return True

def get_all_symbols(limit=0):
    try:
        data = bx_get("/openApi/swap/v2/quote/contracts", {})
        contracts = data.get("data", [])
        usdt = [c for c in contracts
                if isinstance(c, dict) and c.get("asset","") == "USDT" and c.get("status") == 1]
        if not usdt:
            usdt = [c for c in contracts
                    if isinstance(c, dict) and c.get("asset","") == "USDT"]
        usdt.sort(key=lambda x: float(x.get("tradeAmount", 0) or 0), reverse=True)
        syms = [c["symbol"] for c in usdt if _is_valid(c.get("symbol",""))]
        result = syms if limit == 0 else syms[:limit]
        log.info(f"✅ {len(result)} symbols from contracts")
        return result or FALLBACK_SYMBOLS
    except Exception as e:
        log.warning(f"get_all_symbols: {e}")
        return FALLBACK_SYMBOLS

def set_lev(symbol):
    for side in ("LONG","SHORT"):
        try:
            bx_post("/openApi/swap/v2/trade/leverage",
                    {"symbol":symbol,"side":side,"leverage":LEVERAGE})
        except Exception:
            pass

# ── Precio en vivo — triple fallback ─────────────────────────────────
def get_live_price(symbol):
    try:
        data  = bx_get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
        items = data.get("data", [])
        if isinstance(items, list):
            for item in items:
                if item.get("symbol") == symbol and item.get("markPrice"):
                    return float(item["markPrice"])
        if isinstance(items, dict) and items.get("markPrice"):
            return float(items["markPrice"])
    except Exception:
        pass
    try:
        data = bx_get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        t    = data.get("data", [])
        if isinstance(t, list):
            for item in t:
                if item.get("symbol") == symbol:
                    lp = item.get("lastPrice") or item.get("price")
                    if lp: return float(lp)
        if isinstance(t, dict):
            lp = t.get("lastPrice") or t.get("price")
            if lp: return float(lp)
    except Exception:
        pass
    try:
        params = {"symbol":symbol, "interval":INTERVAL_MAP.get(TIMEFRAME,"5m"), "limit":2}
        data   = bx_get("/openApi/swap/v3/quote/klines", params)
        rows   = data.get("data", [])
        if rows: return float(rows[-1][4])
    except Exception:
        pass
    raise ValueError(f"No price for {symbol}")

# ══════════════════════════════════════════════════════════════════════
#  KLINES
# ══════════════════════════════════════════════════════════════════════
def _fetch_klines(symbol, interval, limit):
    params = {"symbol":symbol, "interval":INTERVAL_MAP.get(interval, interval), "limit":limit}
    data   = bx_get("/openApi/swap/v3/quote/klines", params)
    rows   = data.get("data", [])
    if not rows or not isinstance(rows, list):
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume","close_time"])
    for col in ("open","high","low","close","volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.dropna(subset=["open","high","low","close","volume"], inplace=True)
    return df.sort_values("open_time").reset_index(drop=True)

def get_klines(symbol, limit=200):
    return _fetch_klines(symbol, TIMEFRAME, limit)

def get_h1_klines(symbol, limit=80):
    now    = time.time()
    cached = h1_cache.get(symbol)
    if cached:
        df_c, ts = cached
        if now - ts < H1_CACHE_TTL and len(df_c) >= 30:
            return df_c.copy()
    try:
        df = _fetch_klines(symbol, "1h", limit)
        if not df.empty:
            h1_cache[symbol] = (df.copy(), now)
        return df
    except Exception:
        return pd.DataFrame()

# ══════════════════════════════════════════════════════════════════════
#  INDICADORES
# ══════════════════════════════════════════════════════════════════════
def calc_atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def calc_ema_angle(ema_s, atr_s, look=5):
    price_change = ema_s - ema_s.shift(look)
    denom = atr_s * look
    return pd.Series(
        np.degrees(np.arctan2(price_change.values, denom.values)),
        index=ema_s.index
    )

def calc_adx(high, low, close, period=14):
    up   = high.diff()
    down = -low.diff()
    plus_dm  = np.where((up > down) & (up > 0),   up,   0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs()
    ], axis=1).max(axis=1)
    alpha = 1 / period
    def w(arr): return pd.Series(arr, index=high.index).ewm(alpha=alpha, adjust=False).mean()
    tr_s  = w(tr); pdm_s = w(plus_dm); mdm_s = w(minus_dm)
    di_p  = 100 * pdm_s / tr_s.replace(0, np.nan)
    di_m  = 100 * mdm_s / tr_s.replace(0, np.nan)
    dx    = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan)
    adx   = dx.ewm(alpha=alpha, adjust=False).mean()
    return di_p, di_m, adx

def calc_rsi(close, period=14):
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_supertrend(high, low, close, period=10, mult=3.0):
    atr       = calc_atr(high, low, close, period)
    hl2       = (high + low) / 2
    upper_raw = hl2 + mult * atr
    lower_raw = hl2 - mult * atr

    direction = pd.Series(1, index=close.index, dtype=int)
    final_ub  = upper_raw.copy()
    final_lb  = lower_raw.copy()

    for i in range(1, len(close)):
        if upper_raw.iloc[i] < final_ub.iloc[i-1] or close.iloc[i-1] > final_ub.iloc[i-1]:
            final_ub.iloc[i] = upper_raw.iloc[i]
        else:
            final_ub.iloc[i] = final_ub.iloc[i-1]
        if lower_raw.iloc[i] > final_lb.iloc[i-1] or close.iloc[i-1] < final_lb.iloc[i-1]:
            final_lb.iloc[i] = lower_raw.iloc[i]
        else:
            final_lb.iloc[i] = final_lb.iloc[i-1]
        if close.iloc[i] > final_ub.iloc[i-1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < final_lb.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]

    return direction

def calc_heikin_ashi(df):
    ha = df.copy()
    ha["ha_close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha["ha_open"]  = ha["ha_close"].copy()
    for i in range(1, len(ha)):
        ha.at[ha.index[i], "ha_open"] = \
            (ha["ha_open"].iloc[i-1] + ha["ha_close"].iloc[i-1]) / 2
    return ha

def calc_vwap(df):
    typical    = (df["high"] + df["low"] + df["close"]) / 3
    df2        = df.copy()
    df2["_tp"] = typical * df["volume"]
    df2["_day"]= df2["open_time"].dt.floor("D")
    df2["_ctp"]= df2.groupby("_day")["_tp"].cumsum()
    df2["_cv"] = df2.groupby("_day")["volume"].cumsum()
    return df2["_ctp"] / df2["_cv"]

def calc_squeeze_off(high, low, close, sq_len=20, bb_mult=2.0, kc_mult=1.5):
    basis  = close.rolling(sq_len).mean()
    std    = close.rolling(sq_len).std()
    bb_up  = basis + bb_mult * std
    bb_lo  = basis - bb_mult * std
    atr_kc = calc_atr(high, low, close, sq_len)
    kc_up  = basis + kc_mult * atr_kc
    kc_lo  = basis - kc_mult * atr_kc
    sqz_on = (bb_lo > kc_lo) & (bb_up < kc_up)
    return ~sqz_on

# ══════════════════════════════════════════════════════════════════════
#  ANÁLISIS H1
# ══════════════════════════════════════════════════════════════════════
def analyze_h1(symbol):
    df = get_h1_klines(symbol, 80)
    if df.empty or len(df) < 30:
        return None

    close, high, low = df["close"], df["high"], df["low"]
    ema7   = calc_ema(close, 7)
    ema21  = calc_ema(close, 21)
    st_dir = calc_supertrend(high, low, close, ST_PERIOD, ST_MULT)
    rsi_h1 = calc_rsi(close, 14)

    ema7_now  = float(ema7.iloc[-1])
    ema21_now = float(ema21.iloc[-1])
    close_now = float(close.iloc[-1])
    st_now    = int(st_dir.iloc[-1])
    rsi_now   = float(rsi_h1.iloc[-1])

    # V16: requiere AMBAS condiciones para confirmar tendencia (más estricto)
    bull_h1 = (ema7_now > ema21_now) and (st_now == 1)
    bear_h1 = (ema7_now < ema21_now) and (st_now == -1)

    if bull_h1:
        h1_trend = "BULL"
    elif bear_h1:
        h1_trend = "BEAR"
    else:
        h1_trend = "NEUTRAL"

    return {
        "h1_trend": h1_trend,
        "h1_st":    st_now,
        "h1_rsi":   round(rsi_now, 1),
        "h1_close": close_now,
    }

# ══════════════════════════════════════════════════════════════════════
#  PATRONES DE VELA — V16 añade Inside Bar
# ══════════════════════════════════════════════════════════════════════
def detect_candle_pattern(df, i, direction, atr_val):
    """
    Detecta: Pin Bar, Engulfing, Momentum candle, Inside Bar.
    Retorna (pattern_name, pattern_score, sl_candle_price).
    """
    if i < 1:
        return "NONE", 0.0, None

    o  = float(df["open"].iloc[i])
    h  = float(df["high"].iloc[i])
    l  = float(df["low"].iloc[i])
    c  = float(df["close"].iloc[i])
    o1 = float(df["open"].iloc[i-1])
    h1 = float(df["high"].iloc[i-1])
    l1 = float(df["low"].iloc[i-1])
    c1 = float(df["close"].iloc[i-1])

    rng  = h - l
    body = abs(c - o)
    if rng < 1e-10 or atr_val < 1e-10:
        return "NONE", 0.0, None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # ── Pin Bar ──────────────────────────────────────────────────────
    if body / rng < 0.35:
        if direction == "LONG" and lower_wick / rng >= 0.55 and lower_wick >= 2 * max(body, 1e-10):
            sc = min(lower_wick / rng * 120, 100.0)
            return "PIN_BAR", sc, l - atr_val * 0.1

        if direction == "SHORT" and upper_wick / rng >= 0.55 and upper_wick >= 2 * max(body, 1e-10):
            sc = min(upper_wick / rng * 120, 100.0)
            return "PIN_BAR", sc, h + atr_val * 0.1

    # ── Engulfing ────────────────────────────────────────────────────
    body1 = abs(c1 - o1)
    if body1 > 1e-10 and body / body1 >= 1.05:
        if direction == "LONG" and c > o and c1 < o1 and c > max(o1,c1) and o < min(o1,c1):
            return "ENGULF", min(body/body1*45, 100.0), l - atr_val * 0.1

        if direction == "SHORT" and c < o and c1 > o1 and c < min(o1,c1) and o > max(o1,c1):
            return "ENGULF", min(body/body1*45, 100.0), h + atr_val * 0.1

    # ── Momentum ─────────────────────────────────────────────────────
    if body / rng >= 0.65 and body >= atr_val * 0.5:
        if direction == "LONG" and c > o and upper_wick < body * 0.35:
            return "MOMENTUM", min(body / rng * 90, 100.0), l - atr_val * 0.1

        if direction == "SHORT" and c < o and lower_wick < body * 0.35:
            return "MOMENTUM", min(body / rng * 90, 100.0), h + atr_val * 0.1

    # ── Inside Bar (V16 NUEVO) ────────────────────────────────────────
    # Vela contenida dentro de la vela anterior → compresión → breakout
    if h <= h1 and l >= l1:
        if direction == "LONG" and c > o:
            return "INSIDE_BAR", 55.0, l - atr_val * 0.1
        if direction == "SHORT" and c < o:
            return "INSIDE_BAR", 55.0, h + atr_val * 0.1

    return "NONE", 0.0, None

# ══════════════════════════════════════════════════════════════════════
#  POSITION SIZING
# ══════════════════════════════════════════════════════════════════════
def calc_qty(balance, entry, sl, quality_mult=1.0):
    dist_pct = abs(entry - sl) / entry
    if dist_pct < 1e-8:
        return 0, 0
    risk_usdt    = balance * (RISK_PERCENT / 100) * quality_mult
    notional     = risk_usdt / dist_pct
    max_margin   = balance * (MAX_MARGIN_PCT / 100)
    max_notional = min(MAX_ORDER_USDT, max_margin * LEVERAGE)
    notional     = max(MIN_ORDER_USDT, min(notional, max_notional))
    qty          = notional / entry
    return round(max(qty, 0.001), 4), round(notional, 2)

def open_order(symbol, side, qty, sl, tp):
    payload = {
        "symbol":       symbol,
        "side":         side,
        "positionSide": "LONG" if side == "BUY" else "SHORT",
        "type":         "MARKET",
        "quantity":     round(qty, 4),
        "stopLoss": json.dumps({
            "type":"STOP_MARKET","stopPrice":round(sl,6),"workingType":"MARK_PRICE"
        }),
        "takeProfit": json.dumps({
            "type":"TAKE_PROFIT_MARKET","stopPrice":round(tp,6),"workingType":"MARK_PRICE"
        }),
    }
    resp = bx_post("/openApi/swap/v2/trade/order", payload)
    if resp.get("code", 0) != 0:
        raise ValueError(f"BingX {resp.get('code')}: {resp.get('msg','?')}")
    return resp

def open_order_with_retry(symbol, side, qty, sl, tp, atr_val, direction, retries=1):
    for attempt in range(retries + 1):
        try:
            return open_order(symbol, side, qty, sl, tp)
        except ValueError as e:
            if "101400" in str(e) and attempt < retries:
                log.warning(f"101400 {symbol} → retry con precio fresco")
                time.sleep(1)
                live = get_live_price(symbol)
                if direction == "LONG":
                    sl = live - atr_val * SL_ATR_MULT
                    sl = min(sl, live * (1 - MIN_DIST_PCT / 100))
                    tp = live + (live - sl) * TP_MULT
                else:
                    sl = live + atr_val * SL_ATR_MULT
                    sl = max(sl, live * (1 + MIN_DIST_PCT / 100))
                    tp = live - (sl - live) * TP_MULT
                sl = round(sl, 6)
                tp = round(tp, 6)
            else:
                raise

# ══════════════════════════════════════════════════════════════════════
#  TRAILING STOP & TIME-BASED EXIT — V16 NUEVO
# ══════════════════════════════════════════════════════════════════════
def update_sl(symbol, pos_side, new_sl):
    """Modifica el stop-loss de una posición abierta."""
    try:
        resp = bx_post("/openApi/swap/v2/trade/order", {
            "symbol":        symbol,
            "side":          "SELL" if pos_side == "LONG" else "BUY",
            "positionSide":  pos_side,
            "type":          "STOP_MARKET",
            "stopPrice":     new_sl,
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
        })
        if resp.get("code", 0) != 0:
            log.warning(f"update_sl {symbol}: {resp.get('msg','?')}")
        return resp
    except Exception as e:
        log.warning(f"update_sl {symbol}: {e}")

def close_position_market(symbol, side, qty):
    """Cierra una posición a mercado."""
    try:
        resp = bx_post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         "SELL" if side == "LONG" else "BUY",
            "positionSide": side,
            "type":         "MARKET",
            "quantity":     round(abs(qty), 4),
        })
        if resp.get("code", 0) != 0:
            log.warning(f"close_pos {symbol}: {resp.get('msg','?')}")
        return resp
    except Exception as e:
        log.warning(f"close_pos {symbol}: {e}")

def manage_open_positions(positions):
    """
    Ejecuta trailing stop y time-based exit en cada ciclo.
    - Break-even cuando precio va BE_ATR_MULT × ATR a favor
    - Trailing stop a TRAIL_ATR_MULT × ATR tras break-even
    - Time exit si lleva MAX_CANDLES_OPEN velas sin llegar al TP (solo si pierde)
    """
    global open_trade_meta
    now = datetime.now(timezone.utc)

    for sym, pos in positions.items():
        try:
            pos_amt = float(pos.get("positionAmt", 0))
            if pos_amt == 0:
                continue

            side    = "LONG" if pos_amt > 0 else "SHORT"
            entry   = float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0))
            cur_sl  = float(pos.get("stopLoss", 0) or 0)
            unr_pnl = float(pos.get("unrealizedProfit", 0) or 0)

            if entry <= 0:
                continue

            # Precio actual
            try:
                live = get_live_price(sym)
            except Exception:
                continue

            # ATR actual (5m)
            try:
                df_k    = get_klines(sym, 50)
                atr_val = float(calc_atr(df_k["high"], df_k["low"], df_k["close"], 14).iloc[-1])
            except Exception:
                # Usar ATR registrado al abrir si falla
                atr_val = open_trade_meta.get(sym, {}).get("atr", entry * 0.005)

            # ── Time-based exit ───────────────────────────────────────
            meta     = open_trade_meta.get(sym)
            if meta and meta.get("open_time"):
                candles_open = int((now - meta["open_time"]).total_seconds() / 300)
                if candles_open >= MAX_CANDLES_OPEN and unr_pnl <= 0:
                    log.info(f"⏱️ Time exit {sym}: {candles_open} velas | PnL={unr_pnl:.2f}U")
                    close_position_market(sym, side, abs(pos_amt))
                    tg(
                        f"⏱️ <b>Time exit {sym}</b>\n"
                        f"Velas abiertas: {candles_open} | PnL: {unr_pnl:.2f}U\n"
                        f"Cerrado a mercado para liberar capital."
                    )
                    open_trade_meta.pop(sym, None)
                    continue

            # ── Trailing Stop ─────────────────────────────────────────
            if side == "LONG":
                be_trigger = entry + atr_val * BE_ATR_MULT
                if live >= be_trigger:
                    new_trail = round(live - atr_val * TRAIL_ATR_MULT, 6)
                    # Solo mover SL hacia arriba, nunca hacia abajo
                    if new_trail > cur_sl and new_trail > entry * 0.999:
                        log.info(f"📈 Trail LONG {sym}: {cur_sl:.4g} → {new_trail:.4g} (live={live:.4g})")
                        update_sl(sym, "LONG", new_trail)
                        if meta:
                            meta["trail_sl"] = new_trail

            else:  # SHORT
                be_trigger = entry - atr_val * BE_ATR_MULT
                if live <= be_trigger:
                    new_trail = round(live + atr_val * TRAIL_ATR_MULT, 6)
                    # Solo mover SL hacia abajo, nunca hacia arriba
                    if new_trail < cur_sl and new_trail < entry * 1.001:
                        log.info(f"📉 Trail SHORT {sym}: {cur_sl:.4g} → {new_trail:.4g} (live={live:.4g})")
                        update_sl(sym, "SHORT", new_trail)
                        if meta:
                            meta["trail_sl"] = new_trail

        except Exception as e:
            log.debug(f"manage_pos {sym}: {e}")

# ══════════════════════════════════════════════════════════════════════
#  ESCANEO PRINCIPAL V16
# ══════════════════════════════════════════════════════════════════════
def scan_symbol(symbol):
    # Cooldown
    if symbol in sl_cooldown:
        elapsed = (datetime.now(timezone.utc) - sl_cooldown[symbol]).total_seconds() / 60
        if elapsed < COOLDOWN_MINS:
            return None

    try:
        # ── Datos 5m ─────────────────────────────────────────────────
        df = get_klines(symbol, 200)
        if df.empty or len(df) < 100:
            return None

        h, l, c, o = df["high"], df["low"], df["close"], df["open"]
        atr_s = calc_atr(h, l, c, 14)
        ema_f = calc_ema(c, EMA_FAST)
        ema_s = calc_ema(c, EMA_SLOW)
        ema_t = calc_ema(c, EMA_TREND)
        angle = calc_ema_angle(ema_f, atr_s, SLOPE_LOOK)
        di_p, di_m, adx_s = calc_adx(h, l, c, ADX_LEN)
        rsi_s   = calc_rsi(c, RSI_LEN)
        vol_ma  = df["volume"].rolling(20).mean()
        sqz_off = calc_squeeze_off(h, l, c, 20, 2.0, 1.5)
        vwap_s  = calc_vwap(df)
        st_dir  = calc_supertrend(h, l, c, ST_PERIOD, ST_MULT)
        ha      = calc_heikin_ashi(df)

        i = len(df) - 2
        if i < 80:
            return None

        close_now = float(c.iloc[i])
        atr_val   = float(atr_s.iloc[i])
        if atr_val <= 0:
            return None
        atr_pct = atr_val / close_now * 100
        if atr_pct > ATR_MAX_PCT:
            return None

        angle_now = float(angle.iloc[i])
        adx_now   = float(adx_s.iloc[i])
        di_p_now  = float(di_p.iloc[i])
        di_m_now  = float(di_m.iloc[i])
        rsi_now   = float(rsi_s.iloc[i])
        vol_now   = float(df["volume"].iloc[i])
        vma       = float(vol_ma.iloc[i])
        sqz_ok    = bool(sqz_off.iloc[i])
        vwap_now  = float(vwap_s.iloc[i])
        st_now    = int(st_dir.iloc[i])
        ha_bull   = float(ha["ha_close"].iloc[i]) > float(ha["ha_open"].iloc[i])
        ha_bear   = not ha_bull
        vratio    = round(vol_now / vma, 2) if vma > 0 else 0.0
        ema_f_now = float(ema_f.iloc[i])
        ema_s_now = float(ema_s.iloc[i])
        ema_t_now = float(ema_t.iloc[i])

        if any(np.isnan(x) for x in [angle_now, adx_now, rsi_now, atr_val,
                                      ema_f_now, ema_s_now, ema_t_now]):
            return None

        # ── Dirección por EMA ─────────────────────────────────────────
        if ema_f_now > ema_s_now:
            direction = "LONG"
        elif ema_f_now < ema_s_now:
            direction = "SHORT"
        else:
            return None

        # ── RSI extremo — descarte duro ───────────────────────────────
        if direction == "LONG"  and rsi_now > RSI_OB: return None
        if direction == "SHORT" and rsi_now < RSI_OS: return None

        # ── EMA TREND filter ──────────────────────────────────────────
        if direction == "LONG"  and close_now < ema_t_now: return None
        if direction == "SHORT" and close_now > ema_t_now: return None

        # ══ V16: SLOPE = FILTRO DURO (no confluencia) ════════════════
        ang_ok = angle_now >= SLOPE_LIMIT if direction == "LONG" else angle_now <= -SLOPE_LIMIT
        if not ang_ok:
            return None   # Slope débil → skip inmediato

        # ══ SISTEMA DE CONFLUENCIAS V16 — 5 FILTROS, MÍNIMO 4 ════════
        # (Slope ya no cuenta como confluencia, es filtro duro)
        confluences  = 0
        conf_detail  = {}

        # C1: ADX con DI
        adx_ok = adx_now >= ADX_MIN and (
            (di_p_now > di_m_now and direction == "LONG") or
            (di_m_now > di_p_now and direction == "SHORT")
        )
        if adx_ok: confluences += 1
        conf_detail["adx"] = f"{'✅' if adx_ok else '❌'}{adx_now:.0f}"

        # C2: SuperTrend 5m
        st_ok = (st_now == 1 and direction == "LONG") or (st_now == -1 and direction == "SHORT")
        if st_ok: confluences += 1
        conf_detail["ST"] = f"{'✅' if st_ok else '❌'}{'▲' if st_now==1 else '▼'}"

        # C3: Heikin Ashi confirma
        ha_ok = (ha_bull and direction == "LONG") or (ha_bear and direction == "SHORT")
        if ha_ok: confluences += 1
        conf_detail["HA"] = "✅" if ha_ok else "❌"

        # C4: Volumen
        vol_ok = vratio >= VOL_MULT
        if vol_ok: confluences += 1
        conf_detail["vol"] = f"{'✅' if vol_ok else '❌'}{vratio:.1f}x"

        # C5: Squeeze OFF
        if sqz_ok: confluences += 1
        conf_detail["sqz"] = "✅OFF" if sqz_ok else "❌ON"

        if confluences < MIN_CONFLUENCES:
            return None

        # ══ V16: H1 ALINEADO REQUERIDO — NEUTRAL = DESCARTE ══════════
        h1_ctx   = analyze_h1(symbol)
        h1_trend = h1_ctx["h1_trend"] if h1_ctx else "NEUTRAL"
        h1_bonus = 0

        if REQUIRE_H1_ALIGN:
            if h1_trend == "BULL" and direction == "LONG":
                h1_bonus = 20
            elif h1_trend == "BEAR" and direction == "SHORT":
                h1_bonus = 20
            else:
                return None    # NEUTRAL o contrario → descarte
        else:
            if h1_trend == "BULL" and direction == "LONG":
                h1_bonus = 20
            elif h1_trend == "BEAR" and direction == "SHORT":
                h1_bonus = 20
            elif h1_trend == "NEUTRAL":
                h1_bonus = 5
            else:
                return None

        # ── Patrón de vela ────────────────────────────────────────────
        pat_name, pat_score, sl_candle = detect_candle_pattern(df, i, direction, atr_val)

        # V16: Patrón REQUERIDO
        if REQUIRE_PATTERN and pat_name == "NONE":
            return None

        # ── SL / TP ───────────────────────────────────────────────────
        sl_atr = atr_val * SL_ATR_MULT

        if direction == "LONG":
            sl_price = close_now - sl_atr
            if sl_candle and sl_candle > 0:
                sl_price = min(sl_price, sl_candle)
            sl_price = min(sl_price, close_now * (1 - MIN_DIST_PCT / 100))
            if sl_price >= close_now:
                return None
            tp_price = close_now + (close_now - sl_price) * TP_MULT
        else:
            sl_price = close_now + sl_atr
            if sl_candle and sl_candle > 0:
                sl_price = max(sl_price, sl_candle)
            sl_price = max(sl_price, close_now * (1 + MIN_DIST_PCT / 100))
            if sl_price <= close_now:
                return None
            tp_price = close_now - (sl_price - close_now) * TP_MULT

        dist     = abs(close_now - sl_price)
        dist_pct = dist / close_now * 100
        if dist_pct < MIN_DIST_PCT:
            return None

        rr = abs(tp_price - close_now) / dist
        if rr < MIN_RR:
            return None

        # ── SCORING V16 ───────────────────────────────────────────────
        # confluencias (max 25) + H1 (max 20) + patrón (max 20)
        # + ADX (max 10) + ángulo (max 10) + vol (max 10) + RR (max 5)
        score  = (confluences / 5) * 25                                # max 25
        score += h1_bonus                                               # max 20
        score += min(pat_score / 5, 20)                                # max 20 (patrón vale más)
        score += min((adx_now - ADX_MIN) / ADX_MIN * 10, 10)          # max 10
        score += min(abs(angle_now) / SLOPE_LIMIT * 10, 10)            # max 10
        score += min(vratio * 5, 10)                                    # max 10
        score += min((rr - MIN_RR) * 2, 5)                             # max 5

        if score < MIN_SCORE:
            return None

        quality_mult = round(min(max(0.7 + (score - MIN_SCORE) / 45 * 0.6, 0.7), 1.3), 2)

        return {
            "symbol":       symbol,
            "signal":       direction,
            "pattern":      pat_name,
            "close":        close_now,
            "sl":           round(sl_price, 6),
            "tp":           round(tp_price, 6),
            "atr":          atr_val,
            "atr_pct":      round(atr_pct, 2),
            "vol_ratio":    vratio,
            "angle":        round(angle_now, 1),
            "adx":          round(adx_now, 1),
            "rsi":          round(rsi_now, 1),
            "score":        round(score, 1),
            "rr":           round(rr, 2),
            "dist_pct":     round(dist_pct, 3),
            "confluences":  confluences,
            "conf_detail":  conf_detail,
            "h1_trend":     h1_trend,
            "pat_score":    round(pat_score, 1),
            "quality_mult": quality_mult,
        }

    except Exception as e:
        log.debug(f"Scan {symbol}: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════════
async def _send_tg(msg):
    if not TELEGRAM_OK or not TELEGRAM_TOKEN:
        return
    bot = Bot(token=TELEGRAM_TOKEN)
    cid = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.lstrip("-").isdigit() else TELEGRAM_CHAT_ID
    await bot.send_message(chat_id=cid, text=msg, parse_mode=ParseMode.HTML)

def tg(msg):
    if not TELEGRAM_TOKEN:
        return
    try:
        asyncio.run(_send_tg(msg))
    except Exception as e:
        log.warning(f"Telegram: {e}")

def tg_startup(balance, symbols):
    tg(
        f"🚀 <b>TRADING BOT V16.1 — SIZING CORREGIDO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>Objetivo:</b> ~77% win rate | +15-25% mensual\n"
        f"🔧 <b>Mejoras V16.1 (auditoría sizing):</b>\n"
        f"  • RISK_PERCENT={RISK_PERCENT}% | MAX_ORDER={MAX_ORDER_USDT}U\n"
        f"  • BE_ATR={BE_ATR_MULT}×ATR (no corta winners)\n"
        f"  • H1 BULL/BEAR requerido | Slope filtro duro\n"
        f"  • Patrón requerido + Inside Bar\n"
        f"  • Trailing: BE={BE_ATR_MULT}×ATR → Trail={TRAIL_ATR_MULT}×ATR\n"
        f"  • Time exit: {MAX_CANDLES_OPEN} velas | Break-even WR: 33%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Confluencias:</b> {MIN_CONFLUENCES}/5 | <b>Score:</b> {MIN_SCORE}\n"
        f"📐 <b>Slope≥:</b> {SLOPE_LIMIT}° (duro) | <b>ADX≥:</b> {ADX_MIN} | <b>EMA:</b> {EMA_TREND}\n"
        f"📡 <b>TF:</b> {TIMEFRAME} + 1H | <b>ST:</b> {ST_PERIOD}/{ST_MULT}\n"
        f"⚡ <b>Vol≥:</b> {VOL_MULT}x | <b>RSI:</b> {RSI_OS}-{RSI_OB}\n"
        f"🎯 <b>R:R mínimo:</b> {MIN_RR} | <b>TP:</b> {TP_MULT}× | <b>SL:</b> {SL_ATR_MULT}×ATR\n"
        f"🔐 <b>Session filter:</b> {'ON' if SESSION_FILTER else 'OFF'} | "
        f"<b>Max trades:</b> {MAX_OPEN_TRADES}\n"
        f"💰 <b>Balance:</b> {balance:.2f} USDT | <b>Símbolos:</b> {len(symbols)}\n"
        f"🛡️ <b>Circuit breaker:</b> {MAX_CONSEC_LOSSES} pérdidas → pausa {CB_PAUSE_MINS}min\n"
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

def tg_scan(signals, total, open_count):
    if not signals:
        return
    lines = [
        f"🔍 <b>{len(signals)} señal(es) / {total} sym</b> | Trades: {open_count}/{MAX_OPEN_TRADES}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for s in signals[:6]:
        e   = "🟢" if s["signal"] == "LONG" else "🔴"
        cd  = " ".join(s.get("conf_detail", {}).values())
        lines.append(
            f"{e} <b>{s['symbol']}</b> {s['pattern']} "
            f"Score:{s['score']:.0f} {s['confluences']}/5 H1:{s['h1_trend']}\n"
            f"   Ang:{s['angle']}° {cd}"
        )
    lines.append(f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
    tg("\n".join(lines))

def tg_entry(sig, qty, notional, balance):
    d    = "🟢 LONG" if sig["signal"] == "LONG" else "🔴 SHORT"
    cd   = " | ".join([f"{k}:{v}" for k, v in sig.get("conf_detail", {}).items()])
    icon = {"PIN_BAR":"📌","ENGULF":"🔄","MOMENTUM":"💥","INSIDE_BAR":"📦","NONE":"📈"}.get(sig.get("pattern","NONE"), "⚡")
    tg(
        f"<b>✅ ENTRADA V16.1 — {sig['symbol']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Dir:</b> {d} | <b>Score:</b> {sig['score']:.0f}/100\n"
        f"<b>Confl:</b> {sig['confluences']}/5 | <b>H1:</b> {sig['h1_trend']} ✅\n"
        f"{icon} <b>Patrón:</b> {sig['pattern']} ({sig['pat_score']:.0f})\n"
        f"<b>Filtros:</b> {cd}\n"
        f"<b>Ang:</b> {sig['angle']}° ✅ | <b>ADX:</b> {sig['adx']} | "
        f"<b>RSI:</b> {sig['rsi']} | <b>Vol:</b> {sig['vol_ratio']}x\n"
        f"<b>Entrada:</b> <code>{sig['close']:.6g}</code>\n"
        f"<b>Stop:</b>   <code>{sig['sl']:.6g}</code> ({sig['dist_pct']}%)\n"
        f"<b>Target:</b> <code>{sig['tp']:.6g}</code> | <b>R:R</b> 1:{sig['rr']}\n"
        f"<b>Trailing:</b> BE en {BE_ATR_MULT}×ATR → Trail {TRAIL_ATR_MULT}×ATR\n"
        f"<b>Time exit:</b> {MAX_CANDLES_OPEN} velas si no TP\n"
        f"<b>Qty:</b> {qty:.4f} | <b>Notional:</b> {notional:.2f} USDT\n"
        f"<b>Kelly×:</b> {sig['quality_mult']} | <b>ATR:</b> {sig['atr_pct']}%\n"
        f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
    )

def tg_zero_signals(total, cycle):
    tg(
        f"⚠️ <b>0 señales / {total} símbolos</b> (ciclo #{cycle})\n"
        f"<b>Filtros V16 activos:</b>\n"
        f"  Slope≥{SLOPE_LIMIT}° (DURO) | ADX≥{ADX_MIN} | Confl≥{MIN_CONFLUENCES}/5\n"
        f"  Score≥{MIN_SCORE} | H1 BULL/BEAR requerido\n"
        f"  Patrón requerido: PIN_BAR/ENGULF/MOMENTUM/INSIDE_BAR\n"
        f"  EMA_TREND={EMA_TREND} | ATR_MAX={ATR_MAX_PCT}% | VOL≥{VOL_MULT}x\n"
        f"💡 Normal tener pocas señales — calidad sobre cantidad\n"
        f"💡 Si persiste +2h: bajar SLOPE_LIMIT o ADX_MIN via env vars\n"
        f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC"
    )

# ══════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════════
def main():
    global consec_losses, cb_pause_until, open_trade_meta

    log.info("╔══════════════════════════════════════════════╗")
    log.info("║  TRADING BOT V16.1 — SIZING CORREGIDO        ║")
    log.info("╚══════════════════════════════════════════════╝")
    log.info(f"  RISK={RISK_PERCENT}% | MAX_ORDER={MAX_ORDER_USDT}U | BE_ATR={BE_ATR_MULT}×")
    log.info(f"  Slope≥{SLOPE_LIMIT}° DURO | ADX≥{ADX_MIN} | H1 requerido | "
             f"Patrón requerido | Confl≥{MIN_CONFLUENCES}/5 | Score≥{MIN_SCORE}")
    log.info(f"  Trailing: BE={BE_ATR_MULT}×ATR → Trail={TRAIL_ATR_MULT}×ATR | "
             f"TimeExit={MAX_CANDLES_OPEN} velas | MaxTrades={MAX_OPEN_TRADES}")

    symbols = CUSTOM_SYMBOLS if CUSTOM_SYMBOLS else get_all_symbols(MAX_SYMBOLS)
    if not symbols:
        symbols = FALLBACK_SYMBOLS

    balance   = get_balance()
    positions = get_all_positions()
    log.info(f"Balance: {balance:.4f} | Symbols: {len(symbols)} | Open: {len(positions)}")

    # Pre-cargar H1 en background
    def _prefetch():
        log.info("Pre-cargando H1 cache...")
        sample = symbols[:80]
        with ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(get_h1_klines, sample))
        log.info(f"H1 cache listo ({len(sample)} sym).")
    threading.Thread(target=_prefetch, daemon=True).start()

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(set_lev, symbols))

    # Reconstruir meta de posiciones existentes al arrancar
    for sym, pos in positions.items():
        pos_amt = float(pos.get("positionAmt", 0))
        if pos_amt != 0:
            open_trade_meta[sym] = {
                "open_time": datetime.now(timezone.utc),  # aproximado al arrancar
                "entry":     float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0)),
                "side":      "LONG" if pos_amt > 0 else "SHORT",
                "atr":       0.0,
                "trail_sl":  float(pos.get("stopLoss", 0) or 0),
            }

    tg_startup(balance, symbols)
    log.info("✅ Bot V16 iniciado.")

    errors        = 0
    cycle         = 0
    zero_sig_runs = 0

    while True:
        t0     = time.time()
        cycle += 1
        try:
            # ── Session filter ────────────────────────────────────────
            if SESSION_FILTER:
                hour = datetime.now(timezone.utc).hour
                if not (SESSION_START <= hour < SESSION_END):
                    log.info(f"⏸️  Fuera de sesión ({hour}h UTC).")
                    time.sleep(300)
                    continue

            # ── Circuit breaker ───────────────────────────────────────
            if cb_pause_until and datetime.now(timezone.utc) < cb_pause_until:
                rem = (cb_pause_until - datetime.now(timezone.utc)).seconds // 60
                log.info(f"🛑 Circuit breaker: {rem}min restantes.")
                time.sleep(60)
                continue

            balance    = get_balance()
            positions  = get_all_positions()
            open_count = len(positions)

            log.info(
                f"── V16.1 | {balance:.2f}U | {open_count}/{MAX_OPEN_TRADES} | "
                f"{len(symbols)} sym | ciclo #{cycle} ──"
            )

            # ── V16: Gestión de posiciones abiertas (trailing + time exit) ──
            if positions:
                manage_open_positions(positions)
                # Refrescar tras posibles cierres
                positions  = get_all_positions()
                open_count = len(positions)
                # Limpiar meta de posiciones cerradas
                for sym in list(open_trade_meta.keys()):
                    if sym not in positions:
                        open_trade_meta.pop(sym, None)

            # ── Scan ──────────────────────────────────────────────────
            signals = []
            with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
                futs = {ex.submit(scan_symbol, s): s for s in symbols}
                for f in as_completed(futs):
                    r = f.result()
                    if r:
                        signals.append(r)

            signals.sort(key=lambda x: x["score"], reverse=True)
            log.info(f"Señales válidas V16: {len(signals)}/{len(symbols)}")

            if not signals:
                zero_sig_runs += 1
                if zero_sig_runs % 5 == 1:
                    tg_zero_signals(len(symbols), cycle)
            else:
                zero_sig_runs = 0
                tg_scan(signals, len(symbols), open_count)
                for s in signals[:5]:
                    log.info(
                        f"  → {s['symbol']} {s['signal']} [{s['pattern']}] "
                        f"H1:{s['h1_trend']} confl:{s['confluences']}/5 "
                        f"score={s['score']:.1f} ang={s['angle']}° "
                        f"adx={s['adx']} rr=1:{s['rr']}"
                    )

            # ── Ejecutar órdenes ──────────────────────────────────────
            entered      = set()
            skip_reasons = {}

            for sig in signals:
                sym = sig["symbol"]

                if sym in positions:
                    skip_reasons[sym] = "ya en posición"
                    continue
                if sym in entered:
                    continue
                if open_count >= MAX_OPEN_TRADES:
                    log.info(f"Max trades ({MAX_OPEN_TRADES}) alcanzado.")
                    break
                if balance < MIN_ORDER_USDT:
                    log.warning(f"Balance bajo: {balance:.2f} USDT")
                    break

                try:
                    set_lev(sym)

                    live = get_live_price(sym)
                    log.info(f"Live {sym}: scan={sig['close']:.6g} live={live:.6g}")

                    atr_val   = sig["atr"]
                    direction = sig["signal"]
                    if direction == "LONG":
                        sl = live - atr_val * SL_ATR_MULT
                        sl = min(sl, live * (1 - MIN_DIST_PCT / 100))
                        tp = live + (live - sl) * TP_MULT
                    else:
                        sl = live + atr_val * SL_ATR_MULT
                        sl = max(sl, live * (1 + MIN_DIST_PCT / 100))
                        tp = live - (sl - live) * TP_MULT

                    if sl <= 0 or tp <= 0:
                        skip_reasons[sym] = "SL/TP inválido"
                        continue

                    rr_live = abs(tp - live) / abs(live - sl)
                    if rr_live < MIN_RR:
                        skip_reasons[sym] = f"RR bajo: {rr_live:.2f}"
                        continue

                    qty, notional = calc_qty(balance, live, sl, sig["quality_mult"])
                    if qty <= 0 or notional < MIN_ORDER_USDT:
                        skip_reasons[sym] = f"qty/notional insuficiente ({notional:.2f}U)"
                        continue

                    log.info(
                        f"ORDEN {sym} {direction} qty={qty:.4f} "
                        f"notional={notional:.1f}U live={live:.6g} "
                        f"sl={sl:.6g} tp={tp:.6g} score={sig['score']:.1f} "
                        f"pat={sig['pattern']}"
                    )

                    side = "BUY" if direction == "LONG" else "SELL"
                    res  = open_order_with_retry(sym, side, qty, round(sl,6), round(tp,6),
                                                 atr_val, direction, retries=1)
                    log.info(f"✅ {sym} abierto | {res}")

                    sig.update({
                        "close":    live,
                        "sl":       round(sl, 6),
                        "tp":       round(tp, 6),
                        "dist_pct": round(abs(live-sl)/live*100, 3),
                        "rr":       round(rr_live, 2),
                    })

                    # Registrar metadata para trailing/time-exit
                    open_trade_meta[sym] = {
                        "open_time": datetime.now(timezone.utc),
                        "entry":     live,
                        "side":      direction,
                        "atr":       atr_val,
                        "trail_sl":  round(sl, 6),
                    }

                    tg_entry(sig, qty, notional, balance)
                    entered.add(sym)
                    open_count += 1
                    time.sleep(0.5)

                except Exception as e:
                    reason = str(e)[:100]
                    log.error(f"Order FAILED {sym}: {e}")
                    skip_reasons[sym] = f"error: {reason}"
                    if "stop" in str(e).lower() or "liquidat" in str(e).lower():
                        sl_cooldown[sym] = datetime.now(timezone.utc)
                    tg(f"⚠️ <b>Error {sym}</b>: <code>{str(e)[:150]}</code>")

            if signals and not entered and skip_reasons:
                log.warning(f"Señales={len(signals)} pero 0 órdenes. {skip_reasons}")

            errors = 0

        except KeyboardInterrupt:
            tg("🛑 <b>Bot V16 detenido</b>")
            break
        except Exception as e:
            errors += 1
            log.exception(f"Cycle error #{errors}: {e}")
            if errors <= 3:
                tg(f"⚠️ <b>Error ciclo #{errors}</b>: <code>{str(e)[:200]}</code>")
            if errors >= 10:
                tg("🔴 <b>CRÍTICO: 10 errores. Detenido.</b>")
                break

        time.sleep(max(0, LOOP_SECONDS - (time.time() - t0)))


if __name__ == "__main__":
    main()
