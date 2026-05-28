/**
 * ╔══════════════════════════════════════════════════════════════╗
 * ║  APEX FUSION ENGINE v1.0                                     ║
 * ║  Fusión: Sniper Predator VSA V8 × QF Machine × JP v3.1      ║
 * ║  Motor analítico — calcula scores de confluencia             ║
 * ╚══════════════════════════════════════════════════════════════╝
 */

const ss = require('simple-statistics');

// ─── UTILIDADES ────────────────────────────────────────────────
const tanh = (x) => {
  const clamped = Math.max(-20, Math.min(20, 2 * x));
  const e2x = Math.exp(clamped);
  return (e2x - 1) / (e2x + 1);
};

const sma = (arr, len) => {
  if (arr.length < len) return null;
  const slice = arr.slice(-len);
  return slice.reduce((a, b) => a + b, 0) / len;
};

const ema = (arr, len) => {
  if (arr.length < len) return null;
  const k = 2 / (len + 1);
  let result = arr[0];
  for (let i = 1; i < arr.length; i++) {
    result = arr[i] * k + result * (1 - k);
  }
  return result;
};

const stdev = (arr, len) => {
  if (arr.length < len) return null;
  const slice = arr.slice(-len);
  const mean = slice.reduce((a, b) => a + b, 0) / len;
  const variance = slice.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / len;
  return Math.sqrt(variance);
};

const highest = (arr, len) => Math.max(...arr.slice(-len));
const lowest  = (arr, len) => Math.min(...arr.slice(-len));

const atr = (highs, lows, closes, len) => {
  const trs = [];
  for (let i = 1; i < closes.length; i++) {
    const tr = Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1])
    );
    trs.push(tr);
  }
  return sma(trs, len);
};

const pearsonR = (xArr, yArr, len) => {
  const xs = xArr.slice(-len);
  const ys = yArr.slice(-len);
  if (xs.length < 3) return 0;
  try {
    return ss.sampleCorrelation(xs, ys);
  } catch { return 0; }
};

const linReg = (arr, len) => {
  const slice = arr.slice(-len);
  const n = slice.length;
  const xArr = slice.map((_, i) => i);
  const sumX  = xArr.reduce((a, b) => a + b, 0);
  const sumY  = slice.reduce((a, b) => a + b, 0);
  const sumXY = xArr.reduce((a, x, i) => a + x * slice[i], 0);
  const sumXX = xArr.reduce((a, x) => a + x * x, 0);
  const denom = n * sumXX - sumX * sumX;
  if (denom === 0) return slice[slice.length - 1];
  const slope = (n * sumXY - sumX * sumY) / denom;
  const intercept = (sumY - slope * sumX) / n;
  return slope * (n - 1) + intercept;
};

