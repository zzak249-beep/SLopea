# Wyckoff Bot v2.1 (BingX · Railway · Telegram)

Ejecuta en BingX las entradas del indicador **Wyckoff ES [theUltimator5]**. El motor (`wyckoff_engine.py`) es una traducción 1:1 de `f_engine()` del Pine: mismas constantes, fases A→E, resets y lógica de entrada (una por campaña según exigencia).

## Archivos

| Archivo | Qué hace |
|---|---|
| `wyckoff_engine.py` | Motor Wyckoff vela a vela (pivotes con la regla de empates de TradingView) |
| `strategy.py` | Plan SL/TP1/TP2, filtros, contexto de TF superior, simulación de la gestión, selección de operaciones |
| `main.py` | Bucle multi-TF: velas cerradas → motor → señal → SIGNAL (virtual) o LIVE (órdenes) |
| `bingx.py` | Cliente BingX swap v2 (firma sobre el string exacto enviado, Hedge/One-Way, SL adjunto) |
| `notify.py` | Telegram (avisos + comandos) y diario `journal.csv` |
| `universe.py` | Clasifica cada perpetuo: cripto, forex, materia prima, acción, índice |
| `config.py` | Variables de entorno (quita comillas) |
| `backtest.py` | Backtest con el mismo motor/filtros/gestión, coste incluido, partición 70/30 y desgloses |
| `sweep.py` | Barrido de variantes: elige en el 70% inicial, enseña el 30% final, corrige por nº de pruebas |
| `test_engine.py` | Prueba sin red con ciclos sintéticos |
| `railway.env.txt` | Plantilla para el Raw Editor de Railway |

## Gestión de cada operación
- Entrada a mercado al cierre de la vela que valida el motor (no persigue si ya avanzó `CHASE_MAX_R`).
- **SL dentro de la orden de entrada** (`ATTACH_SL`); si BingX no lo acepta, se pone aparte al instante.
- **Guardián del stop**: cada `MANAGE_EVERY_S` comprueba que la posición tiene stop y lo repone si falta.
- TP1 = borde opuesto del rango (`TP1_FRACTION`), TP2 = proyección de la altura. Tras TP1 el SL se sustituye por uno a breakeven con la cantidad restante.
- Tamaño por riesgo (`RISK_PCT`), margen aislado, topes por bot, por cuenta y por pérdida diaria.
- Al arrancar cancela órdenes huérfanas `wyk*` de despliegues anteriores.

## Universo: todos los perpetuos de BingX
`UNIVERSE=all` analiza **todos** los perpetuos USDT de BingX, cripto y TradFi (BingX los lista en el mismo mercado, p. ej. `NCFXEUR2USD-USDT`).
- `CATEGORIES` elige clases: `crypto,forex,commodity,stock,index,tradfi`.
- `MIN_QUOTE_VOL` (cripto) y `MIN_QUOTE_VOL_TRADFI` quitan lo que no tiene liquidez para entrar y salir.
- Velas con mercado cerrado (precio plano y volumen 0) no alimentan al motor.
- TradFi: no abre los viernes desde `TRADFI_NO_ENTRY_FRI_UTC` y avisa de las abiertas antes del fin de semana (el SL no se ejecuta con el mercado cerrado).
- `TRADFI_EFFORT=rango` usa el rango de la vela como esfuerzo en vez del volumen de BingX.
- `BINGX_MAX_RPS` limita peticiones/segundo; con cientos de símbolos la vuelta tarda decenas de segundos.
- Backtest de TradFi: `python backtest.py --symbols NCFXEUR2USD-USDT,NCCOGOLD2USD-USDT --tf 1h --days 180` (datos de BingX).

## Contexto de TF superior (`CONTEXT_TF`)
Un segundo motor Wyckoff corre en 4h. Cada señal sale marcada **a favor / en contra / neutral** según la estructura mayor, y va al diario y al backtest. `CONTEXT_FILTER=aviso` por defecto: se mide antes de usarlo para filtrar.

## Comandos de Telegram
`/estado` `/posiciones` `/stats` `/pausa` `/reanudar` (solo desde `TELEGRAM_CHAT_ID`).

## Despliegue
1. Sube **todos** los archivos juntos al repo.
2. Railway → Variables → Raw Editor → pega `railway.env.txt`.
3. Volumen montado en `/data`.
4. Arranca en `MODE=SIGNAL`. Para operar: `MODE=LIVE` **y** `CONFIRM_LIVE=SI`.

## Antes de LIVE
```
pip install requests
python test_engine.py
python backtest.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT --tf 15m --days 180
python sweep.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT --tf 15m --days 240
```
Manda la columna de **prueba** del sweep, no la de entrenamiento.
