"""
Wyckoff Bot v2 — ejecuta en BingX las entradas del indicador "Wyckoff ES [theUltimator5]".

Ciclo:
  · al cierre de cada vela de cada TF de TIMEFRAMES alimenta el motor de cada símbolo con la vela cerrada
  · motor aparte en CONTEXT_TF (p. ej. 4h): la estructura mayor se registra en cada señal y puede filtrar
  · si el motor valida una ENTRADA en ESA vela → plan SL/TP1/TP2 → filtros
  · SIGNAL: avisa y lleva una operación virtual con la misma gestión y costes que el backtest
  · LIVE:   orden a mercado con el SL DENTRO de la orden + TP1 parcial + TP2; tras TP1, SL a breakeven;
            vigila en cada ciclo que la posición tenga stop y lo repone si falta
"""
import json
import logging
import os
import signal
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import config as C
from bingx import BingX, BingXError
from notify import Journal, Telegram
from universe import is_tradfi
from strategy import TradeSim, alignment, build_signal, context_of, ema_last, filters, trend_dir
from wyckoff_engine import (BIT_CTEST, BIT_SOS, BIT_SOW, BIT_SPRING, BIT_TEST, BIT_UTAD, DIR_ACCUM, PHASE_C,
                            PHASE_D, PHASE_NAMES, WyckoffEngine, has_bit)

