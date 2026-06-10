"""
QF×JP Bot v6.4 — Risk Manager
Kelly Criterion, daily limits, position sizing, daily drawdown.

CAMBIOS v6.4 vs v6.3:
  [FIX]    kelly_position_size: eliminado × LEVERAGE en qty.
           En futuros perpetuos riesgo_USD = qty × risk_per_unit — el leverage
           solo determina el margen requerido (notional/leverage), no el PnL.
           Antes: qty = (risk_usdt × LEV) / risk_per_unit → notionals 5× inflados
           Ahora: qty = risk_usdt / risk_per_unit → notionals correctos

  [FIX]    Notional clampeado internamente en lugar de rechazado.
           Antes el scanner hacía skip de TODAS las señales. Ahora la qty se
           reduce para caber en MAX_NOTIONAL y el trade sí se abre.

  [NEW]    update_balance(balance): recalcula daily_loss_limit con capital real.
           Antes se usaba C.CAPITAL fijo desde __init__.

  [NEW]    Min notional guard: descarta dust trades < _MIN_NOTIONAL_USDT (5 USDT).

  [NEW]    Kelly p cappado en _MAX_KELLY_P=0.75 (antes podía llegar a 0.9 × tier_mult).

  [NEW]    Score normalizado desde C.MIN_SCORE en lugar de desde 0 → más
           diferenciación entre señales medias/altas.

  [NEW]    Tier SUPREMA añadido en hierarchy y tier_mult (1.4×).

  [NEW]    notional_ok() helper para validación externa sin duplicar lógica.

  [NEW]    log.info en cada sizing con notional calculado (antes solo DEBUG).

  [NEW]    status() incluye balance_ref para debugging de límites.

  [NEW]    _MAX_RISK_PCT_TRADE reducido de 8% a 6% (más conservador).
"""

import logging
from datetime import date
from typing import Optional
import asyncio

import config as C

log = logging.getLogger("risk")

# ── Constantes internas (ajustables sin tocar config.py) ─────────────────────
_MAX_KELLY_P        = 0.75   # prob. máxima para Kelly (evita sobreajuste)
_MAX_RISK_PCT_TRADE = 0.06   # cap de riesgo por trade (6% del balance)
_MIN_NOTIONAL_USDT  = 5.0    # notional mínimo para abrir trade (descarta dust)
_KELLY_SCALE_REF    = 0.10   # kelly_f de referencia que mapea a kelly_scale=1.0

# Jerarquía de tiers y multiplicadores (compartida entre métodos)
_TIER_HIERARCHY = {"NONE": -1, "STD": 0, "FUEL": 1, "SUP": 2, "SUPREMA": 3}
_TIER_MULT      = {"STD": 1.0, "FUEL": 1.1, "SUP": 1.25, "SUPREMA": 1.4}