// ─── MÓDULO 1: SNIPER ENGINE ───────────────────────────────────
function sniperEngine(candles, htfCandles, cfg = {}) {
  const {
    emaFast = 7, emaSlow = 17,
    slopeMin = 30,
    pocLookback = 50,
    rvolMin = 1.5,
    atrSlMult = 1.2,
    adxMax = 35,
    pocAtrDist = 1.5,
  } = cfg;

  const closes  = candles.map(c => c.close);
  const highs   = candles.map(c => c.high);
  const lows    = candles.map(c => c.low);
  const volumes = candles.map(c => c.volume);
  const n = closes.length;

  if (n < 60) return { valid: false, reason: 'Datos insuficientes' };

  // EMA trend
  const fastEMAs = [];
  const slowEMAs = [];
  const kF = 2 / (emaFast + 1);
  const kS = 2 / (emaSlow + 1);
  let ef = closes[0], es = closes[0];
  for (let i = 0; i < n; i++) {
    ef = closes[i] * kF + ef * (1 - kF);
    es = closes[i] * kS + es * (1 - kS);
    fastEMAs.push(ef);
    slowEMAs.push(es);
  }
  const emaF = fastEMAs[n - 1];
  const emaS = slowEMAs[n - 1];
  const emaTrendLong  = emaF > emaS;
  const emaTrendShort = emaF < emaS;

  // ATR
  const atrVal  = atr(highs, lows, closes, 14);
  const atrShort = atr(highs, lows, closes, 7);

  // VWAP proxy (session)
  const hlc3 = candles.map(c => (c.high + c.low + c.close) / 3);
  const vwapNum = candles.reduce((a, c, i) => a + hlc3[i] * c.volume, 0);
  const vwapDen = volumes.reduce((a, b) => a + b, 0);
  const vwap = vwapDen > 0 ? vwapNum / vwapDen : closes[n - 1];

  // Magic Slope (velocidad EMA fast en unidades ATR)
  const magicSlope = atrShort > 0
    ? ((fastEMAs[n - 1] - fastEMAs[n - 2]) / atrShort) * 100
    : 0;

  // RVOL
  const volAvg = sma(volumes, 50);
  const rvol = volAvg > 0 ? volumes[n - 1] / volAvg : 1;

  // POC (Point of Control)
  const pocSlice = candles.slice(-pocLookback);
  let maxVol = 0, pocPrice = closes[n - 1];
  pocSlice.forEach(c => {
    if (c.volume > maxVol) { maxVol = c.volume; pocPrice = c.close; }
  });
  const distPoc = Math.abs(closes[n - 1] - pocPrice) > atrVal * pocAtrDist;

  // ADX (simplified Wilder)
  let adxVal = 20;
  if (n >= 20) {
    const dms = [];
    for (let i = 1; i < n; i++) {
      const upMove   = highs[i] - highs[i - 1];
      const downMove = lows[i - 1] - lows[i];
      const tr = Math.max(highs[i] - lows[i], Math.abs(highs[i] - closes[i-1]), Math.abs(lows[i] - closes[i-1]));
      const dmP = upMove > downMove && upMove > 0 ? upMove : 0;
      const dmN = downMove > upMove && downMove > 0 ? downMove : 0;
      dms.push({ tr, dmP, dmN });
    }
    const period = 14;
    const slice = dms.slice(-period);
    const trSum  = slice.reduce((a, d) => a + d.tr, 0);
    const dmPSum = slice.reduce((a, d) => a + d.dmP, 0);
    const dmNSum = slice.reduce((a, d) => a + d.dmN, 0);
    const diP = trSum > 0 ? (dmPSum / trSum) * 100 : 0;
    const diN = trSum > 0 ? (dmNSum / trSum) * 100 : 0;
    adxVal = (diP + diN) > 0 ? Math.abs(diP - diN) / (diP + diN) * 100 : 20;
  }
  const adxOk = adxVal < adxMax;

  // STC (Schaff Trend Cycle proxy)
  const macdLine = fastEMAs.map((f, i) => f - slowEMAs[i]);
  const stcVal = macdLine[n - 1];
  const stcPrev = macdLine[n - 2];
  const stcRising  = stcVal > stcPrev;
  const stcFalling = stcVal < stcPrev;

  // Pivot High/Low (último)
  let lastPeak = null, lastValley = null;
  const pLen = 4;
  for (let i = pLen; i < n - pLen; i++) {
    let isPH = true, isPL = true;
    for (let j = i - pLen; j <= i + pLen; j++) {
      if (j === i) continue;
      if (highs[j] >= highs[i]) isPH = false;
      if (lows[j] <= lows[i]) isPL = false;
    }
    if (isPH) lastPeak = highs[i];
    if (isPL) lastValley = lows[i];
  }

  // HTF trend
  let htfBull = true, htfBear = false;
  if (htfCandles && htfCandles.length >= 21) {
    const htfCloses = htfCandles.map(c => c.close);
    const htfF = ema(htfCloses, 9);
    const htfS = ema(htfCloses, 21);
    htfBull = htfF > htfS;
    htfBear = htfF < htfS;
  }

  // Condiciones Sniper
  const sniperLong = lastValley !== null &&
    closes[n-1] < lastValley &&
    closes[n-1] < vwap &&
    magicSlope > slopeMin &&
    stcRising && adxOk && distPoc && rvol > rvolMin && emaTrendLong;

  const sniperShort = lastPeak !== null &&
    closes[n-1] > lastPeak &&
    closes[n-1] > vwap &&
    magicSlope < -slopeMin &&
    stcFalling && adxOk && distPoc && rvol > rvolMin && emaTrendShort;

  // Score Sniper (0-40 puntos)
  let scoreLong = 0, scoreShort = 0;
  if (emaTrendLong)         scoreLong  += 6;
  if (magicSlope > slopeMin) scoreLong += 8;
  if (rvol > rvolMin)       scoreLong  += 6;
  if (distPoc)              scoreLong  += 5;
  if (adxOk)                scoreLong  += 5;
  if (stcRising)            scoreLong  += 5;
  if (htfBull)              scoreLong  += 5;

  if (emaTrendShort)         scoreShort += 6;
  if (magicSlope < -slopeMin) scoreShort += 8;
  if (rvol > rvolMin)        scoreShort += 6;
  if (distPoc)               scoreShort += 5;
  if (adxOk)                 scoreShort += 5;
  if (stcFalling)            scoreShort += 5;
  if (htfBull === false)     scoreShort += 5;

  return {
    valid: true,
    sniperLong, sniperShort,
    scoreLong, scoreShort,
    metrics: {
      emaF, emaS, vwap, magicSlope, rvol, adxVal, stcVal,
      pocPrice, distPoc, htfBull, htfBear,
      atrVal, lastPeak, lastValley,
      sl_long:  lastValley ? lastValley - atrVal * atrSlMult : null,
      sl_short: lastPeak   ? lastPeak   + atrVal * atrSlMult : null,
    }
  };
}

