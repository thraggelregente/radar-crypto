"""Radar cripto: señales técnicas + botones de Telegram + órdenes spot en Pionex.

MODO POR DEFECTO: SIMULACIÓN. No se envía ninguna orden real hasta que pongas LIVE_TRADING=true en el .env.
Solo opera LONG en spot (se eliminó el "short" con tokens 3S: tiene decaimiento y el cálculo de PnL era incorrecto).
"""
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

# ----------------------------- CONFIGURACIÓN -----------------------------
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
# Usuarios autorizados a apretar botones (por defecto, el mismo id del chat; en un grupo, poné tu user id)
ALLOWED_USERS = {u.strip() for u in (os.getenv("TELEGRAM_USER_IDS") or TELEGRAM_CHAT_ID).split(",") if u.strip()}
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

LIVE_TRADING = (os.getenv("LIVE_TRADING") or "false").strip().lower() == "true"
MODE = "REAL" if LIVE_TRADING else "SIMULACIÓN"

# En Render free el disco se borra en cada reinicio: si tenés un disco persistente, apuntá STATE_FILE ahí.
STATE_FILE = os.getenv("STATE_FILE", "trade_state.json")
SIGNALS_LOG = os.getenv("SIGNALS_LOG", "signals_log.csv")
TRADES_LOG = os.getenv("TRADES_LOG", "trades_log.csv")

WATCHLIST = ["SOLUSDT", "ETHUSDT", "NEARUSDT", "SUIUSDT", "AVAXUSDT"]
ORDER_USDT = float(os.getenv("ORDER_USDT", "5"))
MAX_OPEN_POSITIONS = 2
TAKE_PROFIT_PCT = 2.5
STOP_LOSS_PCT = 2.0
BUY_THRESHOLD = int(os.getenv("BUY_THRESHOLD", "50"))
BTC_BRAKE_PCT = 1.5            # no se avisan compras si BTC cayó más de esto en la última hora
EVAL_EVERY_SEC = 60
ENTRY_COOLDOWN_MIN = 60        # no repetir el aviso de compra del mismo par antes de este tiempo
EXIT_REMINDER_MIN = 10         # cada cuánto repetir el aviso de TP/SL mientras no cierres
SIGNAL_TTL_SEC = 600           # los botones de compra vencen a los 10 minutos

pending_signals = {}           # id de señal -> datos (en memoria; vencen solos)
fail_counts = {}
last_logged_bucket = {}

app = Flask(__name__)


@app.route("/")
@app.route("/health")
def health():
    return f"Radar activo ({MODE})", 200


# ----------------------------- ESTADO -----------------------------
def load_state():
    base = {"positions": {}, "last_update_id": 0, "last_entry_alert": {}}
    if not os.path.exists(STATE_FILE):
        return base
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[estado] No pude leer {STATE_FILE}: {e}", flush=True)
        return base
    for k, v in base.items():
        data.setdefault(k, v)
    return data


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)      # escritura atómica: no queda un archivo a medias si se corta


def append_csv(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_trade(action, pair, price, amount_coin, usdt, pnl, order_id, note=""):
    append_csv(TRADES_LOG,
               ["ts_utc", "modo", "accion", "par", "precio", "cantidad", "usdt", "pnl_pct", "order_id", "nota"],
               [utc_now(), MODE, action, pair, price, amount_coin, usdt, pnl, order_id, note])


# ----------------------------- TELEGRAM -----------------------------
def esc(x):
    return html.escape(str(x), quote=False)


def tg_send(text, buttons=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        r = requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
        if r.ok:
            return True
        print(f"[telegram] Mensaje rechazado: {r.text[:200]}", flush=True)
        payload.pop("parse_mode", None)           # reintento sin formato por si el problema fue el HTML
        payload["text"] = html.unescape(re.sub(r"<[^>]+>", "", text))
        return requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10).ok
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] Error enviando mensaje: {e}", flush=True)
        return False


def pnl_pct(entry, price):
    return (price / entry - 1) * 100 if entry else 0.0


