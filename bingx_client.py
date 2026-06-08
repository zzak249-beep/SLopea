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

def _sign(params: dict) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(
        C.BINGX_SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def _signed_params(params: dict) -> dict:
    p = dict(params)   # copia para no mutar el original
    p["timestamp"] = _ts()
    p["signature"] = _sign(p)
    return p

# ── Cliente base ─────────────────────────────────────────────────────────────

class BingXClient:
    BASE = C.BINGX_BASE_URL

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "X-BX-APIKEY": C.BINGX_API_KEY,
                    "Content-Type": "application/json",
                },
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
                p = _signed_params(base_params) if signed else dict(base_params)
                async with session.get(f"{self.BASE}{path}", params=p) as r:
                    data = await r.json(content_type=None)
                    if data.get("code", 0) not in (0, None) and data.get("code", 0) != 0:
                        log.debug("GET %s → code=%s msg=%s", path, data.get("code"), data.get("msg", ""))
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
                p = _signed_params(params)
                async with session.post(f"{self.BASE}{path}", params=p) as r:
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
                p = _signed_params(params)
                async with session.delete(f"{self.BASE}{path}", params=p) as r:
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
        """Devuelve TODOS los pares USDT de perpetuos BingX con volumen mínimo."""
        data = await self._get("/openApi/swap/v2/quote/contracts")
        log.debug("get_all_symbols raw keys: %s", list(data.keys()) if isinstance(data, dict) else type(data))

        # La API devuelve {"code":0, "data": [...]} o a veces la lista directamente
        raw = data.get("data", [])
        if isinstance(raw, dict):
            # Algunos endpoints envuelven en otro nivel
            raw = raw.get("contracts", raw.get("list", []))
        if not isinstance(raw, list):
            log.warning("get_all_symbols: formato inesperado data=%s", str(data)[:200])
            raw = []

        symbols = []
        vol_map: dict[str, float] = {}

        for item in raw:
            sym = item.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            if sym in C.BLACKLIST:
                continue

            # BingX usa distintos nombres según el endpoint: volume, volume24h, quoteVolume
            vol = float(
                item.get("volume", 0) or
                item.get("volume24h", 0) or
                item.get("quoteVolume", 0) or 0
            )
            vol_map[sym] = vol

            if C.MIN_VOLUME_USDT > 0 and vol < C.MIN_VOLUME_USDT:
                continue
            symbols.append(sym)

        log.info("get_all_symbols: %d contratos raw, %d pasan filtro volumen", len(raw), len(symbols))

        # Si TOP_N_SYMBOLS > 0 limita lista ordenando por volumen descendente
        if C.TOP_N_SYMBOLS > 0:
            symbols.sort(key=lambda s: vol_map.get(s, 0), reverse=True)
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
            payload = data.get("data", {})

            # Formato 1: data.balance es una lista de dicts por asset
            balance_field = payload.get("balance", payload)
            if isinstance(balance_field, list):
                for a in balance_field:
                    if isinstance(a, dict) and a.get("asset", "") == "USDT":
                        return float(a.get("availableMargin", 0))

            # Formato 2: data.balance es un dict único (un solo asset)
            if isinstance(balance_field, dict):
                if balance_field.get("asset", "") == "USDT":
                    return float(balance_field.get("availableMargin", 0))
                # Puede ser directamente el nivel de data
                if "availableMargin" in balance_field:
                    return float(balance_field["availableMargin"])

            # Formato 3: data es lista directamente
            if isinstance(payload, list):
                for a in payload:
                    if isinstance(a, dict) and a.get("asset", "") == "USDT":
                        return float(a.get("availableMargin", 0))

            log.warning("get_balance: formato inesperado %s", str(data)[:300])
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
