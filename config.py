"""
SAMA APEX Bot - Configuration
Centraliza todos los parámetros del sistema
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── BingX API ───────────────────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

# ─── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Trading Universe ─────────────────────────────────────────────────────────
# Puedes poner varios símbolos: el bot escanea y elige el mejor setup
SYMBOLS = os.getenv("SYMBOLS", "BTC-USDT,ETH-USDT,SOL-USDT,BNB-USDT").split(",")

# ─── Timeframes ───────────────────────────────────────────────────────────────
TF_LOCAL   = os.getenv("TF_LOCAL",   "5m")    # Timeframe de entrada
TF_MACRO_1 = os.getenv("TF_MACRO_1", "15m")   # Filtro intermedio
TF_MACRO_2 = os.getenv("TF_MACRO_2", "1h")    # Filtro macro

# ─── SAMA Parameters (replicados exactos del Pine Script) ─────────────────────
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

# ─── Risk Management ──────────────────────────────────────────────────────────
LEVERAGE          = int(os.getenv("LEVERAGE",          "5"))
RISK_PER_TRADE    = float(os.getenv("RISK_PER_TRADE",  "0.01"))   # 1% del balance por trade
MAX_OPEN_TRADES   = int(os.getenv("MAX_OPEN_TRADES",   "3"))
DAILY_LOSS_LIMIT  = float(os.getenv("DAILY_LOSS_LIMIT","0.05"))   # Circuit breaker: -5% día
MIN_CONFLUENCE    = int(os.getenv("MIN_CONFLUENCE",    "60"))      # Score mínimo para entrar
TRAILING_ENABLED  = os.getenv("TRAILING_ENABLED", "true").lower() == "true"
TRAILING_ATR_MULT = float(os.getenv("TRAILING_ATR_MULT", "1.5"))

# ─── Funding Rate Filter (EDGE ESPECIAL) ──────────────────────────────────────
FUNDING_FILTER    = os.getenv("FUNDING_FILTER", "true").lower() == "true"
FUNDING_EXTREME   = float(os.getenv("FUNDING_EXTREME", "0.0008"))  # 0.08% extremo

# ─── Session Filter (EDGE ESPECIAL) ───────────────────────────────────────────
# Solo opera en sesiones de alta liquidez (London + NY)
SESSION_FILTER    = os.getenv("SESSION_FILTER", "true").lower() == "true"
# Horas UTC de sesiones activas: London 07-16, NY overlap 13-21
SESSION_HOURS_UTC = [(7, 16), (13, 21)]

# ─── Bot Timing ───────────────────────────────────────────────────────────────
SCAN_INTERVAL     = int(os.getenv("SCAN_INTERVAL", "60"))   # segundos entre scans
HEALTH_PORT       = int(os.getenv("PORT", "8080"))           # Railway healthcheck

# ─── Candles needed (buffer para cálculos) ────────────────────────────────────
CANDLES_NEEDED = max(AMA_LENGTH + SLOPE_PERIOD + 10, 250)
