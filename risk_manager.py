"""
QF×JP Bot v6.3 — Risk Manager
Kelly Criterion, daily limits, position sizing, daily drawdown.

FIX v6.3.1: kelly_position_size usa RISK_PCT como base directa.
  Antes: balance × kelly_f × (RISK_PCT/100) → ~0.1 USDT de riesgo (demasiado pequeño)
  Ahora: balance × (RISK_PCT/100) escalado por Kelly → tamaño proporcional al capital
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
        Calcula la cantidad a operar (en moneda base).

        Fórmula:
          risk_usdt = balance × (RISK_PCT/100)          ← base directa
          kelly_scale = escala 0.4–1.0 según calidad    ← ajuste por señal
          qty = (risk_usdt × kelly_scale × LEVERAGE) / |entry - sl|

        Ejemplo con balance=230, RISK_PCT=5, LEVERAGE=5:
          risk_usdt base = 11.5 USDT
          kelly_scale ~0.6–1.0 = riesgo real 6.9–11.5 USDT
          notional = 34–57 USDT por trade
        """
        self._check_reset()

        if balance <= 0 or entry <= 0:
            return 0.0

        risk_per_unit = abs(entry - sl)
        if risk_per_unit < 1e-12:
            log.warning("SL demasiado cercano a entry para %s", entry)
            return 0.0

        # ── Kelly como escalador (0.4 – 1.0) ─────────────────────────────────
        tier_mult  = {"STD": 1.0, "FUEL": 1.1, "SUP": 1.25}.get(tier, 1.0)
        score_mult = 0.7 + 0.3 * (score / 100.0)

        p = min(C.KELLY_WIN_RATE * tier_mult * score_mult, 0.9)
        rr = C.KELLY_RR
        q = 1.0 - p

        kelly_f = (p * rr - q) / rr
        kelly_f = max(0.0, kelly_f)
        kelly_f *= C.KELLY_FRACTION  # fracción de Kelly (default 0.25)

        # Normalizar kelly_f en rango 0.4–1.0
        # kelly_f típico: 0.01–0.12 → mapeamos [0, 0.12] → [0.4, 1.0]
        kelly_scale = 0.4 + 0.6 * min(1.0, kelly_f / 0.10)

        # ── Capital arriesgado por trade ──────────────────────────────────────
        risk_usdt = balance * (C.RISK_PCT / 100.0) * kelly_scale
        # Cap: máximo 8% del capital por trade (antes era 3%)
        risk_usdt = min(risk_usdt, balance * 0.08)

        # ── Qty en moneda base (con apalancamiento) ────────────────────────────
        qty = (risk_usdt * C.LEVERAGE) / risk_per_unit

        log.debug(
            "[sizing] balance=%.2f risk_pct=%.1f%% kelly_scale=%.2f "
            "risk_usdt=%.2f leverage=%dx qty=%.6f",
            balance, C.RISK_PCT, kelly_scale, risk_usdt, C.LEVERAGE, qty,
        )
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
