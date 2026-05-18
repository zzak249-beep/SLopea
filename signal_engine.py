"""
SAMA APEX Bot - Signal Engine FIXED v2
BUGS CORREGIDOS:
  1. prev_all_bull verifica los 3 TFs (no solo local)
  2. last_dir se actualiza SOLO via confirm_signal() tras trade exitoso
  3. Logging diagnóstico detallado para saber por qué no hay señales
"""
import logging
from dataclasses import dataclass
from typing import Optional
from indicators import confluence_score, is_active_session
from config import MIN_CONFLUENCE

logger = logging.getLogger(__name__)


@dataclass
class SignalState:
    sig:      int = 0       # -1=SHORT  0=NEUTRAL  1=LONG
    last_dir: str = "NONE"  # Actualizado SOLO al confirmar trade abierto
    count:    int = 0


class SignalEngine:
    def __init__(self):
        self._states: dict[str, SignalState] = {}

    def _get_state(self, symbol: str) -> SignalState:
        if symbol not in self._states:
            self._states[symbol] = SignalState()
        return self._states[symbol]

    def confirm_signal(self, symbol: str, direction: str):
        """Llamar SOLO cuando BingX confirma que el trade abrió."""
        state = self._get_state(symbol)
        state.last_dir = direction
        state.count   += 1
        logger.info(f"✅ Señal confirmada: {symbol} {direction}")

    def clear_direction(self, symbol: str):
        """Llamar al cerrar posición."""
        state = self._get_state(symbol)
        state.last_dir = "NONE"
        state.sig      = 0
        logger.info(f"🔓 {symbol} reseteado — listo para nueva señal")

    def evaluate(self, symbol: str, local: dict, m1: dict, m2: dict,
                 funding: float = 0.0) -> Optional[dict]:
        """
        Pine Script original:
          buy  = all_bull AND NOT prev_all_bull AND has_volume
          sell = all_bear AND NOT prev_all_bear AND has_volume
          var sig solo cambia si hay nueva señal opuesta
          longsignal = sig==1 AND sig[1]!=1
        """
        state   = self._get_state(symbol)
        session = is_active_session()

        lt, m1t, m2t           = local["trend"], m1["trend"], m2["trend"]
        prev_lt  = local.get("prev_trend", "CHOP")
        prev_m1t = m1.get("prev_trend",   "CHOP")
        prev_m2t = m2.get("prev_trend",   "CHOP")
        has_volume = local["has_volume"]

        # BUY / SELL — Pine Script exact
        all_bull      = (lt == "BULL" and m1t == "BULL" and m2t == "BULL")
        prev_all_bull = (prev_lt == "BULL" and prev_m1t == "BULL" and prev_m2t == "BULL")
        buy  = all_bull and not prev_all_bull and has_volume

        all_bear      = (lt == "BEAR" and m1t == "BEAR" and m2t == "BEAR")
        prev_all_bear = (prev_lt == "BEAR" and prev_m1t == "BEAR" and prev_m2t == "BEAR")
        sell = all_bear and not prev_all_bear and has_volume

        # Diagnóstico siempre visible
        logger.debug(
            f"{symbol} TF={lt}/{m1t}/{m2t} prev={prev_lt}/{prev_m1t}/{prev_m2t} "
            f"rvol={local['rvol']:.2f} buy={buy} sell={sell} sig={state.sig} last={state.last_dir}"
        )

        if not buy and not sell:
            if all_bull or all_bear:
                logger.debug(f"{symbol}: alineado {lt} pero sin cambio de estado o sin volumen (rvol={local['rvol']:.2f})")
            return None

        # State machine
        if buy  and state.sig <= 0: state.sig = 1
        if sell and state.sig >= 0: state.sig = -1

        long_signal  = (state.sig ==  1) and (state.last_dir != "LONG")
        short_signal = (state.sig == -1) and (state.last_dir != "SHORT")

        if not long_signal and not short_signal:
            return None

        direction = "LONG" if long_signal else "SHORT"

        # Confluence
        conf = confluence_score(local, m1, m2, funding, session)
        logger.info(
            f"📡 {symbol} {direction} score={conf['score']}/100 | "
            f"slopes={local['slope']:.0f}/{m1['slope']:.0f}/{m2['slope']:.0f}° | "
            f"rvol={local['rvol']:.2f} | fund={funding*100:.4f}%"
        )

        if conf["score"] < MIN_CONFLUENCE:
            logger.info(f"{symbol}: descartada — score {conf['score']} < {MIN_CONFLUENCE}")
            return None

        return {
            "symbol":     symbol,
            "direction":  direction,
            "confluence": conf,
            "entry":      local["close"],
            "upper_band": local["upper_band"],
            "lower_band": local["lower_band"],
            "atr":        local["atr"],
        }