// ─── MÓDULO 2: VSA ENGINE ──────────────────────────────────────
function vsaEngine(candles, cfg = {}) {
  const {
    lookback = 100,
    rThreshold = 0.5,
    vsaThreshold = 1.0,
    expiry = 10,
  } = cfg;

  const closes  = candles.map(c => c.close);
  const highs   = candles.map(c => c.high);
  const lows    = candles.map(c => c.low);
  const opens   = candles.map(c => c.open);
  const volumes = candles.map(c => c.volume);
  const n = closes.length;

  if (n < lookback) return { valid: false, vsaLong: false, vsaShort: false, score: 0, pattern: 'NINGUNO' };

  const atrVSA   = atr(highs, lows, closes, lookback);
  const normRange  = candles.map(c => atrVSA > 0 ? (c.high - c.low) / atrVSA : 0);
  const volSma    = sma(volumes, lookback);
  const normVol   = volumes.map(v => volSma > 0 ? v / volSma : 1);

  // Regresión lineal VSA (vol normalizado → rango normalizado)
  const xArr = normVol.slice(-lookback);
  const yArr = normRange.slice(-lookback);
  const meanX  = xArr.reduce((a, b) => a + b, 0) / lookback;
  const meanY  = yArr.reduce((a, b) => a + b, 0) / lookback;
  const sumXY  = xArr.reduce((a, x, i) => a + x * yArr[i], 0);
  const sumXX  = xArr.reduce((a, x) => a + x * x, 0);
  const denom  = sumXX / lookback - meanX * meanX;
  const slopeVSA  = denom !== 0 ? (sumXY / lookback - meanX * meanY) / denom : 0;
  const intercept = meanY - slopeVSA * meanX;

  // Correlación Pearson
  const rVal = pearsonR(normVol, normRange, lookback);
  const rOk  = Math.abs(rVal) >= rThreshold && slopeVSA > 0;

  // Desviación de la barra actual
  const curNormVol = normVol[n - 1];
  const predRange  = intercept + slopeVSA * curNormVol;
  const dev = rOk ? normRange[n - 1] - predRange : 0;

  // Morfología actual
  const barBody  = Math.abs(closes[n-1] - opens[n-1]);
  const barRange = highs[n-1] - lows[n-1];
  const upWick   = highs[n-1] - Math.max(opens[n-1], closes[n-1]);
  const lowWick  = Math.min(opens[n-1], closes[n-1]) - lows[n-1];
  const upWickR  = barRange > 0 ? upWick / barRange : 0;
  const lowWickR = barRange > 0 ? lowWick / barRange : 0;
  const bodyR    = barRange > 0 ? barBody / barRange : 0;
  const isBull   = closes[n-1] > opens[n-1];
  const isBear   = closes[n-1] < opens[n-1];
  const isWide   = normRange[n-1] > 1.5;
  const isNarrow = normRange[n-1] < 0.6;
  const isHighVol = normVol[n-1] > 1.5;
  const isLowVol  = normVol[n-1] < 0.7;
  const isDoji    = bodyR < 0.2;
  const closeUpperHalf = barRange > 0 ? (closes[n-1] - lows[n-1]) / barRange > 0.6 : false;

  const sigNeg = dev < -vsaThreshold;
  const sigPos = dev >  vsaThreshold;

  // Patrones VSA
  const isSC  = sigNeg && isBear && isWide && isHighVol;                          // Selling Climax → LONG
  const isDT  = sigNeg && (isDoji || isNarrow) && isHighVol;                      // Down Thrust → LONG
  const isSV  = sigNeg && isBear && isHighVol && closeUpperHalf;                  // Stopping Volume → LONG
  const isHB  = sigNeg && isBull && isHighVol;                                    // Hidden Buying → LONG
  const isSPR = sigPos && lowWickR > 0.35 && isHighVol && isBull;                 // Spring → LONG
  const isEOM = sigPos && isWide && isLowVol && isBull;                           // Ease of Movement → LONG
  const isBC  = sigPos && isBull && isWide && isHighVol && !closeUpperHalf;       // Buying Climax → SHORT
  const isUT  = sigPos && upWickR > 0.35 && isHighVol;                            // Upthrust → SHORT

  let pattern = 'NINGUNO';
  let patternDir = 0; // 1=long, -1=short
  if (isSC)  { pattern = 'SC Climax Venta';  patternDir = 1; }
  if (isDT)  { pattern = 'DT Down Thrust';   patternDir = 1; }
  if (isSV)  { pattern = 'SV Vol Parada';    patternDir = 1; }
  if (isHB)  { pattern = 'HB Compra Oculta'; patternDir = 1; }
  if (isSPR) { pattern = 'SPR Spring';       patternDir = 1; }
  if (isEOM) { pattern = 'EoM Mov Fácil';   patternDir = 1; }
  if (isBC)  { pattern = 'BC Climax Compra'; patternDir = -1; }
  if (isUT)  { pattern = 'UT Upthrust';      patternDir = -1; }

  const vsaLong  = patternDir === 1;
  const vsaShort = patternDir === -1;

  // Score VSA (0-30 puntos)
  let vsaScore = 0;
  if (rOk && Math.abs(dev) > vsaThreshold * 2) vsaScore = 30;
  else if (rOk && Math.abs(dev) > vsaThreshold) vsaScore = 20;
  else if (rOk) vsaScore = 10;

  return {
    valid: true,
    vsaLong, vsaShort, pattern, patternDir,
    metrics: { dev, rVal, slopeVSA, normVol: curNormVol, normRange: normRange[n-1], rOk }
  };
}

