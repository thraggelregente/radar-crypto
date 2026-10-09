"""Chequeo de conexión SIN operar: no envía ninguna orden.

Uso:  py selftest.py
Verifica: velas de Pionex, precios, reglas de los pares, firma/permisos de la API (lectura de saldos),
Telegram y el puntaje de cada par. Si algo sale mal, copiá el resultado completo y pasámelo.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

import pionex_client as px                                   # noqa: E402
from data_sources import get_btc_change_1h, get_klines, get_live_price, get_symbol_rules  # noqa: E402
from engine import evaluate_strategy                         # noqa: E402

WATCHLIST = ["SOLUSDT", "ETHUSDT", "NEARUSDT", "SUIUSDT", "AVAXUSDT"]


def titulo(t):
    print(f"\n=== {t} ===")


titulo("1. Variables de entorno (solo si existen, no se muestran)")
for name in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PIONEX_API_KEY", "PIONEX_API_SECRET"]:
    print(f"  {name}: {'OK' if os.getenv(name) else 'FALTA'}")
print(f"  LIVE_TRADING: {os.getenv('LIVE_TRADING', 'false')}  (false = simulación)")

titulo("2. Velas de Pionex (SOL 15m)")
df = get_klines("SOLUSDT", "15m", 100)
if df.empty:
    print("  PROBLEMA: no llegaron velas. Mirá el mensaje [datos] de arriba.")
else:
    ordenadas = ("time" not in df.columns) or df["time"].iloc[0] < df["time"].iloc[-1]
    print(f"  Velas: {len(df)} | orden ascendente: {ordenadas} | último cierre: {df['close'].iloc[-1]}")
    print(f"  Columnas: {list(df.columns)}")

titulo("3. Precios en vivo")
for p in WATCHLIST + ["BTCUSDT"]:
    print(f"  {p}: {get_live_price(p)}")
print(f"  BTC última hora: {get_btc_change_1h()}")

titulo("4. Reglas de los pares (precisión y mínimos)")
rules = get_symbol_rules([p.replace("USDT", "_USDT") for p in WATCHLIST])
print(f"  {rules if rules else 'No pude leerlas: se usan los decimales de respaldo'}")

titulo("5. API de Pionex (solo lectura de saldos, firma y permisos)")
bal = px.get_balances_dict()
if bal is None:
    print("  PROBLEMA: no pude leer saldos (clave, permisos o firma). Mirá el mensaje [pionex] de arriba.")
else:
    print(f"  USDT libre: {bal.get('USDT', 0.0)}")

titulo("6. Telegram")
tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
if tok and chat:
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": "✅ selftest del radar: conexión OK"}, timeout=10)
    print(f"  Respuesta: {r.status_code} {'OK' if r.ok else r.text[:200]}")
else:
    print("  Saltado (faltan variables).")

titulo("7. Puntaje actual de cada par")
for p in WATCHLIST:
    d = evaluate_strategy(p)
    if d.get("ok"):
        print(f"  {p}: puntaje {d['score']} | RSI {d['rsi']} | precio {d['price']} | {', '.join(d['signals'])}")
    else:
        print(f"  {p}: SIN DATOS ({d.get('reason')})")
print("\nListo. Este script NO envió ninguna orden.")
