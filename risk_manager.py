"""
QF×JP Bot v6.5 — Risk Manager

CAMBIOS v6.5 vs v6.4:
  [FIX]  MIN_SL_PCT guard: descarta señales donde el SL está demasiado
         cerca del entry (< 0.3% del precio). Evita qty explosiva en
         micro-caps como SHIB (SL a 1 pip → 210M contratos → 977 USDT).

  [FIX]  MAX_NOTIONAL_PER_TRADE ahora tiene default 500 USDT si no está
         en config.py. Antes getattr devolvía 0 → sin clamp → trades
         con 600-977 USDT de notional.

  [FIX]  Clamp de notional usa SIEMPRE el entry de la señal (no mark_price)
         para garantizar consistencia entre sizing y validación.

HISTORIAL v6.4:
  [FIX]  kelly_position_size: eliminado × LEVERAGE en qty.
  [FIX]  Notional clampeado internamente (no rechaza señal, reduce qty).
  [NEW]  update_balance(): daily_loss_limit dinámico con capital real.
  [NEW]  Kelly p cappado en 0.75.
  [NEW]  Score normalizado desde C.MIN_SCORE.
  [NEW]  notional_ok() helper para validación externa.
  [NEW]  Tier SUPREMA añadido.
"""

import logging
from datetime import date
from typing import Optional
import asyncio

import config as C

log = logging.getLogger("risk")

# ── Constantes internas ───────────────────────────────────────────────────────
_MAX_KELLY_P        = 0.75    # prob. máxima Kelly (evita sobreajuste)
_MAX_RISK_PCT_TRADE = 0.06    # cap de riesgo por trade (6% del balance)
_MIN_NOTIONAL_USDT  = 5.0     # notional mínimo — descarta dust trades
_KELLY_SCALE_REF    = 0.10    # kelly_f que mapea a kelly_scale = 1.0

# Jerarquía de tiers (compartida entre métodos)
_TIER_HIERARCHY = {"NONE": -1, "STD": 0, "FUEL": 1, "SUP": 2, "SUPREMA": 3}
_TIER_MULT      = {"STD": 1.0, "FUEL": 1.1, "SUP": 1.25, "SUPREMA": 1.4}


