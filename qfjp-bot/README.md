# QF×JP Bot v3.5 PREDATOR 🤖

Bot de trading automático que replica la estrategia del indicador **QF Machine × JP Fusion v3.5** en Python puro, conectado a BingX Perpetual Futures con notificaciones Telegram.

---

## Arquitectura

```
src/
├── main.py          # FastAPI app + healthcheck Railway + arranque scanner
├── scanner.py       # Loop: obtiene símbolos BingX → calcula score → abre trades
├── indicators.py    # Motor completo: ATR, ADX, CVD, FVG, TL Ruptura, CHoCH/BoS,
│                    #   MFI, VDI, EQH/EQL, HTF EHM, Score compuesto
├── bingx_client.py  # API BingX: órdenes MARKET + SL + TP1 + TP2
├── telegram_client.py # Notificaciones con panel completo
├── risk_manager.py  # Kelly Criterion, límites diarios, tamaño posición
└── config.py        # Todas las variables vía env vars
```

---

## Lógica de señal (replica QF×JP v3.5)

1. **TL Ruptura** (gate principal): detecta ruptura de trendline bajista (→ LONG) o alcista (→ SHORT)
2. **HTF EHM**: pesos exponenciales 15m×1 + 1h×2 + 4h×4 — mínimo 2 TFs alineados
3. **Score compuesto** (0–100): combina OBV/momentum, CVD, MFI, VDI, estructura CHoCH/BoS
4. **Tiers**: STD ≥55 · FUEL ≥68 · SUP ≥80
5. **Circuit Breaker**: pausa tras vela gigante (>3×ATR)
6. **Kelly sizing**: posición ajustada al tier y score

---

## Deploy Railway

### 1. Subir a GitHub
```bash
git init
git add .
git commit -m "QFxJP Bot v3.5"
git remote add origin https://github.com/TU_USUARIO/qfjp-bot
git push -u origin main
```

### 2. Railway → New Project → Deploy from GitHub

### 3. Variables de entorno (Railway → Variables)

| Variable | Valor |
|----------|-------|
| `BINGX_API_KEY` | tu API key |
| `BINGX_SECRET_KEY` | tu secret key |
| `TELEGRAM_TOKEN` | token del bot |
| `TELEGRAM_CHAT_ID` | ID del canal/grupo |
| `MODE` | `SIGNAL` o `LIVE` |
| `CAPITAL` | capital en USDT (ej: 1000) |
| `MIN_TIER` | `FUEL` (recomendado) |
| `TOP_N_SYMBOLS` | 30 |
| `SCAN_INTERVAL` | 180 |
| `LEVERAGE` | 10 |

Ver `.env.example` para la lista completa.

---

## Modos

- **`MODE=SIGNAL`**: escanea y notifica en Telegram sin abrir trades reales (para backtesting visual)
- **`MODE=LIVE`**: abre trades reales en BingX con SL + TP1 (50%) + TP2 (50%)

---

## Panel Telegram (ejemplo)

```
📡 SEÑAL — QF×JP v3.5 PREDATOR
━━━━━━━━━━━━━━━━━━━━
Par:       BTC-USDT
Dir:       🟢 LONG
Tier:      🔥 FUEL
Score:     71/100
━━━━━━━━━━━━━━━━━━━━
Entry:     43250.000000
SL:        42890.000000
TP1 (50%): 43790.000000
TP2 (50%): 44330.000000
ATR:       360.000000
━━━━━━━━━━━━━━━━━━━━
TL Ruptura: LONG 🔥
Estructura: BoS↑
ADX:       32.4
MFI:       38.2
VDI:       🟢 BULL (+1.82σ)
HTF Score: 0.86
CVD:       +0.412
Momentum:  +0.338
```

---

## Símbolos recomendados para blacklist

Basado en backtest previo:
```
BLACKLIST=SOL-USDT,XRP-USDT
```

---

## Endpoints

- `GET /health` — healthcheck Railway
- `GET /status` — estado del bot (trades abiertos, límites)
