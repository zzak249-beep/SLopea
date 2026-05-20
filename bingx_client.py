"""
SAMA APEX Bot - BingX Client FIXED v3
FIX CRÍTICO: Signature mismatch
  - Query string construido manualmente ANTES de firmar
  - La misma cadena que se firma es la que se envía (sin reordenar)
  - Compatible con BingX Swap v2/v3
"""
import hmac
import hashlib
import time
import math
import logging
import pandas as pd
import aiohttp
from urllib.parse import urlencode
from config import BINGX_API_KEY, BINGX_SECRET_KEY, BINGX_BASE_URL, LEVERAGE

logger = logging.getLogger(__name__)

TF_MAP = {
    "1m":"1m","3m":"3m","5m":"5m","15m":"15m",
    "30m":"30m","1h":"1h","2h":"2h","4h":"4h",
    "6h":"6h","12h":"12h","1d":"1d",
}

_contracts_cache: dict = {}


def _ts() -> int:
    return int(time.time() * 1000)


def _build_qs(params: dict) -> str:
    """Construye query string en el MISMO orden que se enviará."""
    return "&".join(f"{k}={v}" for k, v in params.items())


def _sign(query_string: str) -> str:
    """Firma la cadena exacta que se envía."""
    return hmac.new(
        BINGX_SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


class BingXClient:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict = None, auth: bool = False) -> dict:
        params = dict(params or {})
        if auth:
            params["timestamp"] = _ts()
            qs  = _build_qs(params)
            sig = _sign(qs)
            url = f"{BINGX_BASE_URL}{path}?{qs}&signature={sig}"
        else:
            qs  = _build_qs(params)
            url = f"{BINGX_BASE_URL}{path}?{qs}" if qs else f"{BINGX_BASE_URL}{path}"

        headers = {"X-BX-APIKEY": BINGX_API_KEY} if auth else {}
        try:
            async with self.session.get(url, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json()
                if data.get("code") != 0:
                    logger.warning(f"GET {path} code={data.get('code')} msg={data.get('msg')}")
                return data
        except Exception as e:
            logger.error(f"GET {path} error: {e}")
            return {"code": -1, "msg": str(e)}

    async def _post(self, path: str, params: dict = None) -> dict:
        params = dict(params or {})
        params["timestamp"] = _ts()
        qs  = _build_qs(params)
        sig = _sign(qs)
        url = f"{BINGX_BASE_URL}{path}?{qs}&signature={sig}"
        headers = {"X-BX-APIKEY": BINGX_API_KEY}
        try:
            async with self.session.post(url, headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json()
                if data.get("code") != 0:
                    logger.error(f"POST {path} FAILED code={data.get('code')} msg={data.get('msg')}")
                else:
                    logger.debug(f"POST {path} OK")
                return data
        except Exception as e:
            logger.error(f"POST {path} error: {e}")
            return {"code": -1, "msg": str(e)}

    async def _delete(self, path: str, params: dict = None) -> dict:
        params = dict(params or {})
        params["timestamp"] = _ts()
        qs  = _build_qs(params)
        sig = _sign(qs)
        url = f"{BINGX_BASE_URL}{path}?{qs}&signature={sig}"
        headers = {"X-BX-APIKEY": BINGX_API_KEY}
        try:
            async with self.session.delete(url, headers=headers,
                                           timeout=aiohttp.ClientTimeout(total=12)) as r:
                return await r.json()
        except Exception as e:
            logger.error(f"DELETE {path} error: {e}")
            return {"code": -1}

    # ── Market Data ───────────────────────────────────────────────────────────

    async def get_klines(self, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
        tf   = TF_MAP.get(interval, interval)
        data = await self._get("/openApi/swap/v3/quote/klines",
                               {"symbol": symbol, "interval": tf, "limit": min(limit, 1440)})
        rows = data.get("data") or []
        if not rows:
            return pd.DataFrame()
        result = []
        for c in rows:
            try:
                result.append({
                    "time":   int(c.get("time",   c.get("t", 0))),
                    "open":   float(c.get("open",  c.get("o", 0))),
                    "high":   float(c.get("high",  c.get("h", 0))),
                    "low":    float(c.get("low",   c.get("l", 0))),
                    "close":  float(c.get("close", c.get("c", 0))),
                    "volume": float(c.get("volume",c.get("v", 0))),
                })
            except Exception:
                continue
        return pd.DataFrame(result).sort_values("time").reset_index(drop=True)

    async def get_ticker(self, symbol: str) -> dict:
        data = await self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return data.get("data") or {}

    async def get_funding_rate(self, symbol: str) -> float:
        data = await self._get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
        try:
            return float(data["data"]["lastFundingRate"])
        except Exception:
            return 0.0

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_balance(self) -> float:
        data = await self._get("/openApi/swap/v2/user/balance", {}, auth=True)
        logger.debug(f"balance raw: {str(data)[:200]}")
        try:
            inner = data.get("data", {})
            b = inner.get("balance", inner)
            if isinstance(b, list):
                for a in b:
                    if str(a.get("asset","")).upper() == "USDT":
                        b = a
                        break
            for key in ("availableMargin", "equity", "balance"):
                val = b.get(key) if isinstance(b, dict) else None
                if val is not None:
                    v = float(val)
                    if v > 0:
                        logger.info(f"💰 Balance: {v:.2f} USDT")
                        return v
        except Exception as e:
            logger.error(f"balance parse error: {e} raw={data}")
        return 0.0

    async def get_positions(self, symbol: str = None) -> list:
        params = {"symbol": symbol} if symbol else {}
        data = await self._get("/openApi/swap/v2/user/positions", params, auth=True)
        pos = data.get("data") or []
        return [p for p in pos if abs(float(p.get("positionAmt", 0))) > 0]

    async def get_position_mode(self) -> str:
        data = await self._get("/openApi/swap/v1/positionSide/dual", {}, auth=True)
        try:
            dual = data["data"]["dualSidePosition"]
            mode = "HEDGE" if dual else "ONE_WAY"
            logger.info(f"📋 Modo: {mode}")
            return mode
        except Exception:
            logger.warning("No se pudo detectar modo — usando ONE_WAY")
            return "ONE_WAY"

    # ── Trading ───────────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        for side in ["LONG", "SHORT"]:
            res = await self._post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": side, "leverage": leverage,
            })
            if res.get("code") not in (0, 200):
                logger.warning(f"leverage {symbol} {side}: {res.get('msg')}")
        return True

    async def place_market_order(self, symbol: str, side: str,
                                  quantity: float, position_side: str = "BOTH") -> dict:
        logger.info(f"📤 ORDER {symbol} {side} ps={position_side} qty={quantity}")
        return await self._post("/openApi/swap/v2/trade/order", {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         "MARKET",
            "quantity":     quantity,
        })

    async def place_tp_sl_order(self, symbol: str, side: str, quantity: float,
                                 stop_price: float, order_type: str,
                                 position_side: str = "BOTH") -> dict:
        return await self._post("/openApi/swap/v2/trade/order", {
            "symbol":        symbol,
            "side":          side,
            "positionSide":  position_side,
            "type":          order_type,
            "stopPrice":     round(stop_price, 6),
            "quantity":      quantity,
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
        })

    async def cancel_all_orders(self, symbol: str) -> dict:
        return await self._delete("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    async def update_trailing_stop(self, symbol: str, position_side: str,
                                    new_sl: float, quantity: float,
                                    mode: str = "ONE_WAY") -> dict:
        await self.cancel_all_orders(symbol)
        side = "SELL" if position_side == "LONG" else "BUY"
        ps   = "BOTH" if mode == "ONE_WAY" else position_side
        return await self.place_tp_sl_order(symbol, side, quantity, new_sl, "STOP_MARKET", ps)

    # ── Symbol Info ───────────────────────────────────────────────────────────

    async def _load_contracts(self) -> dict:
        global _contracts_cache
        if _contracts_cache:
            return _contracts_cache
        data = await self._get("/openApi/swap/v2/quote/contracts")
        for c in data.get("data", []):
            sym = c.get("symbol", "")
            if sym:
                _contracts_cache[sym] = c
        logger.info(f"📋 {len(_contracts_cache)} contratos cargados")
        return _contracts_cache


    async def get_orderbook_depth(self, symbol: str, limit: int = 10) -> dict:
        """Top N niveles del libro de órdenes."""
        data = await self._get("/openApi/swap/v2/quote/depth",
                               {"symbol": symbol, "limit": limit})
        return data.get("data") or {}

    async def get_qty_precision(self, symbol: str) -> int:
        contracts = await self._load_contracts()
        try:
            return int(contracts.get(symbol, {}).get("quantityPrecision", 1))
        except Exception:
            return 1

    async def get_min_qty(self, symbol: str) -> float:
        contracts = await self._load_contracts()
        try:
            return float(contracts.get(symbol, {}).get("minQty",
                         contracts.get(symbol, {}).get("minimumTradeVolume", 1)))
        except Exception:
            return 1.0

    async def round_quantity(self, symbol: str, raw_qty: float) -> float:
        precision = await self.get_qty_precision(symbol)
        min_qty   = await self.get_min_qty(symbol)
        if precision == 0:
            qty = max(1.0, math.floor(raw_qty))
        else:
            factor = 10 ** precision
            qty    = math.floor(raw_qty * factor) / factor
        qty = round(qty, precision)
        if qty < min_qty:
            logger.warning(f"{symbol}: qty {qty} < minQty {min_qty}")
            qty = min_qty
        return qty
