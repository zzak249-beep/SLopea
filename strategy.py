import math
import logging
import numpy as np
from config import EMA_FAST, EMA_SLOW, MIN_SLOPE_DEG, SLOPE_LOOKBACK, ATR_PERIOD

logger = logging.getLogger(__name__)


# ─── Indicators ───────────────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    """Average True Range (last value)."""
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    # Wilder smoothing
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val


def slope_angle(ema_series: list[float], lookback: int) -> float:
    """
    Calculate EMA slope angle in degrees.
    Method: percentage change per bar → arctan → degrees.
    tan(30°) ≈ 0.577  →  ~0.577% change per bar minimum.
    """
    if len(ema_series) < lookback + 1:
        return 0.0
    e_now  = ema_series[-1]
    e_prev = ema_series[-(lookback + 1)]
    if e_prev == 0:
        return 0.0
    pct_change_per_bar = ((e_now - e_prev) / e_prev * 100) / lookback
    angle = math.degrees(math.atan(pct_change_per_bar))
    return angle  # positive = upslope, negative = downslope


# ─── Signal logic ─────────────────────────────────────────────────────────────

def analyze(candles: list) -> dict:
    """
    Returns signal dict:
    {
        "signal": "LONG" | "SHORT" | "NONE",
        "slope":  float (degrees),
        "ema_fast": float,
        "ema_slow": float,
        "atr":    float,
        "close":  float,
    }
    """
    if len(candles) < max(EMA_SLOW + 5, ATR_PERIOD + 5):
        return {"signal": "NONE", "slope": 0, "ema_fast": 0, "ema_slow": 0, "atr": 0, "close": 0}

    closes = [c[4] for c in candles]
    highs  = [c[2] for c in candles]
    lows   = [c[3] for c in candles]

    ema_fast_series = ema(closes, EMA_FAST)
    ema_slow_series = ema(closes, EMA_SLOW)

    if len(ema_fast_series) < SLOPE_LOOKBACK + 2 or len(ema_slow_series) < 2:
        return {"signal": "NONE", "slope": 0, "ema_fast": 0, "ema_slow": 0, "atr": 0, "close": 0}

    ef_now   = ema_fast_series[-1]
    ef_prev  = ema_fast_series[-2]
    es_now   = ema_slow_series[-1]
    es_prev  = ema_slow_series[-2]

    fast_slope = slope_angle(ema_fast_series, SLOPE_LOOKBACK)
    slow_slope = slope_angle(ema_slow_series, SLOPE_LOOKBACK)

    current_atr = atr(highs, lows, closes, ATR_PERIOD)
    close       = closes[-1]

    signal = "NONE"

    # ── LONG: EMA7 crosses above EMA17, slope >= +MIN_SLOPE_DEG ────────────
    if (ef_now > es_now and ef_prev <= es_prev):
        if fast_slope >= MIN_SLOPE_DEG:
            signal = "LONG"
        else:
            logger.debug(f"LONG blocked: slope {fast_slope:.1f}° < {MIN_SLOPE_DEG}°")

    # ── SHORT: EMA7 crosses below EMA17, slope <= -MIN_SLOPE_DEG ───────────
    elif (ef_now < es_now and ef_prev >= es_prev):
        if fast_slope <= -MIN_SLOPE_DEG:
            signal = "SHORT"
        else:
            logger.debug(f"SHORT blocked: slope {fast_slope:.1f}° > -{MIN_SLOPE_DEG}°")

    return {
        "signal":   signal,
        "slope":    round(fast_slope, 2),
        "ema_fast": round(ef_now, 6),
        "ema_slow": round(es_now, 6),
        "atr":      round(current_atr, 8),
        "close":    round(close, 8),
    }