class RiskManager:
    def __init__(self):
        self._today            = date.today()
        self._daily_trades     = 0
        self._daily_pnl        = 0.0
        self._balance          = max(C.CAPITAL, 1.0)
        self._daily_loss_limit = self._balance * 0.05   # 5% drawdown diario
        self._open_count       = 0
        self._lock             = asyncio.Lock()

    # ── Reset diario ──────────────────────────────────────────────────────────

    def _check_reset(self):
        today = date.today()
        if today != self._today:
            self._today        = today
            self._daily_trades = 0
            self._daily_pnl    = 0.0
            log.info("[risk] Daily stats reset → %s", today)

    # ── Actualizar balance real ───────────────────────────────────────────────

    def update_balance(self, balance: float):
        """
        Llamar tras cada fetch_balance exitoso.
        Recalcula daily_loss_limit con el capital real del exchange.
        """
        if balance > 0:
            self._balance          = balance
            self._daily_loss_limit = balance * 0.05
            log.debug(
                "[risk] balance=%.2f USDT | loss_limit=%.2f USDT (5%%)",
                balance, self._daily_loss_limit,
            )

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

        FÓRMULA CORRECTA para futuros perpetuos:
          risk_usdt  = balance × (RISK_PCT/100) × kelly_scale
          qty        = risk_usdt / |entry - sl|    ← sin × LEVERAGE
          notional   = qty × entry                 ← clampeado a MAX_NOTIONAL

        El leverage no interviene en el sizing porque el P&L en perpetuos es:
          PnL = qty × (exit_price − entry_price)
        y el riesgo si toca el SL es:
          loss = qty × |entry − sl| = risk_usdt  ✓

        El leverage solo determina el margen a bloquear: notional / leverage.

        Ejemplo real (balance=500, RISK_PCT=2, entry=5.0 PROM, SL=4.75 → 5% SL):
          risk_usdt  = 500 × 0.02 × 0.8 = 8.0 USDT
          qty        = 8.0 / 0.25 = 32 PROM
          notional   = 32 × 5.0 = 160 USDT  ← pasa MAX_NOTIONAL=500
          margen req = 160 / 5 (LEV) = 32 USDT (6.4% del balance)
        """
        self._check_reset()

        if balance <= 0 or entry <= 0:
            log.warning("[sizing] balance o entry inválidos (%.2f / %.8f)", balance, entry)
            return 0.0

        risk_per_unit = abs(entry - sl)
        if risk_per_unit < 1e-12:
            log.warning("[sizing] SL demasiado cercano a entry=%.8f", entry)
            return 0.0

        # ── Kelly como escalador de calidad (0.4 – 1.0) ──────────────────────
        tier_mult  = _TIER_MULT.get(tier, 1.0)

        # Normalizar score desde MIN_SCORE para mayor resolución en rango útil
        min_score  = getattr(C, "MIN_SCORE", 40)
        score_norm = max(0.0, (score - min_score) / max(1.0, 100.0 - min_score))
        score_mult = 0.70 + 0.30 * score_norm   # 0.70 (score mínimo) → 1.00 (score=100)

        p = min(C.KELLY_WIN_RATE * tier_mult * score_mult, _MAX_KELLY_P)
        q = 1.0 - p
        rr = C.KELLY_RR

        kelly_f = max(0.0, (p * rr - q) / rr)
        kelly_f *= C.KELLY_FRACTION   # fracción conservadora (ej. 0.25)

        # [0, _KELLY_SCALE_REF] → [0.4, 1.0]
        kelly_scale = 0.4 + 0.6 * min(1.0, kelly_f / _KELLY_SCALE_REF)

        # ── Capital arriesgado por trade ──────────────────────────────────────
        risk_usdt = balance * (C.RISK_PCT / 100.0) * kelly_scale
        risk_usdt = min(risk_usdt, balance * _MAX_RISK_PCT_TRADE)   # cap 6%

        # ── Qty en moneda base (SIN multiplicar por LEVERAGE) ─────────────────
        qty = risk_usdt / risk_per_unit
        notional = qty * entry

        # ── Guard: notional mínimo (dust trade) ───────────────────────────────
        if notional < _MIN_NOTIONAL_USDT:
            log.warning(
                "[sizing] %s score=%.1f notional=%.4f < min=%.1f → skip (dust)",
                tier, score, notional, _MIN_NOTIONAL_USDT,
            )
            return 0.0

        # ── Clampear notional si supera MAX_NOTIONAL ──────────────────────────
        # En lugar de rechazar la señal, reducimos qty para que caber en el límite.
        max_notional = getattr(C, "MAX_NOTIONAL_PER_TRADE", 0)
        if max_notional > 0 and notional > max_notional:
            qty      = max_notional / entry
            notional = max_notional
            log.info(
                "[sizing] %s notional clampeado → %.2f USDT (max=%.0f) qty=%.6f",
                tier, notional, max_notional, qty,
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
        Validación rápida antes de enviar la orden.
        Devuelve (True, "ok") o (False, motivo_str).
        """
        if qty <= 0:
            return False, "qty_zero"
        notional = qty * entry
        if notional < _MIN_NOTIONAL_USDT:
            return False, f"notional_dust({notional:.4f}<{_MIN_NOTIONAL_USDT})"
        max_n = getattr(C, "MAX_NOTIONAL_PER_TRADE", 0)
        if max_n > 0 and notional > max_n:
            return False, f"notional_exceeded({notional:.2f}>{max_n})"
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
                    f"drawdown(pnl={self._daily_pnl:.2f} / "
                    f"limit={-self._daily_loss_limit:.2f})"
                )
            return True, "ok"

    async def on_trade_opened(self):
        async with self._lock:
            self._daily_trades += 1
            self._open_count   += 1

    async def on_trade_closed(self, pnl: float):
        async with self._lock:
            self._open_count = max(0, self._open_count - 1)
            self._daily_pnl += pnl
            log.debug(
                "[risk] trade cerrado pnl=%.4f | daily_pnl=%.4f / limit=%.4f",
                pnl, self._daily_pnl, -self._daily_loss_limit,
            )

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
