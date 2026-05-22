"""
SAMA APEX Bot - Main Orchestrator v4 ELITE
Integra los 7 edges del EdgeEngine sobre la base SAMA.
"""
import asyncio
import logging
import sys
import numpy as np
from datetime import date
from aiohttp import web

from config import (
    SYMBOLS, TF_LOCAL, TF_MACRO_1, TF_MACRO_2,
    LEVERAGE, SCAN_INTERVAL, HEALTH_PORT, CANDLES_NEEDED,
    FUNDING_FILTER, MAX_OPEN_TRADES, MIN_CONFLUENCE,
    RISK_PER_TRADE,
)
from bingx_client   import BingXClient
from indicators     import process_sama
from signal_engine  import SignalEngine
from risk_manager   import RiskManager
from symbol_scanner import fetch_all_symbols
from edge_engine    import (
    is_volatile_regime, PartialProfitManager,
    dynamic_risk_multiplier, funding_bias, funding_score_bonus,
    is_correlated_blocked, orderbook_imbalance, check_ob_for_trade,
    momentum_rank_score,
)
import telegram_notifier as tg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("SAMA-APEX")

BATCH_SIZE = 8


# ── Health ────────────────────────────────────────────────────────────────────
async def main():
    await SamaApexBot().run()

if __name__ == "__main__":
    asyncio.run(main())
