"""
QF×JP Bot v6.3 — Indicators
Motor completo: ATR, ADX, CVD, FVG, CHoCH/BoS, MFI, VDI, EQH/EQL,
HTF EHM, TL Ruptura, Score compuesto 0-100.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signal:
    symbol: str
    direction: str        # LONG | SHORT | NONE
    score: float          # 0–100
    tier: str             # STD | FUEL | SUP | NONE
    entry: float
    sl: float
    tp1: float
    tp2: float
    atr: float
    adx: float
    mfi: float
    vdi: float
    cvd: float
    momentum: float
    htf_score: float
    structure: str        # CHoCH↑ | CHoCH↓ | BoS↑ | BoS↓ | NONE
    tl_break: str         # LONG | SHORT | NONE
    tl_break_active: bool = False
    circuit_breaker: bool = False
    reason: str = ""


# ── Numpy helpers ─────────────────────────────────────────────────────────────

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out

def _rma(arr: np.ndarray, period: int) -> np.ndarray:
    """Wilder RMA (usado por ATR, ADX)."""
    k = 1.0 / period
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out

def _sma(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(period - 1, len(arr)):
        out[i] = arr[i - period + 1 : i + 1].mean()
    return out

# ── ATR ────────────────────────────────────────────────────────────────────────

def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 10) -> np.ndarray:
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    tr = np.concatenate([[tr[0]], tr])
    return _rma(tr, period)

# ── ADX ────────────────────────────────────────────────────────────────────────

def calc_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (adx, +DI, -DI)."""
    up   = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm  = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
    )
    plus_dm  = np.concatenate([[0], plus_dm])
    minus_dm = np.concatenate([[0], minus_dm])
    tr       = np.concatenate([[tr[0]], tr])
    atr14    = _rma(tr, period)
    pdi      = 100 * _rma(plus_dm, period) / np.where(atr14 == 0, 1e-9, atr14)
    mdi      = 100 * _rma(minus_dm, period) / np.where(atr14 == 0, 1e-9, atr14)
    dx       = 100 * np.abs(pdi - mdi) / np.where(pdi + mdi == 0, 1e-9, pdi + mdi)
    adx      = _rma(dx, period)
    return adx, pdi, mdi

# ── OBV / Momentum ────────────────────────────────────────────────────────────

def calc_obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    direction = np.sign(np.diff(close))
    direction = np.concatenate([[0], direction])
    return np.cumsum(direction * volume)

def calc_momentum(close: np.ndarray, period: int = 10) -> np.ndarray:
    mom = np.full_like(close, 0.0)
    for i in range(period, len(close)):
        denom = close[i - period] if close[i - period] != 0 else 1e-9
        mom[i] = (close[i] - close[i - period]) / denom
    return mom

# ── CVD ────────────────────────────────────────────────────────────────────────

def calc_cvd(open_: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    bull_vol = np.where(close > open_, volume, 0.0)
    bear_vol = np.where(close <= open_, volume, 0.0)
    delta = bull_vol - bear_vol
    total = bull_vol + bear_vol
    cvd   = np.where(total == 0, 0.0, delta / total)
    return _ema(cvd, 5)

# ── MFI ────────────────────────────────────────────────────────────────────────

def calc_mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: int = 14) -> np.ndarray:
    tp = (high + low + close) / 3
    mf = tp * volume
    mfi = np.full_like(close, 50.0)
    for i in range(period, len(close)):
        pos = np.sum(mf[i - period + 1 : i + 1][tp[i - period + 1 : i + 1] > tp[i - period : i]])
        neg = np.sum(mf[i - period + 1 : i + 1][tp[i - period + 1 : i + 1] <= tp[i - period : i]])
        if neg == 0:
            mfi[i] = 100.0
        else:
            mfi[i] = 100 - 100 / (1 + pos / neg)
    return mfi

# ── VDI ────────────────────────────────────────────────────────────────────────

def calc_vdi(close: np.ndarray, volume: np.ndarray, period: int = 20) -> np.ndarray:
    """Volume-weighted directional index normalizado en σ."""
    vwap_delta = (close - _sma(close, period)) * volume
    std = np.nanstd(vwap_delta[-period:])
    return vwap_delta / (std + 1e-9)

# ── CHoCH / BoS ───────────────────────────────────────────────────────────────

def detect_structure(high: np.ndarray, low: np.ndarray, close: np.ndarray, lookback: int = 5) -> str:
    """Retorna: CHoCH↑ | CHoCH↓ | BoS↑ | BoS↓ | NONE"""
    if len(high) < lookback * 2 + 5:
        return "NONE"
    prev_hh = high[-lookback * 2 - 1 : -lookback - 1].max()
    prev_ll = low[-lookback * 2 - 1 : -lookback - 1].min()
    curr_h  = high[-lookback - 1 :].max()
    curr_l  = low[-lookback - 1 :].min()
    c       = close[-1]
    prev_c  = close[-lookback - 2]
    if c > prev_hh and curr_h > prev_hh:
        return "BoS↑" if prev_c > prev_ll else "CHoCH↑"
    if c < prev_ll and curr_l < prev_ll:
        return "BoS↓" if prev_c < prev_hh else "CHoCH↓"
    return "NONE"

