"""
SAMA APEX Bot - Indicators Engine FIXED v2
Port exacto del Pine Script v6. prev_trend calculado para los 3 TFs.
"""
import math
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from config import (
    AMA_LENGTH, MAJOR_LENGTH, MINOR_LENGTH,
    SLOPE_PERIOD, SLOPE_RANGE, FLAT_THRESHOLD,
    ATR_PERIOD, ATR_MULT, RVOL_PERIOD, RVOL_MIN,
    SESSION_FILTER, SESSION_HOURS_UTC, FUNDING_EXTREME,
)


def calculate_ama(close: np.ndarray, length: int, min_l: int, maj_l: int) -> np.ndarray:
    min_alpha = 2.0 / (min_l + 1)
    maj_alpha = 2.0 / (maj_l + 1)
    n, ama_val = len(close), math.nan
    ama = np.full(n, math.nan)
    for i in range(length, n):
        w = close[i - length: i + 1]
        hh, ll = w.max(), w.min()
        denom = hh - ll
        mult  = abs(2 * close[i] - ll - hh) / denom if denom != 0 else 0.0
        fa    = (mult * (min_alpha - maj_alpha) + maj_alpha) ** 2
        if math.isnan(ama_val):
            ama_val = close[i]
        else:
            ama_val = (close[i] - ama_val) * fa + ama_val
        ama[i] = ama_val
    return ama


def calculate_slope(ama: np.ndarray, close: np.ndarray,
                    sp: int, sr: int) -> np.ndarray:
    pi = math.pi
    n  = len(close)
    slopes = np.zeros(n)
    for i in range(sp + 2, n):
        if math.isnan(ama[i]) or math.isnan(ama[i-2]):
            continue
        wh = close[i - sp + 1: i + 1].max()
        wl = close[i - sp + 1: i + 1].min()
        if wh - wl == 0:
            continue
        slope_range = sr / (wh - wl) * wl
        dt  = (ama[i-2] - ama[i]) / close[i] * slope_range
        c   = math.sqrt(1 + dt * dt)
        ang = round(180 * math.acos(1 / c) / pi)
        slopes[i] = -ang if dt > 0 else ang
    return slopes


def calculate_atr(high: np.ndarray, low: np.ndarray,
                  close: np.ndarray, period: int = 14) -> np.ndarray:
    n   = len(close)
    tr  = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i-1]),
                    abs(low[i]  - close[i-1]))
    alpha = 1.0 / period
    atr   = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = tr[i] * alpha + atr[i-1] * (1 - alpha)
    return atr


def calculate_rvol(volume: np.ndarray, period: int = 50) -> np.ndarray:
    n    = len(volume)
    rvol = np.zeros(n)
    for i in range(period, n):
        avg = volume[i - period: i].mean()
        rvol[i] = volume[i] / avg if avg > 0 else 0.0
    return rvol


def classify_trend(slope: float, flat: int = FLAT_THRESHOLD) -> str:
    if slope > flat:   return "BULL"
    if slope <= -flat: return "BEAR"
    return "CHOP"


def is_active_session() -> bool:
    if not SESSION_FILTER:
        return True
    hour = datetime.now(timezone.utc).hour
    for start, end in SESSION_HOURS_UTC:
        if start <= hour < end:
            return True
    return False


def process_sama(df: pd.DataFrame) -> dict:
    """Calcula todas las métricas SAMA para el último cierre del DataFrame."""
    close  = df["close"].values.astype(float)
    high   = df["high"].values.astype(float)
    low    = df["low"].values.astype(float)
    volume = df["volume"].values.astype(float)

    ama   = calculate_ama(close, AMA_LENGTH, MINOR_LENGTH, MAJOR_LENGTH)
    slope = calculate_slope(ama, close, SLOPE_PERIOD, SLOPE_RANGE)
    atr   = calculate_atr(high, low, close, ATR_PERIOD)
    rvol  = calculate_rvol(volume, RVOL_PERIOD)

    last_slope  = float(slope[-1])
    prev_slope  = float(slope[-2]) if len(slope) > 1 else 0.0
    last_ama    = float(ama[-1])
    last_atr    = float(atr[-1])
    last_rvol   = float(rvol[-1])
    last_close  = float(close[-1])

    return {
        "ama":        last_ama,
        "slope":      last_slope,
        "trend":      classify_trend(last_slope),
        "prev_trend": classify_trend(prev_slope),   # ← necesario para señal correcta
        "atr":        last_atr,
        "rvol":       last_rvol,
        "has_volume": last_rvol >= RVOL_MIN,
        "upper_band": last_ama + last_atr * ATR_MULT,
        "lower_band": last_ama - last_atr * ATR_MULT,
        "close":      last_close,
    }


def confluence_score(local: dict, m1: dict, m2: dict,
                     funding_rate: float = 0.0,
                     session_active: bool = True) -> dict:
    """Score 0-100: calidad de la señal."""
    lt, m1t, m2t = local["trend"], m1["trend"], m2["trend"]

    if lt == "CHOP" or lt != m1t or lt != m2t:
        return {"score": 0, "direction": None,
                "lt": lt, "m1t": m1t, "m2t": m2t,
                "avg_slope": 0, "avg_rvol": 0,
                "funding": funding_rate, "session": session_active}

    direction = "LONG" if lt == "BULL" else "SHORT"
    score     = 40  # alineación 3 TF

    avg_slope = (abs(local["slope"]) + abs(m1["slope"]) + abs(m2["slope"])) / 3
    score    += min(20.0, avg_slope / 45.0 * 20)

    avg_rvol  = (local["rvol"] + m1["rvol"] + m2["rvol"]) / 3
    if avg_rvol >= RVOL_MIN:
        score += min(15.0, (avg_rvol - 1.0) * 8)

    if session_active:
        score += 10

    if direction == "LONG":
        if funding_rate < -FUNDING_EXTREME:   score += 15
        elif funding_rate > FUNDING_EXTREME:  score -= 20
    else:
        if funding_rate > FUNDING_EXTREME:    score += 15
        elif funding_rate < -FUNDING_EXTREME: score -= 20

    return {
        "score":     max(0, min(100, round(score))),
        "direction": direction,
        "lt": lt, "m1t": m1t, "m2t": m2t,
        "avg_slope": round(avg_slope, 2),
        "avg_rvol":  round(avg_rvol, 2),
        "funding":   funding_rate,
        "session":   session_active,
    }
