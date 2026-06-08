"""
telegram_client.py — Notificaciones Telegram con panel completo QF×JP
"""
import aiohttp
import logging
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config as cfg

log = logging.getLogger("telegram")

TIER_EMOJI = {"STD": "▲", "FUEL": "🔥", "SUP": "★", "NONE": "–"}
DIR_EMOJI  = {"LONG": "🟢", "SHORT": "🔴", "NONE": "⚪"}


async def send_message(text: str):
    if not cfg.TELEGRAM_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        log.debug("Telegram no configurado")
        return
    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    cfg.TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    log.error(f"Telegram error {r.status}: {await r.text()}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")


async def notify_signal(symbol: str, result, mode: str = "SIGNAL"):
    """Envía panel de señal completo"""
    from indicators import ScoreResult
    r: ScoreResult = result

    tier_e  = TIER_EMOJI.get(r.tier, "–")
    dir_e   = DIR_EMOJI.get(r.direction, "⚪")
    mode_tag = "🚀 TRADE ABIERTO" if mode == "LIVE" else "📡 SEÑAL"

    tl_str  = f"{'LONG 🔥' if r.tl_break == 'LONG' else 'SHORT 🔥' if r.tl_break == 'SHORT' else '–'}"
    choch   = r.choch_bos
    struct  = []
    if choch.get("choch_long"):  struct.append("CHoCH↑")
    if choch.get("choch_short"): struct.append("CHoCH↓")
    if choch.get("bos_long"):    struct.append("BoS↑")
    if choch.get("bos_short"):   struct.append("BoS↓")
    struct_str = " ".join(struct) if struct else "–"

    vdi_str = f"{'🟢 BULL' if r.vdi_bull else '🔴 BEAR' if r.vdi_bear else '–'} ({r.vdi_z:+.2f}σ)"

    text = (
        f"<b>{mode_tag} — QF×JP v3.5 PREDATOR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Par:</b>       {symbol}\n"
        f"<b>Dir:</b>       {dir_e} {r.direction}\n"
        f"<b>Tier:</b>      {tier_e} {r.tier}\n"
        f"<b>Score:</b>     {r.score:.0f}/100\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Entry:</b>     {r.entry_price:.6f}\n"
        f"<b>SL:</b>        {r.sl_price:.6f}\n"
        f"<b>TP1 (50%):</b> {r.tp1_price:.6f}\n"
        f"<b>TP2 (50%):</b> {r.tp2_price:.6f}\n"
        f"<b>ATR:</b>       {r.atr:.6f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>TL Ruptura:</b> {tl_str}\n"
        f"<b>Estructura:</b> {struct_str}\n"
        f"<b>ADX:</b>       {r.adx:.1f}\n"
        f"<b>MFI:</b>       {r.mfi:.1f}\n"
        f"<b>VDI:</b>       {vdi_str}\n"
        f"<b>HTF Score:</b> {r.htf_score:.2f}\n"
        f"<b>CVD:</b>       {r.cvd_score:+.3f}\n"
        f"<b>Momentum:</b>  {r.norm_score:+.3f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Modo: {cfg.MODE} | Leverage: {cfg.LEVERAGE}x</i>"
    )
    await send_message(text)


async def notify_trade_opened(symbol: str, direction: str, qty: float, entry: float,
                               sl: float, tp1: float, tp2: float, tier: str):
    dir_e  = DIR_EMOJI.get(direction, "⚪")
    tier_e = TIER_EMOJI.get(tier, "–")
    text = (
        f"<b>✅ TRADE ABIERTO — {symbol}</b>\n"
        f"{dir_e} {direction} {tier_e} {tier}\n"
        f"<b>Qty:</b>  {qty}\n"
        f"<b>Entry:</b> {entry:.6f}\n"
        f"<b>SL:</b>   {sl:.6f}\n"
        f"<b>TP1:</b>  {tp1:.6f}\n"
        f"<b>TP2:</b>  {tp2:.6f}\n"
        f"<i>{cfg.LEVERAGE}x leverage</i>"
    )
    await send_message(text)


async def notify_error(symbol: str, error: str):
    await send_message(f"⚠️ <b>ERROR {symbol}</b>\n<code>{error}</code>")


async def notify_summary(data: dict):
    text = (
        f"<b>📊 Resumen del escáner</b>\n"
        f"Símbolos escaneados: {data.get('scanned', 0)}\n"
        f"Señales generadas:   {data.get('signals', 0)}\n"
        f"Trades hoy:          {data.get('trades_today', 0)}/{data.get('max_daily', 0)}\n"
        f"Posiciones abiertas: {data.get('open_count', 0)}/{data.get('max_open', 0)}\n"
        f"Símbolos: {', '.join(data.get('open_symbols', [])) or '–'}"
    )
    await send_message(text)
