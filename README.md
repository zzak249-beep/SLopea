# 🔱 APEX FUSION BOT v1.0

> **Sniper Predator VSA V8 × QF Machine × JP Fusion v3.1**  
> Bot de trading automático para BingX Perpetual Futures con señales por Telegram

---

## ⚡ Características

### Motores de análisis fusionados
- **Sniper Engine** — Liquidez, pivotes, VWAP, Magic Slope, STC, ADX, RVOL, POC
- **VSA Engine** — Regresión lineal volumen/spread + correlación Pearson, patrones institucionales
- **QF Machine Engine** — Factores cuantitativos (momentum, mean-reversion, OBV), Decay adaptativo, FVG, Order Blocks, CVD Delta, Squeeze Momentum, Dark Pool proxy

### Ventaja especial: **APEX PRIME** 🔱
Señal ultra-rara cuando los **3 motores confluyen simultáneamente** en la misma dirección + tendencia macro alineada. Probabilidad muy alta.

### Niveles de señal
| Nivel | Score | Descripción |
|-------|-------|-------------|
| 🔱 PRIME | Todos confluyen | Triple confluencia — máxima prioridad |
| ⭐ SUPREMA | ≥85 | Score máximo + Dark Pool o CVD divergencia |
| 🔥 FUEL | ≥72 | Score alto + catalizador (TL break / Squeeze / FVG+OB) |
| 📶 STD | ≥62 | Entrada estándar |

### Mejoras implementadas sobre los scripts originales
- ✅ **Decay adaptativo** (resuelve bloqueo crónico en 3min)
- ✅ **CVD ventana rodante** (elimina deriva acumulativa)
- ✅ **Multi-timeframe 3 niveles** (3m + 15m + 1h)
- ✅ **Sistema de scoring 0-100** fusión de ambos motores
- ✅ **Anti-reentrada** por símbolo y dirección
- ✅ **Trailing stop** automático a 1.5R → mueve SL a BE
- ✅ **Filter de sesión y volumen** (mínimo $500k 24h)
- ✅ **SL estructural** basado en pivot real

---

## 🚀 Setup rápido

### 1. Clonar y dependencias
```bash
git clone https://github.com/tu-usuario/apex-fusion-bot
cd apex-fusion-bot
npm install
```

### 2. Configurar variables de entorno
```bash
cp .env.example .env
nano .env
```

Rellenar:
- `BINGX_API_KEY` y `BINGX_SECRET_KEY` → en BingX → API Management
- `TELEGRAM_BOT_TOKEN` → crear bot con [@BotFather](https://t.me/BotFather)
- `TELEGRAM_CHAT_ID` → obtener con [@userinfobot](https://t.me/userinfobot)

### 3. Configurar BingX API

En BingX → Gestión de API:
1. Crear nueva API Key
2. Activar permisos: **Trading de Futuros** (no retirada)
3. Whitelist IP del servidor Railway

### 4. Probar en Paper Mode
```bash
MODE=paper node src/index.js
```

### 5. Activar trading real
```env
MODE=live
RISK_PER_TRADE=1
LEVERAGE=10
MAX_OPEN_TRADES=3
```

---

## ☁️ Deploy en Railway

### Método rápido
1. Fork este repositorio en GitHub
2. En [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Seleccionar el repo
4. En **Variables** añadir todas las variables del `.env.example`
5. Deploy automático ✅

### Variables obligatorias en Railway
```
BINGX_API_KEY=...
BINGX_SECRET_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
MODE=paper
```

---

## 📱 Comandos Telegram

| Comando | Función |
|---------|---------|
| `/start` | Menú de ayuda |
| `/status` | Estado del bot |
| `/positions` | Posiciones abiertas |
| `/balance` | Balance de cuenta |
| `/stats` | Estadísticas de rendimiento |
| `/pause` | Pausar nuevas entradas |
| `/resume` | Reanudar |
| `/closeall` | Cerrar todas las posiciones |
| `/risk 2` | Cambiar riesgo a 2% |

---

## ⚙️ Configuración recomendada

### Conservador (bajo riesgo)
```env
RISK_PER_TRADE=1
LEVERAGE=5
MAX_OPEN_TRADES=3
MIN_SCORE_ENTRY=68
MIN_SCORE_FUEL=78
LONG_ONLY=true
```

### Balanceado (recomendado)
```env
RISK_PER_TRADE=2
LEVERAGE=10
MAX_OPEN_TRADES=5
MIN_SCORE_ENTRY=62
MIN_SCORE_FUEL=72
LONG_ONLY=false
```

### Agresivo (alto riesgo)
```env
RISK_PER_TRADE=3
LEVERAGE=20
MAX_OPEN_TRADES=8
MIN_SCORE_ENTRY=55
MIN_SCORE_FUEL=68
LONG_ONLY=false
```

---

## 📊 Arquitectura del Score APEX

```
APEX SCORE LONG (0-100)
├── Sniper Engine    → 0-40 pts
│   ├── EMA trend          +6
│   ├── Magic Slope        +8  ← diferenciador clave
│   ├── RVOL               +6
│   ├── Distancia POC      +5
│   ├── ADX < 35           +5
│   ├── STC acelerando     +5
│   └── HTF alcista        +5
├── QF Machine       → 0-40 pts
│   ├── norm_score         30%
│   ├── CVD Delta          25%
│   ├── Momentum           20%
│   ├── Decay adaptativo   15%
│   └── HTF + Asimetría    10%
└── VSA Engine       → 0-20 pts (bonus)
    └── Patrón institucional detectado
```

---

## ⚠️ Riesgos y advertencias

- **No es un sistema infalible.** Todo bot puede tener pérdidas.
- Empieza **siempre en paper mode** al menos 1-2 semanas.
- Nunca arriesgues más del 1-2% por trade en live.
- El apalancamiento amplifica pérdidas tanto como ganancias.
- Los mercados de crypto son extremadamente volátiles.
- Este software se proporciona sin garantías. Úsalo bajo tu propia responsabilidad.

---

## 📁 Estructura del proyecto

```
apex-fusion-bot/
├── src/
│   ├── engine/
│   │   └── apexFusion.js     # Motor principal fusionado
│   ├── exchange/
│   │   └── bingx.js          # Conector BingX API
│   ├── signals/
│   │   ├── scanner.js         # Scanner de pares
│   │   └── tradeController.js # Gestión de trades
│   ├── telegram/
│   │   └── bot.js             # Bot Telegram
│   ├── dashboard/
│   │   └── server.js          # Dashboard web
│   ├── utils/
│   │   └── logger.js
│   └── index.js               # Entry point
├── .env.example
├── railway.json
├── package.json
└── README.md
```

---

## 🔱 La ventaja APEX PRIME

La señal `APEX PRIME` se activa cuando:
1. **Sniper Engine** detecta barrido de liquidez + confluencia completa
2. **VSA Engine** confirma patrón institucional (SC, HB, SPR, etc.)  
3. **QF Machine** tiene score ≥ 35/60 con catalizador activo
4. **Tendencia 1H** alineada con la dirección

Esta cuádruple confluencia es estadísticamente extremadamente rara y suele marcar puntos de inflexión con alta probabilidad.

---

*APEX FUSION BOT v1.0 — Uso bajo propia responsabilidad*