// ─── MÓDULO 3: QF MACHINE ENGINE ──────────────────────────────
function qfEngine(candles, htfCandles, cfg = {}) {
  const {
    momLen = 20, revLen = 8, volLen = 14,
    w1 = 0.40, w2 = 0.30, w3 = 0.30,
    smoothLen = 3, decayLen = 40,
    asimRatio = 1.20, asimLen = 10,
    cvdLen = 20, sqLen = 20,
    sqBBM = 2.0, sqKCM = 1.5,
  } = cfg;

  const closes  = candles.map(c => c.close);
  const highs   = candles.map(c => c.high);
  const lows    = candles.map(c => c.low);
  const opens   = candles.map(c => c.open);
  const volumes = candles.map(c => c.volume);
  const n = closes.length;

  if (n < 60) return { valid: false, scoreLong: 0, scoreShort: 0 };

  // ── L2: Factores cuantitativos ──────────────────────────────
  const roc    = (closes[n-1] - closes[n-1-momLen]) / closes[n-1-momLen];
  const volN   = stdev(closes.slice(-momLen), momLen);
  const smaN   = sma(closes, momLen);
  const fMom   = volN > 0 ? roc / (volN / smaN) : 0;

  const basis  = sma(closes, revLen);
  const bStd   = stdev(closes.slice(-revLen), revLen);
  const fRev   = bStd > 0 ? -(closes[n-1] - basis) / bStd : 0;

  // OBV
  let obv = 0;
  const obvArr = [];
  for (let i = 1; i < n; i++) {
    obv += closes[i] > closes[i-1] ? volumes[i] : closes[i] < closes[i-1] ? -volumes[i] : 0;
    obvArr.push(obv);
  }
  const obvMa  = sma(obvArr, volLen);
  const obvStd = stdev(obvArr.slice(-volLen), volLen);
  const fVol   = obvStd > 0 ? (obvArr[obvArr.length-1] - obvMa) / obvStd : 0;

  const rawScore  = w1 * fMom + w2 * fRev + w3 * fVol;
  const scStd     = stdev(closes.slice(-decayLen), decayLen);
  const normScore = scStd > 0 ? tanh(rawScore / (scStd / closes[n-1] * 10)) : 0;

  // ── L3: Decay adaptativo (MEJORA: percentil en vez de pico) ─
  const icRoll    = Math.abs(normScore);
  const decayNorm = Math.min(1, icRoll / 0.5); // normalizado — siempre activo
  const sigAlive  = decayNorm >= 0.30;          // umbral bajo = más señales en 3m

  // ── L6: Asimetría de momentum ───────────────────────────────
  const upRngs = candles.slice(-asimLen).map(c => c.close > c.open ? c.high - c.low : 0);
  const dnRngs = candles.slice(-asimLen).map(c => c.close < c.open ? c.high - c.low : 0);
  const avgUp  = upRngs.reduce((a, b) => a + b, 0) / asimLen;
  const avgDn  = dnRngs.reduce((a, b) => a + b, 0) / asimLen;
  const asymBull = avgDn > 0 ? avgUp / avgDn >= asimRatio : false;
  const asymBear = avgUp > 0 ? avgDn / avgUp >= asimRatio : false;

  // ── L9: FVG ─────────────────────────────────────────────────
  const atrVal    = atr(highs, lows, closes, 10) || 1;
  const fvgMinSize = atrVal * 0.3;
  const bullFVG = lows[n-1] > highs[n-3] && (lows[n-1] - highs[n-3]) > fvgMinSize;
  const bearFVG = highs[n-1] < lows[n-3] && (lows[n-3] - highs[n-1]) > fvgMinSize;

  // ── L10: Order Blocks ────────────────────────────────────────
  const impulse   = atrVal * 1.5;
  const bullImpulse = (closes[n-1] - opens[n-1]) > impulse;
  const bearImpulse = (opens[n-1] - closes[n-1]) > impulse;
  const bullOB = bullImpulse && closes[n-2] < opens[n-2];
  const bearOB = bearImpulse && closes[n-2] > opens[n-2];

  // ── L11: CVD Delta ──────────────────────────────────────────
  const deltas = candles.map(c => {
    const r = c.high - c.low;
    return r > 0 ? ((c.close - c.low) / r - (c.high - c.close) / r) * c.volume : 0;
  });
  // CVD ventana rodante (MEJORA: evita deriva acumulativa)
  const cvdWindow = deltas.slice(-cvdLen * 3);
  const cvdEma    = ema(cvdWindow, cvdLen);
  const cvdRising = cvdWindow[cvdWindow.length-1] > cvdEma;
  const cvdZ      = stdev(cvdWindow.slice(-cvdLen), cvdLen) > 0
    ? (cvdWindow[cvdWindow.length-1] - cvdEma) / stdev(cvdWindow.slice(-cvdLen), cvdLen)
    : 0;
  const cvdScore  = Math.max(0, Math.min(1, (tanh(cvdZ) + 1) / 2));

  // CVD divergencia
  const prevClose = closes[n-1-5];
  const cvdBullDiv = closes[n-1] < prevClose && deltas.slice(-5).reduce((a,b)=>a+b,0) > 0;
  const cvdBearDiv = closes[n-1] > prevClose && deltas.slice(-5).reduce((a,b)=>a+b,0) < 0;

  // ── L12: Squeeze Momentum ───────────────────────────────────
  const sqBasis  = sma(closes, sqLen) || closes[n-1];
  const sqDev    = stdev(closes.slice(-sqLen), sqLen) || 0;
  const sqBBHi   = sqBasis + sqBBM * sqDev;
  const sqBBLo   = sqBasis - sqBBM * sqDev;
  const sqKcAtr  = atr(highs, lows, closes, sqLen) || atrVal;
  const sqKCHi   = (ema(closes, sqLen) || sqBasis) + sqKCM * sqKcAtr;
  const sqKCLo   = (ema(closes, sqLen) || sqBasis) - sqKCM * sqKcAtr;
  const sqOn     = sqBBHi < sqKCHi && sqBBLo > sqKCLo;
  const sqFire   = !sqOn; // simplified: fire when not squeezed
  const sqVal    = linReg(closes.slice(-sqLen).map(c => c - sqBasis), sqLen);
  const sqBull   = sqFire && sqVal > 0;
  const sqBear   = sqFire && sqVal < 0;

  // ── L4: Dark Pool proxy ─────────────────────────────────────
  const volBase  = sma(volumes, 20) || 1;
  const volSpike = volumes[n-1] > volBase * 2.5;
  const rngNarrow = (highs[n-1] - lows[n-1]) < atrVal * 0.6;
  const dpBuy  = volSpike && rngNarrow && closes[n-1] > opens[n-1];
  const dpSell = volSpike && rngNarrow && closes[n-1] < opens[n-1];

  // HTF
  let htfBull = true;
  if (htfCandles && htfCandles.length >= 21) {
    const htfC = htfCandles.map(c => c.close);
    htfBull = (ema(htfC, 9) || 0) > (ema(htfC, 21) || 0);
  }

  // ── Score Compuesto QF (0-60 puntos) ─────────────────────────
  const nsNorm  = (tanh(normScore) + 1) / 2;
  const momNorm = (tanh(fMom * 2) + 1) / 2;

  // LONG score
  const htfAsymL = (htfBull ? 0.5 : 0) + (asymBull ? 0.5 : 0);
  const compLongRaw = 0.30 * nsNorm + 0.25 * cvdScore + 0.20 * momNorm + 0.15 * decayNorm + 0.10 * htfAsymL;
  const qfScoreLong = Math.round(compLongRaw * 60);

  // SHORT score
  const cvdScoreS = 1 - cvdScore;
  const momNormS  = (tanh(-fMom * 2) + 1) / 2;
  const nsNormS   = (tanh(-normScore) + 1) / 2;
  const htfAsymS  = (!htfBull ? 0.5 : 0) + (asymBear ? 0.5 : 0);
  const compShortRaw = 0.30 * nsNormS + 0.25 * cvdScoreS + 0.20 * momNormS + 0.15 * decayNorm + 0.10 * htfAsymS;
  const qfScoreShort = Math.round(compShortRaw * 60);

  // Catalizadores FUEL
  const fuelLong  = sqBull || bullFVG || bullOB || cvdBullDiv;
  const fuelShort = sqBear || bearFVG || bearOB || cvdBearDiv;

  return {
    valid: true,
    qfScoreLong, qfScoreShort,
    sigAlive, htfBull,
    metrics: {
      fMom, fRev, fVol, normScore, cvdScore, decayNorm,
      asymBull, asymBear, sqBull, sqBear, sqOn,
      bullFVG, bearFVG, bullOB, bearOB,
      dpBuy, dpSell, cvdBullDiv, cvdBearDiv,
      cvdRising, fuelLong, fuelShort,
    }
  };
}

