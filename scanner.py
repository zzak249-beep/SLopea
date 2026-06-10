"""
QF×JP Bot v6.4 — Scanner
Loop principal:
  1. Obtiene TODOS los símbolos BingX (o los configurados)
  2. Descarga klines en paralelo (3m + HTF)
  3. Calcula señal con indicators.py
  4. Aplica filtros risk manager
  5. Abre trades en LIVE o notifica en SIGNAL
  6. Corre junto al position_manager loop

FIX v6.4.1: Eliminado `await risk.on_trade_opened()` duplicado.
  register_trade() ya lo llama internamente → se contaba 2× por trade.
  Efecto: daily_trades y open_count se incrementaban el doble de lo real,
  bloqueando trades legítimos al alcanzar MAX_DAILY_TRADES o MAX_OPEN_TRADES
  prematuramente.

FIX v6.4.2: Validación de notional usa risk.notional_ok() en lugar de
  hardcoded MAX_NOTIONAL_USDT=500. Ahora confía en el qty ya clampeado por
  kelly_position_size (v6.4) y solo rechaza dust trades.

FIX v6.4.3: risk.update_balance(balance) llamado en cada iteración para
  que daily_loss_limit se recalcule con capital real.

NEW v6.4.4: TOP_N_SYMBOLS respetado — slice de símbolos por volumen.

NEW v6.4.5: Shuffle de símbolos por iteración — evita sesgo hacia los
  primeros pares del ranking en cada scan.

NEW v6.4.6: Per-symbol cooldown configurable (SYMBOL_COOLDOWN_MINUTES).
  Evita re-escanear el mismo par demasiado pronto aunque no haya circuit breaker.

NEW v6.4.7: Batch sleep adaptativo — reduce latencia entre batches cuando
  hay señales activas.
"""
import asyncio
import logging
import random
import time
from typing import Optional

import config as C
from bingx_client import BingXClient
from indicators import analyze, Signal
from risk_manager import RiskManager
from position_manager import PositionManager, OpenTrade
import telegram_client as tg

log = logging.getLogger("scanner")

# ── Cooldowns ────────────────────────────────────────────────────────────────
_cb_blacklist:    dict[str, float] = {}   # symbol → ts circuit breaker
_sym_cooldown:    dict[str, float] = {}   # symbol → ts último trade abierto

CB_COOLDOWN      = 600                             # 10 min tras circuit breaker
SYM_COOLDOWN_MIN = getattr(C, "SYMBOL_COOLDOWN_MINUTES", 30)  # minutos sin re-tradear el par
SYM_COOLDOWN_SEC = SYM_COOLDOWN_MIN * 60


async def _fetch_klines_all(client: BingXClient, symbol: str) -> tuple[list, list, list, list]:
    """Descarga 4 TFs en paralelo. Retorna (3m, 15m, 1h, 4h)."""
    results = await asyncio.gather(
        client.get_klines(symbol, C.TIMEFRAME,      200),
        client.get_klines(symbol, C.HTF_TIMEFRAME,  100),
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
    symbol:  str,
    client:  BingXClient,
    risk:    RiskManager,
    pos_mgr: PositionManager,
    balance: float,
) -> Optional[Signal]:
    """Analiza un símbolo y ejecuta la acción correspondiente."""
    now = time.time()

    # ── Skip guards ───────────────────────────────────────────────────────────
    if pos_mgr.is_trading(symbol):
        return None
    if symbol in _cb_blacklist and now - _cb_blacklist[symbol] < CB_COOLDOWN:
        return None
    if symbol in _sym_cooldown and now - _sym_cooldown[symbol] < SYM_COOLDOWN_SEC:
        return None

    # ── Klines ────────────────────────────────────────────────────────────────
    try:
        k3m, k15m, k1h, k4h = await _fetch_klines_all(client, symbol)
    except Exception as e:
        log.debug("[%s] fetch_klines error: %s", symbol, e)
        return None

    if len(k3m) < 60:
        return None

    # ── Análisis ──────────────────────────────────────────────────────────────
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

    # ── Guard geometría — descarta señales degeneradas ────────────────────────
    if sig.atr <= 0:
        log.warning("[%s] descartada: ATR=0 (par micro-cap o velas planas)", symbol)
        return None

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

    # ── SIGNAL mode: notificar y salir ────────────────────────────────────────
    if C.MODE == "SIGNAL":
        await tg.notify_signal(sig)
        return sig

    # ── LIVE mode ─────────────────────────────────────────────────────────────

    # Risk global (daily limits, open count, drawdown)
    can, reason = await risk.can_trade()
    if not can:
        log.info("[%s] Bloqueado por risk: %s", symbol, reason)
        return None

    # Mark price real para sizing y validación de notional
    mark_price = sig.entry
    try:
        ticker = await client.get_ticker(symbol)
        mp = float(ticker.get("lastPrice") or ticker.get("markPrice") or 0)
        if mp > 0:
            mark_price = mp
    except Exception as e:
        log.debug("[%s] get_ticker error: %s — usando entry como precio", symbol, e)

    # Kelly sizing (ya clampeado a MAX_NOTIONAL internamente en v6.4)
    qty = risk.kelly_position_size(
        balance, sig.entry, sig.sl, sig.score, sig.tier,
    )
    if qty <= 0:
        log.warning("[%s] qty=0 tras sizing (balance=%.2f entry=%.8f sl=%.8f)",
                    symbol, balance, sig.entry, sig.sl)
        return None

    # Validación de notional (usa helper del risk manager — sin spam de Telegram)
    ok, reason_n = risk.notional_ok(qty, mark_price)
    if not ok:
        log.warning("[%s] notional check failed: %s", symbol, reason_n)
        return None

    notional = qty * mark_price
    log.info("[%s] qty=%.6f notional=%.2f USDT (price=%.8f)",
             symbol, qty, notional, mark_price)

    # Notificar señal SOLO cuando el trade va a abrirse (evita flood 429)
    await tg.notify_signal(sig)

    # ── Abrir trade ───────────────────────────────────────────────────────────
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

    # register_trade llama a risk.on_trade_opened() internamente
    # NO llamar de nuevo aquí → evita doble conteo de daily_trades y open_count
    await pos_mgr.register_trade(trade)
    await tg.notify_trade_opened(sig, qty, order_id)

    # Cooldown por símbolo tras trade abierto
    _sym_cooldown[symbol] = time.time()

    return sig


