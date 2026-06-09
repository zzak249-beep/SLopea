"""
QF×JP Bot v6.3.2 — Position Manager
Cambios v6.3.2:
  - Trailing stop real: activa tras TP1 hit o BE movido
  - _move_sl reemplaza TP2 para no perderla al cancelar órdenes
  - Cooldown de trail: sólo actualiza si mejora > 0.3×ATR (evita spam de API)
  - Bug fix: be_moved ya no usa _move_to_breakeven separado — todo pasa por _move_sl

Config nuevo requerido:
  TRAIL_ATR_MULT  (default 1.5)  — distancia del trailing en ATRs
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import config as C
from bingx_client import BingXClient
from risk_manager import RiskManager
import telegram_client as tg

log = logging.getLogger("position_mgr")

# ── Fallback si config.py no tiene TRAIL_ATR_MULT aún ───────────────────────
_TRAIL_ATR_MULT = getattr(C, "TRAIL_ATR_MULT", 1.5)


@dataclass
class OpenTrade:
    symbol: str
    direction: str       # LONG | SHORT
    entry: float
    sl: float
    tp1: float
    tp2: float
    qty: float
    atr: float
    order_id: str
    be_moved: bool       = False
    tp1_hit: bool        = False
    trailing_active: bool = False   # ← nuevo: trail en curso
    # No se necesita trail_sl separado — trade.sl refleja siempre el SL activo


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
        log.info("[%s] Trade registrado %s entry=%.6f", trade.symbol, trade.direction, trade.entry)

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

    async def _check_all_positions(self):
        try:
            real_positions = await self.client.get_open_positions()
        except Exception as e:
            log.warning("get_open_positions failed: %s", e)
            return

        real_map: dict[str, dict] = {}
        for pos in real_positions:
            sym = pos.get("symbol", "")
            if sym:
                real_map[sym] = pos

        await self.risk.update_open_count(len(real_map))

        async with self._lock:
            tracked = dict(self._trades)

        for symbol, trade in tracked.items():

            # ── Posición cerrada externamente (SL/TP tocado) ─────────────────
            if symbol not in real_map:
                try:
                    ticker = await self.client.get_ticker(symbol)
                    close_price = float(ticker.get("lastPrice", trade.entry))
                except Exception:
                    close_price = trade.entry

                pnl = self._calc_pnl(trade, close_price)
                log.info("[%s] Posición cerrada externamente. PnL≈%.2f USDT", symbol, pnl)
                await tg.notify_trade_closed(
                    symbol, trade.direction, trade.entry, close_price, trade.qty, "sl_tp_auto", pnl
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

            # ── 1. TP1 hit → activar trailing + mover a BE ───────────────────
            if not trade.tp1_hit:
                tp1_reached = (
                    (trade.direction == "LONG"  and mark_price >= trade.tp1) or
                    (trade.direction == "SHORT" and mark_price <= trade.tp1)
                )
                if tp1_reached:
                    trade.tp1_hit        = True
                    trade.trailing_active = True
                    log.info("[%s] TP1 alcanzado @ %.6f — trailing activado", symbol, mark_price)
                    if not trade.be_moved:
                        await self._move_sl(trade, trade.entry, mark_price)

            # ── 2. Breakeven (si no ha llegado TP1 todavía) ──────────────────
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
                    await self._move_sl(trade, trade.entry, mark_price)
                    # Activar trailing desde aquí también
                    if not trade.trailing_active:
                        trade.trailing_active = True

            # ── 3. Trailing stop ──────────────────────────────────────────────
            if trade.trailing_active:
                await self._update_trail(trade, mark_price)

    # ── Trailing logic ────────────────────────────────────────────────────────

    async def _update_trail(self, trade: OpenTrade, mark_price: float):
        """
        Sube (LONG) / baja (SHORT) el SL siguiendo el precio.
        Sólo mueve si la mejora supera 0.3×ATR para no spamear la API.
        """
        trail_dist = trade.atr * _TRAIL_ATR_MULT
        min_step   = trade.atr * 0.3

        if trade.direction == "LONG":
            new_sl = mark_price - trail_dist
            # Nunca bajar el SL; sólo mejorar si supera el umbral mínimo
            if new_sl > trade.sl + min_step:
                await self._move_sl(trade, new_sl, mark_price)
        else:  # SHORT
            new_sl = mark_price + trail_dist
            if new_sl < trade.sl - min_step:
                await self._move_sl(trade, new_sl, mark_price)

    # ── Mover SL + re-colocar TP2 ─────────────────────────────────────────────

    async def _move_sl(self, trade: OpenTrade, new_sl: float, mark_price: float):
        """
        Cancela todas las órdenes, coloca nuevo SL, y re-coloca TP2 si
        el precio aún no lo alcanzó.
        BingX requiere cancel_all_orders antes de reemplazar un SL condicional.
        """
        try:
            await self.client.cancel_all_orders(trade.symbol)
            await asyncio.sleep(0.3)

            side_close = "SELL" if trade.direction == "LONG" else "BUY"

            # — Nuevo SL (cierra toda la posición restante) ——————————————————
            sl_resp = await self.client.place_stop_market_order(
                trade.symbol, side_close, trade.qty, new_sl,
                trade.direction, close_position=True, order_type="STOP_MARKET",
            )
            if sl_resp.get("code", -1) != 0:
                log.warning("[%s] _move_sl: SL rechazado: %s", trade.symbol, sl_resp)
                return

            # — Re-colocar TP2 si el precio aún no lo tocó ——————————————————
            tp2_pending = (
                (trade.direction == "LONG"  and mark_price < trade.tp2) or
                (trade.direction == "SHORT" and mark_price > trade.tp2)
            )
            if tp2_pending:
                qty_half = round(trade.qty / 2, 8)
                tp2_resp = await self.client.place_stop_market_order(
                    trade.symbol, side_close, qty_half, trade.tp2,
                    trade.direction, close_position=False,
                    order_type="TAKE_PROFIT_MARKET",
                )
                if tp2_resp.get("code", -1) != 0:
                    log.warning("[%s] _move_sl: TP2 no recolocado: %s", trade.symbol, tp2_resp)

            # — Actualizar estado local ————————————————————————————————————
            old_sl = trade.sl
            trade.sl = new_sl

            if new_sl == trade.entry and not trade.be_moved:
                trade.be_moved = True
                log.info("[%s] SL → breakeven @ %.6f", trade.symbol, new_sl)
                await tg.send_message(
                    f"⚡ {trade.symbol} SL movido a BE @ {new_sl:.6f}"
                )
            else:
                log.info(
                    "[%s] Trail SL: %.6f → %.6f (mark=%.6f, gain=+%.2f%%)",
                    trade.symbol, old_sl, new_sl, mark_price,
                    abs(new_sl - trade.entry) / trade.entry * 100,
                )

        except Exception as e:
            log.error("[%s] _move_sl error: %s", trade.symbol, e)

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
            await self.client.close_position_market(symbol, trade.qty, trade.direction)

            ticker = await self.client.get_ticker(symbol)
            close_price = float(ticker.get("lastPrice", trade.entry))
            pnl = self._calc_pnl(trade, close_price)

            log.info("[%s] Cierre emergencia. PnL=%.2f USDT", symbol, pnl)
            await tg.notify_trade_closed(
                symbol, trade.direction, trade.entry, close_price, trade.qty, reason, pnl
            )
            await self.remove_trade(symbol, pnl)
        except Exception as e:
            log.error("[%s] close_emergency error: %s", symbol, e)
            await tg.notify_error(f"close_emergency({symbol})", str(e))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _calc_pnl(self, trade: OpenTrade, close_price: float) -> float:
        if trade.direction == "LONG":
            raw_pnl = (close_price - trade.entry) * trade.qty
        else:
            raw_pnl = (trade.entry - close_price) * trade.qty
        return round(raw_pnl * C.LEVERAGE, 4)

    def get_tracked(self) -> dict[str, OpenTrade]:
        return dict(self._trades)

    def is_trading(self, symbol: str) -> bool:
        return symbol in self._trades
