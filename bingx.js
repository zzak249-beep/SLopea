/**
 * ╔══════════════════════════════════════════════════════════════╗
 * ║  BINGX EXCHANGE CONNECTOR                                    ║
 * ║  Futures perpetuos — REST API v2                             ║
 * ╚══════════════════════════════════════════════════════════════╝
 */

const axios  = require('axios');
const crypto = require('crypto');
const logger = require('../utils/logger');

const BASE_URL = process.env.BINGX_BASE_URL || 'https://open-api.bingx.com';

// ─── Firma HMAC-SHA256 ─────────────────────────────────────────
function sign(queryString, secret) {
  return crypto.createHmac('sha256', secret).update(queryString).digest('hex');
}

function buildQueryString(params) {
  return Object.entries(params)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}=${v}`)
    .join('&');
}

async function apiRequest(method, path, params = {}, signed = false) {
  const apiKey = process.env.BINGX_API_KEY;
  const secret = process.env.BINGX_SECRET_KEY;

  if (signed) params.timestamp = Date.now();
  const qs = buildQueryString(params);
  const signature = signed ? sign(qs, secret) : '';
  const url = `${BASE_URL}${path}${qs ? '?' + qs : ''}${signed ? '&signature=' + signature : ''}`;

  try {
    const res = await axios({
      method,
      url,
      headers: {
        'X-BX-APIKEY': apiKey,
        'Content-Type': 'application/json',
      },
      timeout: 10000,
    });
    if (res.data.code !== 0 && res.data.code !== undefined) {
      throw new Error(`BingX API Error ${res.data.code}: ${res.data.msg}`);
    }
    return res.data.data || res.data;
  } catch (err) {
    logger.error(`BingX API [${method} ${path}]: ${err.message}`);
    throw err;
  }
}

// ─── MERCADO ───────────────────────────────────────────────────

/**
 * Obtiene todos los pares de futuros USDT disponibles
 */
async function getAllSymbols() {
  const data = await apiRequest('GET', '/openApi/swap/v2/quote/contracts');
  if (!data) return [];
  const symbols = data
    .filter(s => s.currency === 'USDT' && s.status === 1)
    .map(s => ({
      symbol: s.symbol,
      pricePrecision: s.pricePrecision,
      quantityPrecision: s.quantityPrecision,
      minQty: parseFloat(s.minOrderNum || 0.001),
      maxLeverage: parseInt(s.maxLeverage || 100),
      contractSize: parseFloat(s.contractSize || 1),
    }));
  return symbols;
}

/**
 * Obtiene velas OHLCV
 * interval: 1m, 3m, 5m, 15m, 1h, 4h, 1d
 */
async function getCandles(symbol, interval = '3m', limit = 200) {
  const data = await apiRequest('GET', '/openApi/swap/v2/quote/klines', {
    symbol,
    interval,
    limit,
  });
  if (!data) return [];
  return data.map(c => ({
    time:   parseInt(c[0]),
    open:   parseFloat(c[1]),
    high:   parseFloat(c[2]),
    low:    parseFloat(c[3]),
    close:  parseFloat(c[4]),
    volume: parseFloat(c[5]),
  })).sort((a, b) => a.time - b.time);
}

/**
 * Volumen 24h de un símbolo
 */
async function get24hTicker(symbol) {
  const data = await apiRequest('GET', '/openApi/swap/v2/quote/ticker', { symbol });
  if (!data) return null;
  return {
    symbol,
    lastPrice: parseFloat(data.lastPrice),
    volume24h: parseFloat(data.quoteVolume || data.volume || 0),
    priceChange: parseFloat(data.priceChangePercent || 0),
  };
}

/**
 * Obtiene todos los tickers 24h (para filtrar por volumen)
 */
async function getAllTickers() {
  const data = await apiRequest('GET', '/openApi/swap/v2/quote/ticker');
  if (!Array.isArray(data)) return [];
  return data.map(t => ({
    symbol: t.symbol,
    lastPrice: parseFloat(t.lastPrice || 0),
    volume24h: parseFloat(t.quoteVolume || t.volume || 0),
    priceChange: parseFloat(t.priceChangePercent || 0),
  }));
}

// ─── CUENTA ────────────────────────────────────────────────────

async function getBalance() {
  const data = await apiRequest('GET', '/openApi/swap/v2/user/balance', {}, true);
  if (!data || !data.balance) return { usdt: 0, equity: 0 };
  const usdt = data.balance.find(b => b.asset === 'USDT') || {};
  return {
    usdt: parseFloat(usdt.balance || 0),
    equity: parseFloat(usdt.equity || 0),
    availableMargin: parseFloat(usdt.availableMargin || 0),
  };
}

async function getOpenPositions() {
  const data = await apiRequest('GET', '/openApi/swap/v2/user/positions', {}, true);
  if (!Array.isArray(data)) return [];
  return data
    .filter(p => parseFloat(p.positionAmt) !== 0)
    .map(p => ({
      symbol: p.symbol,
      side: parseFloat(p.positionAmt) > 0 ? 'LONG' : 'SHORT',
      size: Math.abs(parseFloat(p.positionAmt)),
      entryPrice: parseFloat(p.avgPrice),
      unrealizedPnl: parseFloat(p.unrealizedProfit || 0),
      leverage: parseInt(p.leverage || 1),
      margin: parseFloat(p.initialMargin || 0),
    }));
}

async function getOpenOrders(symbol) {
  const data = await apiRequest('GET', '/openApi/swap/v2/trade/openOrders', { symbol }, true);
  return Array.isArray(data) ? data : (data?.orders || []);
}

// ─── TRADING ───────────────────────────────────────────────────

async function setLeverage(symbol, leverage) {
  try {
    await apiRequest('POST', `/openApi/swap/v2/trade/leverage?symbol=${symbol}&side=LONG&leverage=${leverage}&timestamp=${Date.now()}&signature=`, {}, false);
  } catch {}
  try {
    await apiRequest('POST', `/openApi/swap/v2/trade/leverage?symbol=${symbol}&side=SHORT&leverage=${leverage}&timestamp=${Date.now()}&signature=`, {}, false);
  } catch {}
}

/**
 * Coloca una orden de mercado
 */
async function placeMarketOrder(symbol, side, quantity, reduceOnly = false) {
  const mode = process.env.MODE || 'paper';
  if (mode === 'paper') {
    logger.info(`[PAPER] Market ${side} ${quantity} ${symbol}`);
    return { orderId: `PAPER_${Date.now()}`, status: 'FILLED', price: 0, qty: quantity };
  }

  const params = {
    symbol,
    side: side.toUpperCase(),
    positionSide: side === 'BUY' ? 'LONG' : 'SHORT',
    type: 'MARKET',
    quantity: quantity.toString(),
    reduceOnly: reduceOnly ? 'true' : 'false',
  };

  const data = await apiRequest('POST',
    `/openApi/swap/v2/trade/order?${buildQueryString({ ...params, timestamp: Date.now() })}&signature=${sign(buildQueryString({ ...params, timestamp: Date.now() }), process.env.BINGX_SECRET_KEY)}`,
    {}, false
  );
  return data;
}

/**
 * Orden de stop-loss
 */
async function placeStopOrder(symbol, side, stopPrice, quantity) {
  if (process.env.MODE === 'paper') return { orderId: `PAPER_SL_${Date.now()}` };
  const params = {
    symbol,
    side: side === 'LONG' ? 'SELL' : 'BUY',
    positionSide: side,
    type: 'STOP_MARKET',
    stopPrice: stopPrice.toString(),
    quantity: quantity.toString(),
    closePosition: 'true',
  };
  try {
    return await apiRequest('POST',
      `/openApi/swap/v2/trade/order?${buildQueryString({ ...params, timestamp: Date.now() })}&signature=${sign(buildQueryString({ ...params, timestamp: Date.now() }), process.env.BINGX_SECRET_KEY)}`,
      {}, false
    );
  } catch (e) {
    logger.warn(`SL order failed: ${e.message}`);
    return null;
  }
}

/**
 * Orden de take-profit
 */
async function placeTakeProfitOrder(symbol, side, tpPrice, quantity) {
  if (process.env.MODE === 'paper') return { orderId: `PAPER_TP_${Date.now()}` };
  const params = {
    symbol,
    side: side === 'LONG' ? 'SELL' : 'BUY',
    positionSide: side,
    type: 'TAKE_PROFIT_MARKET',
    stopPrice: tpPrice.toString(),
    quantity: quantity.toString(),
    closePosition: 'true',
  };
  try {
    return await apiRequest('POST',
      `/openApi/swap/v2/trade/order?${buildQueryString({ ...params, timestamp: Date.now() })}&signature=${sign(buildQueryString({ ...params, timestamp: Date.now() }), process.env.BINGX_SECRET_KEY)}`,
      {}, false
    );
  } catch (e) {
    logger.warn(`TP order failed: ${e.message}`);
    return null;
  }
}

/**
 * Cancela todas las órdenes abiertas de un símbolo
 */
async function cancelAllOrders(symbol) {
  if (process.env.MODE === 'paper') return true;
  try {
    await apiRequest('DELETE', `/openApi/swap/v2/trade/allOpenOrders?symbol=${symbol}&timestamp=${Date.now()}&signature=`, {}, false);
    return true;
  } catch { return false; }
}

/**
 * Calcula el tamaño de la posición basado en riesgo
 */
function calculatePositionSize(balance, riskPct, entryPrice, stopPrice, leverage, contractSize = 1) {
  const riskUSD  = balance.equity * (riskPct / 100);
  const slDist   = Math.abs(entryPrice - stopPrice);
  if (slDist === 0) return 0;
  const qty = (riskUSD * leverage) / (entryPrice * (slDist / entryPrice));
  const maxQtyByCapital = (balance.availableMargin * leverage) / entryPrice;
  const maxUSD = parseFloat(process.env.MAX_TRADE_SIZE_USD || 100);
  const maxBySize = (maxUSD * leverage) / entryPrice;
  return Math.min(qty, maxQtyByCapital * 0.9, maxBySize);
}

module.exports = {
  getAllSymbols,
  getCandles,
  get24hTicker,
  getAllTickers,
  getBalance,
  getOpenPositions,
  getOpenOrders,
  setLeverage,
  placeMarketOrder,
  placeStopOrder,
  placeTakeProfitOrder,
  cancelAllOrders,
  calculatePositionSize,
};
