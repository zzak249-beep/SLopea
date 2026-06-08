"""
config.py — Variables de entorno para QF×JP Bot
"""
import os

# ── BingX ──────────────────────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Riesgo ─────────────────────────────────────────────────────────────────
CAPITAL          = float(os.getenv("CAPITAL", "1000"))
RISK_PCT         = float(os.getenv("RISK_PCT", "1.0"))       # % por trade
LEVERAGE         = int(os.getenv("LEVERAGE", "10"))
MAX_OPEN_TRADES  = int(os.getenv("MAX_OPEN_TRADES", "5"))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "20"))

# ── Kelly ──────────────────────────────────────────────────────────────────
KELLY_WIN_RATE   = float(os.getenv("KELLY_WIN_RATE", "0.55"))
KELLY_RR         = float(os.getenv("KELLY_RR", "1.8"))
KELLY_FRACTION   = float(os.getenv("KELLY_FRACTION", "0.25"))

# ── Señales ────────────────────────────────────────────────────────────────
MIN_SCORE        = int(os.getenv("MIN_SCORE", "55"))          # umbral STD
FUEL_SCORE       = int(os.getenv("FUEL_SCORE", "68"))         # umbral FUEL
SUP_SCORE        = int(os.getenv("SUP_SCORE", "80"))          # umbral SUP
MIN_TIER         = os.getenv("MIN_TIER", "STD")               # STD | FUEL | SUP
REQUIRE_TL_BREAK = os.getenv("REQUIRE_TL_BREAK", "true").lower() == "true"
REQUIRE_CHoCH    = os.getenv("REQUIRE_CHoCH", "false").lower() == "true"

# ── Scanner ────────────────────────────────────────────────────────────────
SCAN_INTERVAL    = int(os.getenv("SCAN_INTERVAL", "180"))     # segundos (3m velas)
TOP_N_SYMBOLS    = int(os.getenv("TOP_N_SYMBOLS", "30"))      # top N por volumen
BLACKLIST        = os.getenv("BLACKLIST", "").split(",")       # ej: "SOL-USDT,XRP-USDT"
TIMEFRAME        = os.getenv("TIMEFRAME", "3m")
HTF_TIMEFRAME    = os.getenv("HTF_TIMEFRAME", "15m")
HTF2_TIMEFRAME   = os.getenv("HTF2_TIMEFRAME", "1h")
HTF5_TIMEFRAME   = os.getenv("HTF5_TIMEFRAME", "4h")

# ── ATR / SL / TP ──────────────────────────────────────────────────────────
ATR_LEN          = int(os.getenv("ATR_LEN", "10"))
SL_ATR_MULT      = float(os.getenv("SL_ATR_MULT", "1.0"))
TP1_ATR_MULT     = float(os.getenv("TP1_ATR_MULT", "1.5"))
TP2_ATR_MULT     = float(os.getenv("TP2_ATR_MULT", "3.0"))

# ── Circuit Breaker ────────────────────────────────────────────────────────
CB_ENABLED       = os.getenv("CB_ENABLED", "true").lower() == "true"
CB_ATR_MULT      = float(os.getenv("CB_ATR_MULT", "3.0"))
CB_BARS          = int(os.getenv("CB_BARS", "10"))

# ── ADX ────────────────────────────────────────────────────────────────────
ADX_LEN          = int(os.getenv("ADX_LEN", "14"))
ADX_TREND        = int(os.getenv("ADX_TREND", "25"))
ADX_LATERAL      = int(os.getenv("ADX_LATERAL", "20"))

# ── FVG ────────────────────────────────────────────────────────────────────
FVG_MIN_ATR      = float(os.getenv("FVG_MIN_ATR", "0.3"))
FVG_BARS         = int(os.getenv("FVG_BARS", "40"))

# ── MFI ────────────────────────────────────────────────────────────────────
MFI_LEN          = int(os.getenv("MFI_LEN", "14"))
MFI_OB           = int(os.getenv("MFI_OB", "80"))
MFI_OS           = int(os.getenv("MFI_OS", "20"))

# ── Pesos Score ────────────────────────────────────────────────────────────
W_SCORE    = float(os.getenv("W_SCORE",  "0.22"))
W_CVD      = float(os.getenv("W_CVD",    "0.20"))
W_MOM      = float(os.getenv("W_MOM",    "0.15"))
W_DECAY    = float(os.getenv("W_DECAY",  "0.08"))
W_HTF      = float(os.getenv("W_HTF",    "0.14"))
W_STRUC    = float(os.getenv("W_STRUC",  "0.08"))
W_VP       = float(os.getenv("W_VP",     "0.05"))
W_SENT     = float(os.getenv("W_SENT",   "0.04"))
W_VDI      = float(os.getenv("W_VDI",    "0.04"))

# ── Modo ───────────────────────────────────────────────────────────────────
MODE             = os.getenv("MODE", "SIGNAL")                # SIGNAL | LIVE
PORT             = int(os.getenv("PORT", "8080"))
