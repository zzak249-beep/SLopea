/**
 * ╔══════════════════════════════════════════════════════════════╗
 * ║  APEX FUSION BOT v1.0 — Entry Point                         ║
 * ║  Sniper Predator VSA V8 × QF Machine × JP v3.1              ║
 * ║  BingX Perpetual Futures — Telegram Signals                  ║
 * ╚══════════════════════════════════════════════════════════════╝
 */

require('dotenv').config();
const { CronJob }        = require('cron');
const logger             = require('./logger');
const telegram           = require('./bot');
const scanner            = require('./scanner');
const TradeController    = require('./tradeController');
const bingx              = require('./bingx');

// ─── Validación de entorno ──────────────────────────────────────
function validateEnv() {
  const required = ['BINGX_API_KEY', 'BINGX_SECRET_KEY', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'];
  const missing  = required.filter(k => !process.env[k]);
  if (missing.length > 0) {
    if (process.env.MODE !== 'paper') {
      logger.error(`Variables de entorno faltantes: ${missing.join(', ')}`);
      process.exit(1);
    } else {
      logger.warn(`Paper mode: variables faltantes ignoradas: ${missing.join(', ')}`);
    }
  }
}

// ─── Arranque ──────────────────────────────────────────────────
async function main() {
  logger.info('╔══════════════════════════════════════╗');
  logger.info('║   APEX FUSION BOT v1.0 INICIANDO     ║');
  logger.info('╚══════════════════════════════════════╝');

  validateEnv();

  const mode      = process.env.MODE || 'paper';
  const timeframe = process.env.TIMEFRAME_PRIMARY || '3m';
  const risk      = process.env.RISK_PER_TRADE || '2';
  const maxTrades = process.env.MAX_OPEN_TRADES || '5';
  const minScore  = process.env.MIN_SCORE_ENTRY || '62';
  const leverage  = process.env.LEVERAGE || '10';

  logger.info(`Modo: ${mode.toUpperCase()}`);
  logger.info(`Timeframe: ${timeframe} | HTF: ${process.env.TIMEFRAME_HTF || '15m'}`);
  logger.info(`Riesgo: ${risk}% | Leverage: ${leverage}x | Max trades: ${maxTrades}`);
  logger.info(`Score mínimo entrada: ${minScore}`);

  // Inicializar Trade Controller
  const controller = new TradeController();

  // Inicializar Telegram
  telegram.init(controller);

  // Obtener número de pares activos
  let symbolCount = 0;
  try {
    const symbols = await scanner.getActiveSymbols();
    symbolCount = symbols.length;
    controller.symbolsScanned = symbolCount;
  } catch (e) {
    logger.warn(`No se pudo obtener pares: ${e.message}`);
  }

  // Mensaje de inicio a Telegram
  telegram.sendStartup({
    mode, timeframe, risk: parseFloat(risk),
    maxTrades: parseInt(maxTrades),
    minScore: parseInt(minScore),
    leverage: parseInt(leverage),
    symbolCount,
  });

  // ─── SCANNER CONTINUO ────────────────────────────────────────
  // Intervalo: igual al timeframe primario (3 minutos)
  const intervalMin = timeframe === '3m' ? 3 : timeframe === '5m' ? 5 : 1;
  const scanInterval = scanner.startContinuousScan(controller, intervalMin);

  // ─── MONITOR DE POSICIONES ──────────────────────────────────
  // Monitorear posiciones abiertas cada 30 segundos
  const monitorInterval = setInterval(async () => {
    try {
      await controller.monitorPositions();
    } catch (e) {
      logger.error(`Monitor: ${e.message}`);
    }
  }, 30 * 1000);

  // ─── REPORTE DIARIO ─────────────────────────────────────────
  const dailyReport = new CronJob('0 8 * * *', () => {
    const stats = controller.getStats();
    const wr = stats.total > 0 ? ((stats.wins / stats.total) * 100).toFixed(1) : 0;
    telegram.sendAlert(
      `📊 *Reporte Diario*\n` +
      `Trades: ${stats.total} | WR: ${wr}%\n` +
      `PnL: ${stats.pnl >= 0 ? '+' : ''}${stats.pnl.toFixed(2)} USDT`
    );
  }, null, true, 'UTC');

  // ─── Graceful shutdown ───────────────────────────────────────
  process.on('SIGTERM', async () => {
    logger.info('SIGTERM recibido — cerrando bot...');
    clearInterval(scanInterval);
    clearInterval(monitorInterval);
    telegram.sendAlert('🛑 Bot detenido (SIGTERM)');
    process.exit(0);
  });

  process.on('SIGINT', async () => {
    logger.info('SIGINT recibido — cerrando bot...');
    clearInterval(scanInterval);
    clearInterval(monitorInterval);
    telegram.sendAlert('🛑 Bot detenido manualmente');
    process.exit(0);
  });

  process.on('uncaughtException', (e) => {
    logger.error(`Uncaught Exception: ${e.message}`);
    telegram.sendAlert(`❌ Error crítico: ${e.message}`);
  });

  logger.info('Bot en marcha. Esperando señales...');
}

main().catch(e => {
  console.error('Error fatal al iniciar:', e);
  process.exit(1);
});
