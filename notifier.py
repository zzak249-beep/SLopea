import requests
import logging
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send(message: str):
    """Send a Telegram message."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        logger.error(f"Telegram error: {e}")


def signal_alert(symbol: str, signal: str, close: float,
                 sl: float, tp: float, slope: float,
                 ema_fast: float, ema_slow: float, qty: float):
    emoji = "🟢" if signal == "LONG" else "🔴"
    direction = "LONG  📈" if signal == "LONG" else "SHORT 📉"
    msg = (
        f"{emoji} <b>EMA SCALPING BOT</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<b>Par:</b>    {symbol}\n"
        f"<b>Dir:</b>    {direction}\n"
        f"<b>Precio:</b> {close}\n"
        f"<b>EMA7:</b>  {ema_fast}\n"
        f"<b>EMA17:</b> {ema_slow}\n"
        f"<b>Slope:</b> {slope}°\n"
        f"<b>Qty:</b>   {qty}\n"
        f"<b>SL:</b>    {sl}\n"
        f"<b>TP:</b>    {tp}\n"
        f"━━━━━━━━━━━━━━━━"
    )
    send(msg)


def close_alert(symbol: str, side: str, pnl: float = None):
    emoji = "⚪"
    pnl_str = f"\n<b>PnL:</b> {pnl:.4f} USDT" if pnl is not None else ""
    msg = (
        f"{emoji} <b>CERRADO</b>\n"
        f"<b>Par:</b>  {symbol}\n"
        f"<b>Dir:</b>  {side}"
        f"{pnl_str}"
    )
    send(msg)


def error_alert(msg: str):
    send(f"⚠️ <b>ERROR BOT</b>\n{msg}")


def startup_alert(n_symbols: int, balance: float):
    msg = (
        f"🚀 <b>EMA SCALPING BOT INICIADO</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<b>Balance:</b>   {balance:.2f} USDT\n"
        f"<b>Símbolos:</b>  {n_symbols}\n"
        f"<b>Estrategia:</b> EMA7 / EMA17\n"
        f"<b>Slope min:</b>  30°\n"
        f"━━━━━━━━━━━━━━━━"
    )
    send(msg)
