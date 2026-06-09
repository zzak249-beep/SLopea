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
    params["timestamp"] = _ts()
    params["signature"] = _sign(params)
    return params

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
        p = params or {}
        if signed:
            p = _signed_params(p)
        for attempt in range(3):
            try:
                async with session.get(f"{self.BASE}{path}", params=p) as r:
                    data = await r.json()
                    return data
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    async def _post(self, path: str, params: dict) -> dict:
        session = await self._get_session()
        p = _signed_params(params)
        for attempt in range(3):
            try:
                async with session.post(f"{self.BASE}{path}", params=p) as r:
                    data = await r.json()
                    return data
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    async def _delete(self, path: str, params: dict) -> dict:
        session = await self._get_session()
        p = _signed_params(params)
        for attempt in range(3):
            try:
                async with session.delete(f"{self.BASE}{path}", params=p) as r:
                    data = await r.json()
                    return data
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    # ── Mercado ───────────────────────────────────────────────────────────────

    async def get_all_symbols(self) -> list[str]:
        """Devuelve TODOS los pares USDT de perpetuos BingX con volumen mínimo.
        Maneja múltiples formatos de respuesta del endpoint de contratos.
        """
        data = await self._get("/openApi/swap/v2/quote/contracts")
        raw = data.get("data", [])

        # BingX a veces envuelve la lista en otro nivel
        if isinstance(raw, dict):
            raw = raw.get("contracts", raw.get("list", []))

        if not isinstance(raw, list) or len(raw) == 0:
            # Fallback: usar endpoint de tickers que siempre devuelve lista plana
            log.warning("get_all_symbols: contracts vacío, usando tickers fallback")
            ticker_data = await self._get("/openApi/swap/v2/quote/premiumIndex")
            raw = ticker_data.get("data", [])
            if not isinstance(raw, list):
                raw = []

        symbols = []
        vol_map = {}
        vol_detected = 0  # DEBUG: cuántos símbolos tienen volumen detectado

        for item in raw:
            if not isinstance(item, dict):
                continue
            # BingX usa 'symbol' en contratos y tickers
            sym = item.get("symbol", "")
            if not sym:
                continue
            # Normalizar: BTC-USDT o BTCUSDT → siempre BTC-USDT
            if "-" not in sym and sym.endswith("USDT"):
                sym = sym[:-4] + "-USDT"
            if not sym.endswith("-USDT"):
                continue
            if sym in C.BLACKLIST:
                continue
            # Filtro sintéticos
            base = sym.replace("-USDT", "")
            if any(base.startswith(p) for p in ("BEAR", "BULL", "PUMP", "NCS")):
                continue

            # ── FIX: campo de volumen ampliado ────────────────────────────────
            # BingX cambió nombres de campos entre versiones de la API.
            # Se prueba todos los nombres conocidos.
            vol_raw = (
                item.get("volume24h") or item.get("vol24h") or
                item.get("quoteVolume") or item.get("turnover24h") or
                item.get("tradeAmt") or item.get("quoteVol") or
                item.get("volValue") or item.get("amount") or
                item.get("lastTradedVolume") or item.get("vol") or
                item.get("quantity24h") or 0
            )
            vol = float(vol_raw) if vol_raw else 0.0
            if vol > 0:
                vol_detected += 1
            vol_map[sym] = vol

            # ── FIX CRÍTICO: solo filtrar cuando el volumen ES CONOCIDO ───────
            # Si vol=0 significa que no detectamos el campo → incluir símbolo
            # Si vol>0 y está bajo el mínimo → excluir
            if C.MIN_VOLUME_USDT > 0 and vol > 0 and vol < C.MIN_VOLUME_USDT:
                continue

            symbols.append(sym)

        # Ordenar por volumen descendente (los sin volumen van al final)
        symbols.sort(key=lambda s: vol_map.get(s, 0), reverse=True)

        if C.TOP_N_SYMBOLS > 0:
            symbols = symbols[: C.TOP_N_SYMBOLS]

        log.info(
            "get_all_symbols: %d símbolos válidos (raw=%d, con_vol=%d)",
            len(symbols), len(raw), vol_detected,
        )
        # Si vol_detected=0 significa que el campo de volumen no se detectó —
        # el bot sigue funcionando pero el filtro MIN_VOLUME_USDT no aplica.
        if vol_detected == 0 and len(raw) > 0:
            log.warning(
                "⚠️  Volumen no detectado en ningún símbolo — "
                "MIN_VOLUME_USDT ignorado. Revisar campos del endpoint."
            )
        return symbols

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[list]:
        """
        Retorna lista de velas: [open_time, open, high, low, close, volume, ...]
        """
        data = await self._get(
            "/openApi/swap/v3/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        raw = data.get("data", [])
        if not raw:
            return []
        result = []
        for c in raw:
            try:
                result.append([
                    int(c["time"]),
                    float(c["open"]),
                    float(c["high"]),
                    float(c["low"]),
                    float(c["close"]),
                    float(c["volume"]),
                ])
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
        """Retorna balance disponible en USDT."""
        data = await self._get(
            "/openApi/swap/v3/user/balance",
            {"currency": "USDT"},
            signed=True,
        )
        raw = data.get("data", {})

        if isinstance(raw, list):
            for a in raw:
                if a.get("asset", "") == "USDT":
                    return float(a.get("availableMargin", 0) or 0)
            for a in raw:
                v = a.get("availableMargin")
                if v is not None:
                    return float(v or 0)
            return 0.0

        if isinstance(raw, dict):
            bal = raw.get("balance", raw)
            if isinstance(bal, list):
                for a in bal:
                    if a.get("asset", "") == "USDT":
                        return float(a.get("availableMargin", 0) or 0)
            if isinstance(bal, dict):
                return float(bal.get("availableMargin", 0) or 0)
            try:
                return float(bal)
            except Exception:
                pass

        log.warning("get_balance: formato no reconocido %s", str(data)[:200])
        return 0.0

    # ── Posiciones abiertas ────────────────────────────────────────────────────

    async def get_open_positions(self) -> list[dict]:
        data = await self._get(
            "/openApi/swap/v2/user/positions",
            {},
            signed=True,
        )
        positions = data.get("data", [])
        if not isinstance(positions, list):
            return []
        return [p for p in positions if float(p.get("positionAmt", 0)) != 0]

    async def get_open_orders(self, symbol: str) -> list[dict]:
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
        side: str,
        quantity: float,
        position_side: str = "LONG",
    ) -> dict:
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
        order_type: str = "STOP_MARKET",
    ) -> dict:
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
        position_side: str,
    ) -> dict:
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
        direction: str,
        quantity: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
    ) -> dict:
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
        sl_resp = await self.place_stop_market_order(
            symbol, side_close, quantity, sl_price, direction,
            close_position=True, order_type="STOP_MARKET",
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
