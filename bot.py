"""
SAMA APEX Bot - Main Orchestrator v4 ELITE
Integra los 7 edges del EdgeEngine sobre la base SAMA.
"""
import asyncio
import logging
import sys
import numpy as np
from datetime import date
from aiohttp import web

from config import (
    SYMBOLS, TF_LOCAL, TF_MACRO_1, TF_MACRO_2,
    LEVERAGE, SCAN_INTERVAL, HEALTH_PORT, CANDLES_NEEDED,
    FUNDING_FILTER, MAX_OPEN_TRADES, MIN_CONFLUENCE,
    RISK_PER_TRADE,
)
from bingx_client   import BingXClient
from indicators     import process_sama
from signal_engine  import SignalEngine
from risk_manager   import RiskManager
from symbol_scanner import fetch_all_symbols
from edge_engine    import (
    is_volatile_regime, PartialProfitManager,
    dynamic_risk_multiplier, funding_bias, funding_score_bonus,
    is_correlated_blocked, orderbook_imbalance, check_ob_for_trade,
    momentum_rank_score,
)
import telegram_notifier as tg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("SAMA-APEX")

BATCH_SIZE = 8


# ── Health ────────────────────────────────────────────────────────────────────
async def health_handler(req): return web.Response(text="OK")
async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HEALTH_PORT).start()
    logger.info(f"✅ Health :{HEALTH_PORT}")


