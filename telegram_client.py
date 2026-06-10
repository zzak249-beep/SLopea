"""
QF×JP Bot v6.3.1 — Telegram Client
Rate limiter: máx 1 mensaje cada 3s, cola interna de 20, dedup 60s.
Evita el ban 429 que ocurre cuando el bot envía decenas de mensajes/min.
"""
import asyncio
import logging
import time
from typing import Optional

import aiohttp

import config as C

log = logging.getLogger("telegram")
_BASE = "https://api.telegram.org"

# ── Rate limiter ──────────────────────────────────────────────────────────────
_lock       = asyncio.Lock()
_last_sent  = 0.0          # timestamp del último envío exitoso
_MIN_GAP    = 3.0          # segundos mínimos entre mensajes
_sent_cache: dict[str, float] = {}  # hash → timestamp para dedup
_DEDUP_TTL  = 60.0         # segundos antes de permitir el mismo msg

def _should_skip(text: str) -> bool:
    """Devuelve True si el mismo mensaje fue enviado hace menos de DEDUP_TTL seg."""
    key = text[:80]  # primeros 80 chars como clave
    now = time.time()
    last = _sent_cache.get(key, 0.0)
    if now - last < _DEDUP_TTL:
        return True
    _sent_cache[key] = now
    # Limpiar entradas viejas
    old = [k for k, v in _sent_cache.items() if now - v > _DEDUP_TTL * 2]
    for k in old:
        del _sent_cache[k]
    return False

def _esc(text: str) -> str:
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

# ── Envío base ────────────────────────────────────────────────────────────────

async def send_message(text: str) -> bool:
    """Envía mensaje HTML con rate limiting. Silencioso si no hay token."""
    if not C.TELEGRAM_TOKEN or not C.TELEGRAM_CHAT_ID:
        return False

    global _last_sent

    async with _lock:
        # Throttle: respetar gap mínimo entre mensajes
        now   = time.time()
        gap   = now - _last_sent
        if gap < _MIN_GAP:
            await asyncio.sleep(_MIN_GAP - gap)

        # Dedup: no enviar el mismo mensaje dos veces en 60s
        if _should_skip(text):
            return True

        url     = f"{_BASE}/bot{C.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id":                  C.TELEGRAM_CHAT_ID,
            "text":                     text,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }

        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url, json=payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as r:
                        if r.status == 200:
                            _last_sent = time.time()
                            return True
                        body = await r.text()
                        # 429 = rate limit → esperar retry_after
                        if r.status == 429:
                            import json as _json
                            try:
                                data       = _json.loads(body)
                                retry_after = int(data.get("parameters", {}).get("retry_after", 30))
                                log.warning("Telegram 429 — esperando %ds", retry_after)
                                # No esperar retry_after completo (puede ser 12h)
                                # Solo logear y salir
                            except Exception:
                                pass
                            return False
                        if r.status == 400:
                            log.warning("Telegram 400 (msg mal formado): %s", body[:100])
                            return False
                        log.warning("Telegram %d: %s", r.status, body[:100])
            except Exception as e:
                log.warning("send_message attempt %d error: %s", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(2)

    return False

# ── Notificaciones específicas ────────────────────────────────────────────────

async def notify_signal(sig) -> None:
    """Solo en SIGNAL mode — en LIVE no llamar (usar notify_trade_opened)."""
    tier_emoji = {"STD": "⚪", "FUEL": "🟡", "SUP": "🔵"}.get(sig.tier, "⚪")
    dir_str    = "LONG" if sig.direction == "LONG" else "SHORT"
    text = (
        f"{tier_emoji} <b>{dir_str} {sig.tier}</b> — {_esc(sig.symbol)}\n"
        f"Score: <b>{sig.score:.0f}</b> | Entry: <code>{sig.entry:.6f}</code>\n"
        f"SL: <code>{sig.sl:.6f}</code> | TP1: <code>{sig.tp1:.6f}</code>"
    )
    await send_message(text)


async def notify_trade_opened(sig, qty: float, order_id: str) -> None:
    dir_str   = "🟢 LONG" if sig.direction == "LONG" else "🔴 SHORT"
    sl_pct    = abs(sig.entry - sig.sl) / sig.entry * 100 * C.LEVERAGE
    text = (
        f"✅ <b>TRADE ABIERTO — {dir_str}</b>\n"
        f"📌 <b>{_esc(sig.symbol)}</b> | {sig.tier} | Score {sig.score:.0f}\n"
        f"Entry: <code>{sig.entry:.6f}</code> | Qty: <code>{qty:.4f}</code>\n"
        f"SL: <code>{sig.sl:.6f}</code> ({sl_pct:.1f}% riesgo)\n"
        f"TP1: <code>{sig.tp1:.6f}</code> | TP2: <code>{sig.tp2:.6f}</code>"
    )
    await send_message(text)


async def notify_trade_closed(
    symbol: str, direction: str, entry: float, close_price: float,
    qty: float, reason: str, pnl: float,
) -> None:
    reason_map = {
        "sl_tp_auto":    "🏁 SL/TP auto",
        "tp1_partial":   "🎯 TP1 50%",
        "max_hold_time": "⏱ Tiempo máximo",
        "manual_close":  "🖐 Manual",
        "emergency":     "🚨 Emergencia",
    }
    r_str    = reason_map.get(reason, reason)
    pnl_icon = "✅" if pnl >= 0 else "❌"
    text = (
        f"{pnl_icon} <b>CERRADO {direction}</b> — {_esc(symbol)}\n"
        f"{r_str} | Entry: <code>{entry:.6f}</code> → <code>{close_price:.6f}</code>\n"
        f"PnL: <b>{'+' if pnl >= 0 else ''}{pnl:.4f} USDT</b>"
    )
    await send_message(text)


async def notify_error(context: str, error: str) -> None:
    text = (
        f"🚨 <b>ERROR</b> — {_esc(context)}\n"
        f"<code>{_esc(str(error)[:300])}</code>"
    )
    await send_message(text)


async def notify_status(risk_status: dict, balance: float, n_symbols: int) -> None:
    text = (
        f"📡 <b>STATUS</b> | {_esc(C.MODE)}\n"
        f"Balance: <code>{balance:.2f} USDT</code>\n"
        f"Posiciones: <code>{risk_status.get('open_positions',0)}"
        f"/{risk_status.get('max_open_trades',0)}</code>\n"
        f"Trades hoy: <code>{risk_status.get('daily_trades',0)}"
        f"/{risk_status.get('max_daily_trades',0)}</code>\n"
        f"PnL hoy: <code>{risk_status.get('daily_pnl',0):+.2f} USDT</code>"
    )
    await send_message(text)


async def notify_circuit_breaker(symbol: str) -> None:
    await send_message(f"⚡ <b>CIRCUIT BREAKER</b> — {_esc(symbol)}")
