"""Radar Cripto Bidireccional (LONG y SHORT) en Pionex Futures."""
import csv
import html
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
from flask import Flask

import pionex_client as px
from data_sources import get_btc_change_1h, get_live_price, get_symbol_rules
from engine import evaluate_strategy

load_dotenv()

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
ALLOWED_USERS = {u.strip() for u in (os.getenv("TELEGRAM_USER_IDS") or TELEGRAM_CHAT_ID).split(",") if u.strip()}
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

LIVE_TRADING = (os.getenv("LIVE_TRADING") or "false").strip().lower() == "true"
MODE = "REAL (FUTUROS)" if LIVE_TRADING else "SIMULACIÓN"

STATE_FILE = os.getenv("STATE_FILE", "trade_state.json")
WATCHLIST = ["SOLUSDT", "ETHUSDT", "NEARUSDT", "SUIUSDT", "AVAXUSDT"]
ORDER_USDT = float(os.getenv("ORDER_USDT", "5"))
MAX_OPEN_POSITIONS = 2
TAKE_PROFIT_PCT = 2.5
STOP_LOSS_PCT = 2.0
ENTRY_THRESHOLD = int(os.getenv("BUY_THRESHOLD", "40"))
EVAL_EVERY_SEC = 60
ENTRY_COOLDOWN_MIN = 45
EXIT_REMINDER_MIN = 10
SIGNAL_TTL_SEC = 600

pending_signals = {}
fail_counts = {}

app = Flask(__name__)

@app.route("/")
@app.route("/health")
def health():
    return f"Radar Bidireccional Activo ({MODE})", 200

def load_state():
    base = {"positions": {}, "last_update_id": 0, "last_entry_alert": {}}
    if not os.path.exists(STATE_FILE):
        return base
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return base
    for k, v in base.items():
        data.setdefault(k, v)
    return data

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)

def esc(x):
    return html.escape(str(x), quote=False)

