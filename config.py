import os

# ─── BingX API ───────────────────────────────────────────────────────────────
BINGX_API_KEY    = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BINGX_BASE_URL   = "https://open-api.bingx.com"

# ─── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Strategy ─────────────────────────────────────────────────────────────────
EMA_FAST         = int(os.getenv("EMA_FAST", "7"))
EMA_SLOW         = int(os.getenv("EMA_SLOW", "17"))
TIMEFRAME        = os.getenv("TIMEFRAME", "5m")       # 1m 3m 5m 15m
MIN_SLOPE_DEG    = float(os.getenv("MIN_SLOPE_DEG", "30"))   # minimum slope angle
SLOPE_LOOKBACK   = int(os.getenv("SLOPE_LOOKBACK", "3"))     # bars for slope calc

# ─── Risk ─────────────────────────────────────────────────────────────────────
LEVERAGE         = int(os.getenv("LEVERAGE", "10"))
RISK_PCT         = float(os.getenv("RISK_PCT", "1.0"))       # % of balance per trade
ATR_SL_MULT      = float(os.getenv("ATR_SL_MULT", "1.5"))    # ATR multiplier for SL
ATR_TP_MULT      = float(os.getenv("ATR_TP_MULT", "2.5"))    # ATR multiplier for TP
ATR_PERIOD       = int(os.getenv("ATR_PERIOD", "14"))
MAX_OPEN_TRADES  = int(os.getenv("MAX_OPEN_TRADES", "5"))

# ─── Scanner ──────────────────────────────────────────────────────────────────
SCAN_INTERVAL    = int(os.getenv("SCAN_INTERVAL", "60"))     # seconds between scans
MIN_VOLUME_USDT  = float(os.getenv("MIN_VOLUME_USDT", "5000000"))  # 24h volume filter
MAX_WORKERS      = int(os.getenv("MAX_WORKERS", "10"))       # parallel threads
CANDLES_NEEDED   = 60                                         # history candles to fetch

# ─── Margin mode ──────────────────────────────────────────────────────────────
MARGIN_TYPE      = os.getenv("MARGIN_TYPE", "ISOLATED")      # ISOLATED or CROSSED
