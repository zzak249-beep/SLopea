"""
risk_manager.py — Gestión de riesgo: Kelly, tamaño posición, límites diarios
"""
import math
import logging
from datetime import datetime, date

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import config as cfg

log = logging.getLogger("risk")


class RiskManager:
    def __init__(self):
        self.daily_trades: dict[date, int] = {}
        self.open_symbols: set[str]        = set()

    # ── Kelly Criterion ────────────────────────────────────────────────────

    def kelly_fraction(self, win_rate: float = None, rr: float = None) -> float:
        w  = win_rate or cfg.KELLY_WIN_RATE
        r  = rr       or cfg.KELLY_RR
        k  = (w * r - (1 - w)) / r
        return max(0.01, k * cfg.KELLY_FRACTION)

    # ── Tamaño de posición ────────────────────────────────────────────────

    def position_size(
        self,
        capital:    float,
        entry:      float,
        sl:         float,
        tier:       str = "STD",
        score:      float = 55,
    ) -> float:
        """
        Retorna la cantidad en contratos (qty base asset) para abrir.
        Usa Kelly ajustado al tier y al score.
        """
        risk_usdt = capital * (cfg.RISK_PCT / 100)

        # Boost por tier
        tier_mult = {"STD": 1.0, "FUEL": 1.25, "SUP": 1.5}.get(tier, 1.0)

        # Boost por score (0% extra a score=55, +20% a score=100)
        score_mult = 1.0 + (score - cfg.MIN_SCORE) / (100 - cfg.MIN_SCORE) * 0.20

        kelly = self.kelly_fraction()
        adjusted_risk = risk_usdt * tier_mult * score_mult * kelly

        sl_distance = abs(entry - sl)
        if sl_distance < entry * 0.0001:
            log.warning("SL demasiado cercano, usando 0.5% de distancia")
            sl_distance = entry * 0.005

        # qty = riesgo_USDT / (sl_distance * leverage)
        qty = (adjusted_risk * cfg.LEVERAGE) / (sl_distance * cfg.LEVERAGE)
        qty = max(qty, 0.001)

        log.info(f"Kelly sizing: risk={adjusted_risk:.2f} USDT sl_dist={sl_distance:.6f} qty={qty:.4f}")
        return round(qty, 4)

    # ── Límites diarios ───────────────────────────────────────────────────

    def check_daily_limit(self) -> bool:
        today = date.today()
        count = self.daily_trades.get(today, 0)
        if count >= cfg.MAX_DAILY_TRADES:
            log.warning(f"Límite diario alcanzado: {count}/{cfg.MAX_DAILY_TRADES}")
            return False
        return True

    def increment_daily(self):
        today = date.today()
        self.daily_trades[today] = self.daily_trades.get(today, 0) + 1

    # ── Posiciones abiertas ───────────────────────────────────────────────

    def check_max_open(self) -> bool:
        if len(self.open_symbols) >= cfg.MAX_OPEN_TRADES:
            log.warning(f"Max posiciones abiertas: {len(self.open_symbols)}/{cfg.MAX_OPEN_TRADES}")
            return False
        return True

    def add_position(self, symbol: str):
        self.open_symbols.add(symbol)
        self.increment_daily()

    def remove_position(self, symbol: str):
        self.open_symbols.discard(symbol)

    def can_trade(self, symbol: str) -> tuple[bool, str]:
        if symbol in self.open_symbols:
            return False, "ALREADY_OPEN"
        if not self.check_max_open():
            return False, "MAX_OPEN"
        if not self.check_daily_limit():
            return False, "DAILY_LIMIT"
        return True, "OK"

    def daily_summary(self) -> dict:
        today = date.today()
        return {
            "trades_today": self.daily_trades.get(today, 0),
            "max_daily":    cfg.MAX_DAILY_TRADES,
            "open_count":   len(self.open_symbols),
            "max_open":     cfg.MAX_OPEN_TRADES,
            "open_symbols": list(self.open_symbols),
        }
