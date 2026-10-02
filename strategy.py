"""
Capa de estrategia común a bot, backtest y sweep: plan SL/TP (f_riskPlan del indicador),
filtros, contexto de TF superior y simulación de la gestión (TP1 parcial + SL a BE, resto TP2).
Bot y backtest llaman a ESTAS funciones: se mide lo mismo que se opera.
"""
from wyckoff_engine import (DIR_ACCUM, DIR_DIST, ENTRY_NAMES, PHASE_A, PHASE_NAMES, TYPE_NAMES, WyckoffEngine, na)


def risk_plan(long, entry, rh, rl, exc, excP, testP, atr, sl_buffer_atr, tick):
    """Port de f_riskPlan. Devuelve (sl, tp1, tp2, rr_tp2) o None si faltan niveles."""
    if na(rh) or na(rl) or na(entry) or na(atr):
        return None
    hgt = max(rh - rl, tick)
    if long:
        ref = min(excP, rl) if (exc and not na(excP)) else (min(testP, rl) if not na(testP) else rl)
        sl = ref - atr * sl_buffer_atr
        risk = max(entry - sl, tick)
        tp1 = rh if entry < rh - atr * 0.25 else entry + risk
        tp2 = max(rh + hgt, tp1 + risk)
    else:
        ref = max(excP, rh) if (exc and not na(excP)) else (max(testP, rh) if not na(testP) else rh)
        sl = ref + atr * sl_buffer_atr
        risk = max(sl - entry, tick)
        tp1 = rl if entry > rl + atr * 0.25 else entry - risk
        tp2 = min(rl - hgt, tp1 - risk)
    rr2 = abs(tp2 - entry) / max(abs(entry - sl), tick)
    return sl, tp1, tp2, rr2


def build_signal(d, cfg, tick):
    """A partir del estado del motor en la vela de entrada, arma la señal completa."""
    if not d.get("entry_now") or d["outcome"] not in (DIR_ACCUM, DIR_DIST):
        return None
    long = d["outcome"] == DIR_ACCUM
    plan = risk_plan(long, d["close"], d["rh"], d["rl"], d["exc"], d["excP"], d["testP"], d["atr"],
                     cfg.SL_BUFFER_ATR, tick)
    if plan is None:
        return None
    sl, tp1, tp2, rr = plan
    return {
        "side": "LONG" if long else "SHORT", "entry": d["close"], "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
        "kind": ENTRY_NAMES.get(d["entryKind"], "-"), "rh": d["rh"], "rl": d["rl"], "atr": d["atr"],
        "conf": d["conf"], "val": d["val"], "time": d["time"], "risk_pct": abs(d["close"] - sl) / d["close"] * 100,
    }


# ── tendencia HTF (EMA de la vela anterior ya cerrada, como el indicador v2) ──
def ema_last(closes, n):
    if len(closes) < n:
        return float("nan")
    k = 2.0 / (n + 1)
    e = sum(closes[:n]) / n  # ta.ema de Pine arranca con SMA
    for x in closes[n:]:
        e = x * k + e * (1 - k)
    return e


def trend_dir(price, ema):
    if na(ema):
        return 0
    return 1 if price > ema else -1 if price < ema else 0


# ── contexto Wyckoff de TF superior ──
def context_of(ctx_state):
    """Dirección de la estructura del TF superior: +1 acumulación, -1 distribución, 0 nada/Fase A."""
    if not ctx_state or ctx_state.get("phase", 0) <= PHASE_A:
        return 0, "sin estructura"
    w = ctx_state.get("wdir", 0)
    label = f"{TYPE_NAMES.get(ctx_state.get('type', 0), '-')} fase {PHASE_NAMES[ctx_state['phase']]}"
    return (1 if w == DIR_ACCUM else -1 if w == DIR_DIST else 0), label


def alignment(side, ctx_dir):
    d = 1 if side == "LONG" else -1
    if ctx_dir == 0:
        return "neutral"
    return "a favor" if ctx_dir == d else "en contra"


def filters(sig, cfg, trend, ctx_align="neutral"):
    """Devuelve la lista de motivos por los que NO se abre (vacía = se puede abrir)."""
    why = []
    if cfg.MIN_RR > 0 and sig["rr"] < cfg.MIN_RR:
        why.append(f"R:R {sig['rr']:.2f} < {cfg.MIN_RR}")
    if sig["risk_pct"] > cfg.MAX_RISK_DIST_PCT:
        why.append(f"stop a {sig['risk_pct']:.2f}% (> {cfg.MAX_RISK_DIST_PCT}%)")
    if sig["risk_pct"] < cfg.MIN_RISK_DIST_PCT:
        why.append(f"stop a {sig['risk_pct']:.2f}% (< {cfg.MIN_RISK_DIST_PCT}%, el coste se come la R)")
    against = (sig["side"] == "LONG" and trend < 0) or (sig["side"] == "SHORT" and trend > 0)
    sig["against_trend"] = against
    sig["ctx_align"] = ctx_align
    if against and cfg.TREND_FILTER == "bloquea":
        why.append(f"contra tendencia EMA {cfg.TREND_TF}")
    if ctx_align == "en contra" and cfg.CONTEXT_FILTER == "bloquea":
        why.append(f"estructura {cfg.CONTEXT_TF} en contra")
    return why