# ----------------------------- COMPRA / VENTA -----------------------------
def wait_for_fill(symbol, coin, order_id):
    """Espera unos segundos a que Pionex informe cuánto y a qué precio se ejecutó la orden."""
    fill = None
    for _ in range(8):
        time.sleep(1)
        got = px.parse_fill(px.get_order(symbol, order_id), coin)
        if got:
            fill = got
            if str(got.get("status")).upper() == "CLOSED":
                break
    return fill


def handle_buy(signal_id, snapshot):
    sig = pending_signals.get(signal_id)
    if not sig or time.time() - sig["ts"] > SIGNAL_TTL_SEC:
        tg_send("⌛ Esa señal ya venció. Esperá una nueva.")
        return
    if sig["used"]:
        tg_send("⚠️ Esa señal ya se usó. No se envió otra orden.")
        return
    sym, coin = sig["symbol_trade"], sig["coin"]
    state = load_state()
    if sym in state["positions"]:
        tg_send(f"⚠️ Ya tenés una posición abierta en {esc(sym)}.")
        return
    if len(state["positions"]) >= MAX_OPEN_POSITIONS:
        tg_send(f"⚠️ Máximo de {MAX_OPEN_POSITIONS} posiciones abiertas.")
        return
    min_amount = (px.RULES.get(sym) or {}).get("min_amount")
    if min_amount and ORDER_USDT < min_amount:
        tg_send(f"❌ {ORDER_USDT} USDT está por debajo del mínimo de {esc(sym)} ({min_amount}).")
        return
    sig["used"] = True    # se marca antes de operar: un segundo clic nunca duplica la orden

    price_now = get_live_price(sym) or sig["price"]
    if not LIVE_TRADING:
        amount_coin = ORDER_USDT / price_now
        state["positions"][sym] = {
            "symbol_trade": sym, "coin": coin, "underlying": sig["underlying"], "buy_price": price_now,
            "amount_coin": amount_coin, "amount_usdt": ORDER_USDT, "opened_ts": time.time(),
            "order_id": "SIM", "simulated": True, "last_alert_ts": 0,
        }
        save_state(state)
        log_trade("BUY", sym, price_now, amount_coin, ORDER_USDT, "", "SIM")
        tg_send(f"🧪 <b>SIMULACIÓN</b>: compra de {ORDER_USDT} USDT en <code>{esc(sym)}</code> a "
                f"<code>${price_now:.4f}</code>. No se envió ninguna orden real.")
        return

    bal_before = px.get_balances_dict()
    if bal_before is None:
        tg_send("❌ No pude leer tu saldo de Pionex. No se envió la orden.")
        return
    if bal_before.get("USDT", 0.0) < ORDER_USDT:
        tg_send(f"❌ Saldo USDT insuficiente ({bal_before.get('USDT', 0.0):.2f}).")
        return

    tg_send(f"⏳ Enviando compra de {ORDER_USDT} USDT en {esc(sym)}…")
    res = px.place_market_order(sym, "BUY", amount_usdt=ORDER_USDT, client_order_id=f"rdr-b-{signal_id}")
    if not res.get("result"):
        tg_send(f"❌ <b>Error al comprar:</b> {esc(res.get('code') or '')} {esc(res.get('message') or '')}")
        return
    order_id = (res.get("data") or {}).get("orderId", "N/A")

    fill = wait_for_fill(sym, coin, order_id) if order_id != "N/A" else None
    amount_coin = fill["size"] if fill else 0.0
    buy_price = (fill or {}).get("avg_price")
    if amount_coin <= 0:                      # respaldo: diferencia de saldo antes/después
        bal_after = px.get_balances_dict() or {}
        amount_coin = max(bal_after.get(coin, 0.0) - bal_before.get(coin, 0.0), 0.0)
    if not buy_price:
        buy_price = price_now
    if amount_coin <= 0:
        tg_send(f"⚠️ La orden <code>{esc(order_id)}</code> se envió pero no pude confirmar cuánto se compró. "
                f"Revisá Pionex a mano; no registré la posición.")
        return

    state = load_state()
    state["positions"][sym] = {
        "symbol_trade": sym, "coin": coin, "underlying": sig["underlying"], "buy_price": buy_price,
        "amount_coin": amount_coin, "amount_usdt": ORDER_USDT, "opened_ts": time.time(),
        "order_id": str(order_id), "simulated": False, "last_alert_ts": 0,
    }
    save_state(state)
    log_trade("BUY", sym, buy_price, amount_coin, ORDER_USDT, "", order_id)
    tg_send(
        f"✅ <b>Compra ejecutada en Pionex</b>\n"
        f"• Par: <code>{esc(sym)}</code>\n• ID: <code>{esc(order_id)}</code>\n"
        f"• Precio real de entrada: <code>${buy_price:.4f}</code>\n• Cantidad: <code>{amount_coin}</code>\n"
        f"• Take Profit: +{TAKE_PROFIT_PCT}% | Stop Loss: -{STOP_LOSS_PCT}% (te aviso, vos confirmás)"
    )


