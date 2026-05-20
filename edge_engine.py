"""
SAMA APEX Bot - Edge Engine v1
Los 7 edges que separan este bot del resto:
  1. Volatility Regime Filter (BBW)
  2. Partial Profit Taking (50% en 1R + breakeven)
  3. Dynamic Risk Scaling (reduce size en drawdown)
  4. Funding Rate Extreme Fade (alpha puro)
  5. Correlation Guard (evita posiciones duplicadas)
  6. Order Book Imbalance Filter
  7. Momentum Ranking Score
"""
import logging
import numpy as np
from config import FUNDING_EXTREME

logger = logging.getLogger(__name__)


# ── 1. VOLATILITY REGIME FILTER ───────────────────────────────────────────────
def bollinger_band_width(close: np.ndarray, period: int = 20, std_mult: float = 2.0) -> float:
    """
    BBW = (upper - lower) / middle
    BBW bajo = mercado comprimido = evitar entradas (breakouts falsos)
    BBW alto = expansión = señales más fiables
    """
    if len(close) < period:
        return 0.05
    window = close[-period:]
    mid    = window.mean()
    std    = window.std()
    if mid == 0:
        return 0.05
    return (std_mult * 2 * std) / mid


def bbw_percentile(close: np.ndarray, period: int = 20, lookback: int = 100) -> float:
    """Percentil actual del BBW respecto a los últimos N períodos."""
    if len(close) < lookback + period:
        return 50.0
    bbws = []
    for i in range(lookback):
        idx  = len(close) - lookback + i
        w    = close[max(0, idx-period): idx]
        if len(w) >= period:
            mid = w.mean()
            std = w.std()
            bbws.append((2 * 2 * std) / mid if mid > 0 else 0)
    current = bbw_percentile_val = bbws[-1] if bbws else 0.05
    pct = sum(1 for b in bbws if b < current) / len(bbws) * 100 if bbws else 50
    return pct


def is_volatile_regime(close: np.ndarray, min_bbw_pct: float = 25.0) -> tuple[bool, float]:
    """
    True si el mercado está en expansión (BBW > percentil 25).
    False = mercado comprimido = no entrar.
    """
    pct = bbw_percentile(close, period=20, lookback=100)
    ok  = pct >= min_bbw_pct
    logger.debug(f"BBW percentil: {pct:.1f}% → {'✅ expansión' if ok else '⛔ comprimido'}")
    return ok, pct


# ── 2. PARTIAL PROFIT TAKING ──────────────────────────────────────────────────
class PartialProfitManager:
    """
    Gestiona salidas parciales:
    - Al llegar a 1R: cierra 50%, mueve SL a breakeven
    - Deja el 50% restante correr con trailing
    """
    def __init__(self):
        self.half_closed: set[str] = set()

    def should_take_partial(self, symbol: str, direction: str,
                             current_price: float, entry: float, sl: float) -> bool:
        if symbol in self.half_closed:
            return False
        risk = abs(entry - sl)
        if direction == "LONG":
            return current_price >= entry + risk   # 1R profit
        else:
            return current_price <= entry - risk

    def mark_partial_taken(self, symbol: str):
        self.half_closed.add(symbol)

    def clear(self, symbol: str):
        self.half_closed.discard(symbol)

    def get_breakeven_sl(self, direction: str, entry: float, fee_buffer: float = 0.0005) -> float:
        """SL en breakeven + pequeño buffer para cubrir comisiones."""
        if direction == "LONG":
            return entry * (1 + fee_buffer)
        else:
            return entry * (1 - fee_buffer)


# ── 3. DYNAMIC RISK SCALING ───────────────────────────────────────────────────
def dynamic_risk_multiplier(daily_pnl_pct: float, base_risk: float) -> float:
    """
    Escala el riesgo según el PnL del día:
    PnL > 0%:    1.0x (normal)
    PnL -1/-2%:  0.75x
    PnL -2/-4%:  0.5x
    PnL < -4%:   0.25x (modo supervivencia)
    """
    if daily_pnl_pct >= 0:
        mult = 1.0
    elif daily_pnl_pct >= -0.02:
        mult = 0.75
    elif daily_pnl_pct >= -0.04:
        mult = 0.5
    else:
        mult = 0.25

    scaled = base_risk * mult
    if mult < 1.0:
        logger.info(f"⚖️  Dynamic risk: PnL={daily_pnl_pct*100:+.1f}% → risk×{mult} ({scaled*100:.2f}%)")
    return scaled


