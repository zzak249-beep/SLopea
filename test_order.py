"""
Script de diagnóstico — prueba una orden MARKET real en BingX
para ver el error exacto del 109400.

Uso:
  BINGX_API_KEY=xxx BINGX_SECRET_KEY=xxx python test_order.py

Prueba con MANA-USDT, cantidad mínima posible.
NO abre trades reales si TEST_ONLY=True.
"""
import asyncio
import os
import hmac
import hashlib
import time
from urllib.parse import urlencode
import aiohttp

API_KEY    = os.getenv("BINGX_API_KEY", "")
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")
BASE       = "https://open-api.bingx.com"
TEST_ONLY  = True   # ← cambiar a False para enviar orden real

def _build_url(path, params):
    params["timestamp"]  = str(int(time.time() * 1000))
    params["recvWindow"] = "10000"
    query = urlencode(params)
    sig = hmac.new(SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{BASE}{path}?{query}&signature={sig}"

async def main():
    headers = {"X-BX-APIKEY": API_KEY, "Content-Type": "application/json"}
    async with aiohttp.ClientSession(headers=headers) as session:

        # 1. Obtener info del contrato MANA-USDT
        print("=== Contratos MANA-USDT ===")
        async with session.get(f"{BASE}/openApi/swap/v2/quote/contracts") as r:
            data = await r.json()
        raw = data.get("data", [])
        if isinstance(raw, dict):
            raw = raw.get("contracts", [])
        for item in raw:
            sym = item.get("symbol", "")
            if "MANA" in sym:
                print(f"  {sym}: {item}")
                break

        # 2. Ticker MANA-USDT
        print("\n=== Ticker MANA-USDT ===")
        async with session.get(f"{BASE}/openApi/swap/v2/quote/ticker",
                               params={"symbol": "MANA-USDT"}) as r:
            data = await r.json()
        print(f"  {data.get('data', {})}")

        # 3. Intentar orden mínima (solo si TEST_ONLY=False)
        symbol = "MANA-USDT"
        test_params_list = [
            # Variante A: sin price, solo quantity
            {"symbol": symbol, "side": "BUY", "positionSide": "LONG",
             "type": "MARKET", "quantity": "1"},
            # Variante B: quantity como número
            {"symbol": symbol, "side": "BUY", "positionSide": "LONG",
             "type": "MARKET", "quantity": "10"},
            # Variante C: con newClientOrderId
            {"symbol": symbol, "side": "BUY", "positionSide": "LONG",
             "type": "MARKET", "quantity": "1",
             "newClientOrderId": f"test_{int(time.time())}"},
        ]

        for i, params in enumerate(test_params_list):
            url = _build_url("/openApi/swap/v2/trade/order", dict(params))
            print(f"\n=== Variante {chr(65+i)} — params originales: {params} ===")
            print(f"  URL (sin signature): ...{url[url.find('?'):url.find('&signature=')]}")
            if TEST_ONLY:
                print("  [TEST_ONLY=True — no se envía]")
            else:
                async with session.post(url) as r:
                    resp = await r.json()
                print(f"  Respuesta: {resp}")
                if resp.get("code") == 0:
                    print("  ✅ ORDEN EXITOSA")
                    break

asyncio.run(main())