def handle_sell(sym, snapshot):
    state = load_state()
    pos = state["positions"].get(sym)
    if not pos:
        tg_send(f"⚠️ No hay una posición registrada en {esc(sym)} (ya se cerró o nunca se abrió).")
        return
    coin = pos["coin"]
    price_now = (snapshot.get(pos["underlying"]) or {}).get("price") or get_live_price(sym)

    if pos.get("simulated") or not LIVE_TRADING:
        if not pos.get("simulated"):
            tg_send("⚠️ Esta posición es REAL pero el bot está en modo simulación (LIVE_TRADING=false). "
                    "No se envió ninguna orden: cerrala a mano en Pionex o activá LIVE_TRADING.")
            return
        result = pnl_pct(pos["buy_price"], price_now)
        del state["positions"][sym]
        save_state(state)
        log_trade("SELL", sym, price_now, pos["amount_coin"], pos["amount_usdt"], round(result, 3), "SIM")
        tg_send(f"🧪 <b>SIMULACIÓN</b>: posición cerrada en <code>{esc(sym)}</code> a <code>${price_now:.4f}</code> "
                f"({result:+.2f}%). No se envió ninguna orden real.")
        return

    balances = px.get_balances_dict()
    if balances is None:
        tg_send("❌ No pude leer tu saldo de Pionex. No se envió la venta.")
        return
    size = px.floor_to(min(pos["amount_coin"], balances.get(coin, 0.0)), px.size_decimals(sym))
    min_size = (px.RULES.get(sym) or {}).get("min_size")
    if size <= 0 or (min_size and size < min_size):
        tg_send(f"❌ Cantidad a vender ({size}) inválida o menor al mínimo. Cerrala a mano en Pionex.")
        return

    tg_send(f"⏳ Cerrando {esc(sym)} a mercado ({size} {esc(coin)})…")
    res = px.place_market_order(sym, "SELL", size_coin=size, client_order_id=f"rdr-s-{coin.lower()}-{int(time.time())}")
    if not res.get("result"):
        tg_send(f"❌ <b>Error al vender:</b> {esc(res.get('code') or '')} {esc(res.get('message') or '')}")
        return
    order_id = (res.get("data") or {}).get("orderId", "N/A")
    fill = wait_for_fill(sym, coin, order_id) if order_id != "N/A" else None
    sell_price = (fill or {}).get("avg_price") or price_now
    result = pnl_pct(pos["buy_price"], sell_price)

    state = load_state()
    state["positions"].pop(sym, None)
    save_state(state)
    log_trade("SELL", sym, sell_price, size, pos["amount_usdt"], round(result, 3), order_id)
    tg_send(f"✅ <b>Posición cerrada</b>\n• Par: <code>{esc(sym)}</code>\n• ID: <code>{esc(order_id)}</code>\n"
            f"• Salida: <code>${sell_price:.4f}</code> | Resultado aprox.: <b>{result:+.2f}%</b> (antes de comisiones)")


