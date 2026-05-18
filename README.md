# ⚡ SAMA APEX Bot v1.0
> Bot de trading algorítmico para BingX Perpetual Futures  
> Basado en el indicador MZ SAMA V4: Sovereign Apex (Pine Script v6)

---

## 🧠 ¿Qué hace este bot?

Port completo del indicador SAMA a un bot de trading autónomo:

| Componente | Descripción |
|---|---|
| **AMA** | Adaptive Moving Average (port exacto del Pine Script) |
| **Slope Engine** | Cálculo de ángulo en grados para clasificar tendencia |
| **Multi-TF** | Alineación de 3 timeframes simultáneos |
| **RVOL** | Filtro de volumen relativo (evita señales en baja liquidez) |
| **ATR Bands** | TP/SL dinámicos basados en bandas ATR del indicador |

## 🔥 Edge Especial (ventaja sobre otros bots)

1. **Confluence Score (0-100)**: No opera si no hay calidad suficiente en la señal. Ponderación de: alineación TF + fuerza de slope + RVOL + sesión + funding rate.

2. **Funding Rate Filter**: Detecta funding extremo. Si el mercado está muy cargado en una dirección, penaliza esa dirección o la favorece si coincide con el fade.

3. **Session Filter**: Solo opera en sesiones London (7-16 UTC) y NY (13-21 UTC), donde la liquidez es real. Elimina la mayoría de señales falsas de Asia.

4. **Adaptive Position Sizing**: El tamaño varía según el confluence score:
   - Score 60-74 → 1.0x risk
   - Score 75-89 → 1.25x risk
   - Score 90-100 → 1.5x risk

5. **Trailing Stop ATR**: Una vez en profit de 1 ATR, mueve el SL automáticamente protegiendo ganancias.

6. **Circuit Breaker**: Si el bot pierde más del X% en un día, para completamente hasta el día siguiente.

7. **Multi-Symbol Scanner**: Escanea hasta 10 pares simultáneamente y prioriza el que tenga mayor confluence score.

---

## 📁 Estructura del Proyecto

```
sama-apex-bot/
├── bot.py               # Loop principal y orquestador
├── config.py            # Todos los parámetros (ENV vars)
├── indicators.py        # AMA, Slope, ATR, RVOL, Confluence Score
├── signal_engine.py     # Generación de señales multi-TF
├── bingx_client.py      # Cliente REST para BingX Perpetual Futures
├── risk_manager.py      # Sizing, trailing stop, circuit breaker
├── telegram_notifier.py # Alertas y notificaciones
├── requirements.txt
├── Procfile             # Para Railway
├── railway.toml         # Configuración Railway
├── .env.example         # Variables de entorno (template)
└── .gitignore
```

---

## 🚀 Setup: Paso a Paso

### 1. Requisitos previos
- Cuenta BingX con API Key habilitada para Futures
- Bot de Telegram (crear con @BotFather)
- Cuenta Railway (railway.app)

### 2. Clonar y configurar localmente
```bash
git clone https://github.com/TU_USUARIO/sama-apex-bot.git
cd sama-apex-bot

# Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

pip install -r requirements.txt

# Copiar y editar .env
cp .env.example .env
# Editar .env con tus keys
```

### 3. Obtener Telegram Chat ID
```
1. Crea un bot con @BotFather → obtienes TELEGRAM_TOKEN
2. Envía un mensaje al bot
3. Visita: https://api.telegram.org/bot<TOKEN>/getUpdates
4. Copia el "chat" → "id"
```

### 4. BingX API Key
```
1. BingX → Perfil → API Management
2. Crear API Key
3. Permisos: Read + Trade (NO Withdraw)
4. Whitelist de IPs: Railway IP o dejar vacío
```

### 5. Probar localmente
```bash
python bot.py
```

### 6. Deploy en Railway
```bash
# Instalar Railway CLI
npm install -g @railway/cli

railway login
railway init
railway up

# Variables de entorno en Railway Dashboard:
# Settings → Variables → añadir todas las del .env.example
```

### 7. Variables Railway obligatorias
```
BINGX_API_KEY
BINGX_SECRET_KEY
TELEGRAM_TOKEN
TELEGRAM_CHAT_ID
SYMBOLS
```

---

## ⚙️ Parámetros Clave

### Risk Management
| Variable | Default | Descripción |
|---|---|---|
| `LEVERAGE` | 5 | Apalancamiento (recomendado: 3-10x) |
| `RISK_PER_TRADE` | 0.01 | 1% del balance por trade |
| `MAX_OPEN_TRADES` | 3 | Máximo posiciones simultáneas |
| `DAILY_LOSS_LIMIT` | 0.05 | Circuit breaker: -5% del día |
| `MIN_CONFLUENCE` | 60 | Score mínimo para entrar |

### Timeframes (Estrategia por defecto)
| Variable | Default | Rol |
|---|---|---|
| `TF_LOCAL` | 5m | Entrada (detección de señal) |
| `TF_MACRO_1` | 15m | Filtro intermedio |
| `TF_MACRO_2` | 1h | Filtro macro (tendencia principal) |

### Alternativas de configuración probadas
```bash
# Scalping agresivo
TF_LOCAL=1m, TF_MACRO_1=5m, TF_MACRO_2=15m

# Swing conservador
TF_LOCAL=15m, TF_MACRO_1=1h, TF_MACRO_2=4h

# Balance (recomendado)
TF_LOCAL=5m, TF_MACRO_1=15m, TF_MACRO_2=1h
```

---

## 📊 Lógica de Señal

```
LONG cuando:
  ✅ SAMA local en tendencia BULL (slope > flat_threshold)
  ✅ SAMA 15m en tendencia BULL
  ✅ SAMA 1h en tendencia BULL
  ✅ RVOL > 1.2x (volumen por encima de media)
  ✅ Cambio de estado (no repetir señal ya activa)
  ✅ Confluence score ≥ MIN_CONFLUENCE

SHORT: mismo pero BEAR
```

---

## 📩 Mensajes Telegram

El bot envía alertas para:
- 🚀 Señal nueva con análisis completo (confluence, slopes, funding, sesión)
- 🟢/🔴 Trade abierto (entry, SL, TP, qty)
- ✅/❌ Trade cerrado (PnL real)
- 🔄 Trailing stop actualizado
- 🚨 Circuit breaker activado
- 📊 Resumen diario al abrir el día nuevo

---

## ⚠️ Disclaimer

Este bot opera con dinero real. Úsalo bajo tu propio riesgo.  
Empieza con **RISK_PER_TRADE=0.005** (0.5%) hasta validar el rendimiento.  
Ningún bot garantiza rentabilidad. Los mercados de crypto son altamente volátiles.
