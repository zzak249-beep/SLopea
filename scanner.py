"""
QF×JP Bot v6.3.1 — Scanner
FIX TELEGRAM: en LIVE mode NO se llama notify_signal antes de abrir.
  Solo se notifica con notify_trade_opened tras apertura exitosa.
  Esto evita el ban 429 por spam (17+ señales × cada 60s).
"""
import asyncio
import logging
import time
from typing import Optional

import config as C
from bingx_client import BingXClient
from indicators import analyze, Signal
from risk_manager import RiskManager
from position_manager import PositionManager, OpenTrade
import telegram_client as tg

log = logging.getLogger("scanner")

_cb_blacklist: dict[str, float] = {}
CB_COOLDOWN = 600


async def _fetch_klines_all(client: BingXClient, symbol: str) -> tuple[list, list, list, list]:
    results = await asyncio.gather(
        client.get_klines(symbol, C.TIMEFRAME, 200),
        client.get_klines(symbol, C.HTF_TIMEFRAME, 100),
        client.get_klines(symbol, C.HTF2_TIMEFRAME, 100),
        client.get_klines(symbol, C.HTF5_TIMEFRAME, 100),
        return_exceptions=True,
    )
    def _safe(r, default):
        return r if isinstance(r, list) else default
    return (
        _safe(results[0], []),
        _safe(results[1], []),
        _safe(results[2], []),
        _safe(results[3], []),
    )


async def _process_symbol(
    symbol: str,
    client: BingXClient,
    risk: RiskManager,
    pos_mgr: PositionManager,
) -> Optional[Signal]:

    if pos_mgr.is_trading(symbol):
        return None

    now = time.time()
    if symbol in _cb_blacklist and now - _cb_blacklist[symbol] < CB_COOLDOWN:
        return None

    try:
        k3m, k15m, k1h, k4h = await _fetch_klines_all(client, symbol)
    except Exception as e:
        log.debug("[%s] fetch_klines error: %s", symbol, e)
        return None

    if len(k3m) < 60:
        return None

    try:
        sig = analyze(symbol, k3m, k15m, k1h, k4h)
    except Exception as e:
        log.warning("[%s] analyze error: %s", symbol, e)
        return None

    if sig.direction == "NONE":
        return None

    if sig.circuit_breaker:
        _cb_blacklist[symbol] = now
        await tg.notify_circuit_breaker(symbol)
        return None

    if not risk.tier_ok(sig.tier):
        return None

    log.info("[%s] Señal %s tier=%s score=%.1f", symbol, sig.direction, sig.tier, sig.score)

    # ── SIGNAL mode: notificar y salir ───────────────────────────────────────
    if C.MODE == "SIGNAL":
        await tg.notify_signal(sig)
        return sig

    # ── LIVE mode ─────────────────────────────────────────────────────────────
    can, reason = await risk.can_trade()
    if not can:
        log.info("[%s] Bloqueado por risk: %s", symbol, reason)
        return None

    try:
        balance = await client.get_balance()
    except Exception as e:
        log.error("[%s] get_balance error: %s", symbol, e)
        return None

    qty = risk.kelly_position_size(balance, sig.entry, sig.sl, sig.score, sig.tier)
    if qty <= 0:
        log.warning("[%s] qty=0, skip", symbol)
        return None

    # ── NO notify_signal aquí — evita spam Telegram ───────────────────────────
    # Solo se notifica si el trade abre con éxito (notify_trade_opened abajo)

    try:
        results = await client.open_trade(
            symbol=symbol,
            direction=sig.direction,
            quantity=qty,
            sl_price=sig.sl,
            tp1_price=sig.tp1,
            tp2_price=sig.tp2,
        )
    except Exception as e:
        log.error("[%s] open_trade error: %s", symbol, e)
        await tg.notify_error(f"open_trade({symbol})", str(e))
        return None

    entry_resp = results.get("entry", {})
    if entry_resp.get("code", -1) != 0:
        log.error("[%s] Entrada rechazada: %s", symbol, entry_resp)
        # NO notificar entradas rechazadas — evita spam de errores
        return None

    order_id = str(
        entry_resp.get("data", {}).get("order", {}).get("orderId", "unknown")
        or entry_resp.get("data", {}).get("orderId", "unknown")
    )

    trade = OpenTrade(
        symbol=symbol,
        direction=sig.direction,
        entry=sig.entry,
        sl=sig.sl,
        tp1=sig.tp1,
        tp2=sig.tp2,
        qty=qty,
        atr=sig.atr,
        order_id=order_id,
    )
    await pos_mgr.register_trade(trade)

    # ── Notificar SOLO tras apertura exitosa ──────────────────────────────────
    await tg.notify_trade_opened(sig, qty, order_id)

    return sig


async def scan_loop(
    client: BingXClient,
    risk: RiskManager,
    pos_mgr: PositionManager,
):
    log.info("Scanner iniciado. Modo: %s | Interval: %ds | TOP_N: %s",
             C.MODE, C.SCAN_INTERVAL,
             C.TOP_N_SYMBOLS if C.TOP_N_SYMBOLS > 0 else "TODAS")

    try:
        balance = await client.get_balance()
    except Exception:
        balance = 0.0
    symbols = []
    iteration = 0

    while True:
        start = time.time()
        iteration += 1

        # Refrescar símbolos cada 10 iteraciones
        if iteration == 1 or iteration % 10 == 0 or not symbols:
            try:
                new_symbols = await client.get_all_symbols()
                if new_symbols:
                    symbols = new_symbols
                    log.info("Símbolos activos: %d", len(symbols))
                else:
                    log.warning("get_all_symbols devolvió lista vacía (iter=%d)", iteration)
            except Exception as e:
                log.error("get_all_symbols error: %s", e)
                if not symbols:
                    await asyncio.sleep(30)
                    continue

        if not symbols:
            await asyncio.sleep(10)
            continue

        # Status periódico — cada 20 iteraciones (no cada iteración)
        if iteration % 20 == 0:
            try:
                balance = await client.get_balance()
                await tg.notify_status(risk.status(), balance, len(symbols))
            except Exception as e:
                log.warning("status notify error: %s", e)

        BATCH_SIZE   = 10
        signals_found = 0

        for i in range(0, len(symbols), BATCH_SIZE):
            batch   = symbols[i: i + BATCH_SIZE]
            tasks   = [_process_symbol(sym, client, risk, pos_mgr) for sym in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Signal) and r.direction != "NONE":
                    signals_found += 1
            await asyncio.sleep(0.5)

        elapsed = time.time() - start
        log.info("Iteración %d | %d símbolos | %d señales | %.1fs",
                 iteration, len(symbols), signals_found, elapsed)

        wait = max(0.0, C.SCAN_INTERVAL - elapsed)
        await asyncio.sleep(wait)