# ----------------------------- TELEGRAM: ENTRADA -----------------------------
def status_report(snapshot):
    state = load_state()
    pos = state["positions"]
    ranking = "\n".join(
        f"• <b>{esc(s.replace('USDT', ''))}</b>: ${d['price']:.4f} | puntaje <code>{d['score']}</code> | RSI <code>{d['rsi']}</code>"
        for s, d in snapshot.items()
    ) or "Sin datos todavía."
    pos_txt = "Sin posiciones abiertas."
    if pos:
        lines = []
        for sym, p in pos.items():
            cur = (snapshot.get(p["underlying"]) or {}).get("price") or get_live_price(sym) or p["buy_price"]
            tag = " (sim)" if p.get("simulated") else ""
            lines.append(f"• <b>{esc(p['coin'])}</b>{tag}: entrada ${p['buy_price']:.4f} | actual ${cur:.4f} | "
                         f"<code>{pnl_pct(p['buy_price'], cur):+.2f}%</code>")
        pos_txt = "\n".join(lines)
    btc = get_btc_change_1h()
    btc_txt = f"{btc:+.2f}%" if btc is not None else "sin datos"
    usdt_txt = "—"
    if LIVE_TRADING:
        bal = px.get_balances_dict()
        usdt_txt = f"{bal.get('USDT', 0.0):.2f}" if bal else "no disponible"
    return (f"📊 <b>RADAR ({MODE})</b>\n\n{ranking}\n\n• BTC última hora: <code>{btc_txt}</code>\n"
            f"• USDT libre: <code>{usdt_txt}</code>\n\n🎯 <b>Posiciones ({len(pos)}/{MAX_OPEN_POSITIONS}):</b>\n{pos_txt}")


def answer_callback(cb_id, text=None):
    try:
        payload = {"callback_query_id": cb_id}
        if text:
            payload.update({"text": text, "show_alert": True})
        requests.post(f"{API_URL}/answerCallbackQuery", json=payload, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def process_updates(snapshot):
    state = load_state()
    try:
        r = requests.get(f"{API_URL}/getUpdates", timeout=8, params={
            "offset": state["last_update_id"] + 1, "timeout": 1,
            "allowed_updates": json.dumps(["message", "callback_query"])})
        res = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] Error consultando updates: {e}", flush=True)
        return
    if not res.get("ok"):
        print(f"[telegram] getUpdates falló: {res.get('description')} "
              "(si dice 'Conflict', otro programa usa el mismo token de bot)", flush=True)
        time.sleep(5)
        return

    for upd in res.get("result", []):
        state["last_update_id"] = upd["update_id"]
        save_state(state)           # se guarda ANTES de procesar: si algo falla, el clic no se repite (nunca duplica órdenes)

        msg = upd.get("message")
        if msg and "text" in msg:
            sender = str((msg.get("from") or {}).get("id", ""))
            chat = str((msg.get("chat") or {}).get("id", ""))
            if chat == TELEGRAM_CHAT_ID and sender in ALLOWED_USERS:
                text = msg["text"].strip().lower()
                if text in ("/status", "status"):
                    tg_send(status_report(snapshot))
                elif text in ("/help", "/start"):
                    tg_send("Comandos: /status (ranking, posiciones, saldo). Las compras y ventas se hacen con los botones de cada aviso.")

        cb = upd.get("callback_query")
        if not cb:
            continue
        user_id = str((cb.get("from") or {}).get("id", ""))
        chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
        if user_id not in ALLOWED_USERS or (chat_id and chat_id != TELEGRAM_CHAT_ID):
            answer_callback(cb["id"], "No autorizado")
            print(f"[telegram] Clic de usuario no autorizado: {user_id}", flush=True)
            continue
        answer_callback(cb["id"])
        data = cb.get("data") or ""
        if data == "I":
            tg_send("Acción descartada.")
        elif data.startswith("B|"):
            handle_buy(data[2:], snapshot)
        elif data.startswith("S|"):
            handle_sell(data[2:], snapshot)


