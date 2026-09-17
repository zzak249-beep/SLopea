"""
analizar_panel.py — lee el panel y contesta si el crowding tiene señal.

Se ejecuta a mano cuando quieras:  python analizar_panel.py /data/crowding_panel.csv

No necesita ninguna operación cerrada. Trabaja con el retorno futuro de TODOS
los símbolos, no solo de los que dispararon, así que a los dos días ya tiene
más información que las 80 operaciones de los 15.

═══════════════════════════════════════════════════════════════════════
LAS TRES COSAS QUE CALCULA Y POR QUÉ
═══════════════════════════════════════════════════════════════════════
1. IC (coeficiente de información): correlación de rangos entre basis_z y el
   retorno futuro, calculada DENTRO de cada instantánea. Al hacerlo dentro,
   el movimiento común del mercado se cancela: un día en que todo cae no
   mueve la correlación, porque todos los símbolos caen. Es la medida limpia
   de "¿ordena bien?". Un IC medio de 0,02-0,05 sostenido ya es una señal
   explotable; los fondos cuantitativos trabajan ahí.

2. Cartera larga-corta por deciles: comprar el decil de basis más bajo y
   vender el más alto, en cada instantánea. Neutral al mercado por resta.
   Esto es lo que tu bot querría capturar si la hipótesis del amontonamiento
   es cierta.

3. Error típico AGRUPADO POR DÍA. El t ingenuo trata cada fila como
   independiente y con correlación intradía de 0,4 da 24% de falsos
   positivos en vez del 5%. Medido por simulación:

       correlación intradía   falsos positivos del t ingenuo
              0,0                        5,3%
              0,2                       15,1%
              0,4                       23,7%
              0,6                       29,7%

   Agrupar por día trata cada día como UNA observación. Pierdes potencia,
   pero el número que sale significa lo que dice que significa.

═══════════════════════════════════════════════════════════════════════
ADEMÁS: EL AVISO DE MIRAR TODOS LOS DÍAS
═══════════════════════════════════════════════════════════════════════
Mirar el t cada día durante 15 días y parar cuando pase de 2 declara
"significativo" el 30,4% de las veces aunque la ventaja real sea CERO
(simulación de 20.000 repeticiones, 5 ops/día, sd 1,09R). Mirando solo al
final serían el 5%.

Por eso este script imprime siempre el número de veces que lo has
ejecutado sobre el mismo fichero y ajusta el umbral. Si vas a mirar a
diario, el umbral honesto no es 2.
"""
from __future__ import annotations

import csv
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

HORIZONTES = [1, 4, 16]          # en instantáneas: 15m, 1h, 4h con PANEL_CADA_SEG=900


