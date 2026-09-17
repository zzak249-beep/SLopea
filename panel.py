"""
panel.py — el diario TRANSVERSAL. Es la mejora que más cambia el bot, y no
toca ni una regla de entrada.

═══════════════════════════════════════════════════════════════════════
EL PROBLEMA QUE RESUELVE
═══════════════════════════════════════════════════════════════════════
Ahora mismo el bot produce ~5 observaciones al día: las operaciones que
cruzan las tres puertas. En 15 días son ~80, y con desviación típica de
1,09 R eso da una potencia del 23% para detectar +0,15 R/op. Traducido: si
la ventaja es real pero moderada, el experimento la declara "no concluyente"
tres de cada cuatro veces. No es un problema de estrategia, es de diseño de
medición.

Pero el bot YA mira 300 símbolos cada ciclo y YA calcula basis_z y oi_z para
todos. De esos 300 cálculos tira 299 y se queda con el que dispara. Este
módulo apunta los 300.

    ahora:    ~5 filas/día,  binarias (±R)
    con esto: ~28.800 filas/día, continuas (retorno futuro)

═══════════════════════════════════════════════════════════════════════
Y LO QUE MIDE ES OTRA COSA, MEJOR
═══════════════════════════════════════════════════════════════════════
Tu z-score es ABSOLUTO: se compara el basis de un símbolo contra su propia
historia. Eso tiene dos consecuencias feas:

 1. El número de señales depende de la volatilidad general del mercado, así
    que se amontonan en los días de desplome. Por eso tu informe dice que un
    día se lleva todo el resultado.
 2. Lo que mide es en buena parte el factor común de las alts. Cuando BTC
    cae, TODOS los basis se hunden a la vez. Estás midiendo beta, no señal.

Este módulo apunta además el RANGO TRANSVERSAL: en cada instantánea ordena
los 300 símbolos por basis_z y guarda el percentil y el decil. Con eso se
puede medir la cartera larga-corta (decil 1 contra decil 10), que es neutral
al mercado POR CONSTRUCCIÓN. El factor común se cancela en la resta. Si el
crowding tiene señal propia, ahí aparece limpia; si lo único que tenías era
beta corta en días rojos, ahí desaparece. Es exactamente la pregunta que no
puedes contestar con 80 operaciones.

═══════════════════════════════════════════════════════════════════════
COSTE
═══════════════════════════════════════════════════════════════════════
Una instantánea cada PANEL_CADA_SEG (900 por defecto = una por vela de 15m,
no una por ciclo: más no añade información, las velas no se han movido).
300 símbolos x 96 al día x 15 días = 432.000 filas, ~40 MB en el volumen.
Cero llamadas extra a la API: todo sale de lo que el ciclo ya calculó.

NO decide nada. No filtra, no veta, no abre. Solo escribe.
"""
from __future__ import annotations

import csv
import logging
import os
import time
from typing import Any, Sequence

log = logging.getLogger("panel")

DEFAULTS = {
    "PANEL_ENABLED": True,
    "PANEL_CSV": "/data/crowding_panel.csv",
    "PANEL_CADA_SEG": 900.0,
    "PANEL_MIN_SIMBOLOS": 30,   # por debajo de esto el rango transversal no dice nada
}

COLS = ["ts", "symbol", "px", "basis_z", "oi_z", "pct_precio", "atr_pct",
        "funding", "coste_r", "rank_basis", "decil_basis", "rank_oi", "n_universo"]

_ultima = 0.0


def cfg(config: Any, key: str):
    return getattr(config, key, DEFAULTS[key])


def toca(config: Any) -> bool:
    """¿Toca instantánea? Se consulta antes de montar las filas, para no
    gastar trabajo en un ciclo que no va a escribir."""
    if not cfg(config, "PANEL_ENABLED"):
        return False
    return (time.time() - _ultima) >= float(cfg(config, "PANEL_CADA_SEG"))


def _percentiles(valores: Sequence[float]) -> list[float]:
    """Percentil transversal de cada valor dentro de su propia instantánea."""
    n = len(valores)
    orden = sorted(range(n), key=lambda i: valores[i])
    pct = [0.0] * n
    for puesto, i in enumerate(orden):
        pct[i] = puesto / (n - 1) * 100.0 if n > 1 else 50.0
    return pct


def registrar(config: Any, filas: list[dict]) -> int:
    """
    filas: un dict por símbolo evaluado, con al menos
        symbol, px, basis_z, oi_z, pct_precio, atr_pct, funding, coste_r

    Devuelve cuántas filas escribió. Nunca lanza: si falla, el bot sigue.
    """
    global _ultima
    try:
        if not cfg(config, "PANEL_ENABLED") or not filas:
            return 0
        if len(filas) < int(cfg(config, "PANEL_MIN_SIMBOLOS")):
            return 0

        ts = int(time.time())
        n = len(filas)
        pb = _percentiles([f["basis_z"] for f in filas])
        po = _percentiles([f["oi_z"] for f in filas])

        ruta = cfg(config, "PANEL_CSV")
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        nuevo = not os.path.exists(ruta) or os.path.getsize(ruta) == 0
        with open(ruta, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
            if nuevo:
                w.writeheader()
            for i, f in enumerate(filas):
                w.writerow({
                    "ts": ts,
                    "symbol": f["symbol"],
                    "px": f"{f['px']:.10g}",
                    "basis_z": round(f["basis_z"], 4),
                    "oi_z": round(f["oi_z"], 4),
                    "pct_precio": round(f["pct_precio"], 2),
                    "atr_pct": round(f["atr_pct"], 4),
                    "funding": round(f["funding"], 6),
                    "coste_r": round(f["coste_r"], 4),
                    "rank_basis": round(pb[i], 2),
                    # Decil 0 = basis más negativo (cortos amontonados),
                    # decil 9 = basis más positivo (largos amontonados).
                    "decil_basis": min(9, int(pb[i] / 10.0)),
                    "rank_oi": round(po[i], 2),
                    "n_universo": n,
                })
        _ultima = ts
        return n
    except Exception:
        log.exception("panel.registrar falló")
        return 0