# ----------------------------- EVALUACIÓN PERIÓDICA -----------------------------
def evaluate_all(snapshot):
    state = load_state()
    positions = state["positions"]
    bucket = int(time.time() // 900)       # una fila de log por vela de 15 minutos y par

    for pair in WATCHLIST:
        data = evaluate_strategy(pair)
        if not data.get("ok"):
            fail_counts[pair] = fail_counts.get(pair, 0) + 1
            print(f"[radar] {pair}: {data.get('reason')} ({fail_counts[pair]} seguidos)", flush=True)
            if fail_counts[pair] == 5:
                tg_send(f"⚠️ Sin datos de {esc(pair)} hace 5 ciclos seguidos. Revisá la conexión con Pionex.")
            continue
        fail_counts[pair] = 0
        snapshot[pair] = data
        if last_logged_bucket.get(pair) != bucket:
            last_logged_bucket[pair] = bucket
            append_csv(SIGNALS_LOG, ["ts_utc", "par", "precio", "rsi", "ema_rapida", "ema_lenta", "vol_ratio", "puntaje"],
                       [utc_now(), pair, data["price"], data["rsi"], round(data["ema_fast"], 6),
                        round(data["ema_slow"], 6), data["volume_ratio"], data["score"]])

    print(f"[{time.strftime('%H:%M:%S')}] " + " | ".join(
        f"{p.replace('USDT', '')} {d['score']}" for p, d in snapshot.items()) + f" | posiciones {len(positions)}", flush=True)

    # --- Gestión de TP / SL (con recordatorio cada EXIT_REMINDER_MIN minutos) ---
    changed = False
    for sym, p in positions.items():
        cur = (snapshot.get(p["underlying"]) or {}).get("price") or get_live_price(sym)
        if not cur:
            continue
        result = pnl_pct(p["buy_price"], cur)
        hit_tp, hit_sl = result >= TAKE_PROFIT_PCT, result <= -STOP_LOSS_PCT
        if (hit_tp or hit_sl) and time.time() - p.get("last_alert_ts", 0) >= EXIT_REMINDER_MIN * 60:
            p["last_alert_ts"] = time.time()
            changed = True
            title = "🎯 <b>TAKE PROFIT alcanzado</b>" if hit_tp else "⚠️ <b>STOP LOSS alcanzado</b>"
            tg_send(f"{title} ({esc(p['coin'])}{' · sim' if p.get('simulated') else ''})\n\n"
                    f"• Resultado: <code>{result:+.2f}%</code>\n• Entrada: <code>${p['buy_price']:.4f}</code> | "
                    f"Actual: <code>${cur:.4f}</code>\n\n¿Cerrar la posición?",
                    [[{"text": f"🔴 Cerrar {p['coin']} a mercado", "callback_data": f"S|{sym}"}],
                     [{"text": "⚪ Mantener", "callback_data": "I"}]])
    if changed:
        save_state(state)

    # --- Nuevas entradas (solo LONG) ---
    if len(positions) >= MAX_OPEN_POSITIONS:
        return
    candidates = [d for s, d in snapshot.items()
                  if d["score"] >= BUY_THRESHOLD and f"{s.replace('USDT', '')}_USDT" not in positions]
    if not candidates:
        return
    best = max(candidates, key=lambda d: d["score"])
    coin = best["symbol"].replace("USDT", "")
    sym = f"{coin}_USDT"
    if time.time() - state["last_entry_alert"].get(sym, 0) < ENTRY_COOLDOWN_MIN * 60:
        return
    btc = get_btc_change_1h()
    if btc is not None and btc <= -BTC_BRAKE_PCT:
        print(f"[radar] Señal en {sym} frenada: BTC {btc:+.2f}% en 1h", flush=True)
        return
    if LIVE_TRADING:
        bal = px.get_balances_dict()
        if bal is None or bal.get("USDT", 0.0) < ORDER_USDT:
            print("[radar] Señal ignorada: saldo USDT insuficiente o no disponible", flush=True)
            return

    signal_id = uuid.uuid4().hex[:8]
    pending_signals[signal_id] = {"symbol_trade": sym, "coin": coin, "underlying": best["symbol"],
                                  "price": best["price"], "ts": time.time(), "used": False}
    for k in [k for k, v in pending_signals.items() if time.time() - v["ts"] > 3600]:
        del pending_signals[k]
    state["last_entry_alert"][sym] = time.time()
    save_state(state)
    tg_send(f"🟢 <b>SEÑAL ALCISTA ({MODE})</b>\n\n• Par: <code>{esc(coin)}/USDT</code>\n"
            f"• Precio: <code>${best['price']:.4f}</code>\n• Puntaje: <code>{best['score']}</code>\n"
            f"• BTC última hora: <code>{(f'{btc:+.2f}%' if btc is not None else 'sin datos')}</code>\n\n"
            f"<b>Condiciones:</b>\n" + "\n".join(f"• {esc(s)}" for s in best["signals"]) +
            f"\n\nEl botón vence en {SIGNAL_TTL_SEC // 60} minutos.",
            [[{"text": f"🟢 Comprar {ORDER_USDT:g} USDT en {coin}", "callback_data": f"B|{signal_id}"}],
             [{"text": "⚪ Ignorar", "callback_data": "I"}]])


# ----------------------------- ARRANQUE -----------------------------
def reconcile():
    """Avisa si hay saldo de las monedas vigiladas que el bot no tiene registrado (por ej. tras un reinicio)."""
    if not LIVE_TRADING:
        return
    bal = px.get_balances_dict()
    if not bal:
        return
    state = load_state()
    notes = []
    for pair in WATCHLIST:
        coin = pair.replace("USDT", "")
        free = bal.get(coin, 0.0)
        price = get_live_price(pair)
        if free > 0 and price and free * price >= 1.0 and f"{coin}_USDT" not in state["positions"]:
            notes.append(f"• {esc(coin)}: {free} (~${free * price:.2f}) sin posición registrada")
    if notes:
        tg_send("ℹ️ <b>Saldos sin registrar en el bot</b> (pueden ser tus tenencias o una posición perdida por un reinicio):\n"
                + "\n".join(notes))


def skip_pending_updates():
    """Tras un reinicio (en Render pasa seguido) Telegram reenvía lo que no se procesó en las últimas 24 h:
    clics viejos y comandos viejos. Se descartan para no responder ni operar sobre mensajes atrasados."""
    state = load_state()
    try:
        res = requests.get(f"{API_URL}/getUpdates", params={"offset": -1, "timeout": 0}, timeout=8).json()
        updates = res.get("result") or []
        if updates:
            state["last_update_id"] = max(state["last_update_id"], updates[-1]["update_id"])
            save_state(state)
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] No pude descartar mensajes viejos: {e}", flush=True)