os.makedirs(C.DATA_DIR, exist_ok=True)
logging.basicConfig(level=getattr(logging, C.LOG_LEVEL.upper(), logging.INFO),
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s", stream=sys.stdout)
log = logging.getLogger("main")

STATE_PATH = os.path.join(C.DATA_DIR, "state.json")
ALL_TFS = list(dict.fromkeys(C.TIMEFRAMES + ([C.CONTEXT_TF] if C.CONTEXT_TF else [])))


def tf_ms(tf):
    return C.tf_seconds(tf) * 1000


def now_ms():
    return int(time.time() * 1000)


def utc_day():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def dead_bar(o, h, l, c, v):
    """Vela sin negociación (mercado TradFi cerrado): precio plano y volumen cero."""
    return v <= 0 and h == l


def friday_cutoff():
    n = datetime.now(timezone.utc)
    return C.TRADFI_NO_ENTRY_FRI_UTC >= 0 and n.weekday() == 4 and n.hour >= C.TRADFI_NO_ENTRY_FRI_UTC


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class Bot:
    def __init__(self):
        self.ex = BingX(C.API_KEY, C.API_SECRET, C.VST)
        self.tg = Telegram(C.TG_TOKEN, C.TG_CHAT)
        self.journal = Journal(C.DATA_DIR)
        self.pool = ThreadPoolExecutor(max_workers=max(1, C.FETCH_WORKERS))
        self.engines = {}          # (símbolo, tf) → motor
        self.symbols = []
        self.universe_ts = 0
        self.trend_cache = {}
        self.cooldown = {}
        self.running = True
        self.attach_ok = C.ATTACH_SL
        self.state = self.load_state()
        self.last_status = time.time()

    def fp(self, x, sym):
        pp = self.ex.contracts.get(sym, {}).get("pp", 6)
        return f"{x:.{pp}f}"

    # ── estado persistente ──
    def load_state(self):
        base = {"positions": {}, "sims": {}, "daily": {"day": utc_day(), "r": 0.0, "n": 0},
                "stats": {"n": 0, "wins": 0, "sum_r": 0.0, "gw": 0.0, "gl": 0.0},
                "last_trade": time.time(), "idle_warned": False, "paused": False}
        try:
            with open(STATE_PATH) as f:
                st = json.load(f)
            for k, v in base.items():
                st.setdefault(k, v)
            for rec in list(st["positions"].values()) + list(st["sims"].values()):
                rec.setdefault("tf", C.TIMEFRAME)
            return st
        except (OSError, ValueError):
            return base

    def save_state(self):
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1, default=str)
        os.replace(tmp, STATE_PATH)

    def wr(self):
        st = self.state["stats"]
        return round(st["wins"] * 100 / st["n"]) if st["n"] else 0

    def stats_text(self):
        st = self.state["stats"]
        pf = f"{st['gw'] / st['gl']:.2f}" if st["gl"] > 0 else "∞"
        avg = st["sum_r"] / st["n"] if st["n"] else 0
        return (f"{st['n']} ops · acierto {self.wr()}% · media {avg:+.3f}R · total {st['sum_r']:+.2f}R · PF {pf}"
                + (" (muestra pequeña: un dibujo, no evidencia)" if st["n"] < 30 else ""))

    def roll_day(self):
        d = utc_day()
        if self.state["daily"]["day"] != d:
            old = self.state["daily"]
            self.tg.send(f"📅 <b>Resumen {old['day']}</b>\n{old['n']} cerradas · {old['r']:+.2f}R\n"
                         f"Acumulado: {self.stats_text()}\n"
                         f"Compara la media por operación con la del backtest en los mismos símbolos.")
            self.state["daily"] = {"day": d, "r": 0.0, "n": 0}
            self.save_state()

    def register_result(self, r):
        st, dl = self.state["stats"], self.state["daily"]
        st["n"] += 1
        st["wins"] += 1 if r > 0 else 0
        st["sum_r"] += r
        st["gw"] += r if r > 0 else 0
        st["gl"] += -r if r < 0 else 0
        dl["r"] += r
        dl["n"] += 1
        self.state["last_trade"] = time.time()
        self.state["idle_warned"] = False
        self.save_state()
        if dl["r"] <= -C.MAX_DAILY_LOSS_R:
            self.tg.send(f"🛑 Pérdida diaria {dl['r']:+.2f}R ≥ {C.MAX_DAILY_LOSS_R}R: sin nuevas aperturas hasta 00:00 UTC.")

    # ── universo y calentamiento ──
    def refresh_universe(self, force=False):
        if not force and time.time() - self.universe_ts < C.UNIVERSE_REFRESH_H * 3600:
            return
        self.ex.load_contracts()
        norm = lambda s: s if "-" in s else s.replace("USDT", "-USDT")
        if C.SYMBOLS:
            syms = [norm(s) for s in C.SYMBOLS]
        else:
            rows, skipped = [], {"categoría": 0, "volumen": 0, "API cerrada": 0}
            vols = {t.get("symbol", ""): float(t.get("quoteVolume", 0) or 0) for t in self.ex.tickers()}
            for s, c in self.ex.contracts.items():
                if c["cls"] not in C.CATEGORIES:
                    skipped["categoría"] += 1
                    continue
                if not c["api_open"]:
                    skipped["API cerrada"] += 1
                    continue
                qv = vols.get(s, 0.0)
                if qv < (C.MIN_QUOTE_VOL if c["cls"] == "crypto" else C.MIN_QUOTE_VOL_TRADFI):
                    skipped["volumen"] += 1
                    continue
                rows.append((qv, s))
            rows.sort(reverse=True)
            n = C.AUTO_TOP_N if C.UNIVERSE == "top" else C.MAX_SYMBOLS
            syms = [s for _, s in rows[:n]]
            self.universe_note = (f"{len(self.ex.contracts)} perpetuos en BingX · fuera por " +
                                  ", ".join(f"{k} {v}" for k, v in skipped.items() if v) +
                                  (f", tope {n}" if len(rows) > n else ""))
        bl = {norm(b) for b in C.BLACKLIST}
        syms = [s for s in syms if s in self.ex.contracts and s not in bl]
        for s in list(self.state["positions"]) + list(self.state["sims"]):
            if s not in syms and s in self.ex.contracts:
                syms.append(s)  # no abandonar lo que está abierto
        jobs = [(s, tf) for s in syms for tf in ALL_TFS if (s, tf) not in self.engines]
        self.warmup_many(jobs)
        for key in list(self.engines):
            if key[0] not in syms:
                del self.engines[key]
        self.symbols = [s for s in syms if all((s, tf) in self.engines for tf in C.TIMEFRAMES)]
        self.universe_ts = time.time()
        log.info("universo: %d símbolos · %d motores", len(self.symbols), len(self.engines))
        by = {}
        for s in self.symbols:
            k = self.ex.contracts[s]["cls_label"]
            by[k] = by.get(k, 0) + 1
        txt = " · ".join(f"{k} {v}" for k, v in sorted(by.items(), key=lambda x: -x[1]))
        if txt != getattr(self, "_last_universe_txt", None):
            self._last_universe_txt = txt
            self.tg.send(f"🌐 Universo: {len(self.symbols)} símbolos ({txt})\n{getattr(self, 'universe_note', '')}")

    def warmup_many(self, jobs):
        def fetch(job):
            s, tf = job
            n = C.WARMUP_BARS if tf in C.TIMEFRAMES else C.CONTEXT_WARMUP
            try:
                return job, self.ex.klines_history(s, tf, n + 1, tf_ms(tf))
            except BingXError as e:
                log.warning("warmup %s %s: %s", s, tf, e)
                return job, None

        for (s, tf), rows in self.pool.map(fetch, jobs):
            if rows:
                self.build_engine(s, tf, rows)

    def build_engine(self, sym, tf, rows):
        eng = WyckoffEngine(C.tf_seconds(tf), self.ex.contracts[sym]["tick"], C.ENTRY_STRICTNESS,
                            keep_bars=C.ENGINE_KEEP_BARS,
                            range_effort=is_tradfi(sym) and C.TRADFI_EFFORT == "rango")
        cut, last = now_ms(), 0
        for t, o, h, l, c, v in rows:
            if t + tf_ms(tf) <= cut:
                if not dead_bar(o, h, l, c, v):
                    eng.update(t, o, h, l, c, v)
                last = t
        eng.last_t = last
        self.engines[(sym, tf)] = eng

    # ── filtros de contexto ──
    def trend(self, sym, price):
        if C.TREND_FILTER == "off":
            return 0
        ms = tf_ms(C.TREND_TF)
        key = (sym, now_ms() // ms)
        if key not in self.trend_cache:
            try:
                rows = self.ex.klines(sym, C.TREND_TF, C.TREND_EMA * 4 + 10)
                closes = [r[4] for r in rows if r[0] + ms <= now_ms()]
                self.trend_cache = {k: v for k, v in self.trend_cache.items() if k[1] == key[1]}
                self.trend_cache[key] = ema_last(closes, C.TREND_EMA)
            except BingXError as e:
                log.warning("tendencia %s: %s", sym, e)
                return 0
        return trend_dir(price, self.trend_cache[key])

    def context(self, sym, tf):
        if not C.CONTEXT_TF or C.tf_seconds(C.CONTEXT_TF) <= C.tf_seconds(tf):
            return 0, "-"
        eng = self.engines.get((sym, C.CONTEXT_TF))
        return context_of(eng.last if eng else None)

    # ── cierre de velas ──
    def process_tf(self, tf):
        cut, ms = now_ms(), tf_ms(tf)
        syms = [s for s in self.symbols if (s, tf) in self.engines]

        def fetch(sym):
            try:
                return sym, self.ex.klines(sym, tf, 6)
            except BingXError as e:
                log.warning("klines %s %s: %s", sym, tf, e)
                return sym, None

        trading = tf in C.TIMEFRAMES
        watch, rebuild = [], []
        for sym, rows in self.pool.map(fetch, syms):
            eng = self.engines.get((sym, tf))
            if rows is None or eng is None:
                continue
            fresh = [r for r in rows if r[0] > eng.last_t and r[0] + ms <= cut]
            if len(fresh) > 4:  # hueco grande (caída/redeploy): reconstruir
                rebuild.append((sym, tf))
                continue
            d = None
            for t, o, h, l, c, v in fresh:
                eng.last_t = t
                if dead_bar(o, h, l, c, v):
                    continue  # mercado cerrado (TradFi): no alimenta al motor
                d = eng.update(t, o, h, l, c, v)
                if trading:
                    self.step_sim(sym, tf, t, h, l)
            if d is None or not trading:
                continue
            note = self.event_note(d)
            if note:
                watch.append(f"{sym} {tf} {note}")
            if d["entry_now"] and d["time"] == fresh[-1][0]:
                self.handle_entry(sym, tf, d)
        if rebuild:
            self.warmup_many(rebuild)
        if watch:
            self.tg.send("👀 <b>Eventos Wyckoff</b>\n" + "\n".join(watch[:25]))

    @staticmethod
    def event_note(d):
        sig = d["sig"]
        long = d["wdir"] == DIR_ACCUM
        side = "LONG" if long else "SHORT"
        if has_bit(sig, BIT_SPRING):
            return f"Spring → preparando {side} (falta Test/SOS)"
        if has_bit(sig, BIT_UTAD):
            return f"UTAD → preparando {side} (falta Test/SOW)"
        if (has_bit(sig, BIT_TEST) or has_bit(sig, BIT_CTEST)) and not d["entry_now"]:
            return f"Test Fase C sin entrada (madurez {d['conf']}, validación {d['val']})"
        if (has_bit(sig, BIT_SOS) or has_bit(sig, BIT_SOW)) and not d["entry_now"]:
            return f"{'SOS' if long else 'SOW'} → Fase D, esperando {'LPS' if long else 'LPSY'}"
        return ""

    # ── señales ──
    def handle_entry(self, sym, tf, d):
        sig = build_signal(d, C, self.ex.contracts[sym]["tick"])
        if sig is None or time.time() - self.cooldown.get(sym, 0) < C.SIGNAL_COOLDOWN_MIN * 60:
            return
        self.cooldown[sym] = time.time()
        sig["tf"] = tf
        sig["trend"] = self.trend(sym, sig["entry"])
        cdir, clabel = self.context(sym, tf)
        sig["ctx_label"] = clabel
        why = filters(sig, C, sig["trend"], alignment(sig["side"], cdir))
        if self.state["paused"]:
            why.append("bot en pausa (/reanudar)")
        if sym in self.state["sims"] or sym in self.state["positions"]:
            why.append("ya hay una operación en este símbolo")
        cinfo = self.ex.contracts[sym]
        if cinfo["cls"] != "crypto":
            if friday_cutoff():
                why.append(f"TradFi en viernes ≥{C.TRADFI_NO_ENTRY_FRI_UTC}h UTC: el SL no se ejecuta con el mercado cerrado")
            if not cinfo["api_open"]:
                why.append("BingX tiene cerrada la apertura por API")
        if C.LIVE:
            why += self.live_blockers(sym)
        icon = "🟢" if sig["side"] == "LONG" else "🔴"
        ctx_icon = {"a favor": "✅", "en contra": "⚠", "neutral": "·"}[sig["ctx_align"]]
        cls = "" if cinfo["cls"] == "crypto" else f" · {cinfo['cls_label']} {cinfo['name']}"
        txt = (f"{icon} <b>{sig['side']} {sym}</b>{cls} · {tf} · {sig['kind']}\n"
               f"Entrada {self.fp(sig['entry'], sym)} · SL {self.fp(sig['sl'], sym)} ({sig['risk_pct']:.2f}%)\n"
               f"TP1 {self.fp(sig['tp1'], sym)} · TP2 {self.fp(sig['tp2'], sym)} · R:R {sig['rr']:.2f}\n"
               f"Rango {self.fp(sig['rl'], sym)} – {self.fp(sig['rh'], sym)} · madurez {sig['conf']} · validación {sig['val']}\n"
               f"{ctx_icon} Contexto {C.CONTEXT_TF or '-'}: {clabel} ({sig['ctx_align']})"
               + (f"\n⚠ contra EMA{C.TREND_EMA} {C.TREND_TF}" if sig.get("against_trend") else ""))
        if why:
            self.tg.send(txt + "\n⏸ <i>No se abre: " + "; ".join(dict.fromkeys(why)) + "</i>")
            return
        if not C.LIVE:
            self.state["sims"][sym] = {**sig, "bar_t": d["time"], "half": False, "open_ts": time.time()}
            self.save_state()
            self.tg.send(txt + "\n📝 SIGNAL: operación virtual abierta")
            return
        self.open_live(sym, sig, txt)

    def step_sim(self, sym, tf, t, h, l):
        s = self.state["sims"].get(sym)
        if not s or s.get("tf") != tf or t <= s["bar_t"]:
            return
        sim = TradeSim(s, 0, C.FEE_PCT + C.SLIPPAGE_PCT, C.TP1_FRACTION, C.MOVE_SL_TO_BE)
        sim.half = s["half"]
        if s["half"] and C.MOVE_SL_TO_BE:
            sim.s = s["entry"]
        r = sim.step(1, h, l)
        if sim.half and not s["half"]:
            s["half"] = True
            self.tg.send(f"🎯 Virtual {sym} TP1 tocado" + (" · SL a breakeven" if C.MOVE_SL_TO_BE else ""))
        if r is None:
            self.save_state()
            return
        del self.state["sims"][sym]
        self.register_result(r)
        self.journal.write({"open_time": iso(s["open_ts"]), "close_time": iso(time.time()), "symbol": sym,
                            "tf": tf, "side": s["side"], "kind": s["kind"], "entry_expected": s["entry"],
                            "entry_real": s["entry"], "sl": s["sl"], "tp1": s["tp1"], "tp2": s["tp2"],
                            "rr_plan": round(s["rr"], 2), "r_net": round(r, 3),
                            "minutes": round((time.time() - s["open_ts"]) / 60), "conf": s["conf"], "val": s["val"],
                            "against_trend": s.get("against_trend"), "ctx_align": s.get("ctx_align"),
                            "ctx_label": s.get("ctx_label"), "mode": "SIGNAL"})
        self.tg.send(f"{'✅' if r > 0 else '❌'} Virtual {s['side']} {sym} cerrada: {r:+.2f}R "
                     f"(hoy {self.state['daily']['r']:+.2f}R · total {self.state['stats']['sum_r']:+.2f}R)")

    # ── LIVE ──
    def live_blockers(self, sym):
        why = []
        if self.state["daily"]["r"] <= -C.MAX_DAILY_LOSS_R:
            why.append(f"pérdida diaria {self.state['daily']['r']:+.2f}R")
        if len(self.state["positions"]) >= C.MAX_CONCURRENT:
            why.append(f"máximo del bot ({C.MAX_CONCURRENT})")
        try:
            allpos = self.ex.positions()
            if len(allpos) >= C.MAX_TOTAL_POSITIONS:
                why.append(f"máximo de la cuenta ({len(allpos)}/{C.MAX_TOTAL_POSITIONS})")
            if any(p.get("symbol") == sym for p in allpos):
                why.append("la cuenta ya tiene posición en este símbolo")
        except BingXError as e:
            why.append(f"no se pudo leer posiciones ({e})")
        return why

    def open_live(self, sym, sig, txt):
        ex = self.ex
        long = sig["side"] == "LONG"
        d = 1 if long else -1
        try:
            price = ex.price(sym)
            equity, avail = ex.balance()
        except BingXError as e:
            self.tg.send(txt + f"\n⚠ sin precio/balance: {e}")
            return
        risk = abs(sig["entry"] - sig["sl"])
        adv = (price - sig["entry"]) * d / risk
        if adv > C.CHASE_MAX_R or (price - sig["sl"]) * d <= 0:
            self.tg.send(txt + f"\n⏸ No se persigue: el precio ya avanzó {adv:+.2f}R")
            return
        qty = equity * C.RISK_PCT / 100.0 / abs(price - sig["sl"])
        qty = ex.fmt_qty(sym, min(qty, avail * C.LEVERAGE * 0.9 / price))
        c = ex.contracts[sym]
        if qty <= 0 or qty < c["min_qty"] or qty * price < max(c["min_usdt"], 2.0):
            self.tg.send(txt + f"\n⏸ Tamaño insuficiente ({qty}) para equity {equity:.2f}")
            return
        ex.set_margin_mode(sym, C.MARGIN_MODE)
        ex.set_leverage(sym, C.LEVERAGE)
        cid = f"wyk{int(time.time())}{sym.split('-')[0][:6]}"
        sent = False
        for attach in ([True, False] if self.attach_ok else [False]):
            try:
                ex.market_open(sym, long, qty, cid + ("a" if attach else "b"), stop_loss=sig["sl"] if attach else None)
                sent = True
                break
            except BingXError as e:
                time.sleep(2)
                if ex.order_exists(sym, cid + ("a" if attach else "b")):
                    log.warning("respuesta perdida pero la orden existe")
                    sent = True
                    break
                if attach and not str(e).startswith("red"):
                    log.warning("BingX rechaza el SL adjunto (%s); se pone aparte", e)
                    self.attach_ok = False
                    continue
                self.tg.send(txt + f"\n❌ Orden rechazada: {e}")
                return
        if not sent:
            return
        pos = None
        for _ in range(8):
            time.sleep(1)
            try:
                pos = self.find_pos(sym, long)
            except BingXError:
                pos = None
            if pos:
                break
        if not pos:
            self.tg.send(txt + "\n🚨 Orden enviada pero la posición no aparece. REVISA BINGX A MANO.")
            return
        amt = abs(float(pos["positionAmt"]))
        entry = float(pos.get("avgPrice", price) or price)
        rec = {**sig, "qty": amt, "entry_real": entry, "risk": abs(entry - sig["sl"]), "be": False,
               "open_ts": time.time(), "cid": cid, "sl_id": "", "tp1_id": "", "tp2_id": ""}
        if not self.ensure_sl(sym, rec, amt, at_open=True):
            return
        q1 = ex.fmt_qty(sym, amt * C.TP1_FRACTION)
        q2 = ex.fmt_qty(sym, amt - q1)
        try:
            if q1 > 0 and q2 > 0 and q1 >= c["min_qty"] and q2 >= c["min_qty"]:
                rec["tp1_id"] = ex.exit_order(sym, long, "TAKE_PROFIT_MARKET", q1, sig["tp1"])
                rec["tp2_id"] = ex.exit_order(sym, long, "TAKE_PROFIT_MARKET", q2, sig["tp2"])
            else:
                rec["tp2_id"] = ex.exit_order(sym, long, "TAKE_PROFIT_MARKET", amt, sig["tp2"])
        except BingXError as e:
            self.tg.send(f"⚠ {sym}: TP no colocado ({e}). El SL sí está puesto.")
        self.state["positions"][sym] = rec
        self.save_state()
        slip = (entry - sig["entry"]) / sig["entry"] * 100 * d
        self.tg.send(txt + f"\n✅ <b>LIVE</b> abierta {amt} @ {self.fp(entry, sym)} (desliz. {slip:+.3f}%)"
                     + (" · SL en la orden" if self.attach_ok else ""))

    def ensure_sl(self, sym, rec, amt, at_open=False, quiet=False):
        """Garantiza que la posición tiene stop. Si no lo tiene, lo pone; si no puede, cierra (al abrir) o avisa."""
        long = rec["side"] == "LONG"
        try:
            stops = self.ex.stop_orders(sym, long)
        except BingXError as e:
            log.warning("stop_orders %s: %s", sym, e)
            return True  # no se pudo comprobar: se reintenta en el próximo ciclo
        if stops:
            rec["sl_id"] = str(stops[0].get("orderId", rec.get("sl_id", "")))
            return True
        level = rec["entry_real"] if rec["be"] else rec["sl"]
        try:
            rec["sl_id"] = self.ex.exit_order(sym, long, "STOP_MARKET", amt, level)
            if not at_open and not quiet:
                self.tg.send(f"🛡 {sym}: faltaba el stop y se ha repuesto en {self.fp(level, sym)}")
            return True
        except BingXError as e:
            if at_open:
                try:
                    self.ex.market_close(sym, long, amt)
                    self.tg.send(f"🚨 {sym}: no se pudo poner el SL ({e}). Posición cerrada a mercado por seguridad.")
                except BingXError as e2:
                    self.tg.send(f"🚨 {sym}: SIN SL y no se pudo cerrar ({e2}). CIERRA A MANO YA.")
                return False
            self.tg.send(f"🚨 {sym}: SIN STOP ({e}). Pon el SL a mano en BingX YA.")
            return True

    def _stops(self, sym, long):
        try:
            return self.ex.stop_orders(sym, long)
        except BingXError:
            return []

    def find_pos(self, sym, long):
        for p in self.ex.positions(sym):
            amt = float(p.get("positionAmt", 0))
            ps = str(p.get("positionSide", "BOTH")).upper()
            if (ps == "LONG" and long) or (ps == "SHORT" and not long):
                return p
            if ps == "BOTH" and ((amt > 0) == long):
                return p
        return None

    def order_fill(self, sym, oid):
        if not oid:
            return None
        try:
            o = self.ex.get_order(sym, order_id=oid)
            if str(o.get("status", "")).upper() == "FILLED":
                return float(o.get("avgPrice", 0) or o.get("stopPrice", 0) or 0) or None
        except BingXError:
            pass
        return None

    def manage(self):
        if not C.LIVE or not self.state["positions"]:
            return
        ex = self.ex
        for sym, rec in list(self.state["positions"].items()):
            long = rec["side"] == "LONG"
            try:
                pos = self.find_pos(sym, long)
            except BingXError as e:
                log.warning("manage %s: %s", sym, e)
                continue
            if pos is None:
                self.close_record(sym, rec)
                continue
            amt = abs(float(pos["positionAmt"]))
            if not rec.get("half") and amt <= rec["qty"] * (1 - C.TP1_FRACTION) * 1.02:
                rec["half"] = True
                # el SL original cubre la cantidad completa: se sustituye por uno de la cantidad restante
                for o in [{"orderId": rec.get("sl_id")}] + self._stops(sym, long):
                    if o.get("orderId"):
                        ex.cancel(sym, o["orderId"])
                rec["sl_id"], rec["be"] = "", C.MOVE_SL_TO_BE
                self.tg.send(f"🎯 {sym} TP1 tocado" + (f" · SL a breakeven {self.fp(rec['entry_real'], sym)}"
                                                        if C.MOVE_SL_TO_BE else ""))
                self.save_state()
                self.ensure_sl(sym, rec, amt, quiet=True)  # nuevo SL (BE) con la cantidad restante
            self.ensure_sl(sym, rec, amt)  # guardián: repone el stop si falta
            hours = (time.time() - rec["open_ts"]) / 3600
            if hours > C.ZOMBIE_ALERT_HOURS and not rec.get("zombie"):
                rec["zombie"] = True
                self.save_state()
                self.tg.send(f"🧟 {sym} lleva {hours:.0f}h abierta. Decide tú si cerrarla.")

    def close_record(self, sym, rec):
        ex = self.ex
        long = rec["side"] == "LONG"
        d = 1 if long else -1
        e, risk = rec["entry_real"], max(rec["risk"], 1e-12)
        f = C.TP1_FRACTION
        r1 = (rec["tp1"] - e) * d / risk
        px_tp2 = self.order_fill(sym, rec.get("tp2_id"))
        px_sl = self.order_fill(sym, rec.get("sl_id"))
        if px_tp2:
            exit_px, reason = px_tp2, "TP2"
        elif px_sl:
            exit_px, reason = px_sl, "BE" if rec.get("be") else "SL"
        else:
            try:
                exit_px = ex.price(sym)
            except BingXError:
                exit_px = e
            reason = "externo/manual"
        r_exit = (exit_px - e) * d / risk
        r = (f * r1 + (1 - f) * r_exit) if rec.get("half") else r_exit
        r -= 2.0 * C.FEE_PCT / 100.0 * e / risk
        for oid in (rec.get("sl_id"), rec.get("tp1_id"), rec.get("tp2_id")):
            if oid:
                ex.cancel(sym, oid)  # no dejar órdenes huérfanas
        try:
            for o in ex.stop_orders(sym, long):
                ex.cancel(sym, o.get("orderId"))
        except BingXError:
            pass
        del self.state["positions"][sym]
        self.register_result(r)
        mins = (time.time() - rec["open_ts"]) / 60
        self.journal.write({"open_time": iso(rec["open_ts"]), "close_time": iso(time.time()), "symbol": sym,
                            "tf": rec.get("tf"), "side": rec["side"], "kind": rec["kind"],
                            "entry_expected": rec["entry"], "entry_real": e,
                            "slippage_pct": round((e - rec["entry"]) / rec["entry"] * 100 * d, 4),
                            "sl": rec["sl"], "tp1": rec["tp1"], "tp2": rec["tp2"], "rr_plan": round(rec["rr"], 2),
                            "qty": rec["qty"], "exit_reason": reason, "r_net": round(r, 3), "minutes": round(mins),
                            "conf": rec["conf"], "val": rec["val"], "against_trend": rec.get("against_trend"),
                            "ctx_align": rec.get("ctx_align"), "ctx_label": rec.get("ctx_label"), "mode": "LIVE"})
        self.tg.send(f"{'✅' if r > 0 else '❌'} {rec['side']} {sym} cerrada por {reason}: {r:+.2f}R "
                     f"en {mins:.0f} min (hoy {self.state['daily']['r']:+.2f}R)")

    def reconcile(self):
        if not C.LIVE:
            if self.state["positions"]:
                self.tg.send(f"ℹ Modo SIGNAL: limpio {len(self.state['positions'])} posición(es) LIVE del estado "
                             f"anterior (revisa BingX por si siguen abiertas).")
                self.state["positions"] = {}
                self.save_state()
            return
        try:
            allpos = self.ex.positions()
        except BingXError as e:
            self.tg.send(f"⚠ reconcile: no se pudo leer posiciones ({e})")
            return
        for sym, rec in list(self.state["positions"].items()):
            if self.find_pos(sym, rec["side"] == "LONG") is None:
                self.close_record(sym, rec)
        # órdenes huérfanas de despliegues anteriores (marcadas con prefijo wyk)
        try:
            n = 0
            for o in self.ex.open_orders():
                cid = str(o.get("clientOrderId", o.get("clientOrderID", "")))
                if cid.startswith("wyk") and o.get("symbol") not in self.state["positions"]:
                    n += self.ex.cancel(o.get("symbol"), o.get("orderId"))
            if n:
                self.tg.send(f"🧹 Canceladas {n} órdenes huérfanas de un despliegue anterior.")
        except BingXError as e:
            log.warning("limpieza de huérfanas: %s", e)
        foreign = [p["symbol"] for p in allpos if p["symbol"] not in self.state["positions"]]
        if foreign:
            self.tg.send(f"ℹ En la cuenta hay {len(foreign)} posición(es) ajenas a este bot: {', '.join(foreign[:10])}. "
                         f"Cuentan para MAX_TOTAL_POSITIONS.")

    def weekend_watch(self):
        """Viernes: aviso único si hay TradFi abiertas, porque el fin de semana el SL no se ejecuta
        y el precio puede reabrir el lunes al otro lado del stop (gap)."""
        if not friday_cutoff() or self.state.get("weekend_warned") == utc_day():
            return
        self.state["weekend_warned"] = utc_day()
        self.save_state()
        open_tf = [f"{k} {s}" for k, book in (("LIVE", self.state["positions"]), ("virtual", self.state["sims"]))
                   for s in book if is_tradfi(s)]
        if open_tf:
            self.tg.send("📆 Viernes: TradFi abiertas que pasarán el fin de semana con el mercado cerrado: "
                         + ", ".join(open_tf) + ". El SL no protege de un gap del lunes; decide tú si cerrar.")

    # ── Telegram ──
    def commands(self):
        if not C.TG_COMMANDS:
            return
        for cmd in self.tg.poll():
            c = cmd.split()[0].split("@")[0].lower()
            if c in ("/estado", "/status"):
                self.status(force=True)
            elif c in ("/posiciones", "/pos"):
                self.tg.send(self.positions_text())
            elif c == "/stats":
                self.tg.send(f"📈 {self.stats_text()}\nHoy {self.state['daily']['r']:+.2f}R ({self.state['daily']['n']} ops)")
            elif c == "/pausa":
                self.state["paused"] = True
                self.save_state()
                self.tg.send("⏸ Pausado: no se abren operaciones nuevas. Las abiertas se siguen gestionando.")
            elif c == "/reanudar":
                self.state["paused"] = False
                self.save_state()
                self.tg.send("▶ Reanudado.")
            else:
                self.tg.send("Comandos: /estado /posiciones /stats /pausa /reanudar")

    def positions_text(self):
        lines = []
        for kind, book in (("LIVE", self.state["positions"]), ("virtual", self.state["sims"])):
            for sym, r in book.items():
                try:
                    px = self.ex.price(sym)
                    d = 1 if r["side"] == "LONG" else -1
                    e = r.get("entry_real", r["entry"])
                    upnl = (px - e) * d / max(abs(e - r["sl"]), 1e-12)
                    lines.append(f"{kind} {r['side']} {sym} {r.get('tf', '')} · {upnl:+.2f}R"
                                 + (" · TP1 hecho" if r.get("half") else ""))
                except BingXError:
                    lines.append(f"{kind} {r['side']} {sym}")
        return "📂 " + ("\n".join(lines) if lines else "Sin operaciones abiertas")

    def status(self, force=False):
        if not force and time.time() - self.last_status < C.STATUS_EVERY_H * 3600:
            return
        self.last_status = time.time()
        prep, counts = [], {}
        for (sym, tf), eng in self.engines.items():
            if tf not in C.TIMEFRAMES:
                continue
            d = eng.last or {}
            ph = d.get("phase", 0)
            counts[ph] = counts.get(ph, 0) + 1
            if ph in (PHASE_C, PHASE_D) and d.get("outcome"):
                cdir, clabel = self.context(sym, tf)
                prep.append(f"{sym} {tf}: {eng.status_text()} · ctx {clabel}")
        dist = " · ".join(f"{PHASE_NAMES[k]}:{v}" for k, v in sorted(counts.items()) if k)
        self.tg.send(f"📊 <b>Estado</b>{' ⏸ PAUSADO' if self.state['paused'] else ''} · {len(self.symbols)} símbolos"
                     f" · fases {dist or 'ninguna activa'}\n"
                     f"Abiertas: {len(self.state['positions'])} live · {len(self.state['sims'])} virtuales\n"
                     f"Hoy {self.state['daily']['r']:+.2f}R · {self.stats_text()}\n"
                     + ("\n".join(["Preparando:"] + prep[:15]) if prep else "Ninguna estructura en Fase C/D"))
        if C.LIVE and time.time() - self.state["last_trade"] > C.IDLE_ALERT_DAYS * 86400 and not self.state["idle_warned"]:
            self.state["idle_warned"] = True
            self.save_state()
            self.tg.send(f"💤 {C.IDLE_ALERT_DAYS:.0f} días sin operaciones. El bot vive, pero no opera: revisa universo y exigencia.")

    # ── bucle ──
    def run(self):
        self.tg.send(f"🚀 <b>Wyckoff Bot</b> · {C.CODE_VERSION}\n{C.summary()}")
        if C.MODE == "LIVE" and not C.LIVE:
            self.tg.send("⚠ MODE=LIVE pero CONFIRM_LIVE≠SI → arrancando en SIGNAL.")
        if C.LIVE and not (C.API_KEY and C.API_SECRET):
            self.tg.send("❌ LIVE sin BINGX_API_KEY/SECRET. Parado.")
            return
        t0 = time.time()
        self.refresh_universe(force=True)
        self.reconcile()
        self.tg.send(f"✅ {len(self.engines)} estructuras reconstruidas ({len(self.symbols)} símbolos × "
                     f"{'/'.join(ALL_TFS)}) en {time.time() - t0:.0f}s.")
        next_close = {tf: (now_ms() // tf_ms(tf) + 1) * tf_ms(tf) for tf in ALL_TFS}
        last_manage = last_cmd = 0
        while self.running:
            try:
                self.roll_day()
                self.weekend_watch()
                due = [tf for tf in ALL_TFS if now_ms() >= next_close[tf] + C.CANDLE_DELAY_S * 1000]
                if due:
                    self.refresh_universe()
                    for tf in sorted(due, key=C.tf_seconds, reverse=True):  # contexto antes que entradas
                        self.process_tf(tf)
                        next_close[tf] = (now_ms() // tf_ms(tf) + 1) * tf_ms(tf)
                    self.status()
                if time.time() - last_manage >= C.MANAGE_EVERY_S:
                    self.manage()
                    last_manage = time.time()
                if time.time() - last_cmd >= 3:
                    self.commands()
                    last_cmd = time.time()
            except Exception as e:  # un error puntual no debe tumbar el bot
                log.error("ciclo: %s\n%s", e, traceback.format_exc())
                self.tg.send(f"⚠ Error en el ciclo: {e}")
                time.sleep(10)
            time.sleep(1)


def main():
    log.info("%s | %s", C.CODE_VERSION, C.summary())
    bot = Bot()

    def stop(*_):
        bot.running = False
        log.info("parada solicitada")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    bot.run()


if __name__ == "__main__":
    main()
