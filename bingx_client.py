import time
import hmac
import hashlib
import requests
import logging
from urllib.parse import urlencode
from config import BINGX_API_KEY, BINGX_SECRET_KEY, BINGX_BASE_URL

logger = logging.getLogger(__name__)


def _sign(params: dict) -> str:
    query = urlencode(sorted(params.items()))
    return hmac.new(BINGX_SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()


def _headers() -> dict:
    return {"X-BX-APIKEY": BINGX_API_KEY, "Content-Type": "application/json"}


def _get(path: str, params: dict = None) -> dict:
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    try:
        r = requests.get(BINGX_BASE_URL + path, params=params, headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"GET {path} error: {e}")
        return {}


def _post(path: str, params: dict = None) -> dict:
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params)
    try:
        r = requests.post(BINGX_BASE_URL + path, params=params, headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"POST {path} error: {e}")
        return {}


# ─── Market data ──────────────────────────────────────────────────────────────

def get_all_symbols() -> list[str]:
    """Return list of all active USDT perpetual symbols."""
    data = _get("/openApi/swap/v2/quote/contracts")
    symbols = []
    if data.get("code") == 0:
        for c in data.get("data", []):
            sym = c.get("symbol", "")
            if sym.endswith("-USDT") and c.get("status", 1) == 1:
                symbols.append(sym)
    return symbols


def get_ticker_24h(symbol: str) -> dict:
    """24h ticker for volume/price filtering."""
    data = _get("/openApi/swap/v2/quote/ticker", {"symbol": symbol})
    if data.get("code") == 0:
        return data.get("data", {})
    return {}


def get_klines(symbol: str, interval: str, limit: int = 60) -> list:
    """
    Fetch OHLCV candles.
    Returns list of [timestamp, open, high, low, close, volume]
    """
    data = _get("/openApi/swap/v3/quote/klines", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    })
    if data.get("code") == 0:
        raw = data.get("data", [])
        result = []
        for c in raw:
            result.append([
                int(c["time"]),
                float(c["open"]),
                float(c["high"]),
                float(c["low"]),
                float(c["close"]),
                float(c["volume"]),
            ])
        return sorted(result, key=lambda x: x[0])
    return []


# ─── Account ──────────────────────────────────────────────────────────────────

def get_balance() -> float:
    """Return available USDT balance."""
    data = _get("/openApi/swap/v3/user/balance")
    if data.get("code") == 0:
        for asset in data.get("data", {}).get("balance", []):
            if asset.get("asset") == "USDT":
                return float(asset.get("availableMargin", 0))
    # fallback v2
    data2 = _get("/openApi/swap/v2/user/balance")
    if data2.get("code") == 0:
        bal = data2.get("data", {}).get("balance", {})
        if isinstance(bal, dict):
            return float(bal.get("availableMargin", bal.get("available", 0)))
    return 0.0


def get_positions() -> list:
    """Return all open positions."""
    data = _get("/openApi/swap/v2/user/positions")
    if data.get("code") == 0:
        return [p for p in data.get("data", []) if float(p.get("positionAmt", 0)) != 0]
    return []


def get_open_orders(symbol: str) -> list:
    data = _get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
    if data.get("code") == 0:
        return data.get("data", {}).get("orders", [])
    return []


# ─── Trading ──────────────────────────────────────────────────────────────────

def set_leverage(symbol: str, leverage: int) -> bool:
    for side in ["LONG", "SHORT"]:
        _post("/openApi/swap/v2/trade/leverage", {
            "symbol": symbol,
            "side": side,
            "leverage": leverage
        })
    return True


def set_margin_type(symbol: str, margin_type: str) -> bool:
    """ISOLATED or CROSSED."""
    _post("/openApi/swap/v2/trade/marginType", {
        "symbol": symbol,
        "marginType": margin_type
    })
    return True


def place_market_order(symbol: str, side: str, quantity: float,
                       position_side: str = "BOTH") -> dict:
    """
    side: BUY or SELL
    position_side: LONG / SHORT / BOTH (one-way=BOTH)
    """
    params = {
        "symbol": symbol,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": quantity,
    }
    data = _post("/openApi/swap/v2/trade/order", params)
    return data


def place_sl_tp_order(symbol: str, side: str, quantity: float,
                      stop_price: float, order_type: str,
                      position_side: str = "BOTH") -> dict:
    """
    order_type: STOP_MARKET or TAKE_PROFIT_MARKET
    side: opposite of entry (BUY entry → SELL SL/TP)
    """
    params = {
        "symbol": symbol,
        "side": side,
        "positionSide": position_side,
        "type": order_type,
        "quantity": quantity,
        "stopPrice": round(stop_price, 8),
        "workingType": "MARK_PRICE",
    }
    data = _post("/openApi/swap/v2/trade/order", params)
    return data


def cancel_all_orders(symbol: str) -> bool:
    data = _post("/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})
    return data.get("code") == 0


def close_position(symbol: str, position_side: str, quantity: float) -> dict:
    """Market close a position."""
    side = "SELL" if position_side in ("LONG", "BOTH") else "BUY"
    return place_market_order(symbol, side, abs(quantity), position_side)


def get_symbol_info(symbol: str) -> dict:
    """Get precision info for a symbol."""
    data = _get("/openApi/swap/v2/quote/contracts")
    if data.get("code") == 0:
        for c in data.get("data", []):
            if c.get("symbol") == symbol:
                return c
    return {}