def run_radar():
    missing = [n for n, v in {"TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN, "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID}.items() if not v]
    if LIVE_TRADING:
        missing += [n for n, v in {"PIONEX_API_KEY": px.API_KEY, "PIONEX_API_SECRET": px.API_SECRET}.items() if not v]
    if missing:
        print(f"[config] Faltan variables de entorno: {', '.join(missing)}. El radar no arranca.", flush=True)
        return
    px.RULES.update(get_symbol_rules([f"{p.replace('USDT', '')}_USDT" for p in WATCHLIST]))
    print(f"[radar] Reglas de pares cargadas: {len(px.RULES)} de {len(WATCHLIST)}", flush=True)
    tg_send(f"🚀 Radar iniciado en modo <b>{MODE}</b>. Vigilando: {esc(', '.join(WATCHLIST))}.\n"
            f"Comando útil: /status")
    reconcile()
    skip_pending_updates()

    snapshot, last_eval = {}, 0.0
    while True:
        process_updates(snapshot)
        if time.time() - last_eval >= EVAL_EVERY_SEC:
            last_eval = time.time()
            try:
                evaluate_all(snapshot)
            except Exception as e:  # noqa: BLE001
                print(f"[radar] Error en el ciclo de evaluación: {e}", flush=True)
        time.sleep(1)


def start_background_radar():
    """Si el radar se cae por un error inesperado, se reinicia solo (el servidor web sigue vivo)."""
    while True:
        try:
            print(">>> RADAR LANZADO <<<", flush=True)
            run_radar()
            return                              # run_radar solo devuelve si falta configuración
        except Exception as e:  # noqa: BLE001
            print(f">>> ERROR CRÍTICO EN RADAR: {e}. Reintento en 30 s <<<", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    threading.Thread(target=start_background_radar, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    # use_reloader=False evita que Flask duplique el proceso (y el hilo del radar)
    app.run(host="0.0.0.0", port=port, use_reloader=False)
