"""
SAMA APEX Bot - Main Orchestrator v3
Escanea TODOS los pares perpetuos de BingX automáticamente.
Procesa en batches para no saturar la API.
"""
import asyncio
import logging
import sys
from datetime import date
from aiohttp import web

from config import (
    SYMBOLS, TF_LOCAL, TF_MACRO_1, TF_MACRO_2,
    LEVERAGE, SCAN_INTERVAL, HEALTH_PORT, CANDLES_NEEDED,
    FUNDING_FILTER, MAX_OPEN_TRADES, MIN_CONFLUENCE,
)
from bingx_client    import BingXClient
from indicators      import process_sama
from signal_engine   import SignalEngine
from risk_manager    import RiskManager
from symbol_scanner  import fetch_all_symbols
import telegram_notifier as tg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("SAMA-APEX")

BATCH_SIZE = 8   # Pares procesados en paralelo por batch


# ── Health Server ─────────────────────────────────────────────────────────────
async def health_handler(request):
    return web.Response(text="OK", status=200)

async def start_health_server():
    app = web.Application()
    app.router.add_get("/",       health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HEALTH_PORT).start()
    logger.info(f"✅ Health server :{HEALTH_PORT}")


# ── Bot ───────────────────────────────────────────────────────────────────────
class SamaApexBot:
    def __init__(self):
        self.signal_engine = SignalEngine()
        self.risk_manager  = RiskManager()
        self.last_summary  = date.today()
        self.position_mode = "ONE_WAY"
        self.active_symbols: list[str] = []

    def _ps(self, direction: str) -> str:
        return "BOTH" if self.position_mode == "ONE_WAY" else direction

    # ── Fetch single TF ───────────────────────────────────────────────────────
    async def _fetch_tf(self, client: BingXClient, symbol: str, tf: str) -> dict | None:
        df = await client.get_klines(symbol, tf, CANDLES_NEEDED)
        if df.empty or len(df) < 210:
            return None
        return process_sama(df)

    # ── Scan one symbol ───────────────────────────────────────────────────────
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

            logger.info(
                f"  {symbol}: "
                f"LOCAL={local['trend']}({local['slope']:.0f}°) "
                f"M1={m1['trend']}({m1['slope']:.0f}°) "
                f"M2={m2['trend']}({m2['slope']:.0f}°) "
                f"rvol={local['rvol']:.2f}"
            )
            return self.signal_engine.evaluate(symbol, local, m1, m2, funding)

        except Exception as e:
            logger.error(f"Error scan {symbol}: {e}")
            return None

    # ── Scan all symbols in batches ───────────────────────────────────────────
    async def _scan_all(self, client: BingXClient) -> list[dict]:
        signals = []
        symbols = self.active_symbols
        total   = len(symbols)

        for i in range(0, total, BATCH_SIZE):
            batch   = symbols[i: i + BATCH_SIZE]
            results = await asyncio.gather(*[self._scan(client, s) for s in batch],
                                           return_exceptions=True)
            for r in results:
                if isinstance(r, dict):
                    signals.append(r)
            # Pausa breve entre batches para no saturar rate limit
            if i + BATCH_SIZE < total:
                await asyncio.sleep(0.5)

        signals.sort(key=lambda s: s["confluence"]["score"], reverse=True)
        return signals

    # ── Execute signal ────────────────────────────────────────────────────────
    async def _execute(self, client: BingXClient, signal: dict, balance: float):
        symbol    = signal["symbol"]
        direction = signal["direction"]
        entry     = signal["entry"]
        atr       = signal["atr"]
        score     = signal["confluence"]["score"]
        conf      = signal["confluence"]

        sl = signal["lower_band"] if direction == "LONG" else signal["upper_band"]
        tp = self.risk_manager.calculate_tp(entry, sl, direction, atr, score)

        can, reason = self.risk_manager.can_open_trade(symbol, score, balance)
        if not can:
            logger.info(f"⏭️  {symbol} bloqueado: {reason}")
            return

        if balance < 5:
            logger.error(f"❌ Balance {balance:.2f} USDT insuficiente")
            return

        raw_qty = self.risk_manager.calculate_size(balance, entry, sl, score)
        qty     = await client.round_quantity(symbol, raw_qty)
        logger.info(f"📐 {symbol} qty={qty} entry={entry:.4f} sl={sl:.4f} tp={tp:.4f} score={score}")

        await client.set_leverage(symbol, LEVERAGE)

        side  = "BUY"  if direction == "LONG" else "SELL"
        ps    = self._ps(direction)
        order = await client.place_market_order(symbol, side, qty, ps)

        if order.get("code") != 0:
            logger.error(f"❌ Orden rechazada {symbol}: {order.get('msg')}")
            await tg.notify_error(f"Orden rechazada {symbol}: {order.get('msg','')}")
            return

        try:
            fill = float(order["data"]["order"]["avgPrice"] or entry)
            if fill <= 0: fill = entry
        except Exception:
            fill = entry

        sl_side = "SELL" if direction == "LONG" else "BUY"
        await asyncio.gather(
            client.place_tp_sl_order(symbol, sl_side, qty, sl, "STOP_MARKET",         ps),
            client.place_tp_sl_order(symbol, sl_side, qty, tp, "TAKE_PROFIT_MARKET",  ps),
        )

        self.risk_manager.register_position(symbol, direction, fill, qty, sl, tp, atr, score)
        self.signal_engine.confirm_signal(symbol, direction)

        await asyncio.gather(
            tg.notify_signal(symbol, signal, conf, fill, sl, tp, qty, balance),
            tg.notify_trade_opened(symbol, direction, fill, sl, tp, qty),
        )
        logger.info(f"🚀 {symbol} {direction} @ {fill:.4f}")

    # ── Manage open positions ─────────────────────────────────────────────────
    async def _manage(self, client: BingXClient):
        for symbol, pos in list(self.risk_manager.open_positions.items()):
            try:
                ticker = await client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", 0) or 0)
                if not price:
                    continue

                bx = await client.get_positions(symbol)
                ps_check = pos.direction
                still_open = any(
                    p.get("positionSide") in (ps_check, "BOTH")
                    and abs(float(p.get("positionAmt", 0))) > 0
                    for p in bx
                )

                if not still_open:
                    result = self.risk_manager.close_position(symbol, price)
                    self.signal_engine.clear_direction(symbol)
                    if result:
                        await tg.notify_trade_closed(result)
                    continue

                should, new_sl = self.risk_manager.should_update_trailing(symbol, price)
                if should:
                    old_sl = pos.trailing_sl
                    await client.update_trailing_stop(symbol, pos.direction, new_sl, pos.quantity, self.position_mode)
                    await tg.notify_trailing_update(symbol, old_sl, new_sl)

            except Exception as e:
                logger.error(f"Error managing {symbol}: {e}")

    # ── Main loop ─────────────────────────────────────────────────────────────
    async def run(self):
        logger.info("=" * 55)
        logger.info("  SAMA APEX Bot v3 — ALL PAIRS SCANNER")
        logger.info(f"  TFs: {TF_LOCAL} / {TF_MACRO_1} / {TF_MACRO_2}")
        logger.info(f"  Confluence mín: {MIN_CONFLUENCE} | Max trades: {MAX_OPEN_TRADES}")
        logger.info("=" * 55)

        async with BingXClient() as client:
            self.position_mode = await client.get_position_mode()
            logger.info(f"🔧 Modo cuenta: {self.position_mode}")

            balance = await client.get_balance()
            self.risk_manager.set_equity_start(balance)
            logger.info(f"💰 Balance: {balance:.2f} USDT")

            # Cargar símbolos (manual o auto)
            if SYMBOLS:
                self.active_symbols = SYMBOLS
                logger.info(f"📋 Símbolos manuales: {len(self.active_symbols)}")
            else:
                self.active_symbols = await fetch_all_symbols(client.session)

            await tg.notify_startup(self.active_symbols[:10], (TF_LOCAL, TF_MACRO_1, TF_MACRO_2))

            loop    = asyncio.get_event_loop()
            cycle_n = 0

            while True:
                try:
                    t0      = loop.time()
                    cycle_n += 1

                    balance = await client.get_balance()
                    self.risk_manager.set_equity_start(balance)

                    if self.risk_manager.is_circuit_broken(balance):
                        stats = self.risk_manager.get_stats()
                        logger.warning(f"🚨 Circuit breaker | PnL={stats['daily_pnl']*100:+.2f}%")
                        await asyncio.sleep(SCAN_INTERVAL)
                        continue

                    # Refrescar lista de pares cada 40 ciclos (~1h)
                    if not SYMBOLS and cycle_n % 40 == 0:
                        self.active_symbols = await fetch_all_symbols(client.session)

                    await self._manage(client)

                    open_n = len(self.risk_manager.open_positions)
                    stats  = self.risk_manager.get_stats()
                    logger.info(
                        f"── SCAN #{cycle_n} | {len(self.active_symbols)} pares | "
                        f"balance={balance:.2f} USDT | pos={open_n}/{MAX_OPEN_TRADES} | "
                        f"PnL={stats['daily_pnl']*100:+.2f}% | "
                        f"{stats['wins']}W/{stats['losses']}L"
                    )

                    if open_n < MAX_OPEN_TRADES:
                        signals = await self._scan_all(client)

                        if signals:
                            logger.info(f"✨ {len(signals)} señal(es) | mejor: {signals[0]['symbol']} score={signals[0]['confluence']['score']}")
                            for sig in signals:
                                if len(self.risk_manager.open_positions) >= MAX_OPEN_TRADES:
                                    break
                                await self._execute(client, sig, balance)
                        else:
                            logger.info("🔍 Sin señales este ciclo")
                    else:
                        logger.info(f"📋 Slots llenos ({open_n}/{MAX_OPEN_TRADES}) — solo gestión")

                    # Resumen diario
                    today = date.today()
                    if today != self.last_summary:
                        self.last_summary = today
                        await tg.notify_daily_summary(stats, balance)

                    elapsed = loop.time() - t0
                    sleep   = max(5, SCAN_INTERVAL - elapsed)
                    logger.info(f"⏱ Loop {elapsed:.1f}s → sleep {sleep:.0f}s")
                    await asyncio.sleep(sleep)

                except KeyboardInterrupt:
                    logger.info("🛑 Detenido")
                    break
                except Exception as e:
                    logger.error(f"Error loop: {e}", exc_info=True)
                    await tg.notify_error(str(e))
                    await asyncio.sleep(30)


async def main():
    await start_health_server()
    await SamaApexBot().run()

if __name__ == "__main__":
    asyncio.run(main())
