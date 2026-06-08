"""
bingx_client.py — Cliente BingX Perpetual Futures
Soporta: abrir LONG/SHORT, set leverage, SL/TP, cerrar posición
"""
import hashlib
import hmac
import time
import asyncio
import aiohttp
import logging
from urllib.parse import urlencode
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config as cfg

log = logging.getLogger("bingx")


def _sign(params: dict, secret: str) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _ts() -> int:
    return int(time.time() * 1000)


class BingXClient:
    def __init__(self):
        self.base   = cfg.BINGX_BASE_URL
        self.key    = cfg.BINGX_API_KEY
        self.secret = cfg.BINGX_SECRET_KEY
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _request(self, method: str, path: str, params: dict = None, signed: bool = True) -> dict:
        session = await self._get_session()
        params  = params or {}
        if signed:
            params["timestamp"] = _ts()
            params["signature"] = _sign(params, self.secret)
        headers = {"X-BX-APIKEY": self.key, "Content-Type": "application/json"}
        url = self.base + path
        try:
            if method == "GET":
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    return await r.json()
            else:
                async with session.post(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    return await r.json()
        except Exception as e:
            log.error(f"BingX request error {path}: {e}")
            return {"code": -1, "msg": str(e)}

    # ── MERCADO ──────────────────────────────────────────────────────────────

    async def get_contracts(self) -> list:
        """Retorna lista de todos los contratos perpetuos USDT"""
        r = await self._request("GET", "/openApi/swap/v2/quote/contracts", {}, signed=False)
        if r.get("code") == 0:
            return r.get("data", [])
        return []

    async def get_top_symbols_by_volume(self, n: int = 30) -> list[str]:
        """Top N símbolos por volumen 24h USDT"""
        r = await self._request("GET", "/openApi/swap/v2/quote/ticker", {}, signed=False)
        if r.get("code") != 0:
            return []
        tickers = r.get("data", [])
        # Filtrar solo USDT y excluir blacklist
        tickers = [
            t for t in tickers
            if t.get("symbol", "").endswith("-USDT")
            and t.get("symbol") not in cfg.BLACKLIST
        ]
        tickers.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        return [t["symbol"] for t in tickers[:n]]

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list:
        """Retorna velas OHLCV como lista de dicts"""
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        r = await self._request("GET", "/openApi/swap/v3/quote/klines", params, signed=False)
        if r.get("code") == 0:
            data = r.get("data", [])
            candles = []
            for c in data:
                candles.append({
                    "time":   int(c[0]),
                    "open":   float(c[1]),
                    "high":   float(c[2]),
                    "low":    float(c[3]),
                    "close":  float(c[4]),
                    "volume": float(c[5]),
                })
            return sorted(candles, key=lambda x: x["time"])
        return []

    async def get_balance(self) -> float:
        """Balance disponible en USDT"""
        r = await self._request("GET", "/openApi/swap/v2/user/balance", {})
        if r.get("code") == 0:
            assets = r.get("data", {}).get("balance", {})
            return float(assets.get("availableMargin", 0))
        return 0.0

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        params = {"symbol": symbol, "side": "LONG",  "leverage": leverage}
        r1 = await self._request("POST", "/openApi/swap/v2/trade/leverage", params)
        params["side"] = "SHORT"
        r2 = await self._request("POST", "/openApi/swap/v2/trade/leverage", params)
        ok = r1.get("code") == 0 and r2.get("code") == 0
        if not ok:
            log.warning(f"set_leverage {symbol} {leverage}x: {r1} {r2}")
        return ok

    async def get_position(self, symbol: str) -> Optional[dict]:
        """Retorna posición abierta en symbol o None"""
        r = await self._request("GET", "/openApi/swap/v2/user/positions", {"symbol": symbol})
        if r.get("code") == 0:
            for pos in r.get("data", []):
                if abs(float(pos.get("positionAmt", 0))) > 0:
                    return pos
        return None

    async def open_market_order(
        self,
        symbol:    str,
        side:      str,   # "BUY" | "SELL"
        qty:       float,
        sl_price:  float,
        tp1_price: float,
        tp2_price: float,
    ) -> dict:
        """Abre orden market + SL + TP en BingX One-Way (sin positionSide)"""
        await self.set_leverage(symbol, cfg.LEVERAGE)

        # ── Orden principal ──────────────────────────────────────────────────
        order_params = {
            "symbol":     symbol,
            "side":       side,
            "positionSide": "BOTH",      # One-Way mode
            "type":       "MARKET",
            "quantity":   round(qty, 4),
        }
        r = await self._request("POST", "/openApi/swap/v2/trade/order", order_params)
        if r.get("code") != 0:
            log.error(f"open_market_order {symbol} {side}: {r}")
            return r

        order_id = r.get("data", {}).get("order", {}).get("orderId", "")
        log.info(f"✅ Orden abierta {symbol} {side} qty={qty} id={order_id}")

        # ── SL ───────────────────────────────────────────────────────────────
        sl_side = "SELL" if side == "BUY" else "BUY"
        sl_params = {
            "symbol":        symbol,
            "side":          sl_side,
            "positionSide":  "BOTH",
            "type":          "STOP_MARKET",
            "quantity":      round(qty, 4),
            "stopPrice":     round(sl_price, 6),
            "workingType":   "MARK_PRICE",
        }
        await self._request("POST", "/openApi/swap/v2/trade/order", sl_params)

        # ── TP1 (50% posición) ───────────────────────────────────────────────
        tp1_params = {
            "symbol":       symbol,
            "side":         sl_side,
            "positionSide": "BOTH",
            "type":         "TAKE_PROFIT_MARKET",
            "quantity":     round(qty * 0.5, 4),
            "stopPrice":    round(tp1_price, 6),
            "workingType":  "MARK_PRICE",
        }
        await self._request("POST", "/openApi/swap/v2/trade/order", tp1_params)

        # ── TP2 (50% restante) ───────────────────────────────────────────────
        tp2_params = {
            "symbol":       symbol,
            "side":         sl_side,
            "positionSide": "BOTH",
            "type":         "TAKE_PROFIT_MARKET",
            "quantity":     round(qty * 0.5, 4),
            "stopPrice":    round(tp2_price, 6),
            "workingType":  "MARK_PRICE",
        }
        await self._request("POST", "/openApi/swap/v2/trade/order", tp2_params)

        return {"code": 0, "orderId": order_id}

    async def close_position(self, symbol: str) -> bool:
        pos = await self.get_position(symbol)
        if not pos:
            return False
        amt  = float(pos.get("positionAmt", 0))
        side = "SELL" if amt > 0 else "BUY"
        params = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": "BOTH",
            "type":         "MARKET",
            "quantity":     abs(amt),
        }
        r = await self._request("POST", "/openApi/swap/v2/trade/order", params)
        return r.get("code") == 0

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
