"""
Barrido de variantes SIN engañarse: elige con el primer 70% del tiempo (entrenamiento) y enseña cómo le fue
a esa misma variante en el último 30% (prueba), que no se usó para elegir.

  python sweep.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT --tf 15m --days 240

Probar 48 variantes es hacer 48 apuestas: alguna sale bien por azar. Por eso:
  · la columna que manda es la de PRUEBA, no la de entrenamiento
  · el umbral de t se corrige por el número de variantes (Bonferroni)
  · si la mejor en entrenamiento se hunde en prueba, no hay nada que elegir
"""
import argparse
import itertools
from statistics import NormalDist
from types import SimpleNamespace

import config as C
from backtest import load_symbol, metrics
from strategy import select_trades


def cfg_with(**kw):
    base = {k: getattr(C, k) for k in dir(C) if k.isupper()}
    base.update(kw)
    return SimpleNamespace(**base)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT")
    ap.add_argument("--tf", default=C.TIMEFRAME)
    ap.add_argument("--days", type=int, default=240)
    ap.add_argument("--warmup", type=int, default=400)
    args = ap.parse_args()
    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]  # con guion → datos de BingX (TradFi)
    # candidatos con todo el contexto calculado (los filtros se aplican después)
    scan_cfg = cfg_with(TREND_FILTER="aviso", CONTEXT_FILTER="aviso")
    cands = {}
    for strict in ("Agresivo", "Estándar", "Conservador"):
        allc = []
        for s in syms:
            allc += load_symbol(s, args.tf, args.days, args.warmup, strict, scan_cfg)
        cands[strict] = allc
        print(f"{strict}: {len(allc)} entradas del motor")
    times = sorted(c["open_t"] for c in cands["Agresivo"]) or [0]
    cutoff = times[0] + (times[-1] - times[0]) * 0.7
    grid = list(itertools.product(("Agresivo", "Estándar", "Conservador"), ("off", "bloquea"), ("off", "bloquea"),
                                  (0.0, 1.0, 1.5, 2.0)))
    rows = []
    for strict, trend, ctx, rr in grid:
        cfg = cfg_with(TREND_FILTER=trend, CONTEXT_FILTER=ctx, MIN_RR=rr)
        tr = select_trades(cands[strict], cfg)
        tr_a = [x["r"] for x in tr if x["open_t"] < cutoff]
        tr_b = [x["r"] for x in tr if x["open_t"] >= cutoff]
        rows.append(((strict, trend, ctx, rr), metrics(tr_a), metrics(tr_b), metrics(tr_a + tr_b)))
    rows.sort(key=lambda r: (r[1]["avg"] if r[1]["n"] >= 15 else -9), reverse=True)
    crit = NormalDist().inv_cdf(1 - 0.025 / len(grid))
    print(f"\n{len(grid)} variantes · ordenadas por ENTRENAMIENTO (≥15 ops) · t crítico Bonferroni {crit:.2f}")
    print(f"{'exigencia':<12}{'EMA':<9}{'ctx':<9}{'RR':>4} | {'entren. n':>9} {'media':>7} | {'PRUEBA n':>8} {'media':>7} {'PF':>5} | {'total t':>7}")
    for (strict, trend, ctx, rr), a, b, t in rows[:20]:
        flag = " ✓" if t["t"] >= crit and b["avg"] > 0 else ""
        print(f"{strict:<12}{trend:<9}{ctx:<9}{rr:>4.1f} | {a['n']:>9} {a['avg']:+7.3f} | {b['n']:>8} {b['avg']:+7.3f} "
              f"{b['pf']:5.2f} | {t['t']:+7.2f}{flag}")
    best = rows[0]
    print(f"\nMejor en entrenamiento: {best[0]} → prueba {best[2]['n']} ops, media {best[2]['avg']:+.3f}R")
    if best[2]["avg"] <= 0:
        print("⚠ La mejor en entrenamiento NO aguanta en prueba: no hay variante que elegir con estos datos.")
    elif best[3]["t"] < crit:
        print(f"⚠ Aguanta en prueba pero t={best[3]['t']:.2f} < {crit:.2f}: compatible con azar tras {len(grid)} pruebas.")
    else:
        print("✓ Aguanta en prueba y supera Bonferroni. Aun así: confírmalo en SIGNAL antes de dinero real.")


if __name__ == "__main__":
    main()
