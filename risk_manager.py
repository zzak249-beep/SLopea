"""
SAMA APEX Bot - Risk Manager
Position sizing adaptativo, circuit breakers, trailing stop management
"""
import logging
from datetime import datetime, timezone, date
from dataclasses import dataclass, field
from typing import Optional
from config import (
    LEVERAGE, RISK_PER_TRADE, MAX_OPEN_TRADES,
    DAILY_LOSS_LIMIT, MIN_CONFLUENCE,
    TRAILING_ENABLED, TRAILING_ATR_MULT
)

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol:        str
    direction:     str          # LONG / SHORT
    entry_price:   float
    quantity:      float
    sl_price:      float
    tp_price:      float
    atr:           float
    confluence:    int
    opened_at:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trailing_sl:   float = 0.0  # SL actual (puede ir subiendo con trailing)
    pnl_pct:       float = 0.0


class RiskManager:
    def __init__(self):
        self.open_positions:  dict[str, Position] = {}
        self.daily_pnl_pct:   float = 0.0
        self.daily_date:      date  = date.today()
        self.trades_today:    int   = 0
        self.wins_today:      int   = 0
        self.losses_today:    int   = 0
        self.equity_start:    float = 0.0   # Balance al inicio del día
        self._circuit_broken: bool  = False

    # ─── Daily reset ──────────────────────────────────────────────────────────

    def _check_daily_reset(self):
        today = date.today()
        if today != self.daily_date:
            logger.info(f"📅 Nuevo día: reseteando PnL diario (ayer: {self.daily_pnl_pct:.2%})")
            self.daily_pnl_pct   = 0.0
            self.daily_date      = today
            self.trades_today    = 0
            self.wins_today      = 0
            self.losses_today    = 0
            self._circuit_broken = False

    # ─── Circuit Breaker ──────────────────────────────────────────────────────

    def is_circuit_broken(self, current_balance: float) -> bool:
        self._check_daily_reset()
        if self.equity_start <= 0:
            return False
        daily_loss = (current_balance - self.equity_start) / self.equity_start
        if daily_loss <= -DAILY_LOSS_LIMIT:
            if not self._circuit_broken:
                logger.warning(f"🚨 CIRCUIT BREAKER: PnL día {daily_loss:.2%} (límite {-DAILY_LOSS_LIMIT:.2%})")
                self._circuit_broken = True
            return True
        return False

    def set_equity_start(self, balance: float):
        if self.equity_start <= 0:
            self.equity_start = balance
            logger.info(f"💰 Equity inicial del día: {balance:.2f} USDT")

    # ─── Can Trade? ───────────────────────────────────────────────────────────

    def can_open_trade(self, symbol: str, confluence: int, balance: float) -> tuple[bool, str]:
        """Retorna (True, '') si se puede abrir, o (False, 'motivo') si no"""
        self._check_daily_reset()

        if self.is_circuit_broken(balance):
            return False, "Circuit breaker activo"

        if len(self.open_positions) >= MAX_OPEN_TRADES:
            return False, f"Máx posiciones abiertas ({MAX_OPEN_TRADES})"

        if symbol in self.open_positions:
            return False, f"Ya hay posición abierta en {symbol}"

        if confluence < MIN_CONFLUENCE:
            return False, f"Confluence score {confluence} < mínimo {MIN_CONFLUENCE}"

        return True, ""

    # ─── Position Sizing ──────────────────────────────────────────────────────

    def calculate_size(self, balance: float, entry: float,
                       sl: float, confluence: int) -> float:
        """
        Sizing adaptativo basado en confluence score:
        - Score 60-74:  1x risk
        - Score 75-89:  1.25x risk
        - Score 90-100: 1.5x risk

        Máximo riesgo real = RISK_PER_TRADE % del balance
        quantity = (risk_usdt / sl_distance_per_unit) * leverage
        """
        risk_mult = 1.0
        if confluence >= 90:
            risk_mult = 1.5
        elif confluence >= 75:
            risk_mult = 1.25

        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            return 0.0

        risk_usdt  = balance * RISK_PER_TRADE * risk_mult
        # quantity = cuántos contratos necesito para que si el precio baja sl_distance
        # pierda exactamente risk_usdt
        quantity = (risk_usdt / sl_distance)
        return round(quantity, 4)

    def calculate_tp(self, entry: float, sl: float, direction: str,
                     atr: float, confluence: int) -> float:
        """
        TP dinámico: ratio mínimo 1.5:1, ajustado por confluence
        - Score 60-74:  ratio 1.5
        - Score 75-89:  ratio 2.0
        - Score 90-100: ratio 2.5
        """
        risk   = abs(entry - sl)
        ratio  = 1.5
        if confluence >= 90:
            ratio = 2.5
        elif confluence >= 75:
            ratio = 2.0

        if direction == "LONG":
            return entry + risk * ratio
        else:
            return entry - risk * ratio

    # ─── Register / Close ─────────────────────────────────────────────────────

    def register_position(self, symbol: str, direction: str,
                           entry: float, qty: float,
                           sl: float, tp: float,
                           atr: float, confluence: int):
        pos = Position(
            symbol=symbol, direction=direction,
            entry_price=entry, quantity=qty,
            sl_price=sl, tp_price=tp,
            atr=atr, confluence=confluence,
            trailing_sl=sl,
        )
        self.open_positions[symbol] = pos
        self.trades_today += 1
        logger.info(f"✅ Posición registrada: {symbol} {direction} @ {entry:.4f} | SL {sl:.4f} | TP {tp:.4f}")

    def close_position(self, symbol: str, exit_price: float) -> Optional[dict]:
        pos = self.open_positions.pop(symbol, None)
        if not pos:
            return None

        if pos.direction == "LONG":
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - exit_price) / pos.entry_price

        if pnl_pct > 0:
            self.wins_today += 1
        else:
            self.losses_today += 1

        self.daily_pnl_pct += pnl_pct
        result = {
            "symbol":    symbol,
            "direction": pos.direction,
            "entry":     pos.entry_price,
            "exit":      exit_price,
            "pnl_pct":   pnl_pct,
            "confluence": pos.confluence,
        }
        logger.info(f"{'✅ WIN' if pnl_pct > 0 else '❌ LOSS'} {symbol}: {pnl_pct:.2%}")
        return result

    # ─── Trailing Stop ────────────────────────────────────────────────────────

    def should_update_trailing(self, symbol: str, current_price: float) -> tuple[bool, float]:
        """
        Mueve el SL usando múltiplo de ATR.
        Solo se activa si la posición está en profit >= 1 ATR.
        Retorna (True, nuevo_sl) si hay que actualizar.
        """
        if not TRAILING_ENABLED:
            return False, 0.0

        pos = self.open_positions.get(symbol)
        if not pos:
            return False, 0.0

        new_sl = pos.trailing_sl

        if pos.direction == "LONG":
            # En profit si precio > entrada + 1 ATR
            if current_price > pos.entry_price + pos.atr:
                potential_sl = current_price - pos.atr * TRAILING_ATR_MULT
                if potential_sl > pos.trailing_sl:
                    new_sl = potential_sl
        else:  # SHORT
            if current_price < pos.entry_price - pos.atr:
                potential_sl = current_price + pos.atr * TRAILING_ATR_MULT
                if potential_sl < pos.trailing_sl:
                    new_sl = potential_sl

        if new_sl != pos.trailing_sl:
            pos.trailing_sl = new_sl
            return True, round(new_sl, 4)

        return False, 0.0

    # ─── Stats ────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        total = self.wins_today + self.losses_today
        wr    = self.wins_today / total if total > 0 else 0.0
        return {
            "trades":    self.trades_today,
            "wins":      self.wins_today,
            "losses":    self.losses_today,
            "win_rate":  wr,
            "daily_pnl": self.daily_pnl_pct,
            "positions": len(self.open_positions),
            "circuit":   self._circuit_broken,
        }
