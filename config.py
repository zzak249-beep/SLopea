import os
from dotenv import load_dotenv
load_dotenv()

BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# SYMBOLS vacío = auto-carga todos los pares de BingX
_sym = os.getenv("SYMBOLS", "")
SYMBOLS = [s.strip() for s in _sym.split(",") if s.strip()] if _sym.strip() else []

TF_LOCAL   = os.getenv("TF_LOCAL",   "5m")
TF_MACRO_1 = os.getenv("TF_MACRO_1", "15m")
TF_MACRO_2 = os.getenv("TF_MACRO_2", "1h")

AMA_LENGTH     = int(os.getenv("AMA_LENGTH",     "200"))
MAJOR_LENGTH   = int(os.getenv("MAJOR_LENGTH",   "14"))
MINOR_LENGTH   = int(os.getenv("MINOR_LENGTH",   "6"))
SLOPE_PERIOD   = int(os.getenv("SLOPE_PERIOD",   "34"))
SLOPE_RANGE    = int(os.getenv("SLOPE_RANGE",    "25"))
FLAT_THRESHOLD = int(os.getenv("FLAT_THRESHOLD", "17"))
ATR_PERIOD     = int(os.getenv("ATR_PERIOD",     "14"))
ATR_MULT       = float(os.getenv("ATR_MULT",     "2.0"))
RVOL_PERIOD    = int(os.getenv("RVOL_PERIOD",    "50"))
RVOL_MIN       = float(os.getenv("RVOL_MIN",     "1.2"))

LEVERAGE          = int(os.getenv("LEVERAGE",           "5"))
RISK_PER_TRADE    = float(os.getenv("RISK_PER_TRADE",   "0.02"))
MAX_OPEN_TRADES   = int(os.getenv("MAX_OPEN_TRADES",    "5"))
DAILY_LOSS_LIMIT  = float(os.getenv("DAILY_LOSS_LIMIT", "0.06"))
MIN_CONFLUENCE    = int(os.getenv("MIN_CONFLUENCE",     "55"))
TRAILING_ENABLED  = os.getenv("TRAILING_ENABLED", "true").lower() == "true"
TRAILING_ATR_MULT = float(os.getenv("TRAILING_ATR_MULT", "1.5"))

FUNDING_FILTER  = os.getenv("FUNDING_FILTER", "true").lower() == "true"
FUNDING_EXTREME = float(os.getenv("FUNDING_EXTREME", "0.0008"))

SESSION_FILTER    = os.getenv("SESSION_FILTER", "false").lower() == "true"
SESSION_HOURS_UTC = [(7, 16), (13, 21)]

SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL", "90"))
HEALTH_PORT    = int(os.getenv("PORT", "8080"))
CANDLES_NEEDED = max(AMA_LENGTH + SLOPE_PERIOD + 10, 250)

# Filtros para el scanner automático de pares
MIN_VOLUME_USDT   = float(os.getenv("MIN_VOLUME_USDT",  "5000000"))   # 5M USDT 24h mínimo
MAX_SYMBOLS_SCAN  = int(os.getenv("MAX_SYMBOLS_SCAN",   "60"))        # máx pares a escanear