# ── TL Ruptura ────────────────────────────────────────────────────────────────

def detect_tl_break(high: np.ndarray, low: np.ndarray, close: np.ndarray, lookback: int = 20) -> str:
    """Detecta ruptura de trendline bajista (→ LONG) o alcista (→ SHORT)."""
    if len(high) < lookback + 5:
        return "NONE"
    h = high[-lookback:]
    l = low[-lookback:]
    # Línea bajista: desde max hasta penúltima barra
    tl_bear_slope = (h[-2] - h[0]) / lookback
    tl_bear_now   = h[0] + tl_bear_slope * (lookback - 1)
    # Línea alcista: desde min hasta penúltima barra
    tl_bull_slope = (l[-2] - l[0]) / lookback
    tl_bull_now   = l[0] + tl_bull_slope * (lookback - 1)

    c = close[-1]
    if c > tl_bear_now and close[-2] <= tl_bear_now:
        return "LONG"
    if c < tl_bull_now and close[-2] >= tl_bull_now:
        return "SHORT"
    return "NONE"

# ── FVG detector ──────────────────────────────────────────────────────────────

def detect_fvg(high: np.ndarray, low: np.ndarray) -> str:
    """BULL | BEAR | NONE — último FVG en las últimas 5 velas."""
    for i in range(len(high) - 1, max(len(high) - 6, 1), -1):
        if low[i] > high[i - 2]:
            return "BULL"
        if high[i] < low[i - 2]:
            return "BEAR"
    return "NONE"

# ── Circuit Breaker ───────────────────────────────────────────────────────────

def check_circuit_breaker(high: np.ndarray, low: np.ndarray, atr: np.ndarray, mult: float = 3.0, bars: int = 10) -> bool:
    for i in range(len(high) - 1, max(len(high) - bars - 1, 0), -1):
        candle_range = high[i] - low[i]
        if atr[i] > 0 and candle_range > mult * atr[i]:
            return True
    return False

# ── HTF EHM ───────────────────────────────────────────────────────────────────

def htf_score(
    klines_15m: list,
    klines_1h: list,
    klines_4h: list,
) -> float:
    """
    Exponential HTF multiplier: 15m×1 + 1h×2 + 4h×4
    Retorna 0.0 – 1.0 (score de alineación HTF).
    """
    scores = []
    weights = []
    for klines, weight in [(klines_15m, 1), (klines_1h, 2), (klines_4h, 4)]:
        if len(klines) < 30:
            continue
        arr = np.array(klines)
        c   = arr[:, 4]
        v   = arr[:, 5]
        ema20 = _ema(c, 20)
        ema50 = _ema(c, 50) if len(c) >= 50 else _ema(c, 20)
        trend = 1 if ema20[-1] > ema50[-1] else -1
        mom   = calc_momentum(c, 10)[-1]
        s = 0.5 + 0.5 * trend * min(abs(mom) * 10, 1.0)
        scores.append(s * weight)
        weights.append(weight)
    if not weights:
        return 0.5
    return sum(scores) / sum(weights)

# ── Score compuesto ───────────────────────────────────────────────────────────

def composite_score(
    direction: str,
    adx: float,
    cvd: float,
    momentum: float,
    mfi: float,
    vdi: float,
    structure: str,
    tl_break: str,
    htf_s: float,
    fvg: str,
) -> float:
    """Retorna score 0–100."""
    s = 0.0

    # ADX (25 pts)
    s += min(adx / 40.0, 1.0) * 25

    # CVD (15 pts)
    if direction == "LONG":
        s += max(0.0, min(cvd, 1.0)) * 15
    else:
        s += max(0.0, min(-cvd, 1.0)) * 15

    # Momentum (15 pts)
    if direction == "LONG":
        s += max(0.0, min(momentum * 30, 1.0)) * 15
    else:
        s += max(0.0, min(-momentum * 30, 1.0)) * 15

    # MFI (10 pts)
    if direction == "LONG":
        s += max(0.0, (mfi - 50) / 50) * 10
    else:
        s += max(0.0, (50 - mfi) / 50) * 10

    # VDI (10 pts)
    if direction == "LONG":
        s += max(0.0, min(vdi / 3.0, 1.0)) * 10
    else:
        s += max(0.0, min(-vdi / 3.0, 1.0)) * 10

    # Structure (10 pts)
    struct_pts = {
        "CHoCH↑": (10 if direction == "LONG" else 0),
        "CHoCH↓": (10 if direction == "SHORT" else 0),
        "BoS↑":   (7 if direction == "LONG" else 0),
        "BoS↓":   (7 if direction == "SHORT" else 0),
    }
    s += struct_pts.get(structure, 0)

    # HTF (10 pts)
    if direction == "LONG":
        s += htf_s * 10
    else:
        s += (1 - htf_s) * 10

    # FVG bonus (5 pts)
    if (direction == "LONG" and fvg == "BULL") or (direction == "SHORT" and fvg == "BEAR"):
        s += 5

    return round(min(s, 100.0), 1)

