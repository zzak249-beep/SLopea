"""
Entry point Railway - Health server arranca PRIMERO, luego el bot.
"""
import asyncio
import logging
import sys
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("STARTUP")

# ── Health server instantáneo ─────────────────────────────────────────────────
async def health_handler(request):
    return web.Response(text="OK", status=200)

async def start_health_server(port: int):
    app = web.Application()
    app.router.add_get("/",       health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"✅ Health server listo en :{port}")


async def main():
    import os
    port = int(os.getenv("PORT", "8080"))

    # 1. Health server PRIMERO — Railway lo verifica en ~10s
    await start_health_server(port)
    logger.info("🟢 Healthcheck OK — iniciando bot...")

    # 2. Pequeña pausa para que Railway confirme el health
    await asyncio.sleep(2)

    # 3. Lanzar el bot (que tiene su propio loop)
    from bot import SamaApexBot
    bot = SamaApexBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
