import time
import logging
import sys
from config import SCAN_INTERVAL, BINGX_API_KEY, BINGX_SECRET_KEY, TELEGRAM_TOKEN
import bingx_client as api
import scanner
import trader
import notifier

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("main")


def validate_config():
    errors = []
    if not BINGX_API_KEY:
        errors.append("BINGX_API_KEY not set")
    if not BINGX_SECRET_KEY:
        errors.append("BINGX_SECRET_KEY not set")
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_TOKEN not set — notifications disabled")
    if errors:
        for e in errors:
            logger.critical(e)
        sys.exit(1)


def main():
    validate_config()
    logger.info("=" * 60)
    logger.info("  EMA SCALPING BOT — EMA7 / EMA17 + Slope 30°")
    logger.info("=" * 60)

    # Load universe (once, refresh every 6h)
    universe_refresh_interval = 6 * 3600
    last_universe_load = 0
    universe = []

    balance = api.get_balance()
    logger.info(f"Starting balance: {balance:.2f} USDT")

    scan_count = 0

    while True:
        try:
            now = time.time()

            # ── Refresh universe periodically ──────────────────────────────
            if now - last_universe_load > universe_refresh_interval:
                universe = scanner.load_universe()
                last_universe_load = now

                if scan_count == 0:
                    balance = api.get_balance()
                    notifier.startup_alert(len(universe), balance)

            if not universe:
                logger.warning("Empty universe, retrying in 60s")
                time.sleep(60)
                continue

            # ── Sync open positions with exchange ─────────────────────────
            trader.sync_positions()

            # ── Scan all symbols ──────────────────────────────────────────
            scan_count += 1
            logger.info(f"── Scan #{scan_count} | open trades: {len(trader.open_trades)} ──")

            signals = scanner.scan_all(universe)

            # ── Process signals ───────────────────────────────────────────
            for symbol, sig in signals:
                direction = sig["signal"]
                logger.info(
                    f"SIGNAL {direction:5s} {symbol:20s} | "
                    f"slope={sig['slope']:+.1f}° | "
                    f"EMA7={sig['ema_fast']:.4f} EMA17={sig['ema_slow']:.4f}"
                )
                # Check if reverse signal → close existing trade
                trader.check_exit(symbol, direction)
                # Try to open new trade
                trader.enter_trade(symbol, sig)

            logger.info(f"Sleeping {SCAN_INTERVAL}s until next scan...")
            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            notifier.send("🛑 <b>EMA SCALPING BOT DETENIDO</b>")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)
            notifier.error_alert(f"Main loop: {e}")
            time.sleep(30)


if __name__ == "__main__":
    main()
