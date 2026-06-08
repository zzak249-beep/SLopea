"""
scanner.py — Escanea todas las monedas de BingX cada 3m
Calcula QF×JP v3.5, filtra por TL Ruptura + Score, abre trades
"""
import asyncio
import logging
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config as cfg
from bingx_client   import BingXClient
from indicators     import compute_score, ScoreResult
from risk_manager   import RiskManager
import telegram_client as tg

log = logging.getLogger("scanner")


class Scanner:
    def __init__(self):
        self.bingx   = BingXClient()
        self.risk    = RiskManager()
        self._running = False

    async def fetch_candles_multi(self, symbol: str) -> tuple[list, list, list, list]:
        """Carga candles 3m + 15m + 1h + 4h en paralelo"""
        tasks = [
            self.bingx.get_klines(symbol, cfg.TIMEFRAME,    200),
            self.bingx.get_klines(symbol, cfg.HTF_TIMEFRAME, 100),
            self.bingx.get_klines(symbol, cfg.HTF2_TIMEFRAME, 60),
            self.bingx.get_klines(symbol, cfg.HTF5_TIMEFRAME, 50),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return tuple(r if isinstance(r, list) else [] for r in results)

    async def scan_symbol(self, symbol: str) -> Optional[ScoreResult]:
        """Escanea un símbolo y retorna ScoreResult si hay señal, sino None"""
        try:
            c3, c15, c1h, c4h = await self.fetch_candles_multi(symbol)
            if len(c3) < 50:
                return None

            result = compute_score(c3, c15, c1h, c4h)

            if result.tier == "NONE" or result.direction == "NONE":
                return None

            return result

        except Exception as e:
            log.error(f"scan_symbol {symbol}: {e}", exc_info=True)
            return None

    async def process_signal(self, symbol: str, result: ScoreResult):
        """Envía señal a Telegram y opcionalmente abre trade en BingX"""
        log.info(f"🎯 SEÑAL {symbol} {result.direction} {result.tier} score={result.score:.0f}")

        # Siempre notificar
        await tg.notify_signal(symbol, result, cfg.MODE)

        if cfg.MODE != "LIVE":
            log.info(f"Modo SIGNAL: no se abre trade en {symbol}")
            return

        # Check riesgo
        can, reason = self.risk.can_trade(symbol)
        if not can:
            log.warning(f"Riesgo bloqueó {symbol}: {reason}")
            return

        # Calcular qty Kelly
        balance = await self.bingx.get_balance()
        capital = min(balance, cfg.CAPITAL)
        qty     = self.risk.position_size(
            capital = capital,
            entry   = result.entry_price,
            sl      = result.sl_price,
            tier    = result.tier,
            score   = result.score,
        )

        side = "BUY" if result.direction == "LONG" else "SELL"
        r = await self.bingx.open_market_order(
            symbol    = symbol,
            side      = side,
            qty       = qty,
            sl_price  = result.sl_price,
            tp1_price = result.tp1_price,
            tp2_price = result.tp2_price,
        )

        if r.get("code") == 0:
            self.risk.add_position(symbol)
            await tg.notify_trade_opened(
                symbol    = symbol,
                direction = result.direction,
                qty       = qty,
                entry     = result.entry_price,
                sl        = result.sl_price,
                tp1       = result.tp1_price,
                tp2       = result.tp2_price,
                tier      = result.tier,
            )
        else:
            log.error(f"Error abriendo trade {symbol}: {r}")
            await tg.notify_error(symbol, str(r))

    async def run_scan_cycle(self):
        """Un ciclo completo de escaneo de todos los símbolos"""
        symbols = await self.bingx.get_top_symbols_by_volume(cfg.TOP_N_SYMBOLS)
        log.info(f"Escaneando {len(symbols)} símbolos...")

        # Escanear en batches de 10 para no saturar la API
        batch_size = 10
        signals    = 0

        for i in range(0, len(symbols), batch_size):
            batch   = symbols[i:i + batch_size]
            tasks   = [self.scan_symbol(sym) for sym in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for sym, res in zip(batch, results):
                if isinstance(res, ScoreResult) and res.tier != "NONE":
                    signals += 1
                    await self.process_signal(sym, res)

            # Pequeña pausa entre batches
            await asyncio.sleep(0.5)

        log.info(f"Ciclo completado: {len(symbols)} escaneados, {signals} señales")

        # Resumen cada 10 ciclos
        if not hasattr(self, "_cycle_count"):
            self._cycle_count = 0
        self._cycle_count += 1
        if self._cycle_count % 10 == 0:
            summary = self.risk.daily_summary()
            summary["scanned"] = len(symbols)
            summary["signals"] = signals
            await tg.notify_summary(summary)

    async def start(self):
        self._running = True
        log.info(f"Scanner iniciado — intervalo {cfg.SCAN_INTERVAL}s — modo {cfg.MODE}")
        await tg.send_message(
            f"🤖 <b>QF×JP Bot iniciado</b>\n"
            f"Modo: {cfg.MODE}\nIntervalo: {cfg.SCAN_INTERVAL}s\n"
            f"Top {cfg.TOP_N_SYMBOLS} símbolos | Min tier: {cfg.MIN_TIER}"
        )

        while self._running:
            try:
                await self.run_scan_cycle()
            except Exception as e:
                log.error(f"run_scan_cycle error: {e}", exc_info=True)
                await tg.notify_error("SCANNER", str(e))
            await asyncio.sleep(cfg.SCAN_INTERVAL)

    async def stop(self):
        self._running = False
        await self.bingx.close()