class RiskManager:
    def __init__(self):
        self._today            = date.today()
        self._daily_trades     = 0
        self._daily_pnl        = 0.0
        self._balance          = max(float(C.CAPITAL), 1.0)
        self._daily_loss_limit = self._balance * 0.05
        self._open_count       = 0
        self._lock             = asyncio.Lock()

    # ── Reset diario ──────────────────────────────────────────────────────────

    def _check_reset(self):
        today = date.today()
        if today != self._today:
            self._today        = today
            self._daily_trades = 0
            self._daily_pnl    = 0.0
            log.info("[risk] Daily reset → %s", today)

    # ── Actualizar balance real ───────────────────────────────────────────────

    def update_balance(self, balance: float):
        """Llamar tras cada fetch_balance exitoso para loss_limit dinámico."""
        if balance > 0:
            self._balance          = balance
            self._daily_loss_limit = balance * 0.05
            log.debug("[risk] balance=%.2f USDT | loss_limit=%.2f USDT",
                      balance, self._daily_loss_limit)

    # ── Kelly sizing ──────────────────────────────────────────────────────────

    def kelly_position_size(
        self,
        balance: float,
        entry:   float,
        sl:      float,
        score:   float,
        tier:    str,
    ) -> float:
        """
        Calcula qty en moneda base para futuros perpetuos.

        Fórmula:
          risk_usdt = balance × (RISK_PCT/100) × kelly_scale
          qty       = risk_usdt / |entry - sl|    ← sin × LEVERAGE
          notional  = qty × entry                 ← clampeado a MAX_NOTIONAL

        El leverage no afecta qty porque en perpetuos:
          PnL = qty × (exit − entry)   →   riesgo si SL = qty × |entry − sl|

        El leverage solo determina margen = notional / leverage.

        Ejemplo (balance=500, RISK_PCT=2, entry=5.0, SL=4.75):
          risk_usdt = 500 × 0.02 × 0.8 = 8 USDT
          qty       = 8 / 0.25 = 32 contratos
          notional  = 32 × 5.0 = 160 USDT ✓
          margen    = 160 / 5 (LEV) = 32 USDT (6.4% balance)
        """
        self._check_reset()

        if balance <= 0 or entry <= 0:
            log.warning("[sizing] datos inválidos balance=%.2f entry=%.8f", balance, entry)
            return 0.0

        risk_per_unit = abs(entry - sl)
        if risk_per_unit < 1e-12:
            log.warning("[sizing] SL=entry (%.8f) → skip", entry)
            return 0.0

        # ── Guard: SL mínimo como % del entry (evita qty explosiva micro-caps) ─
        min_sl_pct        = getattr(C, "MIN_SL_PCT", 0.003)   # default 0.3%
        min_risk_per_unit = entry * min_sl_pct
        if risk_per_unit < min_risk_per_unit:
            actual_pct = 100.0 * risk_per_unit / entry
            log.warning(
                "[sizing] SL demasiado apretado %.4f%% < %.1f%% → skip (%s)",
                actual_pct, min_sl_pct * 100, tier,
            )
            return 0.0

        # ── Kelly como escalador de calidad 0.4 – 1.0 ────────────────────────
        tier_mult  = _TIER_MULT.get(tier, 1.0)
        min_score  = getattr(C, "MIN_SCORE", 40)
        score_norm = max(0.0, (score - min_score) / max(1.0, 100.0 - min_score))
        score_mult = 0.70 + 0.30 * score_norm   # 0.70 → 1.00

        p = min(C.KELLY_WIN_RATE * tier_mult * score_mult, _MAX_KELLY_P)
        q = 1.0 - p
        rr = C.KELLY_RR

        kelly_f = max(0.0, (p * rr - q) / rr)
        kelly_f *= C.KELLY_FRACTION

        kelly_scale = 0.4 + 0.6 * min(1.0, kelly_f / _KELLY_SCALE_REF)

        # ── Capital arriesgado ────────────────────────────────────────────────
        risk_usdt = balance * (C.RISK_PCT / 100.0) * kelly_scale
        risk_usdt = min(risk_usdt, balance * _MAX_RISK_PCT_TRADE)  # cap 6%

        # ── Qty en moneda base SIN leverage ──────────────────────────────────
        qty      = risk_usdt / risk_per_unit
        notional = qty * entry

        # ── Guard mínimo (dust trade) ─────────────────────────────────────────
        if notional < _MIN_NOTIONAL_USDT:
            log.warning("[sizing] notional=%.4f < %.1f USDT → skip (dust)",
                        notional, _MIN_NOTIONAL_USDT)
            return 0.0

        # ── Clamp a MAX_NOTIONAL (default 500 si no está en config.py) ───────
        max_notional = float(getattr(C, "MAX_NOTIONAL_PER_TRADE", 500))
        if notional > max_notional:
            qty_orig = qty
            qty      = max_notional / entry
            notional = max_notional
            log.info(
                "[sizing] %s notional clampeado %.2f→%.2f USDT "
                "(qty %.6f→%.6f, entry=%.8f)",
                tier, qty_orig * entry, notional, qty_orig, qty, entry,
            )

        log.info(
            "[sizing] %s score=%.1f ks=%.2f risk=%.2f USDT "
            "qty=%.6f notional=%.2f USDT (entry=%.8f SL=%.8f)",
            tier, score, kelly_scale, risk_usdt,
            qty, notional, entry, sl,
        )
        return round(qty, 6)

    # ── Validación notional externa ───────────────────────────────────────────

    def notional_ok(self, qty: float, entry: float) -> tuple[bool, str]:
        """
        Validación final antes de enviar orden.
        Retorna (True, "ok") o (False, motivo).
        """
        if qty <= 0:
            return False, "qty_zero"
        notional = qty * entry
        if notional < _MIN_NOTIONAL_USDT:
            return False, f"dust({notional:.4f}<{_MIN_NOTIONAL_USDT})"
        max_n = float(getattr(C, "MAX_NOTIONAL_PER_TRADE", 500))
        if notional > max_n * 1.05:   # 5% tolerancia por diferencia entry/mark_price
            return False, f"notional({notional:.2f}>{max_n})"
        return True, "ok"

    # ── Límites diarios ───────────────────────────────────────────────────────

    async def can_trade(self) -> tuple[bool, str]:
        async with self._lock:
            self._check_reset()
            if self._daily_trades >= C.MAX_DAILY_TRADES:
                return False, f"daily_trades({self._daily_trades}/{C.MAX_DAILY_TRADES})"
            if self._open_count >= C.MAX_OPEN_TRADES:
                return False, f"max_open({self._open_count}/{C.MAX_OPEN_TRADES})"
            if self._daily_pnl <= -self._daily_loss_limit:
                return False, (
                    f"drawdown(pnl={self._daily_pnl:.2f}"
                    f"/limit={-self._daily_loss_limit:.2f})"
                )
            return True, "ok"

    async def on_trade_opened(self):
        async with self._lock:
            self._daily_trades += 1
            self._open_count   += 1
            log.info("[risk] trade abierto → daily=%d/%d open=%d/%d",
                     self._daily_trades, C.MAX_DAILY_TRADES,
                     self._open_count,   C.MAX_OPEN_TRADES)

    async def on_trade_closed(self, pnl: float):
        async with self._lock:
            self._open_count = max(0, self._open_count - 1)
            self._daily_pnl += pnl
            log.info("[risk] trade cerrado pnl=%.4f | daily_pnl=%.4f/%.4f",
                     pnl, self._daily_pnl, -self._daily_loss_limit)

    async def update_open_count(self, n: int):
        async with self._lock:
            self._open_count = n

    # ── Tier filter ───────────────────────────────────────────────────────────

    def tier_ok(self, tier: str) -> bool:
        min_tier = getattr(C, "MIN_TIER", "FUEL")
        required = _TIER_HIERARCHY.get(min_tier, 1)
        actual   = _TIER_HIERARCHY.get(tier, -1)
        return actual >= required

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        self._check_reset()
        return {
            "date":             str(self._today),
            "daily_trades":     self._daily_trades,
            "max_daily_trades": C.MAX_DAILY_TRADES,
            "open_positions":   self._open_count,
            "max_open_trades":  C.MAX_OPEN_TRADES,
            "daily_pnl":        round(self._daily_pnl, 2),
            "daily_loss_limit": round(-self._daily_loss_limit, 2),
            "balance_ref":      round(self._balance, 2),
        }
