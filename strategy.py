"""
Capa de estrategia común a bot, backtest y sweep: plan SL/TP (f_riskPlan del indicador),
filtros, contextos (estructura del TF superior, estructura de BTC), simulación de la gestión
y selección de operaciones. Bot y backtest llaman a ESTAS funciones: se mide lo mismo que se opera.

Salidas configurables (para medir, no para creer):
  TP2_MULT   proyección de TP2 = altura del rango × TP2_MULT (1.0 = indicador)
  TRAIL_ATR  tras TP1, stop que persigue a cierre − TRAIL_ATR×ATR (0 = off, indicador)
  TIME_STOP  cierra a mercado si en N velas no ha tocado TP1 (0 = off, indicador)
"""
from wyckoff_engine import (DIR_ACCUM, DIR_DIST, ENTRY_NAMES, PHASE_A, PHASE_NAMES, TYPE_NAMES, WyckoffEngine, na)


def risk_plan(long, entry, rh, rl, exc, excP, testP, atr, sl_buffer_atr, tick, tp2_mult=1.0):
    """Port de f_riskPlan (+ multiplicador de TP2). Devuelve (sl, tp1, tp2, rr_tp2) o None."""
    if na(rh) or na(rl) or na(entry) or na(atr):
        return None
    hgt = max(rh - rl, tick)
    if long:
        ref = min(excP, rl) if (exc and not na(excP)) else (min(testP, rl) if not na(testP) else rl)
        sl = ref - atr * sl_buffer_atr
        risk = max(entry - sl, tick)
        tp1 = rh if entry < rh - atr * 0.25 else entry + risk
        tp2 = max(rh + hgt * tp2_mult, tp1 + risk)
    else:
        ref = max(excP, rh) if (exc and not na(excP)) else (max(testP, rh) if not na(testP) else rh)
        sl = ref + atr * sl_buffer_atr
        risk = max(sl - entry, tick)
        tp1 = rl if entry > rl + atr * 0.25 else entry - risk
        tp2 = min(rl - hgt * tp2_mult, tp1 - risk)
    rr2 = abs(tp2 - entry) / max(abs(entry - sl), tick)
    return sl, tp1, tp2, rr2