def tg_send(text, buttons=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        r = requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
        return r.ok
    except Exception:
        return False

def pnl_pct(entry, current, direction="LONG"):
    if not entry or entry == 0:
        return 0.0
    return ((current / entry) - 1) * 100 if direction == "LONG" else (1 - (current / entry)) * 100

def handle_open(signal_id):
    sig = pending_signals.get(signal_id)
    if not sig or time.time() - sig["ts"] > SIGNAL_TTL_SEC:
        tg_send("⌛ Esa señal ya venció.")
        return
    if sig["used"]:
        tg_send("⚠️ Esa señal ya fue procesada.")
        return

    sym, coin, direction = sig["symbol_trade"], sig["coin"], sig["direction"]
    state = load_state()
    if sym in state["positions"]:
        tg_send(f"⚠️ Ya hay una posición en {esc(sym)}.")
        return
    if len(state["positions"]) >= MAX_OPEN_POSITIONS:
        tg_send(f"⚠️ Límite de {MAX_OPEN_POSITIONS} posiciones alcanzado.")
        return

    sig["used"] = True
    price_now = get_live_price(sym) or sig["price"]
    size_coin = ORDER_USDT / price_now

    if not LIVE_TRADING:
        state["positions"][sym] = {
            "symbol_trade": sym, "coin": coin, "direction": direction,
            "entry_price": price_now, "size_coin": size_coin, "amount_usdt": ORDER_USDT,
            "opened_ts": time.time(), "simulated": True, "last_alert_ts": 0
        }
        save_state(state)
        tg_send(f"🧪 <b>SIMULACIÓN {direction}</b>: {ORDER_USDT} USDT en <code>{esc(sym)}</code> a ${price_now:.4f}.")
        return

    # Operación real en Futuros
    side = "BUY" if direction == "LONG" else "SELL"
    tg_send(f"⏳ Abriendo {direction} de {ORDER_USDT} USDT en {esc(sym)}…")
    res = px.place_futures_order(sym, side, ORDER_USDT, price_now, client_order_id=f"rdr-{signal_id}")
    if not res.get("result"):
        tg_send(f"❌ Error al abrir {direction}: {esc(res.get('message') or '')}")
        return

    state["positions"][sym] = {
        "symbol_trade": sym, "coin": coin, "direction": direction,
        "entry_price": price_now, "size_coin": size_coin, "amount_usdt": ORDER_USDT,
        "opened_ts": time.time(), "simulated": False, "last_alert_ts": 0
    }
    save_state(state)
    tg_send(f"✅ <b>Posición {direction} abierta</b> en {esc(sym)} a ${price_now:.4f}.")

def handle_close(sym):
    state = load_state()
    pos = state["positions"].get(sym)
    if not pos:
        tg_send(f"⚠️ No hay posición activa en {esc(sym)}.")
        return

    direction = pos.get("direction", "LONG")
    price_now = get_live_price(sym) or pos["entry_price"]
    res_pnl = pnl_pct(pos["entry_price"], price_now, direction)

    if pos.get("simulated") or not LIVE_TRADING:
        state["positions"].pop(sym, None)
        save_state(state)
        tg_send(f"🧪 <b>SIMULACIÓN CERRADA</b>: {esc(sym)} ({direction}) a ${price_now:.4f} | Res: <b>{res_pnl:+.2f}%</b>")
        return

    # Cierre real
    side_close = "SELL" if direction == "LONG" else "BUY"
    tg_send(f"⏳ Cerrando {direction} en {esc(sym)}…")
    res = px.close_futures_position(sym, side_close, pos["size_coin"], client_order_id=f"cls-{int(time.time())}")
    state["positions"].pop(sym, None)
    save_state(state)
    tg_send(f"✅ <b>{direction} cerrado</b> en {esc(sym)} | PnL aprox: <b>{res_pnl:+.2f}%</b>")

def status_report(snapshot):
    state = load_state()
    pos = state["positions"]
    ranking = "\n".join(
        f"• <b>{esc(s.replace('USDT', ''))}</b>: ${d['price']:.4f} | {d['direction']} (Ptos: {d['score']}) | RSI {d['rsi']}"
        for s, d in snapshot.items()
    ) or "Sin datos."
    
    pos_lines = []
    for s, p in pos.items():
        cur = get_live_price(s) or p["entry_price"]
        pnl = pnl_pct(p["entry_price"], cur, p.get("direction", "LONG"))
        pos_lines.append(f"• <b>{esc(p['coin'])}</b> [{p.get('direction', 'LONG')}]: Entrada ${p['entry_price']:.4f} | Actual ${cur:.4f} (<code>{pnl:+.2f}%</code>)")
    pos_txt = "\n".join(pos_lines) if pos_lines else "Sin posiciones abiertas."

    bal = px.get_futures_balance() if LIVE_TRADING else 0.0
    return (f"📊 <b>RADAR BIDIRECCIONAL ({MODE})</b>\n\n{ranking}\n\n"
            f"• USDT Futuros disponible: <code>${bal:.2f}</code>\n\n"
            f"🎯 <b>Posiciones ({len(pos)}/{MAX_OPEN_POSITIONS}):</b>\n{pos_txt}")

def process_updates(snapshot):
    state = load_state()
    try:
        r = requests.get(f"{API_URL}/getUpdates", timeout=8, params={
            "offset": state["last_update_id"] + 1, "timeout": 1,
            "allowed_updates": json.dumps(["message", "callback_query"])
        }).json()
    except Exception:
        return

    for upd in r.get("result", []):
        state["last_update_id"] = upd["update_id"]
        save_state(state)

        msg = upd.get("message")
        if msg and "text" in msg:
            sender = str((msg.get("from") or {}).get("id", ""))
            chat = str((msg.get("chat") or {}).get("id", ""))
            if chat == TELEGRAM_CHAT_ID and sender in ALLOWED_USERS:
                txt = msg["text"].strip().lower()
                if txt in ("/status", "status"):
                    tg_send(status_report(snapshot))

        cb = upd.get("callback_query")
        if not cb:
            continue
        data = cb.get("data") or ""
        requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": cb["id"]}, timeout=5)
        if data.startswith("OP|"):
            handle_open(data[3:])
        elif data.startswith("CL|"):
            handle_close(data[3:])

def evaluate_all(snapshot):
    state = load_state()
    positions = state["positions"]

    for pair in WATCHLIST:
        data = evaluate_strategy(pair)
        if data.get("ok"):
            snapshot[pair] = data

    # Verificación TP / SL
    for sym, p in list(positions.items()):
        cur = get_live_price(sym)
        if not cur:
            continue
        direction = p.get("direction", "LONG")
        res = pnl_pct(p["entry_price"], cur, direction)
        if (res >= TAKE_PROFIT_PCT or res <= -STOP_LOSS_PCT) and (time.time() - p.get("last_alert_ts", 0) >= EXIT_REMINDER_MIN * 60):
            p["last_alert_ts"] = time.time()
            save_state(state)
            alerta = "🎯 TAKE PROFIT" if res >= TAKE_PROFIT_PCT else "⚠️ STOP LOSS"
            tg_send(f"{alerta} en {esc(p['coin'])} [{direction}]\nResultado: <b>{res:+.2f}%</b>",
                    [[{"text": f"Cerrar {direction} a mercado", "callback_data": f"CL|{sym}"}]])

    if len(positions) >= MAX_OPEN_POSITIONS:
        return

    # Buscar mejor candidato
    candidates = [d for s, d in snapshot.items() if d["score"] >= ENTRY_THRESHOLD and f"{s.replace('USDT', '')}_USDT" not in positions]
    if not candidates:
        return

    best = max(candidates, key=lambda d: d["score"])
    coin = best["symbol"].replace("USDT", "")
    sym = f"{coin}_USDT"
    if time.time() - state["last_entry_alert"].get(sym, 0) < ENTRY_COOLDOWN_MIN * 60:
        return

    sig_id = uuid.uuid4().hex[:8]
    pending_signals[sig_id] = {
        "symbol_trade": sym, "coin": coin, "price": best["price"],
        "direction": best["direction"], "ts": time.time(), "used": False
    }
    state["last_entry_alert"][sym] = time.time()
    save_state(state)

    icono = "🟢" if best["direction"] == "LONG" else "🔴"
    tg_send(
        f"{icono} <b>SEÑAL {best['direction']} DETECTADA</b>\n\n"
        f"• Par: <code>{esc(coin)}/USDT</code>\n• Precio: ${best['price']:.4f}\n"
        f"• Puntaje: {best['score']} | RSI: {best['rsi']}\n"
        f"• Motivos: {', '.join(best['signals'])}\n\n¿Ejecutar orden?",
        [[{"text": f"{icono} Abrir {best['direction']} ({ORDER_USDT:g} USDT)", "callback_data": f"OP|{sig_id}"}]]
    )

def run_radar():
    px.RULES.update(get_symbol_rules([f"{p.replace('USDT', '')}_USDT" for p in WATCHLIST]))
    tg_send(f"🚀 Radar Bidireccional iniciado en modo <b>{MODE}</b>. Comandos: /status")
    snapshot, last_eval = {}, 0.0
    while True:
        process_updates(snapshot)
        if time.time() - last_eval >= EVAL_EVERY_SEC:
            last_eval = time.time()
            try:
                evaluate_all(snapshot)
            except Exception as e:
                print(f"[radar] Error en ciclo: {e}", flush=True)
        time.sleep(1)

threading.Thread(target=run_radar, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
