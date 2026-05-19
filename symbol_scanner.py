"""
SAMA APEX Bot - Symbol Scanner
Carga automáticamente todos los pares perpetuos de BingX
filtrando por volumen mínimo 24h para quedarnos con los más líquidos.
Se refresca cada 6 horas para capturar nuevos listings.
"""
import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from config import BINGX_BASE_URL, MIN_VOLUME_USDT, MAX_SYMBOLS_SCAN

logger = logging.getLogger(__name__)

# Blacklist: pares problemáticos o muy volátiles sin liquidez real
BLACKLIST = {
    "BTC-USDT-OLD", "LUNA-USDT", "LUNC-USDT",
}

_cached_symbols: list = []
_last_refresh: datetime | None = None
_REFRESH_HOURS = 6


async def fetch_all_symbols(session: aiohttp.ClientSession) -> list[str]:
    """
    Consulta /openApi/swap/v2/quote/contracts y /openApi/swap/v2/quote/ticker
    Filtra por volumen 24h y retorna lista ordenada de mayor a menor volumen.
    """
    global _cached_symbols, _last_refresh

    # Usar caché si tiene menos de 6h
    now = datetime.now(timezone.utc)
    if _cached_symbols and _last_refresh:
        hours_old = (now - _last_refresh).seconds / 3600
        if hours_old < _REFRESH_HOURS:
            logger.debug(f"Usando caché de símbolos ({len(_cached_symbols)} pares)")
            return _cached_symbols

    logger.info("🔍 Cargando todos los pares perpetuos de BingX...")

    try:
        # 1. Obtener todos los contratos activos
        async with session.get(
            f"{BINGX_BASE_URL}/openApi/swap/v2/quote/contracts",
            timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            contracts_data = await r.json()

        all_symbols = set()
        for c in contracts_data.get("data", []):
            sym = c.get("symbol", "")
            if sym.endswith("-USDT") and sym not in BLACKLIST:
                all_symbols.add(sym)

        logger.info(f"📋 {len(all_symbols)} contratos encontrados en BingX")

        # 2. Obtener tickers con volumen 24h para filtrar
        async with session.get(
            f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker",
            timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            tickers_data = await r.json()

        # Construir ranking por volumen
        volume_map: dict[str, float] = {}
        tickers = tickers_data.get("data") or []

        # Puede ser lista o dict
        if isinstance(tickers, dict):
            tickers = [tickers]

        for t in tickers:
            sym = t.get("symbol", "")
            if sym not in all_symbols:
                continue
            try:
                # quoteVolume = volumen en USDT
                vol = float(t.get("quoteVolume", t.get("volume", 0)) or 0)
            except Exception:
                vol = 0.0
            if vol >= MIN_VOLUME_USDT:
                volume_map[sym] = vol

        # Ordenar por volumen descendente y limitar
        ranked = sorted(volume_map.items(), key=lambda x: x[1], reverse=True)
        result = [sym for sym, _ in ranked[:MAX_SYMBOLS_SCAN]]

        if not result:
            # Fallback: si falla el filtro de volumen, usar top pares conocidos
            logger.warning("⚠️ Filtro de volumen retornó 0 pares — usando fallback")
            result = [s for s in all_symbols if s in {
                "BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
                "DOGE-USDT","ADA-USDT","AVAX-USDT","LINK-USDT","DOT-USDT",
                "MATIC-USDT","UNI-USDT","ATOM-USDT","LTC-USDT","ETC-USDT",
            }]

        _cached_symbols = result
        _last_refresh   = now

        logger.info(
            f"✅ {len(result)} pares seleccionados (vol≥{MIN_VOLUME_USDT/1e6:.0f}M USDT) | "
            f"Top 5: {', '.join(result[:5])}"
        )
        return result

    except Exception as e:
        logger.error(f"Error cargando símbolos: {e}")
        if _cached_symbols:
            logger.warning("Usando caché anterior")
            return _cached_symbols
        # Fallback duro
        return ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT",
                "DOGE-USDT","ADA-USDT","AVAX-USDT","LINK-USDT","DOT-USDT"]
