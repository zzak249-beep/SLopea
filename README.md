# QF×JP Bot v6.3 PREDATOR·ENTRY 🤖

Bot de trading automático que replica la estrategia **QF Machine × JP Fusion** en Python puro.  
Conectado a **BingX Perpetual Futures** · Notificaciones **Telegram** · Deploy **Railway**.

---

## Novedades v6.3 vs v3.5

| Feature | v3.5 | v6.3 |
|---|---|---|
| Universo de símbolos | TOP 30 | **TODAS las monedas BingX** (TOP_N=0) |
| Cierre de posiciones | Solo SL/TP automático vía API | **Position Manager completo** |
| Breakeven | ✗ | ✅ Automático (1 ATR) |
| Monitor posiciones | ✗ | ✅ Loop cada 30s |
| Cierre manual | ✗ | ✅ `POST /close/{symbol}` |
| Cierre emergencia | ✗ | ✅ con cancelación de órdenes |
| Endpoint `/positions` | ✗ | ✅ Posiciones live desde BingX |
| Filtro volumen mínimo | ✗ | ✅ `MIN_VOLUME_USDT` |

---

## Arquitectura

```
src/
├── main.py             # FastAPI + healthcheck + arranque de loops
├── scanner.py          # Loop: obtiene símbolos → analiza → abre trades
├── indicators.py       # ATR, ADX, CVD, FVG, CHoCH/BoS, MFI, VDI, TL Ruptura, Score
├── bingx_client.py     # API BingX: klines, órdenes MARKET, SL/TP, cierre posiciones
├── position_manager.py # Monitor posiciones abiertas, breakeven, cierre emergencia
├── telegram_client.py  # Notificaciones: señal, apertura, cierre, status, errores
├── risk_manager.py     # Kelly Criterion, límites diarios, daily drawdown
└── config.py           # Variables via env vars
```

---

## Lógica de señal

1. **TL Ruptura** (gate principal): ruptura de trendline bajista → LONG, alcista → SHORT
2. **HTF EHM**: 15m×1 + 1h×2 + 4h×4 — mínimo 2 TFs alineados
3. **Score compuesto** (0–100): ADX + CVD + Momentum + MFI + VDI + Estructura + HTF + FVG
4. **Tiers**: STD ≥55 · FUEL ≥68 · SUP ≥80
5. **Circuit Breaker**: pausa 10min tras vela >3×ATR
6. **Kelly sizing**: posición ajustada al tier, score y balance real

---

## Gestión de posiciones (v6.3)

- **SL + TP1 (50%) + TP2 (50%)** colocados automáticamente como stop-market en BingX
- **Breakeven**: cuando el precio avanza `BREAKEVEN_ATR_MULT × ATR`, el SL se mueve a entry
- **Monitor loop**: cada `POSITION_CHECK_INTERVAL` segundos sincroniza con posiciones reales en BingX
- **Cierre automático detectado**: si la posición desaparece de BingX (SL/TP hit), notifica PnL por Telegram
- **Cierre manual**: `POST /close/{symbol}` desde cualquier cliente HTTP

---

## Deploy Railway

### 1. Estructura de archivos
```
qfjp-bot/
├── src/
│   ├── main.py
│   ├── scanner.py
│   ├── indicators.py
│   ├── bingx_client.py
│   ├── position_manager.py
│   ├── telegram_client.py
│   ├── risk_manager.py
│   └── config.py
├── .env.example
├── .gitignore
├── Procfile
├── railway.toml
└── requirements.txt
```

### 2. Subir a GitHub
```bash
git init
git add .
git commit -m "QFxJP Bot v6.3"
git remote add origin https://github.com/TU_USUARIO/qfjp-bot
git push -u origin main
```

### 3. Railway → New Project → Deploy from GitHub

### 4. Variables de entorno (Railway → Variables)

| Variable | Valor recomendado |
|---|---|
| `BINGX_API_KEY` | tu API key |
| `BINGX_SECRET_KEY` | tu secret key |
| `TELEGRAM_TOKEN` | token del bot |
| `TELEGRAM_CHAT_ID` | ID canal/grupo |
| `MODE` | `SIGNAL` primero, luego `LIVE` |
| `CAPITAL` | tu capital en USDT |
| `MIN_TIER` | `FUEL` |
| `TOP_N_SYMBOLS` | `0` (todas) o número |
| `MIN_VOLUME_USDT` | `5000000` |
| `SCAN_INTERVAL` | `180` |
| `LEVERAGE` | `10` |
| `POSITION_CHECK_INTERVAL` | `30` |
| `BREAKEVEN_ATR_MULT` | `1.0` |

Ver `.env.example` para la lista completa.

---

## Modos

- **`MODE=SIGNAL`**: escanea y notifica en Telegram, **sin** abrir trades reales
- **`MODE=LIVE`**: abre trades reales + monitor de cierre automático + breakeven

---

## Endpoints

| Endpoint | Descripción |
|---|---|
| `GET /health` | Healthcheck Railway |
| `GET /status` | Estado del bot, balance, trades abiertos, risk |
| `GET /positions` | Posiciones abiertas live desde BingX |
| `POST /close/{SYMBOL}` | Cierre manual forzado (ej: `/close/BTC-USDT`) |

---

## Panel Telegram — Señal

```
📡 SEÑAL — QF×JP v6.3
──────────────────────
Par:       BTC-USDT
Dir:       🟢 LONG
Tier:      🔥 FUEL
Score:     71/100  ███████░░░
──────────────────────
Entry:     43250.000000
SL:        42890.000000
TP1 (50%): 43790.000000
TP2 (50%): 44330.000000
ATR:       360.000000
──────────────────────
TL Ruptura:  LONG 🔥
Estructura:  BoS↑
ADX:         32.4
MFI:         38.2
VDI:         🟢 BULL (+1.82σ)
HTF Score:   86%
CVD:         +0.412
Momentum:    +0.338
```

---

## Configuración recomendada para inicio

```
MODE=SIGNAL           # Empieza en modo señal 1-2 días
MIN_TIER=FUEL
TOP_N_SYMBOLS=0       # Todas las monedas
MIN_VOLUME_USDT=10000000   # Solo monedas con >10M vol
MAX_OPEN_TRADES=3
RISK_PCT=0.5          # Conservador al inicio
KELLY_FRACTION=0.15
```
