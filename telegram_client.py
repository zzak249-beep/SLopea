"""
QF×JP Bot v6.3 — Telegram Client
Notificaciones: señal, apertura trade, cierre trade, errores, status.

FIX v6.3.5: notify_blocked() — avisa cuando risk manager rechaza una señal.
"""
import asyncio
import logging
from typing import Optional

import aiohttp

import config as C

log = logging.getLogger("telegram")

BASE_URL = f"https://api.telegram.org/bot{C.TELEGRAM_TOKEN}"


async def _send(text: str, parse_mode: str = "HTML") -> bool:
    if not C.TELEGRAM_TOKEN or not C.TELEGRAM_CHAT_ID:
        log.debug("Telegram no configurado")
        return False
    payload = {
        "chat_id": C.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{BASE_URL}/sendMessage", json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    data = await r.json()
                    if data.get("ok"):
                        return True
                    log.warning("Telegram error: %s", data)
                    return False
        except Exception as e:
            if attempt == 2:
                log.error("Telegram fallo: %s", e)
                return False
            await asyncio.sleep(2)
    return False


def _tier_emoji(tier: str) -> str:
    return {"STD": "⚪", "FUEL": "🔥", "SUP": "💎"}.get(tier, "⚪")

def _dir_emoji(direction: str) -> str:
    return "🟢" if direction == "LONG" else "🔴"

def _score_bar(score: float) -> str:
    filled = int(score / 10)
    return "█" * filled + "░" * (10 - filled)


async def notify_signal(sig) -> bool:
    """Notificación de señal (siempre — independiente de si se abre trade)."""
    dir_e  = _dir_emoji(sig.direction)
    tier_e = _tier_emoji(sig.tier)
    vdi_sign = "🟢 BULL" if sig.vdi > 0 else "🔴 BEAR"
    htf_pct  = f"{sig.htf_score * 100:.0f}%"

    msg = (
        f"📡 <b>SEÑAL — QF×JP v6.3</b>\n"
        f"{'━' * 22}\n"
        f"<b>Par:</b>       {sig.symbol}\n"
        f"<b>Dir:</b>       {dir_e} {sig.direction}\n"
        f"<b>Tier:</b>      {tier_e} {sig.tier}\n"
        f"<b>Score:</b>     {sig.score}/100  {_score_bar(sig.score)}\n"
        f"{'━' * 22}\n"
        f"<b>Entry:</b>     {sig.entry:.6f}\n"
        f"<b>SL:</b>        {sig.sl:.6f}\n"
        f"<b>TP1 (50%):</b> {sig.tp1:.6f}\n"
        f"<b>TP2 (50%):</b> {sig.tp2:.6f}\n"
        f"<b>ATR:</b>       {sig.atr:.6f}\n"
        f"{'━' * 22}\n"
        f"<b>TL Ruptura:</b>  {sig.tl_break} {'🔥' if sig.tl_break_active else ''}\n"
        f"<b>Estructura:</b>  {sig.structure}\n"
        f"<b>ADX:</b>        {sig.adx:.1f}\n"
        f"<b>MFI:</b>        {sig.mfi:.1f}\n"
        f"<b>VDI:</b>        {vdi_sign} ({sig.vdi:+.2f}σ)\n"
        f"<b>HTF Score:</b>  {htf_pct}\n"
        f"<b>CVD:</b>        {sig.cvd:+.3f}\n"
        f"<b>Momentum:</b>   {sig.momentum:+.3f}\n"
        f"{'━' * 22}\n"
        f"<i>Mode: {C.MODE}</i>"
    )
    return await _send(msg)


async def notify_blocked(sig, reason: str) -> bool:
    """Señal encontrada pero bloqueada por risk manager."""
    dir_e  = _dir_emoji(sig.direction)
    tier_e = _tier_emoji(sig.tier)
    msg = (
        f"🚫 <b>SEÑAL BLOQUEADA — QF×JP v6.3</b>\n"
        f"{'━' * 22}\n"
        f"<b>Par:</b>    {sig.symbol}\n"
        f"<b>Dir:</b>    {dir_e} {sig.direction}\n"
        f"<b>Tier:</b>   {tier_e} {sig.tier}  Score: {sig.score}/100\n"
        f"<b>Razón:</b>  <code>{reason}</code>"
    )
    return await _send(msg)


async def notify_trade_opened(sig, qty: float, order_id: str) -> bool:
    dir_e  = _dir_emoji(sig.direction)
    tier_e = _tier_emoji(sig.tier)
    msg = (
        f"✅ <b>TRADE ABIERTO — QF×JP v6.3</b>\n"
        f"{'━' * 22}\n"
        f"<b>Par:</b>     {sig.symbol}\n"
        f"<b>Dir:</b>     {dir_e} {sig.direction}\n"
        f"<b>Tier:</b>    {tier_e} {sig.tier}  Score: {sig.score}/100\n"
        f"<b>Qty:</b>     {qty}\n"
        f"{'━' * 22}\n"
        f"<b>Entry:</b>   {sig.entry:.6f}\n"
        f"<b>SL:</b>      {sig.sl:.6f}  (-{abs(sig.entry - sig.sl):.6f})\n"
        f"<b>TP1:</b>     {sig.tp1:.6f}\n"
        f"<b>TP2:</b>     {sig.tp2:.6f}\n"
        f"{'━' * 22}\n"
        f"<b>OrderID:</b> <code>{order_id}</code>"
    )
    return await _send(msg)


async def notify_trade_closed(
    symbol: str,
    direction: str,
    entry: float,
    close_price: float,
    qty: float,
    reason: str,
    pnl_usdt: float,
) -> bool:
    pnl_emoji = "🟢" if pnl_usdt >= 0 else "🔴"
    sl_dist = abs(entry - close_price)
    if sl_dist > 0:
        if direction == "LONG":
            rr = (close_price - entry) / sl_dist
        else:
            rr = (entry - close_price) / sl_dist
    else:
        rr = 0.0
    msg = (
        f"{pnl_emoji} <b>TRADE CERRADO — QF×JP v6.3</b>\n"
        f"{'━' * 22}\n"
        f"<b>Par:</b>     {symbol}\n"
        f"<b>Dir:</b>     {_dir_emoji(direction)} {direction}\n"
        f"<b>Razón:</b>   {reason}\n"
        f"{'━' * 22}\n"
        f"<b>Entry:</b>   {entry:.6f}\n"
        f"<b>Cierre:</b>  {close_price:.6f}\n"
        f"<b>Qty:</b>     {qty}\n"
        f"<b>PnL:</b>     {pnl_usdt:+.2f} USDT\n"
        f"<b>R:R:</b>     {rr:.2f}"
    )
    return await _send(msg)


async def notify_error(context: str, error: str) -> bool:
    msg = f"⚠️ <b>ERROR</b>\n<b>Contexto:</b> {context}\n<b>Error:</b> <code>{error[:300]}</code>"
    return await _send(msg)


async def notify_status(status: dict, balance: float, n_symbols: int) -> bool:
    msg = (
        f"📊 <b>STATUS — QF×JP v6.3</b>\n"
        f"{'━' * 22}\n"
        f"<b>Mode:</b>        {C.MODE}\n"
        f"<b>Balance:</b>     {balance:.2f} USDT\n"
        f"<b>Símbolos:</b>    {n_symbols}\n"
        f"<b>Posiciones:</b>  {status['open_positions']}/{status['max_open_trades']}\n"
        f"<b>Trades hoy:</b>  {status['daily_trades']}/{status['max_daily_trades']}\n"
        f"<b>PnL hoy:</b>     {status['daily_pnl']:+.2f} USDT\n"
        f"<b>Límite loss:</b> {status['daily_loss_limit']:.2f} USDT"
    )
    return await _send(msg)


async def notify_circuit_breaker(symbol: str) -> bool:
    msg = f"⚡ <b>CIRCUIT BREAKER</b> — {symbol}\nVela gigante detectada, skip señal."
    return await _send(msg)
