"""
QF×JP Bot v6.3.1 — Position Manager
MEJORAS vs v6.3:
  [TRAIL] Trailing stop real después de breakeven (TRAIL_ATR_MULT × ATR)
  [TP1]   Cierre parcial activo al llegar a TP1 + recoloca SL y TP2
  [HOLD]  Cierre automático por tiempo máximo (MAX_HOLD_MINUTES)
  [QTY]   Sincronización con qty real de BingX en cada operación
  [NOTP]  BE ya NO cancela TP1/TP2 — solo cancela órdenes STOP
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import config as C
from bingx_client import BingXClient
from risk_manager import RiskManager
import telegram_client as tg

log = logging.getLogger("position_mgr")


@dataclass
class OpenTrade:
    symbol:    str
    direction: str        # LONG | SHORT
    entry:     float
    sl:        float
    tp1:       float
    tp2:       float
    qty:       float
    atr:       float
    order_id:  str
    be_moved:      bool  = False
    tp1_hit:       bool  = False
    current_sl:    float = 0.0   # SL vigente (se actualiza con trailing)
    best_price:    float = 0.0   # precio más favorable visto
    open_time:     float = 0.0   # timestamp de apertura (Unix)
    qty_remaining: float = 0.0   # qty tras cierre parcial en TP1

    def __post_init__(self):
        if self.current_sl == 0.0:
            self.current_sl = self.sl
        if self.best_price == 0.0:
            self.best_price = self.entry
        if self.qty_remaining == 0.0:
            self.qty_remaining = self.qty
        if self.open_time == 0.0:
            self.open_time = time.time()


class PositionManager:
    def __init__(self, client: BingXClient, risk: RiskManager):
        self.client = client
        self.risk   = risk
        self._trades: dict[str, OpenTrade] = {}
        self._lock = asyncio.Lock()

    async def register_trade(self, trade: OpenTrade):
        async with self._lock:
            self._trades[trade.symbol] = trade
        await self.risk.on_trade_opened()
        log.info("[%s] Trade registrado %s entry=%.6f qty=%.6f",
                 trade.symbol, trade.direction, trade.entry, trade.qty)

    async def remove_trade(self, symbol: str, pnl: float = 0.0):
        async with self._lock:
            self._trades.pop(symbol, None)
        await self.risk.on_trade_closed(pnl)

    # ── Loop principal ────────────────────────────────────────────────────────

    async def monitor_loop(self):
        log.info("Position monitor iniciado (intervalo=%ds)", C.POSITION_CHECK_INTERVAL)
        while True:
            try:
                await self._check_all_positions()
            except Exception as e:
                log.error("monitor_loop error: %s", e)
                await tg.notify_error("position_monitor", str(e))
            await asyncio.sleep(C.POSITION_CHECK_INTERVAL)

    # ── Check central ─────────────────────────────────────────────────────────

    async def _check_all_positions(self):
        try:
            real_positions = await self.client.get_open_positions()
        except Exception as e:
            log.warning("get_open_positions failed: %s", e)
            return

        real_map: dict[str, dict] = {
            p["symbol"]: p for p in real_positions if p.get("symbol")
        }
        await self.risk.update_open_count(len(real_map))

        async with self._lock:
            tracked = dict(self._trades)

        for symbol, trade in tracked.items():

            # ── Posición ya no existe en BingX (SL/TP auto) ──────────────────
            if symbol not in real_map:
                try:
                    ticker = await self.client.get_ticker(symbol)
                    close_price = float(ticker.get("lastPrice", trade.entry))
                except Exception:
                    close_price = trade.entry

                pnl = self._calc_pnl_qty(trade, close_price, trade.qty_remaining)
                log.info("[%s] Cerrada externamente. PnL≈%.4f USDT", symbol, pnl)
                await tg.notify_trade_closed(
                    symbol, trade.direction, trade.entry, close_price,
                    trade.qty_remaining, "sl_tp_auto", pnl
                )
                await self.remove_trade(symbol, pnl)
                continue

            # ── Precio actual ─────────────────────────────────────────────────
            pos = real_map[symbol]
            try:
                mark_price = float(pos.get("markPrice", 0) or 0)
                if mark_price == 0:
                    ticker = await self.client.get_ticker(symbol)
                    mark_price = float(ticker.get("lastPrice", trade.entry))
            except Exception:
                continue
            if mark_price <= 0:
                continue

            # [HOLD] Cierre por tiempo máximo ─────────────────────────────────
            if C.MAX_HOLD_MINUTES > 0:
                age_min = (time.time() - trade.open_time) / 60.0
                if age_min >= C.MAX_HOLD_MINUTES:
                    log.info("[%s] Max hold alcanzado (%.0fm) → cierre", symbol, age_min)
                    await self.close_position_emergency(symbol, reason="max_hold_time")
                    continue

            # [TRAIL] Actualizar mejor precio visto ───────────────────────────
            if trade.direction == "LONG":
                if mark_price > trade.best_price:
                    trade.best_price = mark_price
            else:
                if mark_price < trade.best_price:
                    trade.best_price = mark_price

            # [TP1] Cierre parcial activo — PRIMERO (antes que BE) ────────────
            if not trade.tp1_hit:
                tp1_hit = (
                    (trade.direction == "LONG"  and mark_price >= trade.tp1) or
                    (trade.direction == "SHORT" and mark_price <= trade.tp1)
                )
                if tp1_hit:
                    await self._handle_tp1(trade, mark_price, pos)
                    continue  # re-evaluar en próximo ciclo

            # [BE] Mover SL a breakeven ───────────────────────────────────────
            if not trade.be_moved:
                be_trigger = (
                    trade.entry + trade.atr * C.BREAKEVEN_ATR_MULT
                    if trade.direction == "LONG"
                    else trade.entry - trade.atr * C.BREAKEVEN_ATR_MULT
                )
                be_reached = (
                    (trade.direction == "LONG"  and mark_price >= be_trigger) or
                    (trade.direction == "SHORT" and mark_price <= be_trigger)
                )
                if be_reached:
                    await self._move_to_breakeven(trade, mark_price)

            # [TRAIL] Trailing stop (solo tras BE) ────────────────────────────
            if trade.be_moved and C.TRAIL_ATR_MULT > 0:
                await self._update_trailing_stop(trade, mark_price)

    # ── [TP1] Cierre parcial + recolocar SL en BE + TP2 ──────────────────────

    async def _handle_tp1(self, trade: OpenTrade, mark_price: float, pos: dict):
        """
        Al llegar a TP1:
          1. Cancelar STOP (SL) y TP1 pendientes
          2. Cerrar 50% de la posición a mercado
          3. Colocar SL en breakeven para el 50% restante
          4. Recolocar TP2 para el 50% restante
        """
        trade.tp1_hit = True
        actual_qty = abs(float(pos.get("positionAmt", trade.qty)))
        if actual_qty <= 0:
            log.warning("[%s] TP1 hit pero positionAmt=0", trade.symbol)
            return

        qty_close = round(actual_qty / 2, 6)
        qty_remain = round(actual_qty - qty_close, 6)
        trade.qty_remaining = qty_remain

        side_close = "SELL" if trade.direction == "LONG" else "BUY"

        # 1. Cancelar todas las órdenes pendientes
        await self.client.cancel_all_orders(trade.symbol)
        await asyncio.sleep(0.3)

        # 2. Cerrar 50% a mercado
        try:
            resp = await self.client.place_market_order(
                trade.symbol, side_close, qty_close, trade.direction
            )
            if resp.get("code", -1) == 0:
                pnl_partial = self._calc_pnl_qty(trade, mark_price, qty_close)
                log.info("[%s] TP1 parcial %.6f @ %.6f PnL≈%.4f USDT",
                         trade.symbol, qty_close, mark_price, pnl_partial)
                await tg.notify_trade_closed(
                    trade.symbol, trade.direction, trade.entry,
                    mark_price, qty_close, "tp1_partial", pnl_partial
                )
            else:
                log.warning("[%s] Fallo cierre parcial TP1: %s", trade.symbol, resp)
                qty_remain = actual_qty  # no se cerró nada
                trade.qty_remaining = qty_remain
        except Exception as e:
            log.error("[%s] _handle_tp1 close error: %s", trade.symbol, e)
            qty_remain = actual_qty
            trade.qty_remaining = qty_remain

        await asyncio.sleep(0.3)

        # 3. SL en breakeven para el restante
        resp_sl = await self.client.place_stop_market_order(
            trade.symbol, side_close, qty_remain, trade.entry,
            trade.direction, close_position=True, order_type="STOP_MARKET"
        )
        if resp_sl.get("code", -1) == 0:
            trade.be_moved = True
            trade.current_sl = trade.entry
            log.info("[%s] SL→BE tras TP1 @ %.6f", trade.symbol, trade.entry)
        else:
            log.warning("[%s] Fallo SL BE tras TP1: %s", trade.symbol, resp_sl)

        await asyncio.sleep(0.3)

        # 4. TP2 para el restante
        resp_tp2 = await self.client.place_stop_market_order(
            trade.symbol, side_close, qty_remain, trade.tp2,
            trade.direction, close_position=False, order_type="TAKE_PROFIT_MARKET"
        )
        if resp_tp2.get("code", -1) == 0:
            log.info("[%s] TP2 recolocado @ %.6f para %.6f u",
                     trade.symbol, trade.tp2, qty_remain)
        else:
            log.warning("[%s] Fallo TP2 recolocado: %s", trade.symbol, resp_tp2)

    # ── [BE] Mover a breakeven (sin cancelar TP) ──────────────────────────────

    async def _move_to_breakeven(self, trade: OpenTrade, current_price: float):
        """
        Cancela SOLO las órdenes STOP (no TP) y coloca nuevo SL en entry.
        """
        try:
            # Cancelar solo STOP_MARKET (preservar TAKE_PROFIT_MARKET)
            open_orders = await self.client.get_open_orders(trade.symbol)
            for order in open_orders:
                if order.get("type") in ("STOP_MARKET", "STOP"):
                    oid = str(order.get("orderId", ""))
                    if oid:
                        await self.client.cancel_order(trade.symbol, oid)

            await asyncio.sleep(0.3)

            side_close = "SELL" if trade.direction == "LONG" else "BUY"
            qty_sl = trade.qty_remaining if trade.qty_remaining > 0 else trade.qty

            resp = await self.client.place_stop_market_order(
                trade.symbol, side_close, qty_sl, trade.entry,
                trade.direction, close_position=True, order_type="STOP_MARKET"
            )
            if resp.get("code", -1) == 0:
                trade.be_moved    = True
                trade.current_sl  = trade.entry
                log.info("[%s] SL→BE @ %.6f", trade.symbol, trade.entry)
                await tg.send_message(
                    f"🔒 <b>{trade.symbol}</b> SL movido a BE @ {trade.entry:.6f}"
                )
            else:
                log.warning("[%s] Fallo BE: %s", trade.symbol, resp)
        except Exception as e:
            log.error("[%s] _move_to_breakeven error: %s", trade.symbol, e)

    # ── [TRAIL] Trailing stop ─────────────────────────────────────────────────

    async def _update_trailing_stop(self, trade: OpenTrade, mark_price: float):
        """
        Trail el SL detrás del mejor precio alcanzado.
        Solo mueve el SL si la mejora supera 0.25 × ATR (evita ruido).
        """
        trail_dist = trade.atr * C.TRAIL_ATR_MULT
        threshold  = trade.atr * 0.25  # mínimo movimiento para actualizar

        if trade.direction == "LONG":
            new_sl = trade.best_price - trail_dist
            should_move = new_sl > trade.current_sl + threshold
        else:
            new_sl = trade.best_price + trail_dist
            should_move = new_sl < trade.current_sl - threshold

        if not should_move:
            return

        try:
            # Obtener qty real
            positions = await self.client.get_open_positions()
            actual_qty = trade.qty_remaining if trade.qty_remaining > 0 else trade.qty
            for p in positions:
                if p.get("symbol") == trade.symbol:
                    q = abs(float(p.get("positionAmt", 0)))
                    if q > 0:
                        actual_qty = q
                    break
            if actual_qty <= 0:
                return

            # Cancelar solo STOP actuales
            open_orders = await self.client.get_open_orders(trade.symbol)
            for order in open_orders:
                if order.get("type") in ("STOP_MARKET", "STOP"):
                    oid = str(order.get("orderId", ""))
                    if oid:
                        await self.client.cancel_order(trade.symbol, oid)

            await asyncio.sleep(0.2)

            side_close = "SELL" if trade.direction == "LONG" else "BUY"
            resp = await self.client.place_stop_market_order(
                trade.symbol, side_close, actual_qty, new_sl,
                trade.direction, close_position=True, order_type="STOP_MARKET"
            )
            if resp.get("code", -1) == 0:
                old_sl = trade.current_sl
                trade.current_sl = new_sl
                log.info("[%s] 📈 Trailing SL: %.6f → %.6f (best=%.6f)",
                         trade.symbol, old_sl, new_sl, trade.best_price)
            else:
                log.warning("[%s] Fallo trailing SL: %s", trade.symbol, resp)
        except Exception as e:
            log.error("[%s] _update_trailing_stop error: %s", trade.symbol, e)

    # ── Cierre de emergencia ──────────────────────────────────────────────────

    async def close_position_emergency(self, symbol: str, reason: str = "emergency"):
        async with self._lock:
            trade = self._trades.get(symbol)
        if not trade:
            log.warning("[%s] close_emergency: trade no registrado", symbol)
            return

        try:
            await self.client.cancel_all_orders(symbol)
            await asyncio.sleep(0.2)

            actual_qty = trade.qty_remaining if trade.qty_remaining > 0 else trade.qty
            # Obtener qty real de BingX
            positions = await self.client.get_open_positions()
            for p in positions:
                if p.get("symbol") == symbol:
                    q = abs(float(p.get("positionAmt", 0)))
                    if q > 0:
                        actual_qty = q
                    break

            await self.client.close_position_market(symbol, actual_qty, trade.direction)

            ticker = await self.client.get_ticker(symbol)
            close_price = float(ticker.get("lastPrice", trade.entry))
            pnl = self._calc_pnl_qty(trade, close_price, actual_qty)

            log.info("[%s] Cierre emergencia (%s). PnL=%.4f USDT", symbol, reason, pnl)
            await tg.notify_trade_closed(
                symbol, trade.direction, trade.entry, close_price,
                actual_qty, reason, pnl
            )
            await self.remove_trade(symbol, pnl)
        except Exception as e:
            log.error("[%s] close_emergency error: %s", symbol, e)
            await tg.notify_error(f"close_emergency({symbol})", str(e))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _calc_pnl_qty(self, trade: OpenTrade, close_price: float, qty: float) -> float:
        if trade.direction == "LONG":
            raw = (close_price - trade.entry) * qty
        else:
            raw = (trade.entry - close_price) * qty
        return round(raw * C.LEVERAGE, 4)

    # Mantener _calc_pnl para compatibilidad
    def _calc_pnl(self, trade: OpenTrade, close_price: float) -> float:
        return self._calc_pnl_qty(trade, close_price, trade.qty_remaining or trade.qty)

    def get_tracked(self) -> dict[str, OpenTrade]:
        return dict(self._trades)

    def is_trading(self, symbol: str) -> bool:
        return symbol in self._trades
