/**
 * ╔══════════════════════════════════════════════════════════════╗
 * ║  TRADE CONTROLLER                                            ║
 * ║  Gestiona el ciclo de vida de los trades                     ║
 * ║  SL, TP, Trailing, Anti-reentrada, Estadísticas             ║
 * ╚══════════════════════════════════════════════════════════════╝
 */

const bingx    = require('../exchange/bingx');
const telegram = require('../telegram/bot');
const logger   = require('../utils/logger');

class TradeController {
  constructor() {
    this.paused    = false;
    this.openTrades = new Map(); // symbol → tradeInfo
    this.stats = {
      total: 0, wins: 0, losses: 0,
      pnl: 0, bestTrade: 0, worstTrade: 0,
      totalRR: 0, avgRR: 0,
    };
    this.startTime = Date.now();
    this.symbolsScanned = 0;
    this.lastSignal = null;

    // Anti-reentrada por símbolo (dirección del último trade cerrado)
    this.lastTradeDir = new Map();
  }

  // ─── HANDLE SIGNAL ─────────────────────────────────────────

  async handleSignal(symbol, result) {
    if (this.paused) return;
    if (!result.signal) return;

    const signalDir = result.signalDir;
    if (!signalDir) return;

    // Límite de trades abiertos
    const maxTrades = parseInt(process.env.MAX_OPEN_TRADES || 5);
    if (this.openTrades.size >= maxTrades) {
      logger.debug(`Max trades reached (${maxTrades}), skip ${symbol}`);
      return;
    }

    // No abrir si ya hay posición en este símbolo
    if (this.openTrades.has(symbol)) return;

    // Anti-reentrada: evitar entrar en la misma dirección inmediatamente
    const lastDir = this.lastTradeDir.get(symbol);
    if (lastDir === signalDir) {
      logger.debug(`Anti-reentrada: ${symbol} ${signalDir} bloqueado`);
      return;
    }

    // Sólo LONG si configurado
    if (process.env.LONG_ONLY === 'true' && signalDir === 'SHORT') return;

    try {
      await this.openTrade(symbol, result);
      this.lastSignal = `${symbol} ${signalDir} [${result.signalLevel}]`;
    } catch (e) {
      logger.error(`handleSignal ${symbol}: ${e.message}`);
    }
  }

  // ─── ABRIR TRADE ───────────────────────────────────────────

  async openTrade(symbol, result) {
    const leverage  = parseInt(process.env.LEVERAGE || 10);
    const riskPct   = parseFloat(process.env.RISK_PER_TRADE || 2);
    const signalDir = result.signalDir;

    // Obtener balance
    let balance;
    try {
      balance = await bingx.getBalance();
    } catch (e) {
      logger.error(`getBalance: ${e.message}`);
      return;
    }

    if (!balance.equity || balance.equity < 10) {
      logger.warn('Balance insuficiente para operar');
      return;
    }

    // Calcular tamaño
    const qty = bingx.calculatePositionSize(
      balance,
      riskPct,
      result.price,
      result.sl,
      leverage
    );

    if (!qty || qty <= 0) {
      logger.warn(`Tamaño inválido para ${symbol}`);
      return;
    }

    // Establecer apalancamiento
    try { await bingx.setLeverage(symbol, leverage); } catch {}

    // Orden de entrada
    const entrySide = signalDir === 'LONG' ? 'BUY' : 'SELL';
    let order;
    try {
      order = await bingx.placeMarketOrder(symbol, entrySide, qty);
      logger.info(`Trade abierto: ${signalDir} ${symbol} x${qty} @ ~${result.price}`);
    } catch (e) {
      logger.error(`placeMarketOrder ${symbol}: ${e.message}`);
      return;
    }

    // Esperar un momento y colocar SL/TP
    await new Promise(r => setTimeout(r, 500));

    let slOrder = null, tpOrder = null;
    if (result.sl) {
      try { slOrder = await bingx.placeStopOrder(symbol, signalDir, result.sl, qty); } catch {}
    }
    if (result.tp) {
      try { tpOrder = await bingx.placeTakeProfitOrder(symbol, signalDir, result.tp, qty); } catch {}
    }

    const trade = {
      symbol,
      side: signalDir,
      entryPrice: result.price,
      sl: result.sl,
      tp: result.tp,
      qty,
      leverage,
      signalLevel: result.signalLevel,
      apexScore: signalDir === 'LONG' ? result.apexScoreLong : result.apexScoreShort,
      vsaPattern: result.vsa?.pattern,
      orderId: order?.orderId,
      slOrderId: slOrder?.orderId,
      tpOrderId: tpOrder?.orderId,
      openTime: Date.now(),
      peakPnl: 0,
      trailingActive: false,
    };

    this.openTrades.set(symbol, trade);
    telegram.sendTradeOpen(trade);
  }

  // ─── MONITOREO CONTINUO ────────────────────────────────────