async def _get_balance_safe(client: BingXClient) -> float:
    try:
        balance = await client.get_balance()
    except Exception as e:
        log.error("get_balance exception: %s — usando CAPITAL=%.2f", e, C.CAPITAL)
        return C.CAPITAL

    if balance <= 0:
        log.warning("get_balance=0 — usando CAPITAL fallback=%.2f USDT", C.CAPITAL)
        return C.CAPITAL

    return balance


async def scan_loop(
    client:  BingXClient,
    risk:    RiskManager,
    pos_mgr: PositionManager,
):
    """Loop principal de escaneo. Corre indefinidamente."""
    log.info(
        "Scanner iniciado. Modo: %s | Interval: %ds | TOP_N: %s | Cooldown: %dmin",
        C.MODE, C.SCAN_INTERVAL,
        C.TOP_N_SYMBOLS if C.TOP_N_SYMBOLS > 0 else "TODAS",
        SYM_COOLDOWN_MIN,
    )

    symbols: list[str] = []
    iteration = 0

    while True:
        start     = time.time()
        iteration += 1

        # ── Refrescar lista de símbolos cada 10 iteraciones ───────────────────
        if iteration == 1 or iteration % 10 == 0 or not symbols:
            try:
                new_symbols = await client.get_all_symbols()
                if new_symbols:
                    # TOP_N_SYMBOLS: respetar límite de pares activos
                    if C.TOP_N_SYMBOLS > 0:
                        new_symbols = new_symbols[: C.TOP_N_SYMBOLS]
                    symbols = new_symbols
                    log.info("Símbolos activos: %d", len(symbols))
                else:
                    log.warning("get_all_symbols vacío (iter=%d)", iteration)
            except Exception as e:
                log.error("get_all_symbols error: %s", e)
                if not symbols:
                    await asyncio.sleep(30)
                    continue

        if not symbols:
            await asyncio.sleep(10)
            continue

        # ── Balance ───────────────────────────────────────────────────────────
        balance = await _get_balance_safe(client)
        if balance <= 0:
            balance = max(C.CAPITAL, 10.0)
            log.warning("Balance=0 → forzado CAPITAL=%.2f USDT", balance)

        # Actualizar risk manager con balance real (para daily_loss_limit dinámico)
        risk.update_balance(balance)
        log.info("Balance activo: %.4f USDT", balance)

        # ── Status periódico ──────────────────────────────────────────────────
        if iteration % 20 == 0:
            try:
                await tg.notify_status(risk.status(), balance, len(symbols))
            except Exception as e:
                log.warning("status notify error: %s", e)

        # ── Shuffle de símbolos para evitar sesgo hacia primeros pares ────────
        shuffled = symbols.copy()
        random.shuffle(shuffled)

        BATCH_SIZE     = 10
        signals_found  = 0

        for i in range(0, len(shuffled), BATCH_SIZE):
            batch = shuffled[i : i + BATCH_SIZE]
            tasks = [
                _process_symbol(sym, client, risk, pos_mgr, balance)
                for sym in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    log.error("_process_symbol excepción: %s", r)
                elif isinstance(r, Signal) and r.direction != "NONE":
                    signals_found += 1

            # Sleep adaptativo: más corto si hay señales activas
            batch_sleep = 0.2 if signals_found > 0 else 0.5
            await asyncio.sleep(batch_sleep)

        elapsed = time.time() - start
        log.info(
            "Iteración %d | %d símbolos | %d señales | %.1fs",
            iteration, len(shuffled), signals_found, elapsed,
        )

        wait = max(0.0, C.SCAN_INTERVAL - elapsed)
        await asyncio.sleep(wait)
