"""
indicators.py — Motor de indicadores QF×JP v3.5 en Python puro
Replica: ATR, ADX, CVD, FVG, OB, TL Ruptura, CHoCH/BoS, MFI, VDI,
         Score compuesto, circuit breaker, EHM HTF
"""
import math
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config as cfg


# ─── HELPERS ──────────────────────────────────────────────────────────────

def _tanh(x: float) -> float:
    e2 = math.exp(max(min(2 * x, 20), -20))
    return (e2 - 1) / (e2 + 1)


def ema(arr: np.ndarray, period: int) -> np.ndarray:
    out  = np.full_like(arr, np.nan)
    k    = 2.0 / (period + 1)
    prev = arr[0]
    for i, v in enumerate(arr):
        if not np.isnan(v):
            prev = v if np.isnan(prev) else prev + k * (v - prev)
        out[i] = prev
    return out


def sma(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(period - 1, len(arr)):
        out[i] = np.mean(arr[i - period + 1:i + 1])
    return out


def stdev(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(period - 1, len(arr)):
        out[i] = np.std(arr[i - period + 1:i + 1], ddof=1)
    return out


def highest(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(period - 1, len(arr)):
        out[i] = np.max(arr[i - period + 1:i + 1])
    return out


def lowest(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    for i in range(period - 1, len(arr)):
        out[i] = np.min(arr[i - period + 1:i + 1])
    return out


# ─── ATR ──────────────────────────────────────────────────────────────────

def calc_atr(highs, lows, closes, period=14) -> np.ndarray:
    tr = np.zeros(len(closes))
    for i in range(1, len(closes)):
        tr[i] = max(
            highs[i]  - lows[i],
            abs(highs[i]  - closes[i - 1]),
            abs(lows[i]   - closes[i - 1]),
        )
    tr[0] = highs[0] - lows[0]
    return ema(tr, period)


# ─── ADX ──────────────────────────────────────────────────────────────────

def calc_adx(highs, lows, closes, period=14):
    n      = len(closes)
    dm_p   = np.zeros(n)
    dm_m   = np.zeros(n)
    tr_arr = np.zeros(n)

    for i in range(1, n):
        up   = highs[i]  - highs[i - 1]
        down = lows[i - 1] - lows[i]
        dm_p[i] = up   if up > down and up > 0   else 0
        dm_m[i] = down if down > up and down > 0 else 0
        tr_arr[i] = max(
            highs[i] - lows[i],
            abs(highs[i]  - closes[i - 1]),
            abs(lows[i]   - closes[i - 1]),
        )

    atr14  = ema(tr_arr, period)
    di_p   = 100 * ema(dm_p, period) / np.where(atr14 == 0, 1e-9, atr14)
    di_m   = 100 * ema(dm_m, period) / np.where(atr14 == 0, 1e-9, atr14)
    dx     = 100 * np.abs(di_p - di_m) / np.where((di_p + di_m) == 0, 1e-9, (di_p + di_m))
    adx    = ema(dx, period)
    return di_p, di_m, adx


# ─── CVD (Cumulative Volume Delta) ────────────────────────────────────────

def calc_cvd(opens, closes, volumes) -> np.ndarray:
    delta = np.where(closes >= opens, volumes, -volumes)
    return np.cumsum(delta)


# ─── OBV ──────────────────────────────────────────────────────────────────

def calc_obv(closes, volumes) -> np.ndarray:
    obv = np.zeros(len(closes))
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv[i] = obv[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            obv[i] = obv[i - 1] - volumes[i]
        else:
            obv[i] = obv[i - 1]
    return obv


# ─── MFI ──────────────────────────────────────────────────────────────────

def calc_mfi(highs, lows, closes, volumes, period=14) -> np.ndarray:
    tp  = (highs + lows + closes) / 3
    rmf = tp * volumes
    n   = len(closes)
    mfi = np.full(n, 50.0)

    for i in range(period, n):
        pos = neg = 0.0
        for j in range(i - period + 1, i + 1):
            if tp[j] > tp[j - 1]:
                pos += rmf[j]
            elif tp[j] < tp[j - 1]:
                neg += rmf[j]
        if neg == 0:
            mfi[i] = 100.0
        elif pos == 0:
            mfi[i] = 0.0
        else:
            mfi[i] = 100 - 100 / (1 + pos / neg)
    return mfi


# ─── FVG (Fair Value Gaps) ────────────────────────────────────────────────

def detect_fvg(highs, lows, atr, min_atr_mult=0.3) -> dict:
    """Devuelve el FVG más reciente activo {bull: bool, top, bot, bar_idx}"""
    n = len(highs)
    fvgs = []
    for i in range(2, n):
        gap    = lows[i] - highs[i - 2]
        gap_dn = lows[i - 2] - highs[i]
        atr_v  = atr[i] if not np.isnan(atr[i]) else 0.001
        if gap > atr_v * min_atr_mult:
            fvgs.append({"bull": True,  "top": lows[i], "bot": highs[i - 2], "bar": i})
        if gap_dn > atr_v * min_atr_mult:
            fvgs.append({"bull": False, "top": lows[i - 2], "bot": highs[i], "bar": i})
    return fvgs[-cfg.FVG_BARS:] if fvgs else []


# ─── TRENDLINE BREAK ──────────────────────────────────────────────────────

def detect_tl_break(highs, lows, closes, lookback=30, pivot_l=5, pivot_r=3):
    """
    Detecta ruptura de trendline bajista (LONG) o alcista (SHORT).
    Retorna: "LONG" | "SHORT" | None
    """
    n = len(closes)
    if n < lookback + pivot_r:
        return None

    # Pivot highs (para TL bajista → señal LONG al romper)
    ph_idx = []
    for i in range(pivot_l, n - pivot_r):
        if all(highs[i] >= highs[i - j] for j in range(1, pivot_l + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, pivot_r + 1)):
            ph_idx.append(i)

    # Pivot lows (para TL alcista → señal SHORT al romper)
    pl_idx = []
    for i in range(pivot_l, n - pivot_r):
        if all(lows[i] <= lows[i - j] for j in range(1, pivot_l + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, pivot_r + 1)):
            pl_idx.append(i)

    # TL bajista: dos pivot highs descendentes
    bear_break = False
    if len(ph_idx) >= 2:
        p1, p2 = ph_idx[-2], ph_idx[-1]
        if highs[p1] > highs[p2]:
            slope = (highs[p2] - highs[p1]) / (p2 - p1)
            tl_price = highs[p2] + slope * (n - 1 - p2)
            if closes[-1] > tl_price:
                bear_break = True

    # TL alcista: dos pivot lows ascendentes
    bull_break = False
    if len(pl_idx) >= 2:
        p1, p2 = pl_idx[-2], pl_idx[-1]
        if lows[p1] < lows[p2]:
            slope = (lows[p2] - lows[p1]) / (p2 - p1)
            tl_price = lows[p2] + slope * (n - 1 - p2)
            if closes[-1] < tl_price:
                bull_break = True

    if bear_break and not bull_break:
        return "LONG"
    if bull_break and not bear_break:
        return "SHORT"
    return None


# ─── CHoCH / BoS ──────────────────────────────────────────────────────────

def detect_choch_bos(highs, lows, closes, lookback=20):
    """
    CHoCH: cambio de carácter (mínimo/máximo roto contra tendencia)
    BoS:   break of structure (en dirección de tendencia)
    Retorna: {"choch_long", "choch_short", "bos_long", "bos_short"}
    """
    n = len(closes)
    if n < lookback + 1:
        return {}

    recent_high = np.max(highs[-lookback - 1:-1])
    recent_low  = np.min(lows[-lookback - 1:-1])
    c = closes[-1]
    h = highs[-1]
    l = lows[-1]

    prev_high = np.max(highs[-lookback - 1:-2])
    prev_low  = np.min(lows[-lookback - 1:-2])

    choch_long  = l < prev_low  and c > recent_low   # swept low then recovered
    choch_short = h > prev_high and c < recent_high
    bos_long    = h > recent_high
    bos_short   = l < recent_low

    return {
        "choch_long":  choch_long,
        "choch_short": choch_short,
        "bos_long":    bos_long,
        "bos_short":   bos_short,
    }


# ─── VDI (Volume Delta Imbalance) ────────────────────────────────────────

def calc_vdi(opens, closes, volumes, window=3, thr_sigma=1.5):
    """Desequilibrio acumulado de delta en últimas `window` velas"""
    n = len(closes)
    if n < window + 20:
        return 0.0, False, False

    deltas = (closes - opens) / np.where(opens == 0, 1e-9, opens) * volumes
    acc    = np.array([np.sum(deltas[max(0, i - window + 1):i + 1]) for i in range(n)])
    mu     = np.mean(acc[-20:])
    sigma  = np.std(acc[-20:]) + 1e-9
    z      = (acc[-1] - mu) / sigma

    bull_imbalance = z >  thr_sigma
    bear_imbalance = z < -thr_sigma
    return z, bull_imbalance, bear_imbalance


# ─── EQH / EQL (Equal Highs / Lows) ─────────────────────────────────────

def detect_eqh_eql(highs, lows, atr, lookback=20, tol_atr=0.15):
    """Detecta Equal Highs (trampa alcista) y Equal Lows (trampa bajista)"""
    n = len(highs)
    if n < lookback + 1:
        return False, False

    tol    = (atr[-1] if not np.isnan(atr[-1]) else 0.001) * tol_atr
    max_h  = np.max(highs[-lookback - 1:-1])
    min_l  = np.min(lows[-lookback - 1:-1])
    eqh    = abs(highs[-1] - max_h) < tol and highs[-1] >= max_h * 0.999
    eql    = abs(lows[-1]  - min_l) < tol and lows[-1]  <= min_l * 1.001
    return eqh, eql


# ─── HTF BIAS ─────────────────────────────────────────────────────────────

def htf_bias(candles: list, fast=9, slow=21):
    """Retorna (bull:bool, bear:bool) basado en EMA cross"""
    if len(candles) < slow + 2:
        return False, False
    closes = np.array([c["close"] for c in candles])
    f = ema(closes, fast)
    s = ema(closes, slow)
    bull = f[-1] > s[-1]
    bear = f[-1] < s[-1]
    return bull, bear


# ─── SCORE COMPUESTO ──────────────────────────────────────────────────────

@dataclass
class ScoreResult:
    score:      float = 0.0
    tier:       str   = "NONE"          # NONE | STD | FUEL | SUP
    direction:  str   = "NONE"          # LONG | SHORT | NONE
    tl_break:   str   = None
    choch_bos:  dict  = field(default_factory=dict)
    mfi:        float = 50.0
    vdi_z:      float = 0.0
    vdi_bull:   bool  = False
    vdi_bear:   bool  = False
    atr:        float = 0.0
    sl_price:   float = 0.0
    tp1_price:  float = 0.0
    tp2_price:  float = 0.0
    entry_price:float = 0.0
    adx:        float = 0.0
    htf_score:  float = 0.0
    circuit_ok: bool  = True
    reject:     str   = ""

    # Panel fields
    norm_score: float = 0.0
    cvd_score:  float = 0.0
    mom_score:  float = 0.0


def compute_score(
    candles_3m:  list,
    candles_15m: list,
    candles_1h:  list,
    candles_4h:  list,
) -> ScoreResult:
    """
    Calcula el score QF×JP v3.5 completo y genera señal.
    Retorna ScoreResult con tier y precios SL/TP.
    """
    res = ScoreResult()

    # ── Arrays ──────────────────────────────────────────────────────────────
    def _arr(candles, key):
        return np.array([c[key] for c in candles], dtype=float)

    o = _arr(candles_3m, "open")
    h = _arr(candles_3m, "high")
    l = _arr(candles_3m, "low")
    c = _arr(candles_3m, "close")
    v = _arr(candles_3m, "volume")
    n = len(c)

    if n < 50:
        res.reject = "INSUFFICIENT_DATA"
        return res

    # ── ATR ──────────────────────────────────────────────────────────────────
    atr  = calc_atr(h, l, c, cfg.ATR_LEN)
    atr_v = float(atr[-1]) if not np.isnan(atr[-1]) else 0.001
    atr_avg20 = float(np.nanmean(atr[-20:])) if n >= 20 else atr_v
    res.atr = atr_v

    # ── Circuit Breaker ──────────────────────────────────────────────────────
    if cfg.CB_ENABLED:
        giant = abs(c[-1] - o[-1]) > atr_avg20 * cfg.CB_ATR_MULT
        if giant:
            res.circuit_ok = False
            res.reject = "CIRCUIT_BREAKER"
            return res

    # ── ADX ──────────────────────────────────────────────────────────────────
    di_p, di_m, adx_arr = calc_adx(h, l, c, cfg.ADX_LEN)
    adx_v        = float(adx_arr[-1])
    res.adx      = adx_v
    trend_strong = adx_v >= cfg.ADX_TREND
    is_lateral   = adx_v < cfg.ADX_LATERAL
    trend_up     = di_p[-1] > di_m[-1] and trend_strong
    trend_dn     = di_m[-1] > di_p[-1] and trend_strong

    # ── OBV / Momentum ───────────────────────────────────────────────────────
    period_mom = 20
    obv_arr    = calc_obv(c, v)
    obv_ema    = ema(obv_arr, 14)
    obv_std_v  = float(np.std(obv_arr[-period_mom:])) + 1e-9
    f_vol_v    = (obv_arr[-1] - obv_ema[-1]) / obv_std_v

    roc_raw    = (c[-1] - c[-period_mom]) / (c[-period_mom] + 1e-9)
    vol_norm   = float(np.std(c[-period_mom:])) / (float(np.mean(c[-period_mom:])) + 1e-9)
    f_mom_v    = roc_raw / (vol_norm + 1e-9)

    basis      = float(np.mean(c[-8:]))
    basis_std  = float(np.std(c[-8:])) + 1e-9
    f_rev_v    = -(c[-1] - basis) / basis_std

    adx_factor = min(1.0, adx_v / (cfg.ADX_TREND * 2.0))
    w_mom_dyn  = cfg.W_MOM + adx_factor * cfg.W_MOM * 0.40
    w_rev_dyn  = max(0.05, 0.30 - adx_factor * 0.30 * 0.50)
    w_total    = w_mom_dyn + w_rev_dyn + 0.30
    raw_score  = (w_mom_dyn * f_mom_v + w_rev_dyn * f_rev_v + 0.30 * f_vol_v) / w_total

    comp_score = float(ema(np.array([raw_score] * 10), 3)[-1])
    sc_std_v   = float(np.std([raw_score] * 20)) + 1e-9
    norm_score = _tanh(comp_score / sc_std_v)
    res.norm_score = norm_score
    res.mom_score  = f_mom_v

    # ── CVD ──────────────────────────────────────────────────────────────────
    cvd        = calc_cvd(o, c, v)
    cvd_ema_v  = ema(cvd, 20)
    cvd_std_v  = float(np.std(cvd[-20:])) + 1e-9
    cvd_norm   = _tanh((cvd[-1] - cvd_ema_v[-1]) / cvd_std_v)
    res.cvd_score = cvd_norm

    # ── MFI ──────────────────────────────────────────────────────────────────
    mfi_arr  = calc_mfi(h, l, c, v, cfg.MFI_LEN)
    mfi_v    = float(mfi_arr[-1])
    res.mfi  = mfi_v

    # ── VDI ──────────────────────────────────────────────────────────────────
    vdi_z, vdi_bull, vdi_bear = calc_vdi(o, c, v, window=3, thr_sigma=cfg.TP1_ATR_MULT)
    res.vdi_z    = vdi_z
    res.vdi_bull = vdi_bull
    res.vdi_bear = vdi_bear

    # ── HTF Bias [EHM] ───────────────────────────────────────────────────────
    bull_15, bear_15 = htf_bias(candles_15m)
    bull_1h,  bear_1h  = htf_bias(candles_1h)
    bull_4h,  bear_4h  = htf_bias(candles_4h)

    ehm_long  = (1 if bull_15 else 0) + (2 if bull_1h else 0) + (4 if bull_4h else 0)
    ehm_short = (1 if bear_15 else 0) + (2 if bear_1h else 0) + (4 if bear_4h else 0)
    ehm_total = 7.0
    htf_long_score  = ehm_long  / ehm_total
    htf_short_score = ehm_short / ehm_total
    res.htf_score   = max(htf_long_score, htf_short_score)

    # ── Trendline Break ──────────────────────────────────────────────────────
    tl = detect_tl_break(h, l, c)
    res.tl_break = tl

    # ── CHoCH / BoS ──────────────────────────────────────────────────────────
    cb_res = detect_choch_bos(h, l, c)
    res.choch_bos = cb_res

    # ── FVG near price ───────────────────────────────────────────────────────
    fvgs     = detect_fvg(h, l, atr, cfg.FVG_MIN_ATR)
    price    = float(c[-1])
    fvg_bull = any(f["bull"] and f["bot"] <= price <= f["top"] for f in fvgs)
    fvg_bear = any(not f["bull"] and f["bot"] <= price <= f["top"] for f in fvgs)

    # ── EQH / EQL ────────────────────────────────────────────────────────────
    eqh, eql = detect_eqh_eql(h, l, atr)

    # ── Volatilidad ATR filter ────────────────────────────────────────────────
    if atr_v < atr_avg20 * cfg.TP1_ATR_MULT * 0.70:
        pass  # vol ok, no filter here

    # ── Score Compuesto ───────────────────────────────────────────────────────
    # Pesos por componente
    sc_long = (
        cfg.W_SCORE  * max(0, norm_score)    +
        cfg.W_CVD    * max(0, cvd_norm)       +
        cfg.W_MOM    * max(0, f_mom_v / 3)    +
        cfg.W_HTF    * htf_long_score          +
        cfg.W_STRUC  * (0.5 * int(cb_res.get("bos_long", False)) + 0.5 * int(cb_res.get("choch_long", False))) +
        cfg.W_VDI    * (1.0 if vdi_bull else 0.0) +
        cfg.W_SENT   * (1.0 if mfi_v < cfg.MFI_OS else 0.0)
    )

    sc_short = (
        cfg.W_SCORE  * max(0, -norm_score)   +
        cfg.W_CVD    * max(0, -cvd_norm)      +
        cfg.W_MOM    * max(0, -f_mom_v / 3)   +
        cfg.W_HTF    * htf_short_score         +
        cfg.W_STRUC  * (0.5 * int(cb_res.get("bos_short", False)) + 0.5 * int(cb_res.get("choch_short", False))) +
        cfg.W_VDI    * (1.0 if vdi_bear else 0.0) +
        cfg.W_SENT   * (1.0 if mfi_v > cfg.MFI_OB else 0.0)
    )

    # Normalizar a 0-100
    sc_long_100  = min(100, round(sc_long  * 100 / (sum([
        cfg.W_SCORE, cfg.W_CVD, cfg.W_MOM, cfg.W_HTF,
        cfg.W_STRUC, cfg.W_VDI, cfg.W_SENT
    ])), 1))
    sc_short_100 = min(100, round(sc_short * 100 / (sum([
        cfg.W_SCORE, cfg.W_CVD, cfg.W_MOM, cfg.W_HTF,
        cfg.W_STRUC, cfg.W_VDI, cfg.W_SENT
    ])), 1))

    # ── TL Ruptura como gate principal ───────────────────────────────────────
    direction = None
    if tl == "LONG" and sc_long_100 > 0:
        direction = "LONG"
        score100  = sc_long_100
    elif tl == "SHORT" and sc_short_100 > 0:
        direction = "SHORT"
        score100  = sc_short_100
    else:
        if cfg.REQUIRE_TL_BREAK:
            res.reject = "NO_TL_BREAK"
            return res
        # Sin TL break, usar score puro
        if sc_long_100 > sc_short_100:
            direction = "LONG"
            score100  = sc_long_100
        else:
            direction = "SHORT"
            score100  = sc_short_100

    # ── HTF alignment mínimo ────────────────────────────────────────────────
    htf_aligned = htf_long_score if direction == "LONG" else htf_short_score
    htf_min     = int(os.getenv("HTF_MIN_ALIGNED", "2"))  # 2 de 3 TFs
    if (htf_long_score * 7 < htf_min) and (htf_short_score * 7 < htf_min):
        res.reject = "HTF_NOT_ALIGNED"
        return res

    # ── Asignar tier ─────────────────────────────────────────────────────────
    if score100 >= cfg.SUP_SCORE:
        tier = "SUP"
    elif score100 >= cfg.FUEL_SCORE:
        tier = "FUEL"
    elif score100 >= cfg.MIN_SCORE:
        tier = "STD"
    else:
        res.reject = f"SCORE_LOW ({score100:.0f})"
        return res

    # ── MIN_TIER gate ────────────────────────────────────────────────────────
    tier_rank = {"STD": 1, "FUEL": 2, "SUP": 3}
    if tier_rank.get(tier, 0) < tier_rank.get(cfg.MIN_TIER, 1):
        res.reject = f"TIER_BELOW_MIN ({tier} < {cfg.MIN_TIER})"
        return res

    # ── SL / TP ──────────────────────────────────────────────────────────────
    entry = float(c[-1])
    sl    = atr_v * cfg.SL_ATR_MULT
    tp1   = atr_v * cfg.TP1_ATR_MULT
    tp2   = atr_v * cfg.TP2_ATR_MULT

    if direction == "LONG":
        res.sl_price  = round(entry - sl,  8)
        res.tp1_price = round(entry + tp1, 8)
        res.tp2_price = round(entry + tp2, 8)
    else:
        res.sl_price  = round(entry + sl,  8)
        res.tp1_price = round(entry - tp1, 8)
        res.tp2_price = round(entry - tp2, 8)

    res.entry_price = entry
    res.score       = score100
    res.tier        = tier
    res.direction   = direction
    return res
