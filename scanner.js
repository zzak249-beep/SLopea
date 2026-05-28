/**
 * ╔══════════════════════════════════════════════════════════════╗
 * ║  SCANNER — Escanea todos los pares de BingX                 ║
 * ║  Filtra por volumen, aplica APEX FUSION, envía señales      ║
 * ╚══════════════════════════════════════════════════════════════╝
 */

const bingx    = require('../exchange/bingx');
const { apexFusion } = require('../engine/apexFusion');
const telegram = require('../telegram/bot');
const logger   = require('../utils/logger');

const TF_PRIMARY = process.env.TIMEFRAME_PRIMARY || '3m';
const TF_HTF     = process.env.TIMEFRAME_HTF     || '15m';
const TF_TREND   = process.env.TIMEFRAME_TREND   || '1h';
const MIN_VOL    = parseFloat(process.env.MIN_VOLUME_24H || 500000);
const EXCLUDED   = (process.env.EXCLUDED_PAIRS || '').split(',').map(s => s.trim()).filter(Boolean);

// Cooldown por símbolo: evita señales repetidas (ms)
const SIGNAL_COOLDOWN = 30 * 60 * 1000; // 30 min
const signalCooldowns = new Map();

// Rate limiting: esperar entre requests para no ser baneado
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

/**
 * Filtra pares por volumen y excluidos
 */
async function getActiveSymbols() {
  try {
    const tickers = await bingx.getAllTickers();
    const filtered = tickers
      .filter(t => {
        if (!t.symbol.endsWith('-USDT') && !t.symbol.endsWith('USDT')) return false;
        if (EXCLUDED.includes(t.symbol)) return false;
        if (t.volume24h < MIN_VOL) return false;
        return true;
      })
      .sort((a, b) => b.volume24h - a.volume24h)
      .slice(0, 80); // máximo 80 pares para no saturar

    logger.info(`Scanner: ${filtered.length} pares activos (vol > $${MIN_VOL.toLocaleString()})`);
    return filtered.map(t => t.symbol);
  } catch (e) {
    logger.error(`getActiveSymbols: ${e.message}`);
    return [];
  }
}

/**
 * Analiza un símbolo con el motor APEX FUSION
 */
async function analyzeSymbol(symbol) {
  try {
    const [candles, htfCandles, trendCandles] = await Promise.all([
      bingx.getCandles(symbol, TF_PRIMARY, 200),
      bingx.getCandles(symbol, TF_HTF,    100),
      bingx.getCandles(symbol, TF_TREND,   60),
    ]);

    if (!candles || candles.length < 120) return null;

    const result = apexFusion(candles, htfCandles, trendCandles);
    if (!result.valid) return null;

    return { symbol, result };
  } catch (e) {
    logger.debug(`analyzeSymbol ${symbol}: ${e.message}`);
    return null;
  }
}

/**
 * Comprueba si el símbolo está en cooldown
 */
function isInCooldown(symbol) {
  const last = signalCooldowns.get(symbol);
  if (!last) return false;
  return Date.now() - last < SIGNAL_COOLDOWN;
}

/**
 * Scan completo de todos los pares
 */
async function runScan(tradeController, onSignal) {
  const symbols = await getActiveSymbols();
  if (!symbols.length) {
    logger.warn('Scanner: sin símbolos activos');
    return [];
  }

  const signals = [];
  let analyzed = 0;

  // Procesar en batches de 5 para no saturar la API
  const BATCH = 5;
  for (let i = 0; i < symbols.length; i += BATCH) {
    const batch = symbols.slice(i, i + BATCH);

    const results = await Promise.all(batch.map(s => analyzeSymbol(s)));
    for (const item of results) {
      if (!item || !item.result.signal) continue;
      if (isInCooldown(item.symbol)) continue;

      signals.push(item);

      // Registrar cooldown
      signalCooldowns.set(item.symbol, Date.now());

      // Notificar señal
      if (onSignal) onSignal(item.symbol, item.result);
      telegram.sendSignal(item.result, item.symbol);

      logger.info(
        `🎯 SEÑAL [${item.result.signalLevel}] ${item.result.signal} ` +
        `${item.symbol} Score: ${item.result.apexScoreLong}L / ${item.result.apexScoreShort}S`
      );
    }

    analyzed += batch.length;
    await sleep(300); // 300ms entre batches
  }

  logger.info(`Scanner: ${analyzed} pares analizados, ${signals.length} señales encontradas`);
  return signals;
}

/**
 * Scan continuo — corre cada N minutos
 */
function startContinuousScan(tradeController, intervalMinutes = 3) {
  logger.info(`Scanner continuo iniciado — intervalo: ${intervalMinutes} min`);

  const run = async () => {
    try {
      await runScan(tradeController, async (symbol, result) => {
        if (tradeController) {
          await tradeController.handleSignal(symbol, result);
        }
      });
    } catch (e) {
      logger.error(`Scan error: ${e.message}`);
    }
  };

  // Primera ejecución inmediata
  run();

  // Repetir cada intervalo
  const intervalMs = intervalMinutes * 60 * 1000;
  return setInterval(run, intervalMs);
}

module.exports = { runScan, startContinuousScan, getActiveSymbols, analyzeSymbol };