def score_to_tier(score: float) -> str:
    import config as C
    if score >= C.SUP_SCORE:
        return "SUP"
    if score >= C.FUEL_SCORE:
        return "FUEL"
    if score >= C.MIN_SCORE:
        return "STD"
    return "NONE"

# ── Función principal ─────────────────────────────────────────────────────────

def analyze(
    symbol: str,
    klines_3m: list,
    klines_15m: list,
    klines_1h: list,
    klines_4h: list,
) -> Signal:
    import config as C

    def _no_signal(reason: str) -> Signal:
        return Signal(
            symbol=symbol, direction="NONE", score=0, tier="NONE",
            entry=0, sl=0, tp1=0, tp2=0, atr=0, adx=0, mfi=50,
            vdi=0, cvd=0, momentum=0, htf_score=0,
            structure="NONE", tl_break="NONE", reason=reason,
        )

    if len(klines_3m) < 60:
        return _no_signal("insufficient_data")

    arr = np.array(klines_3m)
    o   = arr[:, 1]
    h   = arr[:, 2]
    l   = arr[:, 3]
    c   = arr[:, 4]
    v   = arr[:, 5]

    # ── Indicadores base ─────────────────────────────────────────────────────
    atr_arr  = calc_atr(h, l, c, C.ATR_LEN)
    adx_arr, pdi, mdi = calc_adx(h, l, c, C.ADX_LEN)
    atr  = float(atr_arr[-1])
    adx  = float(adx_arr[-1])
    pdim = float(pdi[-1])
    mdim = float(mdi[-1])

    cvd_arr  = calc_cvd(o, c, v)
    mom_arr  = calc_momentum(c, 10)
    mfi_arr  = calc_mfi(h, l, c, v, 14)
    vdi_val  = float(calc_vdi(c, v, 20)[-1])
    obv_arr  = calc_obv(c, v)

    cvd_val  = float(cvd_arr[-1])
    mom_val  = float(mom_arr[-1])
    mfi_val  = float(mfi_arr[-1])

    # ── Circuit breaker ───────────────────────────────────────────────────────
    cb = C.CB_ENABLED and check_circuit_breaker(h, l, atr_arr, C.CB_ATR_MULT, C.CB_BARS)

    # ── Estructura ────────────────────────────────────────────────────────────
    structure = detect_structure(h, l, c, 5)

    # ── TL Ruptura ────────────────────────────────────────────────────────────
    tl_break = detect_tl_break(h, l, c, 20)

    # ── FVG ───────────────────────────────────────────────────────────────────
    fvg = detect_fvg(h, l)

    # ── HTF ───────────────────────────────────────────────────────────────────
    htf_s = htf_score(klines_15m, klines_1h, klines_4h)

    # ── Dirección ─────────────────────────────────────────────────────────────
    if C.REQUIRE_TL_BREAK and tl_break == "NONE":
        return _no_signal("no_tl_break")

    if tl_break != "NONE":
        direction = tl_break
    elif pdim > mdim:
        direction = "LONG"
    else:
        direction = "SHORT"

    # ── HTF alineación mínima ─────────────────────────────────────────────────
    htf_aligned = 0
    for klines, weight in [(klines_15m, 1), (klines_1h, 2), (klines_4h, 4)]:
        if len(klines) < 30:
            continue
        a = np.array(klines)
        cc = a[:, 4]
        ema20 = _ema(cc, 20)
        ema50 = _ema(cc, 50) if len(cc) >= 50 else _ema(cc, 20)
        aligned = (direction == "LONG" and ema20[-1] > ema50[-1]) or \
                  (direction == "SHORT" and ema20[-1] < ema50[-1])
        if aligned:
            htf_aligned += 1
    if htf_aligned < C.HTF_MIN_ALIGNED:
        return _no_signal(f"htf_not_aligned({htf_aligned}/{C.HTF_MIN_ALIGNED})")

    # ── Score ─────────────────────────────────────────────────────────────────
    score = composite_score(
        direction, adx, cvd_val, mom_val, mfi_val, vdi_val,
        structure, tl_break, htf_s, fvg,
    )
    tier = score_to_tier(score)

    entry = float(c[-1])
    if direction == "LONG":
        sl   = entry - atr * C.SL_ATR_MULT
        tp1  = entry + atr * C.TP1_ATR_MULT
        tp2  = entry + atr * C.TP2_ATR_MULT
    else:
        sl   = entry + atr * C.SL_ATR_MULT
        tp1  = entry - atr * C.TP1_ATR_MULT
        tp2  = entry - atr * C.TP2_ATR_MULT

    return Signal(
        symbol=symbol,
        direction=direction,
        score=score,
        tier=tier,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        atr=atr,
        adx=adx,
        mfi=mfi_val,
        vdi=vdi_val,
        cvd=cvd_val,
        momentum=mom_val,
        htf_score=htf_s,
        structure=structure,
        tl_break=tl_break,
        tl_break_active=tl_break != "NONE",
        circuit_breaker=cb,
        reason="ok",
    )