def cargar(ruta: str):
    snaps: dict[int, dict[str, dict]] = defaultdict(dict)
    with open(ruta, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                snaps[int(r["ts"])][r["symbol"]] = {
                    "px": float(r["px"]),
                    "basis_z": float(r["basis_z"]),
                    "oi_z": float(r["oi_z"]),
                    "decil": int(r["decil_basis"]),
                    "rank": float(r["rank_basis"]),
                }
            except (ValueError, KeyError):
                continue
    return [snaps[t] for t in sorted(snaps)], sorted(snaps)


def spearman(a: list[float], b: list[float]) -> float:
    n = len(a)
    if n < 10:
        return 0.0

    def rangos(v):
        o = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:                      # empates promediados
            j = i
            while j + 1 < n and v[o[j + 1]] == v[o[i]]:
                j += 1
            medio = (i + j) / 2.0
            for k in range(i, j + 1):
                r[o[k]] = medio
            i = j + 1
        return r

    ra, rb = rangos(a), rangos(b)
    ma, mb = st.fmean(ra), st.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def t_agrupado(valores: list[float], claves: list[str]) -> tuple[float, float, int]:
    """Media, t agrupando por clave (día) y número de grupos."""
    g: dict[str, list[float]] = defaultdict(list)
    for v, k in zip(valores, claves):
        g[k].append(v)
    medias = [st.fmean(v) for v in g.values()]
    n = len(medias)
    if n < 2:
        return (st.fmean(valores) if valores else 0.0), 0.0, n
    m = st.fmean(medias)
    s = st.stdev(medias)
    return m, (m * math.sqrt(n) / s if s > 1e-12 else 0.0), n


def main(ruta: str):
    if not os.path.exists(ruta):
        print(f"No existe {ruta}")
        return
    snaps, ts = cargar(ruta)
    print(f"Panel: {len(snaps)} instantáneas · "
          f"{sum(len(s) for s in snaps)} filas · "
          f"{(ts[-1] - ts[0]) / 86400:.1f} días" if len(ts) > 1 else "Panel vacío")
    if len(snaps) <= max(HORIZONTES):
        print("Todavía no hay instantáneas suficientes para mirar hacia delante.")
        return

    dia = [__import__("datetime").datetime.utcfromtimestamp(t).strftime("%Y-%m-%d") for t in ts]

    for h in HORIZONTES:
        ics, ls, claves = [], [], []
        for i in range(len(snaps) - h):
            ahora, luego = snaps[i], snaps[i + h]
            comunes = [s for s in ahora if s in luego and ahora[s]["px"] > 0]
            if len(comunes) < 30:
                continue
            fwd = [luego[s]["px"] / ahora[s]["px"] - 1.0 for s in comunes]
            # Neutralizar el movimiento común: restar la mediana transversal.
            med = st.median(fwd)
            fwd = [x - med for x in fwd]
            bz = [ahora[s]["basis_z"] for s in comunes]

            # La hipótesis del crowding dice: basis alto -> caída futura.
            # Un IC NEGATIVO es la señal a favor. Se invierte el signo para
            # que "positivo = la hipótesis funciona" y no haya que pensarlo.
            ics.append(-spearman(bz, fwd))

            bajo = [fwd[k] for k, s in enumerate(comunes) if ahora[s]["decil"] == 0]
            alto = [fwd[k] for k, s in enumerate(comunes) if ahora[s]["decil"] == 9]
            if bajo and alto:
                ls.append(st.fmean(bajo) - st.fmean(alto))
                claves.append(dia[i])

        if not ics:
            continue
        mic, tic, ndias = t_agrupado(ics, claves[:len(ics)] or dia[:len(ics)])
        print(f"\n── horizonte {h} instantáneas ({h * 15} min) ──")
        print(f"IC medio: {mic:+.4f} · t agrupado por día {tic:+.2f} (días={ndias}, n={len(ics)})")
        if ls:
            mls, tls, _ = t_agrupado(ls, claves)
            anual = mls * (96 * 365 / h) * 100
            print(f"Larga-corta decil 0 vs 9: {mls * 100:+.4f}% por periodo · "
                  f"t agrupado {tls:+.2f}")
            print(f"  (sin costes eso serían {anual:+.0f}%/año — el coste de ida y vuelta "
                  f"ronda el 0,25% por operación, así que compáralo con {mls * 100:+.4f}%)")

    # Contador de miradas: cada vistazo es una oportunidad de engañarse.
    reg = ruta + ".miradas.json"
    try:
        n = json.load(open(reg)).get("n", 0) + 1 if os.path.exists(reg) else 1
        json.dump({"n": n}, open(reg, "w"))
    except Exception:
        n = 1
    umbral = 1.96 + 0.35 * math.log(max(n, 1))     # penalización aproximada por mirar
    print(f"\nEsta es la mirada nº {n} sobre este panel. Umbral honesto de |t| "
          f"para esta mirada: ~{umbral:.2f}, no 1,96.")
    print("Mirar cada día durante 15 días declara significativo el 30% de las series "
          "sin ninguna ventaja real.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/data/crowding_panel.csv")
