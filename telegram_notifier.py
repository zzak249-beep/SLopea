"""
SAMA APEX Bot - Telegram Notifier
Mensajes ricos y estructurados para todas las señales y eventos
"""
import aiohttp
import asyncio
import logging
from datetime import datetime, timezone
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


async def _send(text: str, parse_mode: str = "HTML"):
    """Envío async de mensaje a Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram no configurado")
        return
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id":    TELEGRAM_CHAT_ID,
                    "text":       text,
                    "parse_mode": parse_mode,
                },
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                resp = await r.json()
                if not resp.get("ok"):
                    logger.error(f"Telegram error: {resp}")
    except Exception as e:
        logger.error(f"Telegram send error: {e}")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


# ─── Signal Alert ─────────────────────────────────────────────────────────────

async def notify_signal(symbol: str, signal: dict, confluence: dict,
                         entry: float, sl: float, tp: float,
                         qty: float, balance: float):
    dir_emoji = "🟢 LONG" if signal["direction"] == "LONG" else "🔴 SHORT"
    score     = confluence["score"]
    score_bar = "█" * (score // 10) + "░" * (10 - score // 10)

    risk_pct  = abs(entry - sl) / entry * 100
    rr_ratio  = abs(tp - entry) / abs(entry - sl)

    text = f"""
<b>⚡ SAMA APEX — NUEVA SEÑAL</b>
━━━━━━━━━━━━━━━━━━━
<b>Par:</b>      {symbol}
<b>Dirección:</b> {dir_emoji}
<b>Hora:</b>     {_ts()}

<b>— Precios —</b>
📍 <b>Entrada:</b>  <code>{entry:.4f}</code>
🛑 <b>Stop Loss:</b> <code>{sl:.4f}</code>  ({risk_pct:.2f}%)
🎯 <b>Take Profit:</b> <code>{tp:.4f}</code>
⚖️ <b>R:R Ratio:</b>  1 : {rr_ratio:.1f}

<b>— Confluence Score —</b>
<code>[{score_bar}] {score}/100</code>

<b>— Multi-TF Alignment —</b>
🕐 Local:   {confluence.get('lt','?')}
🕒 Macro 1: {confluence.get('m1t','?')}
🕙 Macro 2: {confluence.get('m2t','?')}

<b>— Condiciones —</b>
📊 Slope avg:   {confluence.get('avg_slope', 0):.1f}°
📦 RVOL avg:    {confluence.get('avg_rvol', 0):.2f}x
💰 Funding:     {confluence.get('funding', 0)*100:.4f}%
🏦 Sesión:      {'Activa ✅' if confluence.get('session') else 'Inactiva ⚠️'}

<b>— Orden —</b>
🔢 Qty:      <code>{qty:.4f}</code>
💵 Balance:  <code>{balance:.2f} USDT</code>
━━━━━━━━━━━━━━━━━━━
<i>Señal generada por SAMA APEX Bot</i>
""".strip()

    await _send(text)


# ─── Trade Executed ───────────────────────────────────────────────────────────

async def notify_trade_opened(symbol: str, direction: str,
                               entry: float, sl: float, tp: float, qty: float):
    emoji = "🟢" if direction == "LONG" else "🔴"
    text = f"""
{emoji} <b>TRADE ABIERTO</b> — {symbol}
<b>Dir:</b> {direction}  |  <b>Entry:</b> <code>{entry:.4f}</code>
<b>SL:</b> <code>{sl:.4f}</code>  |  <b>TP:</b> <code>{tp:.4f}</code>
<b>Qty:</b> <code>{qty:.4f}</code>  |  <b>Hora:</b> {_ts()}
""".strip()
    await _send(text)


# ─── Trade Closed ─────────────────────────────────────────────────────────────

async def notify_trade_closed(result: dict):
    pnl = result.get("pnl_pct", 0)
    emoji = "✅ WIN" if pnl >= 0 else "❌ LOSS"
    text = f"""
{emoji} — <b>{result['symbol']}</b>
<b>Dir:</b> {result['direction']}
<b>Entry:</b> <code>{result['entry']:.4f}</code> → <b>Exit:</b> <code>{result['exit']:.4f}</code>
<b>PnL:</b> <code>{pnl*100:+.2f}%</code>  |  <b>Hora:</b> {_ts()}
""".strip()
    await _send(text)


# ─── Trailing Stop Update ─────────────────────────────────────────────────────

async def notify_trailing_update(symbol: str, old_sl: float, new_sl: float):
    text = f"""
🔄 <b>TRAILING STOP</b> — {symbol}
SL movido: <code>{old_sl:.4f}</code> → <code>{new_sl:.4f}</code>
{_ts()}
""".strip()
    await _send(text)


# ─── Circuit Breaker ──────────────────────────────────────────────────────────

async def notify_circuit_breaker(daily_pnl: float, balance: float):
    text = f"""
🚨 <b>CIRCUIT BREAKER ACTIVADO</b>
PnL del día: <code>{daily_pnl*100:+.2f}%</code>
Balance: <code>{balance:.2f} USDT</code>
Bot en pausa hasta mañana (UTC).
{_ts()}
""".strip()
    await _send(text)


# ─── Daily Summary ────────────────────────────────────────────────────────────

async def notify_daily_summary(stats: dict, balance: float):
    wr = stats.get("win_rate", 0)
    text = f"""
📊 <b>RESUMEN DIARIO — SAMA APEX</b>
━━━━━━━━━━━━━━━━━━━
💰 <b>Balance:</b>   <code>{balance:.2f} USDT</code>
📈 <b>PnL día:</b>  <code>{stats['daily_pnl']*100:+.2f}%</code>
🏆 <b>Trades:</b>   {stats['trades']} ({stats['wins']}W / {stats['losses']}L)
🎯 <b>Win Rate:</b> {wr*100:.1f}%
━━━━━━━━━━━━━━━━━━━
{_ts()}
""".strip()
    await _send(text)


# ─── Error Alert ──────────────────────────────────────────────────────────────

async def notify_error(msg: str):
    await _send(f"⚠️ <b>ERROR BOT</b>\n<code>{msg[:300]}</code>\n{_ts()}")


# ─── Startup ──────────────────────────────────────────────────────────────────

async def notify_startup(symbols: list, tfs: tuple):
    text = f"""
🚀 <b>SAMA APEX BOT INICIADO</b>
━━━━━━━━━━━━━━━━━━━
📊 <b>Pares:</b> {', '.join(symbols)}
⏱ <b>TFs:</b>   {tfs[0]} / {tfs[1]} / {tfs[2]}
🕐 <b>Hora:</b>  {_ts()}
━━━━━━━━━━━━━━━━━━━
<i>Monitoring activo. Esperando señales...</i>
""".strip()
    await _send(text)
