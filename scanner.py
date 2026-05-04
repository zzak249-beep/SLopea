import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import TIMEFRAME, CANDLES_NEEDED, MIN_VOLUME_USDT, MAX_WORKERS
import bingx_client as api
import strategy

logger = logging.getLogger(__name__)


def _process_symbol(symbol: str) -> tuple[str, dict] | None:
    """Fetch candles and compute signal for one symbol."""
    try:
        candles = api.get_klines(symbol, TIMEFRAME, CANDLES_NEEDED)
        if len(candles) < 30:
            return None
        sig = strategy.analyze(candles)
        if sig["signal"] != "NONE":
            return (symbol, sig)
        return None
    except Exception as e:
        logger.debug(f"Error processing {symbol}: {e}")
        return None


def _filter_by_volume(symbols: list[str]) -> list[str]:
    """Keep only symbols with 24h volume >= MIN_VOLUME_USDT."""
    filtered = []
    for sym in symbols:
        try:
            ticker = api.get_ticker_24h(sym)
            vol = float(ticker.get("quoteVolume", ticker.get("volume", 0)))
            if vol >= MIN_VOLUME_USDT:
                filtered.append(sym)
        except Exception:
            pass
    logger.info(f"Volume filter: {len(filtered)}/{len(symbols)} passed (>{MIN_VOLUME_USDT:,.0f} USDT)")
    return filtered


def scan_all(symbols: list[str]) -> list[tuple[str, dict]]:
    """
    Scan all symbols in parallel.
    Returns list of (symbol, signal_dict) where signal != NONE.
    """
    signals = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_process_symbol, sym): sym for sym in symbols}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                signals.append(result)
    logger.info(f"Scan complete: {len(signals)} signals from {len(symbols)} symbols")
    return signals


def load_universe() -> list[str]:
    """Load + filter the full BingX perps universe."""
    logger.info("Loading BingX symbol universe...")
    all_syms = api.get_all_symbols()
    logger.info(f"Total perps: {len(all_syms)}")
    filtered = _filter_by_volume(all_syms)
    return filtered
