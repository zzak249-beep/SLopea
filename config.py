"""
QF×JP Bot v6.3 — Config
Todas las variables via env vars con defaults seguros.
"""
import os
from dotenv import load_dotenv

load_dotenv()

def _bool(key: str, default: bool) -> bool:
    v = os.getenv(key, str(default)).strip().lower()
    return v in ("true", "1", "yes")

def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default

def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default

def _list(key: str, default: str) -> list[str]:
    raw = os.getenv(key, default).strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]

# ── BingX API ────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

# ── Telegram ─────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Modo de operación ─────────────────────────────────
MODE = os.getenv("MODE", "SIGNAL").upper()   # SIGNAL | LIVE

# ── Capital y riesgo ──────────────────────────────────
CAPITAL          = _float("CAPITAL", 1000.0)
RISK_PCT         = _float("RISK_PCT", 1.0)
LEVERAGE         = _int("LEVERAGE", 10)
MAX_OPEN_TRADES  = _int("MAX_OPEN_TRADES", 5)
MAX_DAILY_TRADES = _int("MAX_DAILY_TRADES", 20)

# ── Umbrales de señal ─────────────────────────────────
MIN_SCORE  = _float("MIN_SCORE", 55.0)
FUEL_SCORE = _float("FUEL_SCORE", 68.0)
SUP_SCORE  = _float("SUP_SCORE", 80.0)
MIN_TIER   = os.getenv("MIN_TIER", "FUEL").upper()   # STD | FUEL | SUP

# ── Lógica de entrada ─────────────────────────────────
REQUIRE_TL_BREAK  = _bool("REQUIRE_TL_BREAK", True)
HTF_MIN_ALIGNED   = _int("HTF_MIN_ALIGNED", 2)

# ── Scanner ───────────────────────────────────────────
SCAN_INTERVAL   = _int("SCAN_INTERVAL", 180)
# TOP_N_SYMBOLS=0 → TODAS las monedas de BingX
TOP_N_SYMBOLS   = _int("TOP_N_SYMBOLS", 0)
BLACKLIST       = set(_list("BLACKLIST", ""))
MIN_VOLUME_USDT = _float("MIN_VOLUME_USDT", 0.0)  # 0 = sin filtro

# ── Timeframes ────────────────────────────────────────
TIMEFRAME        = os.getenv("TIMEFRAME", "3m")
HTF_TIMEFRAME    = os.getenv("HTF_TIMEFRAME", "15m")
HTF2_TIMEFRAME   = os.getenv("HTF2_TIMEFRAME", "1h")
HTF5_TIMEFRAME   = os.getenv("HTF5_TIMEFRAME", "4h")

# ── ATR / SL / TP ─────────────────────────────────────
ATR_LEN      = _int("ATR_LEN", 10)
SL_ATR_MULT  = _float("SL_ATR_MULT", 1.0)
TP1_ATR_MULT = _float("TP1_ATR_MULT", 1.5)
TP2_ATR_MULT = _float("TP2_ATR_MULT", 3.0)

# ── ADX ───────────────────────────────────────────────
ADX_LEN     = _int("ADX_LEN", 14)
ADX_TREND   = _float("ADX_TREND", 25.0)
ADX_LATERAL = _float("ADX_LATERAL", 20.0)

# ── Kelly ─────────────────────────────────────────────
KELLY_WIN_RATE = _float("KELLY_WIN_RATE", 0.55)
KELLY_RR       = _float("KELLY_RR", 1.8)
KELLY_FRACTION = _float("KELLY_FRACTION", 0.25)

# ── Circuit Breaker ───────────────────────────────────
CB_ENABLED  = _bool("CB_ENABLED", True)
CB_ATR_MULT = _float("CB_ATR_MULT", 3.0)
CB_BARS     = _int("CB_BARS", 10)

# ── Gestión de posiciones / cierre ───────────────────
POSITION_CHECK_INTERVAL = _int("POSITION_CHECK_INTERVAL", 30)  # segundos
BREAKEVEN_ATR_MULT      = _float("BREAKEVEN_ATR_MULT", 1.0)    # mover SL a BE tras 1 ATR

# ── Port ──────────────────────────────────────────────
PORT = _int("PORT", 8080)
