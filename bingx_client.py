"""
QF×JP Bot v6.3 — BingX Client
Maneja: klines, símbolos, órdenes MARKET, SL/TP, cierre de posiciones,
consulta de posiciones abiertas y cancelación de órdenes pendientes.

FIX v6.3.2: _build_signed_url garantiza que 'signature' sea el ÚLTIMO
parámetro en la query string, tal como exige BingX. Se elimina sorted()
que reordenaba los params y rompía la firma.

FIX v6.3.3: stepSize cache — redondea quantity al stepSize del símbolo
antes de enviar órdenes. Evita error 109400 "Invalid parameters".

FIX v6.3.5: get_all_symbols — fallback a /ticker para volumen cuando
/contracts devuelve con_vol=0. MIN_VOLUME_USDT ya funciona.

FIX v6.3.5: get_symbol_precision — lee volumePrecision (campo real BingX,
int = nº de decimales). Evita error 109400 en altcoins como WLD, ARK, etc.
"""
import hmac
import hashlib
import math
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


def _build_signed_url(base: str, path: str, params: dict) -> str:
    """
    Construye la URL firmada garantizando que 'signature' sea el último
    parámetro. BingX rechaza (code 100001) si el orden no es exacto.
    """
    params["timestamp"]  = _ts()
    params["recvWindow"] = "10000"
    query = urlencode(params)
    sig = hmac.new(
        C.BINGX_SECRET_KEY.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{base}{path}?{query}&signature={sig}"


# ── Cliente base ─────────────────────────────────────────────────────────────

class BingXClient:
    BASE = C.BINGX_BASE_URL

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        # Cache de stepSize: {symbol: (qty_step, price_step)}
        self._precision_cache: dict[str, tuple[float, float]] = {}
        # Cache de leverage configurado — evita rate-limit 100410
        self._leverage_cache: set[str] = set()

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
        for attempt in range(3):
            try:
                if signed:
                    url = _build_signed_url(self.BASE, path, dict(params or {}))
                    async with session.get(url) as r:
                        return await r.json()
                else:
                    async with session.get(f"{self.BASE}{path}", params=params or {}) as r:
                        return await r.json()
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    async def _post(self, path: str, params: dict) -> dict:
        session = await self._get_session()
        for attempt in range(3):
            try:
                url = _build_signed_url(self.BASE, path, dict(params))
                log.debug("POST %s", url[:200])
                async with session.post(url) as r:
                    data = await r.json()
                    if isinstance(data, dict) and data.get("code", 0) != 0:
                        log.error("POST %s → code=%s msg=%s",
                                  path, data.get("code"), data.get("msg", "")[:300])
                    return data
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    async def _delete(self, path: str, params: dict) -> dict:
        session = await self._get_session()
        for attempt in range(3):
            try:
                url = _build_signed_url(self.BASE, path, dict(params))
                async with session.delete(url) as r:
                    return await r.json()
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 ** attempt)
        return {}

    # ── Mercado ───────────────────────────────────────────────────────────────

    async def _fetch_ticker_volumes(self) -> dict[str, float]:
        """
        Obtiene volumen 24h en USDT para todos los pares desde /ticker.
        Retorna {symbol: vol_usdt}. Usado como fallback cuando /contracts
        no devuelve datos de volumen (con_vol=0).
        """
        vol_map: dict[str, float] = {}
        try:
            data = await self._get("/openApi/swap/v2/quote/ticker")
            tickers = data.get("data", [])
            if not isinstance(tickers, list):
                tickers = []
            for t in tickers:
                sym = t.get("symbol", "")
                if not sym:
                    continue
                if "-" not in sym and sym.endswith("USDT"):
                    sym = sym[:-4] + "-USDT"
                if not sym.endswith("-USDT"):
                    continue
                # quoteVolume = volumen en USDT (lo que queremos)
                # volume      = volumen en moneda base (menos útil para filtrar)
                vol = float(
                    t.get("quoteVolume") or
                    t.get("turnover") or
                    t.get("vol") or
                    t.get("volume") or 0
                )
                if vol > 0:
                    vol_map[sym] = vol
        except Exception as e:
            log.warning("_fetch_ticker_volumes error: %s", e)
        return vol_map

    async def get_all_symbols(self) -> list[str]:
        """
        Devuelve todos los pares USDT de perpetuos BingX con volumen mínimo.

        Flujo:
        1. /contracts → lista de símbolos + intento de volumen
        2. Si con_vol=0 → fallback a /ticker para volumen (FIX v6.3.5)
        3. Filtra por MIN_VOLUME_USDT, BLACKLIST, tokens sintéticos
        4. Ordena por volumen desc, aplica TOP_N
        """
        data = await self._get("/openApi/swap/v2/quote/contracts")
        raw = data.get("data", [])

        if isinstance(raw, dict):
            raw = raw.get("contracts", raw.get("list", []))

        if not isinstance(raw, list) or len(raw) == 0:
            log.warning("get_all_symbols: contracts vacío, usando tickers fallback")
            ticker_data = await self._get("/openApi/swap/v2/quote/premiumIndex")
            raw = ticker_data.get("data", [])
            if not isinstance(raw, list):
                raw = []

        symbols_raw: list[str] = []
        vol_map: dict[str, float] = {}
        vol_detected = 0

        for item in raw:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol", "")
            if not sym:
                continue
            if "-" not in sym and sym.endswith("USDT"):
                sym = sym[:-4] + "-USDT"
            if not sym.endswith("-USDT"):
                continue
            if sym in C.BLACKLIST:
                continue
            base = sym.replace("-USDT", "")
            if any(base.startswith(p) for p in (
                "BEAR", "BULL", "PUMP", "NCS",
                "NCFX", "DOWN", "UP",
            )):
                continue

            # Intentar leer volumen desde /contracts
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

            symbols_raw.append(sym)

        # ── FIX v6.3.5: Fallback a /ticker si /contracts no devolvió volumen ──
        if vol_detected == 0 and len(symbols_raw) > 0:
            log.info("get_all_symbols: con_vol=0 → fetching volumen desde /ticker")
            ticker_vols = await self._fetch_ticker_volumes()
            if ticker_vols:
                vol_map.update(ticker_vols)
                vol_detected = len(ticker_vols)
                log.info("get_all_symbols: volumen obtenido desde /ticker para %d símbolos", vol_detected)
            else:
                log.warning(
                    "⚠️  Volumen no disponible en /contracts ni /ticker — "
                    "MIN_VOLUME_USDT ignorado."
                )

        # ── Aplicar filtro de volumen mínimo ─────────────────────────────────
        if C.MIN_VOLUME_USDT > 0 and vol_detected > 0:
            symbols_filtered = [
                s for s in symbols_raw
                if vol_map.get(s, 0) >= C.MIN_VOLUME_USDT
            ]
        else:
            symbols_filtered = symbols_raw

        # ── Ordenar por volumen desc ──────────────────────────────────────────
        symbols_filtered.sort(key=lambda s: vol_map.get(s, 0), reverse=True)

        if C.TOP_N_SYMBOLS > 0:
            symbols_filtered = symbols_filtered[: C.TOP_N_SYMBOLS]

        log.info(
            "get_all_symbols: %d símbolos válidos (raw=%d, con_vol=%d)",
            len(symbols_filtered), len(raw), vol_detected,
        )
        return symbols_filtered

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[list]:
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

    # ── Precisión de símbolo (stepSize / volumePrecision) ────────────────────

    async def get_symbol_precision(self, symbol: str) -> tuple[float, float]:
        """
        Retorna (qty_step, price_step) para el símbolo.
        Usa caché en memoria para no llamar la API en cada orden.

        qty_step   → mínimo incremento de cantidad  (ej: 1.0, 0.1, 0.001)
        price_step → mínimo incremento de precio    (ej: 0.001, 0.01)

        FIX v6.3.5: Lee volumePrecision primero (campo real de BingX).
        volumePrecision es un entero que indica el número de decimales:
          0 → step=1, 1 → step=0.1, 2 → step=0.01, 3 → step=0.001, etc.
        """
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]

        try:
            data = await self._get("/openApi/swap/v2/quote/contracts")
            raw = data.get("data", [])
            if isinstance(raw, dict):
                raw = raw.get("contracts", raw.get("list", []))
            if not isinstance(raw, list):
                raw = []

            for item in raw:
                sym = item.get("symbol", "")
                if "-" not in sym and sym.endswith("USDT"):
                    sym = sym[:-4] + "-USDT"
                if sym != symbol:
                    continue

                # ── FIX v6.3.5: volumePrecision es el campo real de BingX ──
                vol_prec_raw = item.get("volumePrecision")
                if vol_prec_raw is not None:
                    try:
                        qty_step = 10.0 ** (-int(float(vol_prec_raw)))
                    except Exception:
                        qty_step = 1.0
                else:
                    # Fallback: otros posibles campos (APIs antiguas)
                    qty_step = float(
                        item.get("tradeMinQuantity") or
                        item.get("stepSize") or
                        item.get("quantityStep") or
                        item.get("lotSize") or
                        item.get("minQty") or 1
                    )

                # pricePrecision: puede ser entero (nº decimales) o float directo
                price_prec_raw = item.get("pricePrecision")
                if price_prec_raw is not None:
                    try:
                        pp = float(price_prec_raw)
                        price_step = 10.0 ** (-int(pp)) if pp >= 1 else pp
                    except Exception:
                        price_step = 0.0001
                else:
                    price_step = float(
                        item.get("tickSize") or
                        item.get("priceStep") or 0.0001
                    )

                self._precision_cache[symbol] = (qty_step, price_step)
                log.debug("[%s] stepSize qty=%.8f price=%.8f", symbol, qty_step, price_step)
                return (qty_step, price_step)

        except Exception as e:
            log.warning("get_symbol_precision(%s) error: %s — usando defaults", symbol, e)

        defaults = (1.0, 0.0001)
        self._precision_cache[symbol] = defaults
        return defaults

    def _round_qty(self, qty: float, step: float) -> float:
        """Redondea qty hacia abajo al múltiplo más cercano de step."""
        if step <= 0:
            return qty
        precision = max(0, -int(math.floor(math.log10(step))))
        rounded = math.floor(qty / step) * step
        return round(rounded, precision)

    # ── Cuenta ────────────────────────────────────────────────────────────────

    async def get_balance(self) -> float:
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
        """
        Configura leverage para el símbolo.
        Cache en memoria: sólo llama la API una vez por símbolo por sesión.
        - code=0      → éxito
        - code=100410 → rate-limit o ya configurado → OK y cacheamos
        - otros codes → warning pero NO bloqueamos la entrada
        """
        cache_key = f"{symbol}:{leverage}"
        if cache_key in self._leverage_cache:
            return True

        data = await self._post(
            "/openApi/swap/v2/trade/leverage",
            {"symbol": symbol, "side": "LONG", "leverage": str(leverage)},
        )
        code = data.get("code", -1)

        if code == 0:
            self._leverage_cache.add(cache_key)
            log.debug("[%s] leverage=%dx configurado OK", symbol, leverage)
            return True
        elif code == 100410:
            self._leverage_cache.add(cache_key)
            log.info("[%s] leverage: 100410 (rate-limit/ya configurado) → OK, cacheado", symbol)
            return True
        else:
            log.warning("[%s] set_leverage code=%s msg=%s → continuando igual",
                        symbol, code, data.get("msg", "")[:200])
            self._leverage_cache.add(cache_key)
            return False

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
        log.info("[%s] MARKET order params: %s", symbol, params)
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

        # 0. Obtener stepSize del símbolo y redondear quantity
        qty_step, price_step = await self.get_symbol_precision(symbol)
        qty_before = quantity
        quantity = self._round_qty(quantity, qty_step)
        log.info("[%s] qty_raw=%.8f → qty_step=%.8f → qty_final=%.8f",
                 symbol, qty_before, qty_step, quantity)
        if quantity <= 0:
            msg = f"qty={quantity} inválida tras redondear a stepSize={qty_step}"
            log.error("[%s] %s", symbol, msg)
            results["entry"] = {"code": -1, "msg": msg}
            return results

        # Validar notional mínimo (BingX requiere >= 5 USDT por orden)
        try:
            ticker = await self.get_ticker(symbol)
            mark_price = float(ticker.get("lastPrice") or ticker.get("markPrice") or 0)
            if mark_price > 0:
                notional = quantity * mark_price
                MIN_NOTIONAL = 5.0
                if notional < MIN_NOTIONAL:
                    msg = (f"notional={notional:.4f} USDT < mínimo {MIN_NOTIONAL} USDT "
                           f"(qty={quantity} × price={mark_price:.6f})")
                    log.error("[%s] %s", symbol, msg)
                    results["entry"] = {"code": -1, "msg": msg}
                    return results
                log.info("[%s] notional=%.2f USDT OK (qty=%.4f × price=%.6f)",
                         symbol, notional, quantity, mark_price)
        except Exception as e:
            log.warning("[%s] no se pudo validar notional: %s", symbol, e)

        # 1. Leverage (no bloqueante)
        lev_ok = await self.set_leverage(symbol, C.LEVERAGE, direction)
        if not lev_ok:
            log.info("[%s] leverage no confirmado pero continuando con entrada", symbol)

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

        # 4. TP1 (50% de la qty, redondeado al step)
        qty_half = self._round_qty(quantity / 2, qty_step)
        if qty_half <= 0:
            qty_half = quantity
        tp1_resp = await self.place_stop_market_order(
            symbol, side_close, qty_half, tp1_price, direction,
            close_position=False, order_type="TAKE_PROFIT_MARKET",
        )
        results["tp1"] = tp1_resp

        # 5. TP2 (50% de la qty, redondeado al step)
        tp2_resp = await self.place_stop_market_order(
            symbol, side_close, qty_half, tp2_price, direction,
            close_position=False, order_type="TAKE_PROFIT_MARKET",
        )
        results["tp2"] = tp2_resp

        return results
