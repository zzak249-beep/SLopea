/**
 * ╔══════════════════════════════════════════════════════════════╗
 * ║  TELEGRAM SIGNAL BOT                                         ║
 * ║  Notificaciones + Control remoto del bot                     ║
 * ╚══════════════════════════════════════════════════════════════╝
 */

const TelegramBot = require('node-telegram-bot-api');
const logger = require('../utils/logger');

let bot = null;
const chatId = process.env.TELEGRAM_CHAT_ID;

// Emojis por nivel de señal
const LEVEL_EMOJI = {
  PRIME:   '🔱',
  SUPREMA: '⭐',
  FUEL:    '🔥',
  STD:     '📶',
};

const DIR_EMOJI = { LONG: '🟢', SHORT: '🔴' };

function init(tradeController) {
  if (!process.env.TELEGRAM_BOT_TOKEN) {
    logger.warn('Telegram: sin token — notificaciones desactivadas');
    return;
  }

  bot = new TelegramBot(process.env.TELEGRAM_BOT_TOKEN, { polling: true });
  logger.info('Telegram bot iniciado');

  // ─── Comandos ─────────────────────────────────────────────
  bot.onText(/\/start/, (msg) => {
    bot.sendMessage(msg.chat.id,
      `🤖 *APEX FUSION BOT* v1.0\n\n` +
      `Fusión: Sniper VSA V8 × QF Machine v3.1\n\n` +
      `Comandos disponibles:\n` +
      `/status — Estado del bot\n` +
      `/positions — Posiciones abiertas\n` +
      `/balance — Balance de cuenta\n` +
      `/stats — Estadísticas de rendimiento\n` +
      `/pause — Pausar trading\n` +
      `/resume — Reanudar trading\n` +
      `/closeall — Cerrar todas las posiciones\n` +
      `/risk [%] — Cambiar riesgo por trade`,
      { parse_mode: 'Markdown' }
    );
  });

  bot.onText(/\/status/, async (msg) => {
    if (!tradeController) return;
    const s = await tradeController.getStatus();
    bot.sendMessage(msg.chat.id, formatStatus(s), { parse_mode: 'Markdown' });
  });

  bot.onText(/\/positions/, async (msg) => {
    if (!tradeController) return;
    const pos = await tradeController.getPositions();
    if (!pos || pos.length === 0) {
      bot.sendMessage(msg.chat.id, '📭 Sin posiciones abiertas.');
      return;
    }
    const text = pos.map(p =>
      `${p.side === 'LONG' ? '🟢' : '🔴'} *${p.symbol}*\n` +
      `Entrada: ${p.entryPrice.toFixed(6)}\n` +
      `PnL: ${p.unrealizedPnl >= 0 ? '+' : ''}${p.unrealizedPnl.toFixed(2)} USDT\n` +
      `Leverage: ${p.leverage}x`
    ).join('\n\n');
    bot.sendMessage(msg.chat.id, text, { parse_mode: 'Markdown' });
  });

  bot.onText(/\/balance/, async (msg) => {
    if (!tradeController) return;
    const bal = await tradeController.getBalance();
    bot.sendMessage(msg.chat.id,
      `💰 *Balance*\n` +
      `Equity: $${bal.equity?.toFixed(2) || '—'}\n` +
      `Disponible: $${bal.availableMargin?.toFixed(2) || '—'}`,
      { parse_mode: 'Markdown' }
    );
  });

  bot.onText(/\/stats/, async (msg) => {
    if (!tradeController) return;
    const st = tradeController.getStats();
    bot.sendMessage(msg.chat.id, formatStats(st), { parse_mode: 'Markdown' });
  });

  bot.onText(/\/pause/, (msg) => {
    if (!tradeController) return;
    tradeController.pause();
    bot.sendMessage(msg.chat.id, '⏸ Bot pausado. Posiciones existentes siguen activas.');
  });

  bot.onText(/\/resume/, (msg) => {
    if (!tradeController) return;
    tradeController.resume();
    bot.sendMessage(msg.chat.id, '▶️ Bot reanudado. Buscando señales...');
  });

  bot.onText(/\/closeall/, async (msg) => {
    if (!tradeController) return;
    bot.sendMessage(msg.chat.id, '⚠️ Cerrando todas las posiciones...');
    await tradeController.closeAll();
    bot.sendMessage(msg.chat.id, '✅ Todas las posiciones cerradas.');
  });

  bot.onText(/\/risk (.+)/, (msg, match) => {
    const pct = parseFloat(match[1]);
    if (isNaN(pct) || pct < 0.1 || pct > 10) {
      bot.sendMessage(msg.chat.id, '❌ Riesgo inválido. Usa un valor entre 0.1 y 10 (%)');
      return;
    }
    process.env.RISK_PER_TRADE = pct.toString();
    bot.sendMessage(msg.chat.id, `✅ Riesgo cambiado a ${pct}% por trade`);
  });

  bot.on('polling_error', (err) => {
    logger.error(`Telegram polling error: ${err.message}`);
  });
}

// ─── Mensajes de señal ─────────────────────────────────────────

