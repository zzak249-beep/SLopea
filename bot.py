"""
SAMA APEX Bot - Main Orchestrator FIXED v2
BUGS CORREGIDOS:
  1. Detecta One-Way vs Hedge mode al inicio y pasa positionSide correcto
  2. confirm_signal() solo se llama tras éxito real en BingX
  3. Balance 0 → log error claro, no crashea
  4. Logging INFO por defecto para ver todo en Railway
  5. can_open_trade() antes de calcular qty (evita trabajo innecesario)
"""
import asyncio
import logging
import sys
import json
from datetime import date
from aiohttp import web

from config import (
    SYMBOLS, TF_LOCAL, TF_MACRO_1, TF_MACRO_2,
    LEVERAGE, SCAN_INTERVAL, HEALTH_PORT, CANDLES_NEEDED,
    FUNDING_FILTER, MAX_OPEN_TRADES, MIN_CONFLUENCE,
)
from bingx_client   import BingXClient
from indicators     import process_sama
from signal_engine  import SignalEngine
from risk_manager   import RiskManager
import telegram_notifier as tg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("SAMA-APEX")


# ── Health Server ─────────────────────────────────────────────────────────────
async def health_handler(request):
    return web.Response(text="OK", status=200)

async def start_health_server():
    app = web.Application()
    app.router.add_get("/",       health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
    await site.start()
    logger.info(f"✅ Health server en :{HEALTH_PORT}")


# ── Bot ───────────────────────────────────────────────────────────────────────
class SamaApexBot:
    def __init__(self):
        self.signal_engine = SignalEngine()
        self.risk_manager  = RiskManager()
        self.last_summary  = date.today()
        self.position_mode = "ONE_WAY"   # se detecta al arrancar

    def _ps(self, direction: str) -> str:
        """positionSide correcto según modo de cuenta."""
        if self.position_mode == "ONE_WAY":
            return "BOTH"
        return "LONG" if direction == "LONG" else "SHORT"

    # ── Fetch TF ──────────────────────────────────────────────────────────────
    async def _fetch_tf(self, client: BingXClient, symbol: str, tf: str) -> dict | None:
        df = await client.get_klines(symbol, tf, CANDLES_NEEDED)
        if df.empty or len(df) < 210:
            logger.warning(f"{symbol}/{tf}: {len(df)} velas — insuficiente (necesito ≥210)")
            return None
        return process_sama(df)

    # ── Scan symbol ───────────────────────────────────────────────────────────
    async def _scan(self, client: BingXClient, symbol: str) -> dict | None:
        try:
            local, m1, m2 = await asyncio.gather(
                self._fetch_tf(client, symbol, TF_LOCAL),
                self._fetch_tf(client, symbol, TF_MACRO_1),
                self._fetch_tf(client, symbol, TF_MACRO_2),
            )
            if not all([local, m1, m2]):
                return None

            funding = await client.get_funding_rate(symbol) if FUNDING_FILTER else 0.0

            # Log estado actual para diagnóstico
            logger.info(
                f"  {symbol}: LOCAL={local['trend']}({local['slope']:.0f}°) "
                f"M1={m1['trend']}({m1['slope']:.0f}°) "
                f"M2={m2['trend']}({m2['slope']:.0f}°) "
                f"rvol={local['rvol']:.2f} fund={funding*100:.4f}%"
            )

            return self.signal_engine.evaluate(symbol, local, m1, m2, funding)

        except Exception as e:
            logger.error(f"Error escaneando {symbol}: {e}", exc_info=True)
            return None

    # ── Execute signal ────────────────────────────────────────────────────────
    async def _execute(self, client: BingXClient, signal: dict, balance: float):
        symbol    = signal["symbol"]
        direction = signal["direction"]
        entry     = signal["entry"]
        atr       = signal["atr"]
        score     = signal["confluence"]["score"]
        conf      = signal["confluence"]

        # SL desde ATR bands (igual que Pine Script)
        sl = signal["lower_band"] if direction == "LONG" else signal["upper_band"]
        tp = self.risk_manager.calculate_tp(entry, sl, direction, atr, score)

        logger.info(f"🎯 {symbol} {direction} | entry={entry:.4f} sl={sl:.4f} tp={tp:.4f} score={score}")

        # ── Can trade? ────────────────────────────────────────────────────────
        can, reason = self.risk_manager.can_open_trade(symbol, score, balance)
        if not can:
            logger.info(f"⏭️  {symbol} bloqueado: {reason}")
            return

        if balance < 10:
            logger.error(f"❌ Balance demasiado bajo: {balance:.2f} USDT — no se puede operar")
            return

        # ── Sizing ────────────────────────────────────────────────────────────
        raw_qty = self.risk_manager.calculate_size(balance, entry, sl, score)
        qty     = await client.round_quantity(symbol, raw_qty)
        logger.info(f"📐 {symbol} qty={qty} (raw={raw_qty:.5f}, balance={balance:.2f})")

        # ── Leverage ─────────────────────────────────────────────────────────
        await client.set_leverage(symbol, LEVERAGE)

        # ── Orden de entrada ──────────────────────────────────────────────────
        side = "BUY" if direction == "LONG" else "SELL"
        ps   = self._ps(direction)

        order = await client.place_market_order(symbol, side, qty, ps)
        if order.get("code") != 0:
            logger.error(f"❌ Orden rechazada {symbol}: code={order.get('code')} msg={order.get('msg')}")
            await tg.notify_error(f"Orden rechazada {symbol}: {order.get('msg','')}")
            return

        # Precio de fill real
        try:
            fill_price = float(order["data"]["order"]["avgPrice"] or entry)
            if fill_price <= 0: fill_price = entry
        except Exception:
            fill_price = entry

        logger.info(f"✅ {symbol} {direction} abierto @ {fill_price:.4f}")

        # ── SL y TP ───────────────────────────────────────────────────────────
        sl_side = "SELL" if direction == "LONG" else "BUY"
        sl_res, tp_res = await asyncio.gather(
            client.place_tp_sl_order(symbol, sl_side, qty, sl, "STOP_MARKET",  ps),
            client.place_tp_sl_order(symbol, sl_side, qty, tp, "TAKE_PROFIT_MARKET", ps),
        )
        if sl_res.get("code") != 0:
            logger.warning(f"⚠️  SL no colocado {symbol}: {sl_res.get('msg')}")
        if tp_res.get("code") != 0:
            logger.warning(f"⚠️  TP no colocado {symbol}: {tp_res.get('msg')}")

        # ── Registrar — solo aquí confirmamos la señal ────────────────────────
        self.risk_manager.register_position(
            symbol, direction, fill_price, qty, sl, tp, atr, score
        )
        self.signal_engine.confirm_signal(symbol, direction)   # FIX BUG 2

        await asyncio.gather(
            tg.notify_signal(symbol, signal, conf, fill_price, sl, tp, qty, balance),
            tg.notify_trade_opened(symbol, direction, fill_price, sl, tp, qty),
        )

    # ── Manage open positions ─────────────────────────────────────────────────
    async def _manage(self, client: BingXClient):
        for symbol, pos in list(self.risk_manager.open_positions.items()):
            try:
                ticker = await client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", 0) or 0)
                if price <= 0:
                    continue

                # ¿Sigue abierta en BingX?
                bx_pos = await client.get_positions(symbol)
                ps_check = "LONG" if pos.direction == "LONG" else "SHORT"
                still_open = any(
                    p.get("positionSide") in (ps_check, "BOTH")
                    and abs(float(p.get("positionAmt", 0))) > 0
                    for p in bx_pos
                )

                if not still_open:
                    result = self.risk_manager.close_position(symbol, price)
                    self.signal_engine.clear_direction(symbol)
                    if result:
                        await tg.notify_trade_closed(result)
                    continue

                # Trailing stop
                should, new_sl = self.risk_manager.should_update_trailing(symbol, price)
                if should:
                    old_sl = pos.trailing_sl
                    await client.update_trailing_stop(
                        symbol, pos.direction, new_sl, pos.quantity, self.position_mode
                    )
                    await tg.notify_trailing_update(symbol, old_sl, new_sl)

            except Exception as e:
                logger.error(f"Error gestionando {symbol}: {e}")

    # ── Daily summary ─────────────────────────────────────────────────────────
    async def _daily_summary(self, balance: float):
        today = date.today()
        if today != self.last_summary:
            self.last_summary = today
            await tg.notify_daily_summary(self.risk_manager.get_stats(), balance)

    # ── Main loop ─────────────────────────────────────────────────────────────
    async def run(self):
        logger.info("=" * 55)
        logger.info("  SAMA APEX Bot v2 — FIXED")
        logger.info(f"  Pares: {', '.join(SYMBOLS)}")
        logger.info(f"  TFs: {TF_LOCAL} / {TF_MACRO_1} / {TF_MACRO_2}")
        logger.info(f"  Confluence mínimo: {MIN_CONFLUENCE}")
        logger.info("=" * 55)

        async with BingXClient() as client:
            # Detectar modo de posición (One-Way vs Hedge)
            self.position_mode = await client.get_position_mode()
            logger.info(f"🔧 Modo cuenta: {self.position_mode}")

            # Balance inicial
            balance = await client.get_balance()
            if balance <= 0:
                logger.error("❌ No se pudo obtener balance — revisa API keys y permisos")
            else:
                self.risk_manager.set_equity_start(balance)

            await tg.notify_startup(SYMBOLS, (TF_LOCAL, TF_MACRO_1, TF_MACRO_2))

            loop = asyncio.get_event_loop()
            while True:
                try:
                    t0 = loop.time()

                    balance = await client.get_balance()
                    self.risk_manager.set_equity_start(balance)

                    # Circuit breaker
                    if self.risk_manager.is_circuit_broken(balance):
                        stats = self.risk_manager.get_stats()
                        logger.warning(f"🚨 Circuit breaker | PnL={stats['daily_pnl']*100:+.2f}%")
                        await asyncio.sleep(SCAN_INTERVAL)
                        continue

                    # Gestionar posiciones abiertas
                    await self._manage(client)

                    # Scan de señales
                    open_n = len(self.risk_manager.open_positions)
                    stats  = self.risk_manager.get_stats()
                    logger.info(
                        f"── SCAN ── balance={balance:.2f} USDT | "
                        f"pos={open_n}/{MAX_OPEN_TRADES} | "
                        f"PnL={stats['daily_pnl']*100:+.2f}% | "
                        f"trades={stats['trades']}({stats['wins']}W/{stats['losses']}L)"
                    )

                    if open_n < MAX_OPEN_TRADES:
                        tasks   = [self._scan(client, s) for s in SYMBOLS]
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        signals = [r for r in results if isinstance(r, dict)]
                        signals.sort(key=lambda s: s["confluence"]["score"], reverse=True)

                        if signals:
                            logger.info(f"✨ {len(signals)} señal(es) — mejor score={signals[0]['confluence']['score']}")
                        else:
                            logger.info("🔍 Sin señales este ciclo")

                        for sig in signals:
                            if len(self.risk_manager.open_positions) >= MAX_OPEN_TRADES:
                                break
                            await self._execute(client, sig, balance)
                    else:
                        logger.info(f"📋 Slots llenos ({open_n}/{MAX_OPEN_TRADES})")

                    await self._daily_summary(balance)

                    elapsed = loop.time() - t0
                    sleep   = max(5, SCAN_INTERVAL - elapsed)
                    logger.info(f"⏱ Loop {elapsed:.1f}s → sleep {sleep:.0f}s")
                    await asyncio.sleep(sleep)

                except KeyboardInterrupt:
                    logger.info("🛑 Detenido manualmente")
                    break
                except Exception as e:
                    logger.error(f"Error en loop: {e}", exc_info=True)
                    await tg.notify_error(str(e))
                    await asyncio.sleep(30)


# ── Entry point ───────────────────────────────────────────────────────────────
async def main():
    await start_health_server()
    bot = SamaApexBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
