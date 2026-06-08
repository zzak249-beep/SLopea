"""
QF×JP Bot v6.3 — BingX Client
Maneja: klines, símbolos, órdenes MARKET, SL/TP, cierre de posiciones,
consulta de posiciones abiertas y cancelación de órdenes pendientes.
"""
import hmac
import hashlib
import time
import asyncio
import logging
from urllib.parse import urlencode
from typing import Optional

import aiohttp

import config as C

log = logging.getLogger("bingx")

# ── Helpers de firma ─────────────────────────────────────────────────────────

def _ts() -> str:
    return str(int(time.time() * 1000))

def _sign(query_string: str) -> str:
    """HMAC-SHA256 sobre el query string ya construido (sin signature)."""
    return hmac.new(
        C.BINGX_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def _build_query(params: dict) -> str:
    """Construye query string en orden de inserción (sin ordenar)."""
    return urlencode(params)

def _signed_query(params: dict) -> str:
    """
    Devuelve el query string completo incluyendo signature al final.
    BingX firma los params en orden de inserción y añade &signature=xxx al final.
    """
    p = dict(params)
    p["timestamp"] = _ts()
    qs = _build_query(p)
    sig = _sign(qs)
    return f"{qs}&signature={sig}"

# ── Cliente base ─────────────────────────────────────────────────────────────

class BingXClient:
    BASE = C.BINGX_BASE_URL

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-BX-APIKEY": C.BINGX_API_KEY},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None, signed: bool = False) -> dict:
        session = await self._get_session()
        base_params = params or {}
        for attempt in range(3):
            try:
                if signed:
                    qs = _signed_query(base_params)
                    url = f"{self.BASE}{path}?{qs}"
                else:
                    url = f"{self.BASE}{path}"
                    if base_params:
                        url += "?" + _build_query(base_params)
                async with session.get(url) as r:
                    data = await r.json(content_type=None)
                    return data
            except Exception as e:
                if attempt == 2:
                    log.error("GET %s falló tras 3 intentos: %s", path, e)
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    async def _post(self, path: str, params: dict) -> dict:
        session = await self._get_session()
        for attempt in range(3):
            try:
                qs = _signed_query(params)
                url = f"{self.BASE}{path}?{qs}"
                # BingX: params van en query string, body vacío
                async with session.post(url, data="") as r:
                    data = await r.json(content_type=None)
                    return data
            except Exception as e:
                if attempt == 2:
                    log.error("POST %s falló tras 3 intentos: %s", path, e)
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    async def _delete(self, path: str, params: dict) -> dict:
        session = await self._get_session()
        for attempt in range(3):
            try:
                qs = _signed_query(params)
                url = f"{self.BASE}{path}?{qs}"
                async with session.delete(url) as r:
                    data = await r.json(content_type=None)
                    return data
            except Exception as e:
                if attempt == 2:
                    log.error("DELETE %s falló tras 3 intentos: %s", path, e)
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    # ── Mercado ───────────────────────────────────────────────────────────────

    async def get_all_symbols(self) -> list[str]:
        """
        Devuelve pares USDT de perpetuos BingX que superen MIN_VOLUME_USDT.
        Usa /ticker (todos) para obtener volumen real en USDT.
        """
        # ── Paso 1: obtener tickers con volumen 24h ───────────────────────────
        ticker_data = await self._get("/openApi/swap/v2/quote/ticker")
        tickers_raw = ticker_data.get("data", [])
        if isinstance(tickers_raw, dict):
            tickers_raw = tickers_raw.get("tickers", tickers_raw.get("list", []))

        # Construir mapa symbol → volumen USDT 24h
        # BingX ticker fields: symbol, quoteVolume (USDT), volume (base coin)
        vol_map: dict[str, float] = {}
        for t in (tickers_raw if isinstance(tickers_raw, list) else []):
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            # quoteVolume es en USDT, volume es en moneda base
            vol = float(
                t.get("quoteVolume", 0) or
                t.get("volume", 0) or 0
            )
            vol_map[sym] = vol

        log.debug("get_all_symbols: %d tickers con volumen USDT", len(vol_map))

        # ── Paso 2: filtrar por volumen y blacklist ───────────────────────────
        symbols = []
        for sym, vol in vol_map.items():
            if sym in C.BLACKLIST:
                continue
            if C.MIN_VOLUME_USDT > 0 and vol < C.MIN_VOLUME_USDT:
                continue
            symbols.append(sym)

        log.info(
            "get_all_symbols: %d contratos raw, %d pasan filtro volumen (min=%.0f USDT)",
            len(vol_map), len(symbols), C.MIN_VOLUME_USDT,
        )

        # ── Paso 3: ordenar y limitar ─────────────────────────────────────────
        symbols.sort(key=lambda s: vol_map.get(s, 0), reverse=True)
        if C.TOP_N_SYMBOLS > 0:
            symbols = symbols[: C.TOP_N_SYMBOLS]

        return symbols

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[list]:
        """
        Retorna lista de velas: [open_time, open, high, low, close, volume, ...]
        Compatible con BingX swap/v3/quote/klines y swap/v2/quote/klines.
        """
        data = await self._get(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        raw = data.get("data", [])

        # Algunos endpoints devuelven {"data": {"klines": [...]}}
        if isinstance(raw, dict):
            raw = raw.get("klines", raw.get("data", []))

        if not raw or not isinstance(raw, list):
            return []

        result = []
        for c in raw:
            try:
                # Formato objeto: {"time":..., "open":..., "high":..., "low":..., "close":..., "volume":...}
                if isinstance(c, dict):
                    result.append([
                        int(c.get("time", c.get("openTime", 0))),
                        float(c.get("open", c.get("o", 0))),
                        float(c.get("high", c.get("h", 0))),
                        float(c.get("low", c.get("l", 0))),
                        float(c.get("close", c.get("c", 0))),
                        float(c.get("volume", c.get("v", 0))),
                    ])
                # Formato array: [time, open, high, low, close, volume]
                elif isinstance(c, (list, tuple)) and len(c) >= 6:
                    result.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
            except Exception:
                continue
        return sorted(result, key=lambda x: x[0])

    async def get_ticker(self, symbol: str) -> dict:
        data = await self._get(
            "/openApi/swap/v2/quote/ticker",
            {"symbol": symbol},
        )
        return data.get("data", {})

    # ── Cuenta ────────────────────────────────────────────────────────────────

    async def get_balance(self) -> float:
        """Retorna balance disponible en USDT (availableMargin)."""
        data = await self._get(
            "/openApi/swap/v3/user/balance",
            {"currency": "USDT"},
            signed=True,
        )
        try:
            # Estructura real de BingX:
            # {"code":0, "msg":"", "data": [{"userId":"...", "asset":"USDT",
            #   "balance":"254.77", "availableMargin":"0.42", ...}, ...]}
            payload = data.get("data", data)   # si no hay "data" usa el root

            # Caso 1: data es una lista de assets directamente
            if isinstance(payload, list):
                for a in payload:
                    if isinstance(a, dict) and a.get("asset", "") == "USDT":
                        return float(a.get("availableMargin", 0))
                # Si no hay asset USDT explícito, devuelve el primero disponible
                for a in payload:
                    if isinstance(a, dict) and "availableMargin" in a:
                        return float(a["availableMargin"])

            # Caso 2: data es un dict con clave "balance" que es lista
            if isinstance(payload, dict):
                bal = payload.get("balance", payload)
                if isinstance(bal, list):
                    for a in bal:
                        if isinstance(a, dict) and a.get("asset", "") == "USDT":
                            return float(a.get("availableMargin", 0))
                elif isinstance(bal, dict) and "availableMargin" in bal:
                    return float(bal["availableMargin"])
                elif "availableMargin" in payload:
                    return float(payload["availableMargin"])

            log.warning("get_balance: no se encontró USDT en payload=%s", str(payload)[:300])
            return 0.0
        except Exception as e:
            log.warning("get_balance error: %s | data=%s", e, str(data)[:300])
            return 0.0

    # ── Posiciones abiertas ────────────────────────────────────────────────────

    async def get_open_positions(self) -> list[dict]:
        """Lista de posiciones abiertas en BingX Perpetual."""
        data = await self._get(
            "/openApi/swap/v2/user/positions",
            None,
            signed=True,
        )
        positions = data.get("data", [])
        if not isinstance(positions, list):
            return []
        return [p for p in positions if float(p.get("positionAmt", 0)) != 0]

    async def get_open_orders(self, symbol: str) -> list[dict]:
        """Órdenes pendientes (SL/TP stop-market) para un símbolo."""
        data = await self._get(
            "/openApi/swap/v2/trade/openOrders",
            {"symbol": symbol},
            signed=True,
        )
        return data.get("data", {}).get("orders", [])

    # ── Apalancamiento ────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int, side: str = "LONG") -> bool:
        data = await self._post(
            "/openApi/swap/v2/trade/leverage",
            {"symbol": symbol, "side": side, "leverage": leverage},
        )
        return data.get("code", -1) == 0

    # ── Órdenes ───────────────────────────────────────────────────────────────

    async def place_market_order(
        self,
        symbol: str,
        side: str,        # BUY | SELL
        quantity: float,
        position_side: str = "LONG",  # LONG | SHORT
    ) -> dict:
        """Abre posición con orden MARKET."""
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": str(quantity),
        }
        data = await self._post("/openApi/swap/v2/trade/order", params)
        return data

    async def place_stop_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        position_side: str = "LONG",
        close_position: bool = True,
        order_type: str = "STOP_MARKET",  # STOP_MARKET | TAKE_PROFIT_MARKET
    ) -> dict:
        """Coloca SL o TP tipo stop-market."""
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": order_type,
            "stopPrice": str(round(stop_price, 8)),
            "closePosition": "true" if close_position else "false",
            "quantity": "0" if close_position else str(quantity),
            "workingType": "MARK_PRICE",
            "priceProtect": "true",
        }
        data = await self._post("/openApi/swap/v2/trade/order", params)
        return data

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        data = await self._delete(
            "/openApi/swap/v2/trade/order",
            {"symbol": symbol, "orderId": order_id},
        )
        return data

    async def cancel_all_orders(self, symbol: str) -> dict:
        data = await self._delete(
            "/openApi/swap/v2/trade/allOpenOrders",
            {"symbol": symbol},
        )
        return data

    async def close_position_market(
        self,
        symbol: str,
        quantity: float,
        position_side: str,  # LONG | SHORT
    ) -> dict:
        """Cierra posición completamente con orden MARKET."""
        side = "SELL" if position_side == "LONG" else "BUY"
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": str(quantity),
        }
        data = await self._post("/openApi/swap/v2/trade/order", params)
        return data

    # ── Helper completo: abrir trade con SL + TP1 + TP2 ─────────────────────

    async def open_trade(
        self,
        symbol: str,
        direction: str,      # LONG | SHORT
        quantity: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
    ) -> dict:
        """
        Secuencia completa:
        1. Set leverage
        2. Orden MARKET de entrada
        3. SL stop-market (cierra todo)
        4. TP1 stop-market (50% qty)
        5. TP2 stop-market (50% qty)
        Retorna dict con resultados de cada paso.
        """
        side_entry = "BUY" if direction == "LONG" else "SELL"
        side_close = "SELL" if direction == "LONG" else "BUY"

        results = {}

        # 1. Leverage
        await self.set_leverage(symbol, C.LEVERAGE, direction)

        # 2. Entrada
        entry_resp = await self.place_market_order(symbol, side_entry, quantity, direction)
        results["entry"] = entry_resp
        if entry_resp.get("code", -1) != 0:
            log.error("[%s] Entrada fallida: %s", symbol, entry_resp)
            return results

        await asyncio.sleep(0.5)

        # 3. SL (cierra toda la posición)
        sl_type = "STOP_MARKET"
        sl_resp = await self.place_stop_market_order(
            symbol, side_close, quantity, sl_price, direction,
            close_position=True, order_type=sl_type,
        )
        results["sl"] = sl_resp

        # 4. TP1 (50% de la qty)
        qty_half = round(quantity / 2, 8)
        tp1_resp = await self.place_stop_market_order(
            symbol, side_close, qty_half, tp1_price, direction,
            close_position=False, order_type="TAKE_PROFIT_MARKET",
        )
        results["tp1"] = tp1_resp

        # 5. TP2 (50% de la qty)
        tp2_resp = await self.place_stop_market_order(
            symbol, side_close, qty_half, tp2_price, direction,
            close_position=False, order_type="TAKE_PROFIT_MARKET",
        )
        results["tp2"] = tp2_resp

        return results