# ── Bot ───────────────────────────────────────────────────────────────────────
class SamaApexBot:
    def __init__(self):
        self.signal_engine   = SignalEngine()
        self.risk_manager    = RiskManager()
        self.partial_manager = PartialProfitManager()
        self.last_summary    = date.today()
        self.position_mode   = "ONE_WAY"
        self.active_symbols: list[str] = []
        self._close_cache: dict[str, np.ndarray] = {}

    def _ps(self, direction: str) -> str:
        return "BOTH" if self.position_mode == "ONE_WAY" else direction

    # ── Fetch & cache close prices ────────────────────────────────────────────
    async def _fetch_tf(self, client: BingXClient, symbol: str, tf: str) -> dict | None:
        df = await client.get_klines(symbol, tf, CANDLES_NEEDED)
        if df.empty or len(df) < 210:
            return None
        # Cache close prices para BBW
        if tf == TF_LOCAL:
            self._close_cache[symbol] = df["close"].values.astype(float)
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
                f"  {symbol}: LOCAL={local['trend']}({local['slope']:.0f}°) "
                f"M1={m1['trend']}({m1['slope']:.0f}°) "
                f"M2={m2['trend']}({m2['slope']:.0f}°) "
                f"rvol={local['rvol']:.2f} fund={funding*100:.4f}%"
            )

            sig = self.signal_engine.evaluate(symbol, local, m1, m2, funding)
            if not sig:
                return None

            # ── EDGE 1: Volatility Regime Filter ─────────────────────────────
            close_arr = self._close_cache.get(symbol)
            if close_arr is not None and len(close_arr) > 120:
                ok_regime, bbw_pct = is_volatile_regime(close_arr)
                if not ok_regime:
                    logger.info(f"  {symbol}: ⛔ BBW comprimido ({bbw_pct:.0f}pct) → skip")
                    return None
                sig["bbw_pct"] = bbw_pct
            else:
                sig["bbw_pct"] = 50.0

            # ── EDGE 4: Funding bias bonus ────────────────────────────────────
            fb = funding_bias(funding)
            if fb["msg"]:
                logger.info(f"  {symbol}: {fb['msg']}")

            # ── EDGE 7: Momentum rank score ───────────────────────────────────
            conf = sig["confluence"]
            sig["rank_score"] = momentum_rank_score(
                conf["score"], conf.get("avg_slope", 20),
                conf.get("avg_rvol", 1.0), funding,
                sig["bbw_pct"], sig["direction"]
            )
            logger.info(f"  {symbol}: ✨ señal {sig['direction']} | score={conf['score']} rank={sig['rank_score']}")
            return sig

        except Exception as e:
            logger.error(f"Error scan {symbol}: {e}")
            return None

    # ── Execute with all edges ────────────────────────────────────────────────
    async def _execute(self, client: BingXClient, signal: dict, balance: float):
        symbol    = signal["symbol"]
        direction = signal["direction"]
        entry     = signal["entry"]
        atr       = signal["atr"]
        score     = signal["confluence"]["score"]
        conf      = signal["confluence"]
        funding   = conf.get("funding", 0.0)

        sl = signal["lower_band"] if direction == "LONG" else signal["upper_band"]
        tp = self.risk_manager.calculate_tp(entry, sl, direction, atr, score)

        # ── can trade basic check ─────────────────────────────────────────────
        can, reason = self.risk_manager.can_open_trade(symbol, score, balance)
        if not can:
            logger.info(f"⏭️  {symbol} bloqueado: {reason}")
            return

        # ── EDGE 5: Correlation Guard ─────────────────────────────────────────
        blocked, corr_reason = is_correlated_blocked(
            symbol, direction, self.risk_manager.open_positions
        )
        if blocked:
            logger.info(f"🔗 {symbol} bloqueado por correlación: {corr_reason}")
            return

        # ── EDGE 6: Order Book Imbalance ──────────────────────────────────────
        try:
            ob_raw  = await client.get_orderbook_depth(symbol)
            ob      = orderbook_imbalance(ob_raw.get("bids",[]), ob_raw.get("asks",[]))
            ob_ok, ob_reason = check_ob_for_trade(direction, ob)
            if not ob_ok:
                logger.info(f"📖 {symbol}: {ob_reason} → skip")
                return
            logger.debug(f"  {symbol} OB ratio={ob['ratio']} bias={ob['bias']}")
        except Exception:
            pass  # Si falla OB, continuar sin filtro

        if balance < 5:
            logger.error(f"❌ Balance {balance:.2f} USDT insuficiente")
            return

        # ── EDGE 3: Dynamic Risk Scaling ──────────────────────────────────────
        daily_pnl = self.risk_manager.get_stats()["daily_pnl"]
        dyn_risk  = dynamic_risk_multiplier(daily_pnl, RISK_PER_TRADE)

        raw_qty = self.risk_manager.calculate_size(balance, entry, sl, score,
                                                    risk_override=dyn_risk)
        qty     = await client.round_quantity(symbol, raw_qty)
        logger.info(f"📐 {symbol} qty={qty} entry={entry:.5f} sl={sl:.5f} tp={tp:.5f} risk={dyn_risk*100:.2f}%")

        await client.set_leverage(symbol, LEVERAGE)
        side  = "BUY" if direction == "LONG" else "SELL"
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

        # ── EDGE 2: Partial Profit TP1 (50% en 1R) + TP2 (full) ──────────────
        sl_side  = "SELL" if direction == "LONG" else "BUY"
        risk_dist = abs(fill - sl)
        tp1 = fill + risk_dist if direction == "LONG" else fill - risk_dist  # 1R
        tp2 = tp  # R:R completo para la otra mitad

        half_qty = await client.round_quantity(symbol, qty / 2)

        await asyncio.gather(
            client.place_tp_sl_order(symbol, sl_side, qty,      sl,  "STOP_MARKET",         ps),
            client.place_tp_sl_order(symbol, sl_side, half_qty, tp1, "TAKE_PROFIT_MARKET",  ps),
            client.place_tp_sl_order(symbol, sl_side, half_qty, tp2, "TAKE_PROFIT_MARKET",  ps),
        )

        self.risk_manager.register_position(symbol, direction, fill, qty, sl, tp2, atr, score)
        self.signal_engine.confirm_signal(symbol, direction)

        # Notificación con info de edges
        edge_info = (
            f"BBW: {signal.get('bbw_pct',50):.0f}pct | "
            f"OB: {ob.get('ratio',1):.2f} | "
            f"Fund: {funding*100:.4f}% | "
            f"Rank: {signal.get('rank_score',score)}"
        )
        await asyncio.gather(
            tg.notify_signal(symbol, signal, conf, fill, sl, tp2, qty, balance),
            tg.notify_trade_opened(symbol, direction, fill, sl, tp2, qty),
        )
        logger.info(f"🚀 {symbol} {direction} @ {fill:.5f} | {edge_info}")
        logger.info(f"   TP1(50%): {tp1:.5f} | TP2(50%): {tp2:.5f} | SL: {sl:.5f}")

    # ── Manage positions ──────────────────────────────────────────────────────
    async def _manage(self, client: BingXClient):
        for symbol, pos in list(self.risk_manager.open_positions.items()):
            try:
                ticker = await client.get_ticker(symbol)
                price  = float(ticker.get("lastPrice", 0) or 0)
                if not price: continue

                bx = await client.get_positions(symbol)
                still_open = any(
                    p.get("positionSide") in (pos.direction, "BOTH")
                    and abs(float(p.get("positionAmt", 0))) > 0
                    for p in bx
                )

                if not still_open:
                    result = self.risk_manager.close_position(symbol, price)
                    self.signal_engine.clear_direction(symbol)
                    self.partial_manager.clear(symbol)
                    if result:
                        await tg.notify_trade_closed(result)
                    continue

                # ── EDGE 2: mover SL a breakeven cuando TP1 hit ──────────────
                if self.partial_manager.should_take_partial(
                    symbol, pos.direction, price, pos.entry_price, pos.sl_price
                ):
                    be_sl = self.partial_manager.get_breakeven_sl(pos.direction, pos.entry_price)
                    await client.cancel_all_orders(symbol)
                    # SL en breakeven para la mitad restante
                    sl_side = "SELL" if pos.direction == "LONG" else "BUY"
                    half_qty = await client.round_quantity(symbol, pos.quantity / 2)
                    await client.place_tp_sl_order(symbol, sl_side, half_qty, be_sl,
                                                    "STOP_MARKET", self._ps(pos.direction))
                    self.partial_manager.mark_partial_taken(symbol)
                    logger.info(f"💰 {symbol}: 1R alcanzado → SL movido a breakeven {be_sl:.5f}")
                    await tg.notify_trailing_update(symbol, pos.sl_price, be_sl)
                    continue

                # Trailing normal
                should, new_sl = self.risk_manager.should_update_trailing(symbol, price)
                if should:
                    old_sl = pos.trailing_sl
                    await client.update_trailing_stop(symbol, pos.direction, new_sl,
                                                       pos.quantity, self.position_mode)
                    await tg.notify_trailing_update(symbol, old_sl, new_sl)

            except Exception as e:
                logger.error(f"Error managing {symbol}: {e}")

    # ── Main loop ─────────────────────────────────────────────────────────────
    async def run(self):
        logger.info("=" * 60)
        logger.info("  SAMA APEX Bot v4 ELITE — 7 EDGES ACTIVOS")
        logger.info(f"  TFs: {TF_LOCAL}/{TF_MACRO_1}/{TF_MACRO_2}")
        logger.info("  Edges: BBW | Partial TP | Dyn Risk | Funding Fade")
        logger.info("         Correlation Guard | OB Imbalance | Rank Score")
        logger.info("=" * 60)

        async with BingXClient() as client:
            self.position_mode = await client.get_position_mode()
            balance = await client.get_balance()
            self.risk_manager.set_equity_start(balance)
            logger.info(f"💰 Balance: {balance:.2f} USDT | Modo: {self.position_mode}")

            if SYMBOLS:
                self.active_symbols = SYMBOLS
            else:
                self.active_symbols = await fetch_all_symbols(client.session)

            await tg.notify_startup(self.active_symbols[:10], (TF_LOCAL, TF_MACRO_1, TF_MACRO_2))

            loop = asyncio.get_event_loop()
            cycle_n = 0

            while True:
                try:
                    t0 = loop.time(); cycle_n += 1
                    balance = await client.get_balance()
                    self.risk_manager.set_equity_start(balance)

                    if self.risk_manager.is_circuit_broken(balance):
                        await asyncio.sleep(SCAN_INTERVAL); continue

                    if not SYMBOLS and cycle_n % 40 == 0:
                        self.active_symbols = await fetch_all_symbols(client.session)

                    await self._manage(client)

                    open_n = len(self.risk_manager.open_positions)
                    stats  = self.risk_manager.get_stats()
                    logger.info(
                        f"── SCAN #{cycle_n} | {len(self.active_symbols)} pares | "
                        f"bal={balance:.2f} | pos={open_n}/{MAX_OPEN_TRADES} | "
                        f"PnL={stats['daily_pnl']*100:+.2f}% | {stats['wins']}W/{stats['losses']}L"
                    )

                    if open_n < MAX_OPEN_TRADES:
                        # Scan en batches
                        signals = []
                        for i in range(0, len(self.active_symbols), BATCH_SIZE):
                            batch   = self.active_symbols[i: i+BATCH_SIZE]
                            results = await asyncio.gather(
                                *[self._scan(client, s) for s in batch],
                                return_exceptions=True
                            )
                            signals += [r for r in results if isinstance(r, dict)]
                            if i + BATCH_SIZE < len(self.active_symbols):
                                await asyncio.sleep(0.5)

                        # EDGE 7: Ordenar por rank_score (no solo confluence)
                        signals.sort(key=lambda s: s.get("rank_score", 0), reverse=True)

                        if signals:
                            best = signals[0]
                            logger.info(
                                f"✨ {len(signals)} señal(es) | "
                                f"mejor: {best['symbol']} rank={best.get('rank_score',0)}"
                            )
                            for sig in signals:
                                if len(self.risk_manager.open_positions) >= MAX_OPEN_TRADES:
                                    break
                                await self._execute(client, sig, balance)
                        else:
                            logger.info("🔍 Sin señales este ciclo")
                    else:
                        logger.info(f"📋 Slots llenos ({open_n}/{MAX_OPEN_TRADES})")

                    today = date.today()
                    if today != self.last_summary:
                        self.last_summary = today
                        await tg.notify_daily_summary(stats, balance)

                    elapsed = loop.time() - t0
                    logger.info(f"⏱ Loop {elapsed:.1f}s → sleep {max(5,SCAN_INTERVAL-elapsed):.0f}s")
                    await asyncio.sleep(max(5, SCAN_INTERVAL - elapsed))

                except KeyboardInterrupt:
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