function sendSignal(result, symbol) {
  if (!bot || !chatId || !result.signal) return;

  const emoji  = LEVEL_EMOJI[result.signalLevel] || '📶';
  const dir    = DIR_EMOJI[result.signalDir] || '•';
  const isPrime = result.signalLevel === 'PRIME';

  const rr = result.sl && result.tp
    ? (Math.abs(result.tp - result.price) / Math.abs(result.price - result.sl)).toFixed(2)
    : '?';

  const text = [
    isPrime ? `🔱🔱 *APEX PRIME SIGNAL* 🔱🔱` : `${emoji} *APEX ${result.signalLevel}* ${emoji}`,
    `${dir} *${result.signalDir}* — \`${symbol}\``,
    ``,
    `💵 Precio: \`${result.price.toFixed(6)}\``,
    result.sl ? `🛑 Stop Loss: \`${result.sl.toFixed(6)}\`` : '',
    result.tp ? `🎯 Take Profit: \`${result.tp.toFixed(6)}\`` : '',
    `📐 R/R: ${rr}:1`,
    ``,
    `📊 *Scores*`,
    `• Apex LONG:  ${result.apexScoreLong}/100`,
    `• Apex SHORT: ${result.apexScoreShort}/100`,
    ``,
    `🧠 *Motores*`,
    `• VSA: ${result.vsa?.pattern || '—'}`,
    `• QF Score: ${result.signalDir === 'LONG' ? result.apexScoreLong : result.apexScoreShort}`,
    `• CVD: ${result.qf?.cvdRising ? '↑ Alcista' : '↓ Bajista'}`,
    result.qf?.sqBull ? '• 🔵 Squeeze Fire ↑' : result.qf?.sqBear ? '• 🔵 Squeeze Fire ↓' : '',
    result.qf?.dpBuy  ? '• 💠 Dark Pool BUY'  : result.qf?.dpSell ? '• 💠 Dark Pool SELL' : '',
    ``,
    `⏱ ${new Date().toUTCString()}`,
    isPrime ? `\n🔱 *TRIPLE CONFLUENCIA DETECTADA — MÁXIMA PRIORIDAD*` : '',
  ].filter(Boolean).join('\n');

  bot.sendMessage(chatId, text, { parse_mode: 'Markdown' }).catch(e => {
    logger.error(`Telegram send signal: ${e.message}`);
  });
}

function sendTradeOpen(trade) {
  if (!bot || !chatId) return;
  const dir = DIR_EMOJI[trade.side] || '•';
  const text = [
    `${dir} *TRADE ABIERTO*`,
    `Par: \`${trade.symbol}\``,
    `Dirección: ${trade.side}`,
    `Precio entrada: \`${trade.entryPrice}\``,
    `Stop Loss: \`${trade.sl}\``,
    `Take Profit: \`${trade.tp}\``,
    `Tamaño: ${trade.qty} contratos`,
    `Apalancamiento: ${trade.leverage}x`,
    `Modo: ${process.env.MODE === 'paper' ? '📝 PAPER' : '💰 REAL'}`,
  ].join('\n');
  bot.sendMessage(chatId, text, { parse_mode: 'Markdown' }).catch(() => {});
}

function sendTradeClose(trade) {
  if (!bot || !chatId) return;
  const pnlEmoji = trade.pnl >= 0 ? '✅' : '❌';
  const text = [
    `${pnlEmoji} *TRADE CERRADO*`,
    `Par: \`${trade.symbol}\``,
    `Resultado: ${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)} USDT`,
    `R/R alcanzado: ${trade.rrReached?.toFixed(2) || '—'}`,
    `Razón: ${trade.reason || '—'}`,
  ].join('\n');
  bot.sendMessage(chatId, text, { parse_mode: 'Markdown' }).catch(() => {});
}

function sendAlert(msg) {
  if (!bot || !chatId) return;
  bot.sendMessage(chatId, `⚠️ ${msg}`, { parse_mode: 'Markdown' }).catch(() => {});
}

function sendStartup(config) {
  if (!bot || !chatId) return;
  const text = [
    `🚀 *APEX FUSION BOT INICIADO*`,
    `Modo: ${config.mode === 'paper' ? '📝 Paper Trading' : '💰 Live Trading'}`,
    `Timeframe: ${config.timeframe}`,
    `Riesgo/trade: ${config.risk}%`,
    `Max trades: ${config.maxTrades}`,
    `Score mínimo: ${config.minScore}`,
    `Leverage: ${config.leverage}x`,
    ``,
    `Bot activo. Escaneando ${config.symbolCount || '?'} pares de BingX...`,
  ].join('\n');
  bot.sendMessage(chatId, text, { parse_mode: 'Markdown' }).catch(() => {});
}

// ─── Formatters ────────────────────────────────────────────────

function formatStatus(s) {
  if (!s) return 'Sin datos';
  return [
    `🤖 *Estado del Bot*`,
    `• Estado: ${s.paused ? '⏸ Pausado' : '▶️ Activo'}`,
    `• Modo: ${s.mode === 'paper' ? '📝 Paper' : '💰 Live'}`,
    `• Trades abiertos: ${s.openTrades}/${s.maxTrades}`,
    `• Pares escaneados: ${s.symbolsScanned}`,
    `• Uptime: ${s.uptime}`,
    `• Última señal: ${s.lastSignal || 'Ninguna'}`,
  ].join('\n');
}

function formatStats(st) {
  if (!st) return 'Sin estadísticas aún';
  const wr = st.total > 0 ? ((st.wins / st.total) * 100).toFixed(1) : '0';
  return [
    `📈 *Estadísticas*`,
    `• Total trades: ${st.total}`,
    `• Ganados: ${st.wins} | Perdidos: ${st.losses}`,
    `• Win Rate: ${wr}%`,
    `• PnL Total: ${st.pnl >= 0 ? '+' : ''}${st.pnl?.toFixed(2)} USDT`,
    `• Mejor trade: +${st.bestTrade?.toFixed(2) || 0} USDT`,
    `• Peor trade: ${st.worstTrade?.toFixed(2) || 0} USDT`,
    `• R/R promedio: ${st.avgRR?.toFixed(2) || 0}`,
  ].join('\n');
}

module.exports = { init, sendSignal, sendTradeOpen, sendTradeClose, sendAlert, sendStartup };