class TradeSim:
    """Gestión del indicador v2: TP1 parcial y SL a BE, resto a TP2.
    Si una vela toca SL y TP a la vez, cuenta el SL (supuesto pesimista)."""

    def __init__(self, sig, bar, cost_pct, tp1_frac=0.5, move_be=True):
        self.d = 1 if sig["side"] == "LONG" else -1
        self.e, self.s, self.t1, self.t2 = sig["entry"], sig["sl"], sig["tp1"], sig["tp2"]
        self.risk = abs(self.e - self.s)
        self.half = False
        self.bar = bar
        self.cost = cost_pct      # comisión + deslizamiento, por lado, en %
        self.f = tp1_frac
        self.be = move_be

    def step(self, bar, h, l):
        """Devuelve R neta al cerrar, o None si sigue abierta."""
        if bar <= self.bar or self.risk <= 0:
            return None
        d, f = self.d, self.f
        hitS = l <= self.s if d == 1 else h >= self.s
        hitT1 = (not self.half) and (h >= self.t1 if d == 1 else l <= self.t1)
        hitT2 = h >= self.t2 if d == 1 else l <= self.t2
        r1 = (self.t1 - self.e) * d / self.risk
        res = None
        if hitS:
            rStop = (self.s - self.e) * d / self.risk
            res = f * r1 + (1 - f) * rStop if self.half else rStop
        else:
            if hitT1:
                self.half = True
                if self.be:
                    self.s = self.e
            if hitT2:
                r2 = (self.t2 - self.e) * d / self.risk
                res = f * r1 + (1 - f) * r2 if self.half else r2
        if res is None:
            return None
        return res - 2.0 * self.cost / 100.0 * self.e / self.risk


def scan_candidates(rows, tf_s, tick, strict, cfg, warmup, htf_ema=None, ctx_rows=None, ctx_tf_s=None,
                    symbol="", range_effort=False):
    """Recorre las velas con el motor y devuelve TODAS las entradas que valida, cada una con su resultado
    (simulado de forma independiente: el resultado solo depende de los precios futuros) y sus etiquetas
    de filtro. El backtest aplica después filtros + "una posición a la vez" de forma exacta.
    htf_ema: lista [(cierre_ms, ema)]; ctx_rows: velas del TF de contexto."""
    eng = WyckoffEngine(tf_s, tick, strict, keep_bars=100000, range_effort=range_effort)
    ctx = WyckoffEngine(ctx_tf_s, tick, strict, keep_bars=100000, range_effort=range_effort) if ctx_rows else None
    j = k = 0
    ema = float("nan")
    cands, opens = [], []
    tf_ms = tf_s * 1000
    for idx, (t, o, h, l, c, v) in enumerate(rows):
        still = []
        for cand, sim in opens:
            r = sim.step(idx, h, l)
            if r is None:
                still.append((cand, sim))
            else:
                cand["r"], cand["close_t"] = r, t + tf_ms
        opens = still
        if v <= 0 and h == l:
            continue  # mercado cerrado (TradFi): igual que en el bot, no alimenta al motor
        d = eng.update(t, o, h, l, c, v)
        bar_close = t + tf_ms
        while htf_ema and j < len(htf_ema) and htf_ema[j][0] <= bar_close:
            ema = htf_ema[j][1]
            j += 1
        while ctx is not None and k < len(ctx_rows) and ctx_rows[k][0] + ctx_tf_s * 1000 <= bar_close:
            if not (ctx_rows[k][5] <= 0 and ctx_rows[k][2] == ctx_rows[k][3]):
                ctx.update(*ctx_rows[k])
            k += 1
        if idx < warmup or not d["entry_now"]:
            continue
        sig = build_signal(d, cfg, tick)
        if sig is None:
            continue
        cdir, clabel = context_of(ctx.last if ctx is not None else None)
        sig.update(symbol=symbol, trend=trend_dir(c, ema), ctx_dir=cdir, ctx_label=clabel,
                   ctx_align=alignment(sig["side"], cdir), open_t=bar_close, r=None, close_t=None)
        cands.append(sig)
        opens.append((sig, TradeSim(sig, idx, cfg.FEE_PCT + cfg.SLIPPAGE_PCT, cfg.TP1_FRACTION, cfg.MOVE_SL_TO_BE)))
    return cands


def select_trades(cands, cfg):
    """Aplica filtros y 'una posición por símbolo a la vez' (greedy en orden temporal)."""
    out, busy_until = [], {}
    for c in sorted(cands, key=lambda x: x["open_t"]):
        if c["r"] is None:
            continue  # sigue abierta al final de los datos
        if filters(dict(c), cfg, c["trend"], c["ctx_align"]):
            continue
        if c["open_t"] < busy_until.get(c["symbol"], 0):
            continue
        busy_until[c["symbol"]] = c["close_t"]
        out.append(c)
    return out