def build_signal(d, cfg, tick, tp2_mult=None):
    """A partir del estado del motor en la vela de entrada, arma la señal completa."""
    if not d.get("entry_now") or d["outcome"] not in (DIR_ACCUM, DIR_DIST):
        return None
    long = d["outcome"] == DIR_ACCUM
    m = cfg.TP2_MULT if tp2_mult is None else tp2_mult
    plan = risk_plan(long, d["close"], d["rh"], d["rl"], d["exc"], d["excP"], d["testP"], d["atr"],
                     cfg.SL_BUFFER_ATR, tick, m)
    if plan is None:
        return None
    sl, tp1, tp2, rr = plan
    return {
        "side": "LONG" if long else "SHORT", "entry": d["close"], "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
        "kind": ENTRY_NAMES.get(d["entryKind"], "-"), "rh": d["rh"], "rl": d["rl"], "atr": d["atr"],
        "conf": d["conf"], "val": d["val"], "time": d["time"], "risk_pct": abs(d["close"] - sl) / d["close"] * 100,
        "range_atr": round(d.get("range_atr", 0.0), 2), "b_bars": d.get("b_bars", 0),
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


# ── contextos Wyckoff (TF superior del propio símbolo, y BTC) ──
def context_of(ctx_state):
    """Dirección de la estructura: +1 acumulación, -1 distribución, 0 nada/Fase A."""
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


def filters(sig, cfg, trend, ctx_align="neutral", btc_align="neutral"):
    """Lista de motivos por los que NO se abre (vacía = se puede abrir)."""
    why = []
    if cfg.MIN_RR > 0 and sig["rr"] < cfg.MIN_RR:
        why.append(f"R:R {sig['rr']:.2f} < {cfg.MIN_RR}")
    if sig["risk_pct"] > cfg.MAX_RISK_DIST_PCT:
        why.append(f"stop a {sig['risk_pct']:.2f}% (> {cfg.MAX_RISK_DIST_PCT}%)")
    if sig["risk_pct"] < cfg.MIN_RISK_DIST_PCT:
        why.append(f"stop a {sig['risk_pct']:.2f}% (< {cfg.MIN_RISK_DIST_PCT}%, el coste se come la R)")
    against = (sig["side"] == "LONG" and trend < 0) or (sig["side"] == "SHORT" and trend > 0)
    sig["against_trend"], sig["ctx_align"], sig["btc_align"] = against, ctx_align, btc_align
    if against and cfg.TREND_FILTER == "bloquea":
        why.append(f"contra tendencia EMA {cfg.TREND_TF}")
    if ctx_align == "en contra" and cfg.CONTEXT_FILTER == "bloquea":
        why.append(f"estructura {cfg.CONTEXT_TF} en contra")
    if btc_align == "en contra" and cfg.BTC_FILTER == "bloquea":
        why.append(f"estructura de BTC {cfg.CONTEXT_TF} en contra")
    return why


class TradeSim:
    """Gestión: TP1 parcial (+ SL a BE), resto a TP2; opcional trailing tras TP1 y salida por tiempo.
    Orden dentro de cada vela: primero se comprueban toques con el stop vigente (si toca SL y TP a la vez,
    cuenta el SL: supuesto pesimista); al cierre se aplican tiempo y trailing, que rigen desde la vela siguiente."""

    def __init__(self, sig, bar, cost_pct, tp1_frac=0.5, move_be=True, trail_atr=0.0, time_stop=0):
        self.d = 1 if sig["side"] == "LONG" else -1
        self.e, self.s, self.t1, self.t2 = sig["entry"], sig["sl"], sig["tp1"], sig["tp2"]
        self.risk = abs(self.e - self.s)
        self.half = False
        self.bar = bar
        self.cost = cost_pct
        self.f = tp1_frac
        self.be = move_be
        self.trail = trail_atr
        self.tstop = time_stop
        self.reason = ""

    def _net(self, r):
        return r - 2.0 * self.cost / 100.0 * self.e / self.risk

    def step(self, bar, h, l, c=None, atr=None):
        """Devuelve R neta al cerrar, o None si sigue abierta. self.reason dice cómo cerró."""
        if bar <= self.bar or self.risk <= 0:
            return None
        d, f = self.d, self.f
        hitS = l <= self.s if d == 1 else h >= self.s
        hitT1 = (not self.half) and (h >= self.t1 if d == 1 else l <= self.t1)
        hitT2 = h >= self.t2 if d == 1 else l <= self.t2
        r1 = (self.t1 - self.e) * d / self.risk
        if hitS:
            rStop = (self.s - self.e) * d / self.risk
            if not self.half:
                self.reason = "SL"
                return self._net(rStop)
            self.reason = "BE" if abs(self.s - self.e) < 1e-12 else ("trailing" if self.trail else "SL tras TP1")
            return self._net(f * r1 + (1 - f) * rStop)
        if hitT1:
            self.half = True
            if self.be:
                self.s = self.e
        if hitT2:
            self.reason = "TP2"
            r2 = (self.t2 - self.e) * d / self.risk
            return self._net(f * r1 + (1 - f) * r2 if self.half else r2)
        if c is not None:
            if self.tstop and not self.half and bar - self.bar >= self.tstop:
                self.reason = "tiempo"
                return self._net((c - self.e) * d / self.risk)
            if self.trail and self.half and atr and atr == atr:
                new = c - d * self.trail * atr
                if (new - self.s) * d > 0:
                    self.s = new
        return None


EXIT_KEYS = ("TP2_MULT", "TRAIL_ATR", "TIME_STOP_BARS")


def exit_variant(cfg, **over):
    """Clave hashable de una variante de salida: (tp2_mult, trail_atr, time_stop)."""
    v = {k: getattr(cfg, k) for k in EXIT_KEYS}
    v.update(over)
    return (float(v["TP2_MULT"]), float(v["TRAIL_ATR"]), int(v["TIME_STOP_BARS"]))


def scan_candidates(rows, tf_s, tick, strict, cfg, warmup, htf_ema=None, ctx_rows=None, ctx_tf_s=None,
                    symbol="", range_effort=False, btc_rows=None, exits=None):
    """Recorre las velas con el motor y devuelve TODAS las entradas que valida. Cada una lleva, por variante
    de salida, su resultado simulado de forma independiente (solo depende de los precios futuros) y sus
    etiquetas de filtro; el backtest aplica después filtros + "una posición a la vez" de forma exacta.
    htf_ema: [(cierre_ms, ema)]; ctx_rows / btc_rows: velas del TF de contexto del símbolo y de BTC."""
    exits = exits or [exit_variant(cfg)]
    eng = WyckoffEngine(tf_s, tick, strict, keep_bars=100000, range_effort=range_effort)
    ctx = WyckoffEngine(ctx_tf_s, tick, strict, keep_bars=100000, range_effort=range_effort) if ctx_rows else None
    btc = WyckoffEngine(ctx_tf_s, 0.1, strict, keep_bars=100000) if (btc_rows and ctx_tf_s) else None
    j = k = kb = 0
    ema = float("nan")
    cands, opens = [], []
    tf_ms = tf_s * 1000
    ctx_ms = (ctx_tf_s or 0) * 1000

    def feed(e, rws, kk, until):
        while e is not None and kk < len(rws) and rws[kk][0] + ctx_ms <= until:
            r = rws[kk]
            if not (r[5] <= 0 and r[2] == r[3]):
                e.update(*r)
            kk += 1
        return kk

    for idx, (t, o, h, l, c, v) in enumerate(rows):
        if v <= 0 and h == l:
            continue  # mercado cerrado (TradFi): igual que en el bot, no alimenta al motor
        d = eng.update(t, o, h, l, c, v)
        still = []
        for cand, key, sim in opens:
            r = sim.step(idx, h, l, c, d["atr"])
            if r is None:
                still.append((cand, key, sim))
            else:
                cand["res"][key] = {"r": r, "close_t": t + tf_ms, "reason": sim.reason, "bars": idx - sim.bar}
        opens = still
        bar_close = t + tf_ms
        while htf_ema and j < len(htf_ema) and htf_ema[j][0] <= bar_close:
            ema = htf_ema[j][1]
            j += 1
        k = feed(ctx, ctx_rows, k, bar_close)
        kb = feed(btc, btc_rows, kb, bar_close)
        if idx < warmup or not d["entry_now"]:
            continue
        base = build_signal(d, cfg, tick, exits[0][0])
        if base is None:
            continue
        cdir, clabel = context_of(ctx.last if ctx is not None else None)
        bdir, blabel = context_of(btc.last if btc is not None else None)
        base.update(symbol=symbol, trend=trend_dir(c, ema), ctx_dir=cdir, ctx_label=clabel,
                    ctx_align=alignment(base["side"], cdir), btc_label=blabel,
                    btc_align=alignment(base["side"], bdir) if btc is not None else "neutral",
                    open_t=bar_close, res={}, rr_by={})
        for key in exits:
            sig = build_signal(d, cfg, tick, key[0])
            base["rr_by"][key] = sig["rr"]
            opens.append((base, key, TradeSim(sig, idx, cfg.FEE_PCT + cfg.SLIPPAGE_PCT, cfg.TP1_FRACTION,
                                              cfg.MOVE_SL_TO_BE, key[1], key[2])))
        cands.append(base)
    return cands


def select_trades(cands, cfg, key=None):
    """Aplica filtros y 'una posición por símbolo a la vez' (greedy en orden temporal) para una variante
    de salida. Devuelve copias con r / close_t / reason de esa variante."""
    key = key or exit_variant(cfg)
    out, busy_until = [], {}
    for c in sorted(cands, key=lambda x: x["open_t"]):
        res = c["res"].get(key)
        if res is None:
            continue  # sigue abierta al final de los datos
        sig = dict(c, rr=c["rr_by"].get(key, c["rr"]))
        if filters(sig, cfg, c["trend"], c["ctx_align"], c.get("btc_align", "neutral")):
            continue
        if c["open_t"] < busy_until.get(c["symbol"], 0):
            continue
        busy_until[c["symbol"]] = res["close_t"]
        sig.update(res)
        out.append(sig)
    return out
