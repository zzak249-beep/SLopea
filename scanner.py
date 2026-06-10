"""
QF×JP Bot v6.3 — Scanner
Loop principal:
  1. Obtiene TODOS los símbolos BingX (o los configurados)
  2. Descarga klines en paralelo (3m + HTF)
  3. Calcula señal con indicators.py
  4. Aplica filtros risk manager
  5. Abre trades en LIVE o notifica en SIGNAL
  6. Corre junto al position_manager loop

FIX v6.3.2: Balance se obtiene UNA vez por iteración
FIX v6.3.4: Notional check en scanner antes de open_trade.
  Consulta ticker para obtener mark_price real y calcular
  notional = qty × price. Aborta si < 5 USDT o > 500 USDT.
FIX v6.3.5: notify_signal movido ANTES de risk checks → Telegram
  siempre recibe señales aunque no se abra trade.
  notify_blocked() cuando risk.can_trade() rechaza.
FIX v6.3.6: Guard geometría de señal — descarta señales con ATR=0,
  TP=Entry, o SL/TP en lado incorrecto. Evita órdenes degeneradas
  en micro-caps como ASTEROIDETH-USDT.
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

# Notional limits para filtrar pares micro-cap y órdenes gigantes
MIN_NOTIONAL_USDT = 5.0
MAX_NOTIONAL_USDT = 500.0

# Símbolos actualmente en blacklist temporal (circuit breaker)
_cb_blacklist: dict[str, float] = {}   # symbol → timestamp de bloqueo
CB_COOLDOWN = 600  # 10 minutos tras circuit breaker


async def _fetch_klines_all(client: BingXClient, symbol: str) -> tuple[list, list, list, list]:
    """Descarga 4 TFs en paralelo. Retorna (3m, 15m, 1h, 4h)."""
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
    balance: float,
) -> Optional[Signal]:
    """Analiza un símbolo y ejecuta la acción correspondiente."""
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

    # ── FIX v6.3.6: Guard geometría — descarta señales degeneradas ───────────
    # ATR=0 → los TP quedan pegados al entry → orden inválida en BingX
    if sig.atr <= 0:
        log.warning("[%s] descartada: ATR=0 (par micro-cap o velas planas)", symbol)
        return None
    # TP igual o peor que entry → no tiene sentido geométrico
    if sig.direction == "LONG":
        if sig.tp1 <= sig.entry:
            log.warning("[%s] LONG descartada: tp1=%.8f <= entry=%.8f",
                        symbol, sig.tp1, sig.entry)
            return None
        if sig.sl >= sig.entry:
            log.warning("[%s] LONG descartada: sl=%.8f >= entry=%.8f",
                        symbol, sig.sl, sig.entry)
            return None
    else:  # SHORT
        if sig.tp1 >= sig.entry:
            log.warning("[%s] SHORT descartada: tp1=%.8f >= entry=%.8f",
                        symbol, sig.tp1, sig.entry)
            return None
        if sig.sl <= sig.entry:
            log.warning("[%s] SHORT descartada: sl=%.8f <= entry=%.8f",
                        symbol, sig.sl, sig.entry)
            return None

    # ── FIX v6.3.5: Notificar señal SIEMPRE (antes de cualquier filtro de trade) ──
    # En SIGNAL mode: notificar y salir.
    # En LIVE mode: notificar igualmente — así Telegram siempre recibe la señal
    # aunque el risk manager decida no abrir el trade.
    await tg.notify_signal(sig)

    if C.MODE == "SIGNAL":
        return sig

    # ── LIVE mode ─────────────────────────────────────────────────────────────
    can, reason = await risk.can_trade()
    if not can:
        log.info("[%s] Bloqueado por risk: %s", symbol, reason)
        await tg.notify_blocked(sig, reason)
        return None

    # ── Obtener mark_price real para validación de notional ──────────────────
    mark_price = sig.entry  # fallback: usar entry de la señal
    try:
        ticker = await client.get_ticker(symbol)
        mp = float(ticker.get("lastPrice") or ticker.get("markPrice") or 0)
        if mp > 0:
            mark_price = mp
    except Exception as e:
        log.debug("[%s] get_ticker error: %s — usando entry como precio", symbol, e)

    # ── Kelly sizing con mark_price real ─────────────────────────────────────
    qty = risk.kelly_position_size(
        balance, sig.entry, sig.sl, sig.score, sig.tier,
        mark_price=mark_price,
    )
    if qty <= 0:
        log.warning("[%s] qty=0 (balance=%.2f entry=%.6f sl=%.6f price=%.6f), skip",
                    symbol, balance, sig.entry, sig.sl, mark_price)
        return None

    # ── Validación de notional en scanner (red de seguridad extra) ────────────
    notional = qty * mark_price
    if notional < MIN_NOTIONAL_USDT:
        log.warning("[%s] notional=%.4f USDT < min=%.1f → skip (par micro-cap)",
                    symbol, notional, MIN_NOTIONAL_USDT)
        return None
    if notional > MAX_NOTIONAL_USDT:
        log.warning("[%s] notional=%.2f USDT > max=%.0f → skip (posición excesiva)",
                    symbol, notional, MAX_NOTIONAL_USDT)
        return None

    log.info("[%s] qty=%.6f notional=%.2f USDT (price=%.6f)",
             symbol, qty, notional, mark_price)

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
        await tg.notify_error(f"entrada_rechazada({symbol})", str(entry_resp))
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
    await tg.notify_trade_opened(sig, qty, order_id)

    return sig


async def _get_balance_safe(client: BingXClient) -> float:
    try:
        balance = await client.get_balance()
    except Exception as e:
        log.error("get_balance exception: %s — usando CAPITAL=%.2f", e, C.CAPITAL)
        return C.CAPITAL

    if balance <= 0:
        log.warning(
            "get_balance=0 — usando CAPITAL fallback=%.2f USDT", C.CAPITAL
        )
        return C.CAPITAL

    return balance


async def scan_loop(
    client: BingXClient,
    risk: RiskManager,
    pos_mgr: PositionManager,
):
    """Loop principal de escaneo. Corre indefinidamente."""
    log.info("Scanner iniciado. Modo: %s | Interval: %ds | TOP_N: %s",
             C.MODE, C.SCAN_INTERVAL,
             C.TOP_N_SYMBOLS if C.TOP_N_SYMBOLS > 0 else "TODAS")

    symbols = []
    iteration = 0

    while True:
        start = time.time()
        iteration += 1

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

        balance = await _get_balance_safe(client)
        log.info("Balance activo: %.4f USDT", balance)

        if iteration % 20 == 0:
            try:
                await tg.notify_status(risk.status(), balance, len(symbols))
            except Exception as e:
                log.warning("status notify error: %s", e)

        BATCH_SIZE = 10
        signals_found = 0

        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i : i + BATCH_SIZE]
            tasks = [
                _process_symbol(sym, client, risk, pos_mgr, balance)
                for sym in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Signal) and r.direction != "NONE":
                    signals_found += 1
            await asyncio.sleep(0.5)

        elapsed = time.time() - start
        log.info(
            "Iteración %d | %d símbolos | %d señales | %.1fs",
            iteration, len(symbols), signals_found, elapsed
        )

        wait = max(0.0, C.SCAN_INTERVAL - elapsed)
        await asyncio.sleep(wait)