// ─── FUSIÓN FINAL: APEX SCORE ──────────────────────────────────
function apexFusion(candles, htfCandles, trendCandles, config = {}) {
  if (!candles || candles.length < 120) {
    return { valid: false, reason: 'Datos insuficientes' };
  }

  const sniper = sniperEngine(candles, htfCandles, config.sniper);
  const vsa    = vsaEngine(candles, config.vsa);
  const qf     = qfEngine(candles, htfCandles, config.qf);

  if (!sniper.valid) return { valid: false, reason: sniper.reason };

  // ── Tendencia 1H (macro filter) ──────────────────────────────
  let trendBull = true, trendBear = false;
  if (trendCandles && trendCandles.length >= 50) {
    const tc = trendCandles.map(c => c.close);
    const t50 = sma(tc, 50);
    const curr = tc[tc.length - 1];
    trendBull = curr > t50;
    trendBear = curr < t50;
  }

  // ── APEX SCORE LONG (0-100) ───────────────────────────────────
  // Sniper: 0-40 pts, QF: 0-40 pts, VSA bonus: 0-20 pts
  const vsaBonusLong  = vsa.vsaLong  ? 20 : 0;
  const vsaBonusShort = vsa.vsaShort ? 20 : 0;

  const apexScoreLong  = Math.min(100, sniper.scoreLong  + qf.qfScoreLong  + vsaBonusLong);
  const apexScoreShort = Math.min(100, sniper.scoreShort + qf.qfScoreShort + vsaBonusShort);

  // ── VENTAJA ESPECIAL: Triple Confluence Spike Detector ────────
  // Si los 3 motores coinciden en la misma dirección → "APEX PRIME"
  // es una señal extremadamente rara y de alta probabilidad
  const apexPrimeLong  = sniper.sniperLong  && vsa.vsaLong  && qf.qfScoreLong  >= 35 && trendBull;
  const apexPrimeShort = sniper.sniperShort && vsa.vsaShort && qf.qfScoreShort >= 35 && trendBear;

  // ── Niveles de señal ─────────────────────────────────────────
  const minStd    = parseInt(process.env.MIN_SCORE_ENTRY  || 62);
  const minFuel   = parseInt(process.env.MIN_SCORE_FUEL   || 72);
  const minSupreme= parseInt(process.env.MIN_SCORE_SUPREME|| 85);

  const longStd   = apexScoreLong  >= minStd   && qf.sigAlive && trendBull;
  const longFuel  = apexScoreLong  >= minFuel  && qf.metrics.fuelLong  && longStd;
  const longSup   = apexScoreLong  >= minSupreme && (qf.metrics.dpBuy || qf.metrics.cvdBullDiv) && longFuel;

  const shortStd  = apexScoreShort >= minStd   && qf.sigAlive && trendBear;
  const shortFuel = apexScoreShort >= minFuel  && qf.metrics.fuelShort && shortStd;
  const shortSup  = apexScoreShort >= minSupreme && (qf.metrics.dpSell || qf.metrics.cvdBearDiv) && shortFuel;

  // Determinar señal final
  let signal = null;
  let signalDir = null;
  let signalLevel = null;

  if (apexPrimeLong) {
    signal = 'APEX_PRIME_LONG';
    signalDir = 'LONG';
    signalLevel = 'PRIME';
  } else if (apexPrimeShort) {
    signal = 'APEX_PRIME_SHORT';
    signalDir = 'SHORT';
    signalLevel = 'PRIME';
  } else if (longSup) {
    signal = 'LONG_SUPREMA';
    signalDir = 'LONG';
    signalLevel = 'SUPREMA';
  } else if (shortSup) {
    signal = 'SHORT_SUPREMA';
    signalDir = 'SHORT';
    signalLevel = 'SUPREMA';
  } else if (longFuel) {
    signal = 'LONG_FUEL';
    signalDir = 'LONG';
    signalLevel = 'FUEL';
  } else if (shortFuel) {
    signal = 'SHORT_FUEL';
    signalDir = 'SHORT';
    signalLevel = 'FUEL';
  } else if (longStd) {
    signal = 'LONG_STD';
    signalDir = 'LONG';
    signalLevel = 'STD';
  } else if (shortStd) {
    signal = 'SHORT_STD';
    signalDir = 'SHORT';
    signalLevel = 'STD';
  }

  const atrVal = sniper.metrics.atrVal;
  const close  = candles[candles.length - 1].close;

  // SL y TP
  let sl = null, tp = null, rr = parseFloat(process.env.MIN_RR || 2.5);
  if (signalDir === 'LONG') {
    sl = sniper.metrics.sl_long || close - atrVal * 1.2;
    const risk = Math.abs(close - sl);
    tp = close + risk * rr;
  } else if (signalDir === 'SHORT') {
    sl = sniper.metrics.sl_short || close + atrVal * 1.2;
    const risk = Math.abs(close - sl);
    tp = close - risk * rr;
  }

  return {
    valid: true,
    signal,
    signalDir,
    signalLevel,
    apexScoreLong,
    apexScoreShort,
    apexPrimeLong,
    apexPrimeShort,
    price: close,
    sl, tp,
    sniper: sniper.metrics,
    vsa: { ...vsa.metrics, pattern: vsa.pattern, vsaLong: vsa.vsaLong, vsaShort: vsa.vsaShort },
    qf: qf.metrics,
    trendBull, trendBear,
    timestamp: Date.now(),
  };
}

module.exports = { apexFusion, sniperEngine, vsaEngine, qfEngine };
