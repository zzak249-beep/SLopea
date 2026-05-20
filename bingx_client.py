"""
SAMA APEX Bot - BingX Client FIXED v2
BUGS CORREGIDOS:
  1. positionSide = "BOTH" para cuentas One-Way mode (default BingX)
  2. get_balance() maneja todas las estructuras de respuesta v2/v3
  3. round_quantity usa tabla de contratos con fallback seguro
  4. _sign usa sorted params (fix HMAC)
  5. Logging de respuestas API para diagnóstico
"""
import hmac
import hashlib
import time
import math
import json
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

# Cache de contratos para no llamar /contracts en cada trade
_contracts_cache: dict = {}


def _sign(params: dict, secret: str) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


def _ts() -> int:
    return int(time.time() * 1000)


class BingXClient:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict = None, auth: bool = False) -> dict:
        params = dict(params or {})
        if auth:
            params["timestamp"] = _ts()
            params["signature"] = _sign(params, BINGX_SECRET_KEY)
        headers = {"X-BX-APIKEY": BINGX_API_KEY} if auth else {}
        url = BINGX_BASE_URL + path
        try:
            async with self.session.get(url, params=params, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json()
                if data.get("code") != 0:
                    logger.warning(f"GET {path} code={data.get('code')} msg={data.get('msg')}")
                return data
        except Exception as e:
            logger.error(f"GET {path} exception: {e}")
            return {"code": -1, "msg": str(e)}

    async def _post(self, path: str, params: dict = None) -> dict:
        params = dict(params or {})
        params["timestamp"] = _ts()
        params["signature"] = _sign(params, BINGX_SECRET_KEY)
        headers = {"X-BX-APIKEY": BINGX_API_KEY}
        url = BINGX_BASE_URL + path
        try:
            async with self.session.post(url, params=params, headers=headers,
                                         timeout=aiohttp.ClientTimeout(total=12)) as r:
                data = await r.json()
                if data.get("code") != 0:
                    logger.error(f"POST {path} FAILED code={data.get('code')} msg={data.get('msg')} params={params}")
                else:
                    logger.debug(f"POST {path} OK")
                return data
        except Exception as e:
            logger.error(f"POST {path} exception: {e}")
            return {"code": -1, "msg": str(e)}

    async def _delete(self, path: str, params: dict = None) -> dict:
        params = dict(params or {})
        params["timestamp"] = _ts()
        params["signature"] = _sign(params, BINGX_SECRET_KEY)
        headers = {"X-BX-APIKEY": BINGX_API_KEY}
        url = BINGX_BASE_URL + path
        try:
            async with self.session.delete(url, params=params, headers=headers,
                                           timeout=aiohttp.ClientTimeout(total=12)) as r:
                return await r.json()
        except Exception as e:
            logger.error(f"DELETE {path} exception: {e}")
            return {"code": -1}

    # ── Market Data ───────────────────────────────────────────────────────────

    async def get_klines(self, symbol: str, interval: str, limit: int = 300) -> pd.DataFrame:
        tf = TF_MAP.get(interval, interval)
        data = await self._get("/openApi/swap/v3/quote/klines", {
            "symbol": symbol, "interval": tf, "limit": min(limit, 1440),
        })
        rows = data.get("data") or []
        if not rows:
            logger.warning(f"get_klines {symbol}/{tf}: vacío code={data.get('code')}")
            return pd.DataFrame()
        result = []
        for c in rows:
            try:
                result.append({
                    "time":   int(c.get("time", c.get("t", 0))),
                    "open":   float(c.get("open",  c.get("o", 0))),
                    "high":   float(c.get("high",  c.get("h", 0))),
                    "low":    float(c.get("low",   c.get("l", 0))),
                    "close":  float(c.get("close", c.get("c", 0))),
                    "volume": float(c.get("volume",c.get("v", 0))),
                })
            except Exception:
                continue
        df = pd.DataFrame(result).sort_values("time").reset_index(drop=True)
        return df

    async def get_ticker(self, symbol: str) -> dict:
        data = await self._get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        return data.get("data", {}) or {}

    async def get_funding_rate(self, symbol: str) -> float:
        data = await self._get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
        try:
            val = data["data"]["lastFundingRate"]
            return float(val)
        except Exception:
            return 0.0

    # ── Account ───────────────────────────────────────────────────────────────

    async def get_balance(self) -> float:
        """
        FIX BUG 2: BingX v2 swap balance tiene varias estructuras.
        Probamos todas y retornamos el mayor valor encontrado.
        """
        data = await self._get("/openApi/swap/v2/user/balance", {}, auth=True)
        logger.debug(f"get_balance raw: {json.dumps(data)[:300]}")

        try:
            inner = data.get("data", {})

            # Formato A: data.balance es un dict con asset/availableMargin
            if isinstance(inner.get("balance"), dict):
                b = inner["balance"]
                for key in ("availableMargin", "equity", "balance"):
                    val = b.get(key)
                    if val is not None:
                        v = float(val)
                        if v > 0:
                            logger.info(f"💰 Balance ({key}): {v:.2f} USDT")
                            return v

            # Formato B: data.balance es lista de assets
            if isinstance(inner.get("balance"), list):
                for a in inner["balance"]:
                    if a.get("asset", "").upper() == "USDT":
                        for key in ("availableMargin", "equity", "balance"):
                            val = a.get(key)
                            if val is not None:
                                v = float(val)
                                if v > 0:
                                    logger.info(f"💰 Balance lista ({key}): {v:.2f} USDT")
                                    return v

            # Formato C: data directamente tiene equity/availableMargin
            for key in ("availableMargin", "equity", "balance"):
                val = inner.get(key)
                if val is not None:
                    v = float(val)
                    if v > 0:
                        logger.info(f"💰 Balance directo ({key}): {v:.2f} USDT")
                        return v

        except Exception as e:
            logger.error(f"get_balance parse error: {e} | raw: {data}")

        logger.error(f"❌ get_balance: no se pudo parsear — raw: {json.dumps(data)[:400]}")
        return 0.0

    async def get_positions(self, symbol: str = None) -> list:
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._get("/openApi/swap/v2/user/positions", params, auth=True)
        positions = data.get("data") or []
        if isinstance(positions, list):
            return [p for p in positions if abs(float(p.get("positionAmt", 0))) > 0]
        return []

    async def get_open_orders(self, symbol: str) -> list:
        data = await self._get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol}, auth=True)
        return (data.get("data") or {}).get("orders", [])

    # ── Account Mode Detection ────────────────────────────────────────────────

    async def get_position_mode(self) -> str:
        """Detecta si la cuenta está en One-Way o Hedge mode."""
        data = await self._get("/openApi/swap/v1/positionSide/dual", {}, auth=True)
        logger.debug(f"position_mode raw: {data}")
        try:
            dual = data["data"]["dualSidePosition"]
            mode = "HEDGE" if dual else "ONE_WAY"
            logger.info(f"📋 Modo de posición: {mode}")
            return mode
        except Exception:
            logger.warning("No se pudo detectar modo — asumiendo ONE_WAY")
            return "ONE_WAY"

    # ── Trading ───────────────────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        for side in ["LONG", "SHORT"]:
            res = await self._post("/openApi/swap/v2/trade/leverage", {
                "symbol": symbol, "side": side, "leverage": leverage,
            })
            if res.get("code") not in (0, 200):
                logger.warning(f"set_leverage {symbol} {side}: {res.get('msg')}")
        return True

    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                  position_side: str = "BOTH") -> dict:
        """
        FIX BUG 5: positionSide depende del modo de cuenta.
        One-Way  → positionSide = "BOTH"
        Hedge    → positionSide = "LONG" / "SHORT"
        Se detecta al inicio del bot y se pasa como parámetro.
        """
        params = {
            "symbol":       symbol,
            "side":         side,
            "positionSide": position_side,
            "type":         "MARKET",
            "quantity":     quantity,
        }
        logger.info(f"📤 ORDER {symbol} {side} {position_side} qty={quantity}")
        return await self._post("/openApi/swap/v2/trade/order", params)

    async def place_tp_sl_order(self, symbol: str, side: str, quantity: float,
                                 stop_price: float, order_type: str,
                                 position_side: str = "BOTH") -> dict:
        params = {
            "symbol":        symbol,
            "side":          side,
            "positionSide":  position_side,
            "type":          order_type,
            "stopPrice":     round(stop_price, 6),
            "quantity":      quantity,
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
        }
        return await self._post("/openApi/swap/v2/trade/order", params)

    async def cancel_all_orders(self, symbol: str) -> dict:
        return await self._delete("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    async def close_position(self, symbol: str, position_side: str,
                              quantity: float, mode: str = "ONE_WAY") -> dict:
        side = "SELL" if position_side == "LONG" else "BUY"
        ps   = "BOTH" if mode == "ONE_WAY" else position_side
        return await self.place_market_order(symbol, side, quantity, ps)

    async def update_trailing_stop(self, symbol: str, position_side: str,
                                    new_sl: float, quantity: float,
                                    mode: str = "ONE_WAY") -> dict:
        await self.cancel_all_orders(symbol)
        side = "SELL" if position_side == "LONG" else "BUY"
        ps   = "BOTH" if mode == "ONE_WAY" else position_side
        return await self.place_tp_sl_order(symbol, side, quantity, new_sl,
                                            "STOP_MARKET", ps)

    # ── Symbol Info / Quantity Rounding ──────────────────────────────────────

    async def _load_contracts(self) -> dict:
        global _contracts_cache
        if _contracts_cache:
            return _contracts_cache
        data = await self._get("/openApi/swap/v2/quote/contracts")
        for c in data.get("data", []):
            sym = c.get("symbol", "")
            _contracts_cache[sym] = c
        logger.info(f"📋 {len(_contracts_cache)} contratos cargados")
        return _contracts_cache

    async def get_qty_precision(self, symbol: str) -> int:
        """Devuelve número de decimales para quantity del símbolo."""
        contracts = await self._load_contracts()
        info = contracts.get(symbol, {})
        # quantityPrecision = número de decimales (e.g. 0 = entero, 3 = 0.001)
        try:
            return int(info.get("quantityPrecision", 3))
        except Exception:
            return 3

    async def get_min_qty(self, symbol: str) -> float:
        contracts = await self._load_contracts()
        info = contracts.get(symbol, {})
        try:
            return float(info.get("minQty", info.get("minimumTradeVolume", 0.001)))
        except Exception:
            return 0.001

    async def round_quantity(self, symbol: str, raw_qty: float) -> float:
        """
        FIX BUG 4: nunca retorna 0 por redondeo equivocado.
        Aplica precision y verifica minQty.
        """
        precision = await self.get_qty_precision(symbol)
        min_qty   = await self.get_min_qty(symbol)

        if precision == 0:
            qty = math.floor(raw_qty)
        else:
            factor = 10 ** precision
            qty    = math.floor(raw_qty * factor) / factor

        qty = round(qty, precision)

        if qty < min_qty:
            logger.warning(f"{symbol}: qty calculada {qty} < minQty {min_qty} — usando minQty")
            qty = min_qty

        logger.debug(f"{symbol}: raw_qty={raw_qty:.6f} → qty={qty} (precision={precision}, minQty={min_qty})")
        return qty