  async monitorPositions() {
    if (this.openTrades.size === 0) return;

    let livePositions;
    try {
      livePositions = await bingx.getOpenPositions();
    } catch (e) {
      logger.error(`getOpenPositions: ${e.message}`);
      return;
    }

    const liveSet = new Set(livePositions.map(p => p.symbol));

    for (const [symbol, trade] of this.openTrades.entries()) {
      const livePos = livePositions.find(p => p.symbol === symbol);

      // Si la posición ya no existe → fue cerrada (SL/TP hit)
      if (!livePos) {
        await this.closeTrade(symbol, trade, 'SL/TP ejecutado');
        continue;
      }

      // Trailing stop: activar a 1.5R
      const risk = Math.abs(trade.entryPrice - trade.sl);
      const currentPnl = trade.side === 'LONG'
        ? livePos.unrealizedPnl
        : livePos.unrealizedPnl;

      if (risk > 0) {
        const rrCurrent = livePos.unrealizedPnl / (risk * trade.qty);
        if (rrCurrent > trade.peakPnl) trade.peakPnl = rrCurrent;

        // Activar trailing a 1.5R
        if (rrCurrent >= 1.5 && !trade.trailingActive) {
          trade.trailingActive = true;
          // Mover SL a BE (break-even)
          const newSL = trade.entryPrice;
          trade.sl = newSL;
          logger.info(`Trailing activado: ${symbol} — SL movido a BE`);
          try {
            await bingx.cancelAllOrders(symbol);
            await bingx.placeStopOrder(symbol, trade.side, newSL, trade.qty);
          } catch (e) {
            logger.warn(`Trailing SL update: ${e.message}`);
          }
        }
      }

      // Timeout: cerrar tras 8 horas si no se ha movido
      const hoursSinceOpen = (Date.now() - trade.openTime) / (1000 * 3600);
      if (hoursSinceOpen > 8) {
        await this.forceClose(symbol, trade, 'Timeout 8h');
      }
    }
  }

  // ─── CERRAR TRADE (por SL/TP automático) ─────────────────

  async closeTrade(symbol, trade, reason = '') {
    this.openTrades.delete(symbol);

    // Estimar PnL (aproximado — el real lo da el exchange)
    const pnl = trade.peakPnl > 0
      ? Math.abs(trade.entryPrice - trade.tp || trade.entryPrice) * trade.qty
      : -Math.abs(trade.entryPrice - trade.sl) * trade.qty;

    this.updateStats(pnl);
    this.lastTradeDir.set(symbol, trade.side);

    telegram.sendTradeClose({ ...trade, pnl, reason, rrReached: trade.peakPnl });
    logger.info(`Trade cerrado: ${symbol} | Razón: ${reason}`);
  }

  // ─── CERRAR FORZADO ────────────────────────────────────────

  async forceClose(symbol, trade, reason = 'Manual') {
    try {
      await bingx.cancelAllOrders(symbol);
      const closeSide = trade.side === 'LONG' ? 'SELL' : 'BUY';
      await bingx.placeMarketOrder(symbol, closeSide, trade.qty, true);
    } catch (e) {
      logger.error(`forceClose ${symbol}: ${e.message}`);
    }
    await this.closeTrade(symbol, trade, reason);
  }

  async closeAll() {
    for (const [symbol, trade] of this.openTrades.entries()) {
      await this.forceClose(symbol, trade, 'Cierre manual global');
    }
  }

  // ─── ESTADÍSTICAS ──────────────────────────────────────────

  updateStats(pnl) {
    this.stats.total++;
    this.stats.pnl += pnl;
    if (pnl >= 0) {
      this.stats.wins++;
      if (pnl > this.stats.bestTrade) this.stats.bestTrade = pnl;
    } else {
      this.stats.losses++;
      if (pnl < this.stats.worstTrade) this.stats.worstTrade = pnl;
    }
  }

  getStats() { return { ...this.stats }; }

  // ─── ESTADO ────────────────────────────────────────────────

  async getStatus() {
    return {
      paused: this.paused,
      mode: process.env.MODE || 'paper',
      openTrades: this.openTrades.size,
      maxTrades: parseInt(process.env.MAX_OPEN_TRADES || 5),
      symbolsScanned: this.symbolsScanned,
      uptime: this.formatUptime(),
      lastSignal: this.lastSignal,
    };
  }

  async getPositions() {
    try {
      return await bingx.getOpenPositions();
    } catch { return []; }
  }

  async getBalance() {
    try {
      return await bingx.getBalance();
    } catch { return {}; }
  }

  pause()  { this.paused = true;  logger.info('Bot PAUSADO'); }
  resume() { this.paused = false; logger.info('Bot REANUDADO'); }

  formatUptime() {
    const ms = Date.now() - this.startTime;
    const h = Math.floor(ms / 3600000);
    const m = Math.floor((ms % 3600000) / 60000);
    return `${h}h ${m}m`;
  }
}

module.exports = TradeController;
