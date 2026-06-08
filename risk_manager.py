"""
QF×JP Bot v6.3 — Risk Manager
Kelly Criterion, daily limits, position sizing, daily drawdown.
"""
import logging
from datetime import date, datetime
from typing import Optional
import asyncio

import config as C

log = logging.getLogger("risk")


class RiskManager:
    def __init__(self):
        self._today: date = date.today()
        self._daily_trades: int = 0
        self._daily_pnl: float = 0.0
        self._daily_loss_limit: float = C.CAPITAL * 0.05  # 5% daily max loss
        self._open_count: int = 0
        self._lock = asyncio.Lock()

    def _check_reset(self):
        today = date.today()
        if today != self._today:
            self._today = today
            self._daily_trades = 0
            self._daily_pnl = 0.0
            log.info("Daily stats reset for %s", today)

    # ── Kelly sizing ──────────────────────────────────────────────────────────

    def kelly_position_size(
        self,
        balance: float,
        entry: float,
        sl: float,
        score: float,
        tier: str,
    ) -> float:
        """
        Calcula la cantidad a operar (en moneda base) usando Kelly fraccional.
        Retorna 0.0 si el riesgo es inválido o los límites están alcanzados.
        """
        self._check_reset()

        if balance <= 0 or entry <= 0:
            return 0.0

        risk_per_unit = abs(entry - sl)
        if risk_per_unit < 1e-12:
            log.warning("SL demasiado cercano a entry para %s", entry)
            return 0.0

        # Ajuste de win_rate y RR por tier
        tier_mult = {"STD": 1.0, "FUEL": 1.1, "SUP": 1.25}.get(tier, 1.0)
        score_mult = 0.7 + 0.3 * (score / 100.0)

        p = min(C.KELLY_WIN_RATE * tier_mult * score_mult, 0.9)
        rr = C.KELLY_RR
        q = 1.0 - p

        kelly_f = (p * rr - q) / rr
        kelly_f = max(0.0, kelly_f)
        kelly_f *= C.KELLY_FRACTION  # fracción de Kelly

        # Capital arriesgado en USDT
        risk_usdt = balance * kelly_f * (C.RISK_PCT / 100.0)
        risk_usdt = min(risk_usdt, balance * 0.03)  # máx 3% del capital por trade

        # Qty en moneda base (con apalancamiento)
        qty = (risk_usdt * C.LEVERAGE) / risk_per_unit
        return round(qty, 6)

    # ── Límites diarios ───────────────────────────────────────────────────────

    async def can_trade(self) -> tuple[bool, str]:
        async with self._lock:
            self._check_reset()
            if self._daily_trades >= C.MAX_DAILY_TRADES:
                return False, f"daily_trades_limit({self._daily_trades}/{C.MAX_DAILY_TRADES})"
            if self._open_count >= C.MAX_OPEN_TRADES:
                return False, f"max_open_trades({self._open_count}/{C.MAX_OPEN_TRADES})"
            if self._daily_pnl <= -self._daily_loss_limit:
                return False, f"daily_drawdown_limit(pnl={self._daily_pnl:.2f})"
            return True, "ok"

    async def on_trade_opened(self):
        async with self._lock:
            self._daily_trades += 1
            self._open_count   += 1

    async def on_trade_closed(self, pnl: float):
        async with self._lock:
            self._open_count = max(0, self._open_count - 1)
            self._daily_pnl += pnl

    async def update_open_count(self, n: int):
        async with self._lock:
            self._open_count = n

    # ── Tier filter ───────────────────────────────────────────────────────────

    def tier_ok(self, tier: str) -> bool:
        hierarchy = {"NONE": -1, "STD": 0, "FUEL": 1, "SUP": 2}
        required  = hierarchy.get(C.MIN_TIER, 1)
        actual    = hierarchy.get(tier, -1)
        return actual >= required

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        self._check_reset()
        return {
            "date": str(self._today),
            "daily_trades": self._daily_trades,
            "max_daily_trades": C.MAX_DAILY_TRADES,
            "open_positions": self._open_count,
            "max_open_trades": C.MAX_OPEN_TRADES,
            "daily_pnl": round(self._daily_pnl, 2),
            "daily_loss_limit": round(self._daily_loss_limit, 2),
        }
