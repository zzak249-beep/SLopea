import math
import logging
import time
from config import (
    LEVERAGE, RISK_PCT, ATR_SL_MULT, ATR_TP_MULT,
    MAX_OPEN_TRADES, MARGIN_TYPE
)
import bingx_client as api
import notifier

logger = logging.getLogger(__name__)

# In-memory trade state: {symbol: {"side": "LONG"|"SHORT", "qty": float, "entry": float}}
open_trades: dict = {}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _round_step(value: float, step: float) -> float:
    if step == 0:
        return value
    return math.floor(value / step) * step


def _get_step_size(symbol: str) -> float:
    info = api.get_symbol_info(symbol)
    try:
        return float(info.get("tradeMinQuantity", 0.001))
    except Exception:
        return 0.001


def calc_quantity(balance: float, close: float, atr: float) -> float:
    """
    Risk-based position sizing.
    risk_usdt = balance * RISK_PCT / 100
    stop_distance = atr * ATR_SL_MULT
    qty = risk_usdt / stop_distance  (in base asset units)
    Leveraged: notional = qty * close  ← must not exceed balance * leverage
    """
    risk_usdt = balance * RISK_PCT / 100
    stop_dist = atr * ATR_SL_MULT
    if stop_dist == 0:
        return 0.0
    qty = risk_usdt / stop_dist
    # Cap by max leverage
    max_qty = (balance * LEVERAGE) / close
    qty = min(qty, max_qty * 0.95)  # 5% safety margin
    return qty


# ─── Setup ────────────────────────────────────────────────────────────────────

def setup_symbol(symbol: str):
    """Set leverage and margin type."""
    try:
        api.set_margin_type(symbol, MARGIN_TYPE)
        time.sleep(0.1)
        api.set_leverage(symbol, LEVERAGE)
        time.sleep(0.1)
    except Exception as e:
        logger.warning(f"Setup {symbol}: {e}")


# ─── Entry ────────────────────────────────────────────────────────────────────

def enter_trade(symbol: str, signal: dict):
    """
    Open a new position for the given signal.
    signal keys: signal, close, atr, ema_fast, ema_slow, slope
    """
    direction = signal["signal"]   # "LONG" or "SHORT"
    close     = signal["close"]
    atr_val   = signal["atr"]

    if symbol in open_trades:
        logger.info(f"Already in trade: {symbol}")
        return

    if len(open_trades) >= MAX_OPEN_TRADES:
        logger.info(f"Max trades reached ({MAX_OPEN_TRADES})")
        return

    balance = api.get_balance()
    if balance < 5:
        logger.warning(f"Balance too low: {balance:.2f} USDT")
        return

    qty_raw = calc_quantity(balance, close, atr_val)
    step    = _get_step_size(symbol)
    qty     = _round_step(qty_raw, step)

    if qty <= 0:
        logger.warning(f"Qty=0 for {symbol}, skipping")
        return

    # SL / TP prices
    if direction == "LONG":
        sl_price = round(close - atr_val * ATR_SL_MULT, 8)
        tp_price = round(close + atr_val * ATR_TP_MULT, 8)
        order_side    = "BUY"
        sl_order_side = "SELL"
        tp_order_side = "SELL"
    else:
        sl_price = round(close + atr_val * ATR_SL_MULT, 8)
        tp_price = round(close - atr_val * ATR_TP_MULT, 8)
        order_side    = "SELL"
        sl_order_side = "BUY"
        tp_order_side = "BUY"

    setup_symbol(symbol)

    # ── Market entry ──────────────────────────────────────────────────────
    result = api.place_market_order(symbol, order_side, qty)
    if result.get("code") != 0:
        logger.error(f"Entry failed {symbol}: {result}")
        notifier.error_alert(f"Entry FAIL {symbol}: {result.get('msg','?')}")
        return

    logger.info(f"✅ ENTER {direction} {symbol} qty={qty} @ ~{close}")

    # ── Stop Loss ─────────────────────────────────────────────────────────
    sl_result = api.place_sl_tp_order(
        symbol, sl_order_side, qty, sl_price, "STOP_MARKET"
    )
    if sl_result.get("code") != 0:
        logger.warning(f"SL failed {symbol}: {sl_result}")

    # ── Take Profit ───────────────────────────────────────────────────────
    tp_result = api.place_sl_tp_order(
        symbol, tp_order_side, qty, tp_price, "TAKE_PROFIT_MARKET"
    )
    if tp_result.get("code") != 0:
        logger.warning(f"TP failed {symbol}: {tp_result}")

    # ── Register ──────────────────────────────────────────────────────────
    open_trades[symbol] = {
        "side":  direction,
        "qty":   qty,
        "entry": close,
        "sl":    sl_price,
        "tp":    tp_price,
    }

    notifier.signal_alert(
        symbol=symbol, signal=direction,
        close=close, sl=sl_price, tp=tp_price,
        slope=signal["slope"],
        ema_fast=signal["ema_fast"], ema_slow=signal["ema_slow"],
        qty=qty
    )


# ─── Sync with exchange ───────────────────────────────────────────────────────

def sync_positions():
    """
    Reconcile in-memory open_trades with actual exchange positions.
    Removes closed trades from state.
    """
    if not open_trades:
        return

    live_positions = {p["symbol"]: p for p in api.get_positions()}

    closed = []
    for symbol, trade in open_trades.items():
        if symbol not in live_positions:
            logger.info(f"Position closed externally: {symbol}")
            notifier.close_alert(symbol, trade["side"])
            closed.append(symbol)

    for s in closed:
        del open_trades[s]


# ─── Exit on reverse signal ───────────────────────────────────────────────────

def check_exit(symbol: str, signal: str):
    """
    Close a LONG if we get a SHORT signal (and vice versa).
    """
    if symbol not in open_trades:
        return
    trade = open_trades[symbol]
    if (trade["side"] == "LONG" and signal == "SHORT") or \
       (trade["side"] == "SHORT" and signal == "LONG"):
        logger.info(f"Reverse signal → closing {symbol}")
        api.cancel_all_orders(symbol)
        time.sleep(0.3)
        api.close_position(symbol, trade["side"], trade["qty"])
        notifier.close_alert(symbol, trade["side"])
        del open_trades[symbol]
