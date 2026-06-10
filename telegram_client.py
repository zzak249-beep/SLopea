"""
QF×JP Bot v6.3.1 — Telegram Client
Notificaciones asíncronas vía Bot API con HTML parse mode.
Funciones: send_message, notify_signal, notify_trade_opened,
           notify_trade_closed, notify_error, notify_status,
           notify_circuit_breaker
"""
import asyncio
import logging
from typing import Optional

import aiohttp

import config as C

log = logging.getLogger("telegram")

_BASE = "https://api.telegram.org"

# ── Envío base ────────────────────────────────────────────────────────────────

async def send_message(text: str) -> bool:
    """Envía un mensaje HTML al chat configurado. Silencia si no hay token."""
    if not C.TELEGRAM_TOKEN or not C.TELEGRAM_CHAT_ID:
        return False

    # Escapar caracteres problemáticos en HTML
    url     = f"{_BASE}/bot{C.TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":                  C.TELEGRAM_CHAT_ID,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    if r.status == 200:
                        return True
                    body = await r.text()
                    log.warning("Telegram %d: %s", r.status, body[:200])
                    # 400 = mensaje mal formado → no reintentar
                    if r.status == 400:
                        return False
        except Exception as e:
            log.warning("send_message attempt %d error: %s", attempt + 1, e)
        await asyncio.sleep(1.5 ** attempt)
    return False


def _esc(text: str) -> str:
    """Escapa caracteres HTML básicos."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


# ── Notificaciones específicas ────────────────────────────────────────────────

async def notify_signal(sig) -> None:
    """Señal detectada (SIGNAL o LIVE antes de abrir)."""
    tier_emoji = {"STD": "⚪", "FUEL": "🟡", "SUP": "🔵"}.get(sig.tier, "⚪")
    dir_emoji  = "🟢 LONG" if sig.direction == "LONG" else "🔴 SHORT"

    text = (
        f"{tier_emoji} <b>SEÑAL {sig.tier} — {dir_emoji}</b>\n"
        f"📌 <b>{_esc(sig.symbol)}</b>\n"
        f"📊 Score: <b>{sig.score:.1f}</b>\n"
        f"💲 Entry: <code>{sig.entry:.6f}</code>\n"
        f"🛡 SL:    <code>{sig.sl:.6f}</code>\n"
        f"🎯 TP1:   <code>{sig.tp1:.6f}</code>\n"
        f"🎯 TP2:   <code>{sig.tp2:.6f}</code>"
    )
    await send_message(text)


async def notify_trade_opened(sig, qty: float, order_id: str) -> None:
    """Trade abierto con éxito en BingX."""
    dir_emoji = "🟢 LONG" if sig.direction == "LONG" else "🔴 SHORT"
    sl_pct = abs(sig.entry - sig.sl) / sig.entry * 100 * C.LEVERAGE

    text = (
        f"✅ <b>TRADE ABIERTO — {dir_emoji}</b>\n"
        f"📌 <b>{_esc(sig.symbol)}</b> | Tier: {sig.tier} | Score: {sig.score:.1f}\n"
        f"💲 Entry:    <code>{sig.entry:.6f}</code>\n"
        f"📦 Qty:      <code>{qty:.6f}</code>\n"
        f"🛡 SL:       <code>{sig.sl:.6f}</code> ({sl_pct:.1f}% riesgo)\n"
        f"🎯 TP1:      <code>{sig.tp1:.6f}</code>\n"
        f"🎯 TP2:      <code>{sig.tp2:.6f}</code>\n"
        f"🆔 Order:    <code>{_esc(order_id)}</code>"
    )
    await send_message(text)


async def notify_trade_closed(
    symbol: str,
    direction: str,
    entry: float,
    close_price: float,
    qty: float,
    reason: str,
    pnl: float,
) -> None:
    """Trade cerrado (SL, TP, trailing, emergencia, tiempo)."""
    reason_map = {
        "sl_tp_auto":    "🏁 SL/TP automático",
        "tp1_partial":   "🎯 TP1 parcial (50%)",
        "max_hold_time": "⏱ Tiempo máximo",
        "manual_close":  "🖐 Cierre manual",
        "emergency":     "🚨 Emergencia",
    }
    reason_str = reason_map.get(reason, f"📤 {reason}")
    pnl_emoji  = "✅" if pnl >= 0 else "❌"
    dir_str    = "LONG" if direction == "LONG" else "SHORT"

    text = (
        f"{pnl_emoji} <b>TRADE CERRADO — {dir_str}</b>\n"
        f"📌 <b>{_esc(symbol)}</b>\n"
        f"📋 Razón:    {reason_str}\n"
        f"💲 Entry:    <code>{entry:.6f}</code>\n"
        f"💲 Cierre:   <code>{close_price:.6f}</code>\n"
        f"📦 Qty:      <code>{qty:.6f}</code>\n"
        f"💰 PnL:      <b>{'+' if pnl >= 0 else ''}{pnl:.4f} USDT</b>"
    )
    await send_message(text)


async def notify_error(context: str, error: str) -> None:
    """Error crítico del bot."""
    text = (
        f"🚨 <b>ERROR — {_esc(context)}</b>\n"
        f"<code>{_esc(error[:400])}</code>"
    )
    await send_message(text)


async def notify_status(risk_status: dict, balance: float, n_symbols: int) -> None:
    """Status periódico del bot."""
    text = (
        f"📡 <b>STATUS BOT</b>\n"
        f"💰 Balance:    <code>{balance:.2f} USDT</code>\n"
        f"📂 Posiciones: <code>{risk_status.get('open_positions', 0)}"
        f"/{risk_status.get('max_open_trades', 0)}</code>\n"
        f"📈 Trades hoy: <code>{risk_status.get('daily_trades', 0)}"
        f"/{risk_status.get('max_daily_trades', 0)}</code>\n"
        f"💵 PnL hoy:    <code>{risk_status.get('daily_pnl', 0):+.2f} USDT</code>\n"
        f"🔍 Símbolos:   <code>{n_symbols}</code>\n"
        f"⚙️ Modo:       <code>{_esc(C.MODE)}</code>"
    )
    await send_message(text)


async def notify_circuit_breaker(symbol: str) -> None:
    """Circuit breaker activado por vela gigante."""
    text = (
        f"⚡ <b>CIRCUIT BREAKER</b>\n"
        f"📌 {_esc(symbol)} — pausa {C.CB_BARS} velas por vela gigante"
    )
    await send_message(text)
