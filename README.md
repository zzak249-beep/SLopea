# EMA SCALPING BOT — EMA7 / EMA17 + Slope 30°

Bot de scalping automático para BingX Perpetual Futures.

## Estrategia

- **Indicadores:** EMA 7 y EMA 17
- **LONG:** EMA7 cruza arriba EMA17 + slope ≥ +30°
- **SHORT:** EMA7 cruza abajo EMA17 + slope ≤ -30°
- **Sin slope suficiente → no opera**
- SL/TP basado en ATR
- Sizing por riesgo % del balance

## Archivos

```
main.py          → Bucle principal
config.py        → Configuración por env vars
bingx_client.py  → BingX REST API
strategy.py      → EMA + slope logic
scanner.py       → Scanner paralelo de todos los pares
trader.py        → Ejecución de órdenes y gestión de posiciones
notifier.py      → Alertas Telegram
requirements.txt
railway.toml
Procfile
.env.example
```

## Deploy Railway

1. Subir todos los archivos a un repo GitHub
2. Conectar repo en Railway → New Project → Deploy from GitHub
3. En Railway → Variables → añadir todas las vars de `.env.example`
4. Railway detecta `railway.toml` y arranca `python main.py`

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `BINGX_API_KEY` | — | BingX API key |
| `BINGX_SECRET_KEY` | — | BingX secret |
| `TELEGRAM_TOKEN` | — | Bot token |
| `TELEGRAM_CHAT_ID` | — | Chat ID |
| `TIMEFRAME` | `5m` | Marco temporal |
| `EMA_FAST` | `7` | EMA rápida |
| `EMA_SLOW` | `17` | EMA lenta |
| `MIN_SLOPE_DEG` | `30` | Slope mínimo en grados |
| `SLOPE_LOOKBACK` | `3` | Barras para calcular slope |
| `LEVERAGE` | `10` | Apalancamiento |
| `RISK_PCT` | `1.0` | % balance por trade |
| `ATR_SL_MULT` | `1.5` | Multiplicador ATR para SL |
| `ATR_TP_MULT` | `2.5` | Multiplicador ATR para TP |
| `MAX_OPEN_TRADES` | `5` | Máximo trades simultáneos |
| `SCAN_INTERVAL` | `60` | Segundos entre scans |
| `MIN_VOLUME_USDT` | `5000000` | Volumen 24h mínimo |
| `MARGIN_TYPE` | `ISOLATED` | ISOLATED o CROSSED |

## Lógica del slope

```
slope_pct_per_bar = (EMA7[-1] - EMA7[-4]) / EMA7[-4] * 100 / 3
angle = arctan(slope_pct_per_bar) en grados

LONG  → angle ≥ +30°  (tan30° ≈ 0.577% por barra)
SHORT → angle ≤ -30°
```

## Notas

- El universo se refresca cada 6 horas
- Filtro de volumen: solo pares con volumen 24h > MIN_VOLUME_USDT
- Sincronización automática con posiciones reales en cada ciclo
- Señal inversa cierra posición actual antes de abrir la nueva