# ── 4. FUNDING RATE EXTREME FADE ──────────────────────────────────────────────
def funding_bias(funding_rate: float, extreme_threshold: float = FUNDING_EXTREME) -> dict:
    """
    Cuando el funding es extremo, el mercado está sobrecargado en una dirección.
    Esto crea alpha al ir en contra o esperar el squeeze.

    Returns: {"bias": "LONG"/"SHORT"/"NONE", "strength": 0-1, "fade": bool}
    """
    abs_fund = abs(funding_rate)

    if funding_rate > extreme_threshold * 2:
        # Longs pagando mucho → buscar SHORTs
        return {"bias": "SHORT", "strength": min(1.0, abs_fund / (extreme_threshold*4)), "fade": True,
                "msg": f"⚡ Funding extremo +{funding_rate*100:.4f}% → favorecer SHORTs"}
    elif funding_rate < -extreme_threshold * 2:
        # Shorts pagando mucho → buscar LONGs
        return {"bias": "LONG",  "strength": min(1.0, abs_fund / (extreme_threshold*4)), "fade": True,
                "msg": f"⚡ Funding extremo {funding_rate*100:.4f}% → favorecer LONGs"}
    elif funding_rate > extreme_threshold:
        return {"bias": "SHORT", "strength": 0.5, "fade": False, "msg": ""}
    elif funding_rate < -extreme_threshold:
        return {"bias": "LONG",  "strength": 0.5, "fade": False, "msg": ""}
    else:
        return {"bias": "NONE",  "strength": 0.0, "fade": False, "msg": ""}


def funding_score_bonus(direction: str, funding_rate: float) -> int:
    """Bonus/malus al confluence score basado en funding."""
    fb = funding_bias(funding_rate)
    if fb["bias"] == "NONE":
        return 0
    elif fb["bias"] == direction:
        return int(fb["strength"] * 20)   # hasta +20 pts bonus
    else:
        return int(-fb["strength"] * 25)  # hasta -25 pts malus


# ── 5. CORRELATION GUARD ──────────────────────────────────────────────────────
# Grupos de correlación alta (>0.7 históricamente)
CORRELATION_GROUPS = [
    {"BTC-USDT", "ETH-USDT", "BNB-USDT"},       # Layer 1 principales
    {"SOL-USDT", "AVAX-USDT", "NEAR-USDT"},      # Alt L1
    {"DYDX-USDT", "GMX-USDT", "PERP-USDT"},      # DeFi derivatives
    {"APE-USDT", "SAND-USDT", "MANA-USDT"},      # GameFi/NFT
    {"LINK-USDT", "BAND-USDT"},                   # Oracles
    {"MATIC-USDT", "OP-USDT", "ARB-USDT"},       # L2s
]

def is_correlated_blocked(new_symbol: str, new_direction: str,
                           open_positions: dict) -> tuple[bool, str]:
    """
    Bloquea si ya hay una posición en la misma dirección
    en un par altamente correlacionado.
    """
    if not open_positions:
        return False, ""

    for group in CORRELATION_GROUPS:
        if new_symbol not in group:
            continue
        for sym, pos in open_positions.items():
            if sym in group and pos.direction == new_direction and sym != new_symbol:
                return True, f"{new_symbol} correlacionado con {sym} ({new_direction})"

    return False, ""


# ── 6. ORDER BOOK IMBALANCE ───────────────────────────────────────────────────
def orderbook_imbalance(bids: list, asks: list) -> dict:
    """
    Calcula el desequilibrio entre bids y asks.
    bids/asks: lista de [price, qty]

    Ratio > 1.5 → más presión compradora → favorable LONG
    Ratio < 0.67 → más presión vendedora → favorable SHORT
    """
    if not bids or not asks:
        return {"ratio": 1.0, "bias": "NEUTRAL", "ok_long": True, "ok_short": True}

    bid_vol = sum(float(b[1]) for b in bids[:10])
    ask_vol = sum(float(a[1]) for a in asks[:10])

    ratio = bid_vol / ask_vol if ask_vol > 0 else 1.0

    if ratio >= 1.5:
        bias = "BUY"
    elif ratio <= 0.67:
        bias = "SELL"
    else:
        bias = "NEUTRAL"

    return {
        "ratio":    round(ratio, 2),
        "bias":     bias,
        "ok_long":  ratio >= 0.8,    # No entrar LONG si asks dominan fuerte
        "ok_short": ratio <= 1.25,   # No entrar SHORT si bids dominan fuerte
    }


def check_ob_for_trade(direction: str, ob: dict) -> tuple[bool, str]:
    """Valida si el order book es favorable para la dirección."""
    if direction == "LONG"  and not ob["ok_long"]:
        return False, f"OB desfavorable LONG (ratio={ob['ratio']}, bias={ob['bias']})"
    if direction == "SHORT" and not ob["ok_short"]:
        return False, f"OB desfavorable SHORT (ratio={ob['ratio']}, bias={ob['bias']})"
    return True, ""


# ── 7. MOMENTUM RANKING ───────────────────────────────────────────────────────
def momentum_rank_score(confluence_score: int, avg_slope: float,
                         rvol: float, funding_rate: float,
                         bbw_pct: float, direction: str) -> float:
    """
    Score final compuesto para ranking de oportunidades.
    Determina qué par ejecutar primero cuando hay múltiples señales.
    """
    # Base: confluence score
    rank = float(confluence_score)

    # Bonus por slope momentum (tendencia fuerte = más confianza)
    rank += min(15, avg_slope / 3)

    # Bonus por volumen relativo
    rank += min(10, rvol * 2)

    # Bonus por expansión de volatilidad
    rank += min(10, bbw_pct / 10)

    # Bonus por funding alineado
    rank += funding_score_bonus(direction, funding_rate)

    return round(rank, 2)
