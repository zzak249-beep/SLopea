# Crowding bot — solo señales

Bot de señales del posicionamiento amontonado en perpetuos de BingX.
**NO OPERA. NO PIDE CLAVES DE API.** Solo endpoints públicos.

## Despliegue en Railway — los cuatro puntos donde siempre falla

1. **Builder: NIXPACKS.** No Dockerfile. Con Dockerfile no se instala
   `requests` y el build muere sin dejar nada en los Deploy Logs.
2. **Start Command: `python crowding_bot.py`.** SIN el `worker:` delante.
   Eso es sintaxis de Procfile; Railway lo ejecuta literal y busca un
   programa llamado `worker:`.
3. **Pre-deploy Command: VACÍO.** `crowding_bot.py` es un bucle infinito y
   nunca termina, así que como pre-deploy deja el despliegue en cola para
   siempre.
4. **Volume montado en `/data`.** Sin él, cada redeploy borra 30-55 horas
   de calentamiento. Créalo ANTES de arrancar, no después.

## Variables (raw editor)

```
TIMEFRAME=15m
SCAN_SEC=120
MIN_VOL_24H=2000000
MAX_SYMBOLS=300
MAX_WORKERS=20
KLINES_LIMIT=260
HIST_HORAS=168
MIN_HORAS=30
MIN_MUESTRAS=200
MUESTRA_MIN_SEG=300
MAX_PUNTOS=2200
OI_LOOK_H=6
Z_BASIS=2.0
Z_OI=1.0
EXT_PCT=80
ATR_LEN=14
SL_ATR=1.5
TP_R=2.0
MAX_BARS=16
MIN_ATR_PCT=0
MAX_ATR_PCT=8
COST_PCT=0.25
MAX_COST_R=0.20
SOLO_VELA_CERRADA=true
NO_NUEVO_EXTREMO=6
ENFRIA_BARRAS=8
PANEL_ENABLED=true
PANEL_CSV=/data/crowding_panel.csv
PANEL_CADA_SEG=900
STATE=/data/crowding_state.json
CSV=/data/crowding_ops.csv
CSV_CON_INFORME=true
CSV_AL_ARRANCAR=false
TG_TOKEN=
TG_CHAT=
TG_SIGNALS=false
TG_CLOSES=false
REPORT_HOUR=7
CONFIRM_ENABLED=true
```

## Qué hace cada archivo

| archivo | para qué |
|---|---|
| `crowding_bot.py` | el bot. Detecta amontonamiento, espera la vela en contra, abre operación virtual y mide el resultado en R |
| `confirm.py` | régimen por ratio de varianzas. Solo APUNTA, nunca decide |
| `panel.py` | diario transversal: guarda los 300 símbolos cada 15 min, no solo los que disparan |
| `analizar_panel.py` | se ejecuta a mano. Contesta si el basis ordena los retornos futuros, sin necesitar ni una operación cerrada |

## Calentamiento

30 h de historia Y 200 muestras por símbolo. Con `MUESTRA_MIN_SEG=300` eso
son unas **30-35 horas de reloj**. Hasta entonces, 0 señales es lo correcto.

## Cómo leer los resultados

| ventaja real | operaciones necesarias |
|---|---|
| 0.50 R/op | 31 |
| 0.30 R/op | 87 |
| 0.20 R/op | 196 |
| 0.10 R/op | 784 |

Y lo que de verdad limita no son las operaciones sino los **días**
independientes: 40 cortos abiertos durante el mismo desplome son una sola
observación, no 40. El informe avisa cuando un día domina el total.
