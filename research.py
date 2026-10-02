"""
Servicio de INVESTIGACIÓN en Railway: ejecuta backtest + sweep (entradas y salidas) + meta-etiquetado con datos
reales y manda los resultados a Telegram (resumen + informe completo como archivo). No opera nunca.

Se activa con RUN_MODE=research en un servicio APARTE del bot (mismo repo). Al terminar se queda en reposo
para que Railway no lo relance en bucle; para repetir: Redeploy (o RESEARCH_FORCE=true).

Variables (todas opcionales):
  RESEARCH_SYMBOLS   BTCUSDT,ETHUSDT,...      RESEARCH_TF     15m
  RESEARCH_DAYS      365                      RESEARCH_STEPS  backtest,entradas,salidas,meta
  RESEARCH_STRICT    (exigencia del backtest; por defecto ENTRY_STRICTNESS)
  RESEARCH_FORCE     true = repetir aunque ya se hiciera con la misma configuración
"""
import contextlib
import hashlib
import io
import os
import re
import sys
import time
import traceback

import requests

import config as C

DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,LINKUSDT,AVAXUSDT,DOTUSDT"
KEY_LINES = re.compile(r"(TOTAL|primer 70|último 30|^t [+-]|trampas|·con entrada|AUC|todas las señales|filtradas|"
                       r"prueba de azar|^→|Mejor en entrenamiento|⚠|✓|Guardado|No se guarda|operaciones ·)")


def env(name, default):
    v = os.getenv(name)
    return default if v is None or v.strip().strip('"') == "" else v.strip().strip('"').strip("'")


class Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def tg_text(text):
    if not (C.TG_TOKEN and C.TG_CHAT):
        print(text)
        return
    for i in range(0, len(text), 3900):
        try:
            requests.post(f"https://api.telegram.org/bot{C.TG_TOKEN}/sendMessage",
                          json={"chat_id": C.TG_CHAT, "text": text[i:i + 3900]}, timeout=15)
        except requests.RequestException as e:
            print("Telegram:", e)


def tg_file(path, caption):
    if not (C.TG_TOKEN and C.TG_CHAT) or not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            requests.post(f"https://api.telegram.org/bot{C.TG_TOKEN}/sendDocument",
                          data={"chat_id": C.TG_CHAT, "caption": caption[:1000]},
                          files={"document": (os.path.basename(path), f)}, timeout=60)
    except requests.RequestException as e:
        print("Telegram archivo:", e)


def binance_ok():
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=10)
        return r.status_code == 200
    except requests.RequestException:
        return False


def run_step(title, fn, argv):
    buf = io.StringIO()
    sys.argv = argv
    t0 = time.time()
    with contextlib.redirect_stdout(Tee(buf, sys.__stdout__)):
        print(f"\n\n████ {title} ████\n$ {' '.join(argv[1:])}\n")
        try:
            fn()
        except SystemExit:
            pass
        except Exception as e:  # un paso que falla no tumba los demás
            print(f"❌ ERROR en {title}: {e}\n{traceback.format_exc()}")
        print(f"\n({time.time() - t0:.0f}s)")
    return buf.getvalue()


def idle():
    while True:
        time.sleep(3600)


def run():
    os.makedirs(C.DATA_DIR, exist_ok=True)
    syms = [s.strip().upper() for s in env("RESEARCH_SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]
    tf = env("RESEARCH_TF", C.TIMEFRAME)
    days = env("RESEARCH_DAYS", "365")
    steps = [s.strip().lower() for s in env("RESEARCH_STEPS", "backtest,entradas,salidas,meta").split(",")]
    strict = env("RESEARCH_STRICT", C.ENTRY_STRICTNESS)
    sig = hashlib.md5(f"{C.CODE_VERSION}|{syms}|{tf}|{days}|{steps}|{strict}".encode()).hexdigest()[:10]
    marker = os.path.join(C.DATA_DIR, f"research_done_{sig}")
    if os.path.exists(marker) and env("RESEARCH_FORCE", "false").lower() not in ("true", "1", "si", "sí"):
        print("Investigación ya hecha con esta configuración; en reposo. RESEARCH_FORCE=true para repetir.")
        idle()

    import backtest as B
    import meta
    import sweep
    B.CACHE = os.path.join(C.DATA_DIR, "cache")
    source = "binance" if binance_ok() else "bingx"
    if source == "bingx":
        syms = [s if "-" in s else s.replace("USDT", "-USDT") for s in syms]
    B.SOURCE = source
    C.META_MODEL = os.path.join(C.DATA_DIR, "meta_model.json")
    sym_arg = ",".join(syms)
    t0 = time.time()
    tg_text(f"🔬 Investigación Wyckoff · {C.CODE_VERSION}\n{len(syms)} símbolos · {tf} · {days} días · pasos: "
            f"{', '.join(steps)}\nDatos: {source.upper()}"
            + ("" if source == "binance" else "\n⚠ Binance bloquea esta región (EE. UU.): datos de BingX y SIN flujo "
                                              "agresor. Para tenerlo, cambia la región del servicio a Europa.")
            + "\nTarda ~10-30 min. Te aviso al terminar.")

    report = []
    if "backtest" in steps:
        report.append(run_step("BACKTEST (configuración actual)", B.main,
                               ["backtest", "--symbols", sym_arg, "--tf", tf, "--days", days, "--strict", strict,
                                "--source", source]))
    if "entradas" in steps:
        B.SOURCE = source
        report.append(run_step("SWEEP ENTRADAS", sweep.main,
                               ["sweep", "--modo", "entradas", "--symbols", sym_arg, "--tf", tf, "--days", days]))
    if "salidas" in steps:
        B.SOURCE = source
        report.append(run_step("SWEEP SALIDAS", sweep.main,
                               ["sweep", "--modo", "salidas", "--symbols", sym_arg, "--tf", tf, "--days", days]))
    if "meta" in steps:
        B.SOURCE = source
        if os.path.exists(C.META_MODEL):
            os.remove(C.META_MODEL)  # solo se envía un modelo si ESTA ejecución lo ha aprobado
        report.append(run_step("META-ETIQUETADO", meta.main,
                               ["meta", "--symbols", sym_arg, "--tf", tf, "--days", days, "--incluir-trampas",
                                "--guardar-si-pasa"]))

    full = "\n".join(report)
    stamp = time.strftime("%Y%m%d_%H%M", time.gmtime())
    path = os.path.join(C.DATA_DIR, f"investigacion_{stamp}.txt")
    with open(path, "w") as f:
        f.write(full)
    summary = []
    for block in report:
        head = block.strip().splitlines()[0] if block.strip() else ""
        summary.append(head)
        summary += [ln.strip() for ln in block.splitlines() if KEY_LINES.search(ln.strip())][:14]
        summary.append("")
    tg_text("📋 RESUMEN (lo que manda: columna de PRUEBA y prueba de azar)\n\n" + "\n".join(summary)
            + f"\nTotal {(time.time() - t0) / 60:.0f} min · informe completo en el archivo adjunto.")
    tg_file(path, "Informe completo de la investigación")
    if os.path.exists(C.META_MODEL):
        tg_file(C.META_MODEL, "meta_model.json: superó la prueba de azar. Súbelo al repo del BOT y usa "
                              "META_FILTER=aviso unas semanas antes de 'bloquea'.")
    open(marker, "w").write(stamp)
    print("Investigación terminada; en reposo.")
    idle()


if __name__ == "__main__":
    run()
