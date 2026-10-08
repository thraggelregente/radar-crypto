import os
import time
import json
import threading
import requests
from flask import Flask
from dotenv import load_dotenv
from engine import evaluate_strategy
from pionex_client import get_balances_dict, place_market_order, DECIMALS

load_dotenv()

# --- SERVIDOR WEB PARA MANTENER VIVO EN RENDER ---
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return "Radar Cripto Activo 24/7", 200

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- CONFIGURACIÓN DEL BOT ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID"))
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

WATCHLIST = ["SOLUSDT", "ETHUSDT", "NEARUSDT", "SUIUSDT", "AVAXUSDT"]
STATE_FILE = "trade_state.json"

MAX_OPEN_POSITIONS = 2
TAKE_PROFIT_PCT = 2.5
STOP_LOSS_PCT = 2.0

last_update_id = 0

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"positions": {}}
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            return data if "positions" in data else {"positions": {}}
    except Exception:
        return {"positions": {}}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

def send_alert_with_buttons(message: str, buttons: list):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": buttons}
    }
    try:
        requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        print(f"Error al enviar mensaje con botones: {e}")

def send_simple_message(text: str):
    try:
        requests.post(
            f"{API_URL}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"Error al enviar confirmación: {e}")

def process_telegram_updates(market_snapshot):
    global last_update_id
    try:
        url = f"{API_URL}/getUpdates?offset={last_update_id + 1}&timeout=1"
        res = requests.get(url, timeout=5).json()
        
        for update in res.get("result", []):
            last_update_id = update["update_id"]
            
            # Comandos de texto
            msg = update.get("message")
            if msg and "text" in msg:
                text = msg["text"].strip().lower()
                sender_id = str(msg.get("chat", {}).get("id"))
                
                if sender_id == TELEGRAM_CHAT_ID and text in ["/status", "status"]:
                    balances = get_balances_dict()
                    state = load_state()
                    pos = state.get("positions", {})
                    
                    pos_text = ""
                    if not pos:
                        pos_text = "⚪ Sin posiciones abiertas (2 slots libres)."
                    else:
                        for trade_sym, p_data in pos.items():
                            cur_p = market_snapshot.get(p_data["underlying"], {}).get("price", p_data["buy_price"])
                            bp = p_data["buy_price"]
                            direction = p_data.get("direction", "LONG")
                            
                            pnl = ((cur_p - bp) / bp) * 100 if direction == "LONG" else ((bp - cur_p) / bp) * 100
                            pos_text += (
                                f"• *{p_data['coin']} ({direction})*\n"
                                f"  Entrada: `${bp}` | Actual: `${cur_p}` | PnL: `{pnl:+.2f}%`\n"
                            )

                    ranking_txt = "\n".join([
                        f"• *{s.replace('USDT','')}:* ${d['price']} | Score: `{d['score']}` | RSI: `{d['rsi']}`"
                        for s, d in market_snapshot.items()
                    ])
                    
                    report = (
                        f"📊 *RADAR BIDIRECCIONAL (2 SLOTS)*\n\n"
                        f"{ranking_txt}\n\n"
                        f"💼 *Saldos Pionex:*\n"
                        f"• USDT libre: `${balances['USDT']:.2f}`\n\n"
                        f"🎯 *Posiciones Abiertas ({len(pos)}/{MAX_OPEN_POSITIONS}):*\n{pos_text}"
                    )
                    send_simple_message(report)

            # Clics de botones
            callback = update.get("callback_query")
            if not callback:
                continue
            
            query_id = callback["id"]
            data = callback.get("data")
            requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": query_id})
            state = load_state()

            if data.startswith("BUY_"):
                parts = data.split("_")
                symbol_trade = f"{parts[1]}_{parts[2]}"
                direction = parts[3]
                underlying = parts[4]
                coin = parts[1]
                
                price = market_snapshot.get(underlying, {}).get("price", 1.0)
                send_simple_message(f"⏳ Enviando compra de 5 USDT en {symbol_trade}...")
                
                order_res = place_market_order(symbol=symbol_trade, side="BUY", amount_usdt=5.0)
                if order_res.get("result"):
                    order_id = order_res.get("data", {}).get("orderId", "N/A")
                    dec = DECIMALS.get(symbol_trade, 2)
                    amount_coin = round(5.0 / price, dec) if price > 0 else 0.0
                    
                    state["positions"][symbol_trade] = {
                        "symbol_trade": symbol_trade,
                        "underlying": underlying,
                        "coin": coin,
                        "direction": direction,
                        "buy_price": price,
                        "amount_coin": amount_coin,
                        "amount_usdt": 5.0
                    }
                    save_state(state)
                    
                    send_simple_message(
                        f"✅ *Orden completada en Pionex*\n"
                        f"• Par: `{symbol_trade}` ({direction})\n"
                        f"• ID: `{order_id}`\n"
                        f"• Referencia: `${price}`\n"
                        f"• Take Profit: `+{TAKE_PROFIT_PCT}%` | Stop Loss: `-{STOP_LOSS_PCT}%`"
                    )
                else:
                    send_simple_message(f"❌ *Error al comprar:* {order_res.get('message')}")

            elif data.startswith("SELL_"):
                parts = data.split("_")
                symbol_trade = f"{parts[1]}_{parts[2]}"
                p_data = state["positions"].get(symbol_trade, {})
                coin_amount = p_data.get("amount_coin", 0.0)
                
                send_simple_message(f"⏳ Cerrando posición en {symbol_trade} a mercado...")
                order_res = place_market_order(symbol=symbol_trade, side="SELL", size_coin=coin_amount)
                if order_res.get("result"):
                    order_id = order_res.get("data", {}).get("orderId", "N/A")
                    if symbol_trade in state["positions"]:
                        del state["positions"][symbol_trade]
                        save_state(state)
                    send_simple_message(f"✅ *Posición cerrada con éxito*\nID: `{order_id}`")
                else:
                    send_simple_message(f"❌ *Error al cerrar:* {order_res.get('message')}")

            elif data == "IGNORE":
                send_simple_message("Acción descartada.")
    except Exception as e:
        print(f"Error procesando updates: {e}")

def run_radar():
    print("Iniciando Radar Bidireccional Multipar en la nube...")
    balances = get_balances_dict()
    print(f"Pionex conectado. Saldo USDT: ${balances['USDT']:.2f}")
    
    BUY_THRESHOLD = 50
    SELL_SHORT_THRESHOLD = -50
    last_eval_time = 0
    market_snapshot = {}

    while True:
        if market_snapshot:
            process_telegram_updates(market_snapshot)

        current_time = time.time()
        if current_time - last_eval_time >= 60:
            last_eval_time = current_time
            state = load_state()
            open_pos = state.get("positions", {})
            
            top_long_candidate = None
            top_short_candidate = None
            highest_score = -999
            lowest_score = 999

            for pair in WATCHLIST:
                try:
                    data = evaluate_strategy(pair)
                    market_snapshot[pair] = data
                    sc = data["score"]

                    if sc > highest_score:
                        highest_score = sc
                        top_long_candidate = data
                    if sc < lowest_score:
                        lowest_score = sc
                        top_short_candidate = data
                except Exception as e:
                    print(f"Error evaluando {pair}: {e}")

            # Gestión TP / SL
            for trade_sym, p_data in list(open_pos.items()):
                underlying = p_data["underlying"]
                cur_price = market_snapshot.get(underlying, {}).get("price", p_data["buy_price"])
                bp = p_data["buy_price"]
                direction = p_data.get("direction", "LONG")
                
                pnl_pct = ((cur_price - bp) / bp) * 100 if direction == "LONG" else ((bp - cur_price) / bp) * 100

                btn = [
                    [{"text": f"🔴 Cerrar {p_data['coin']} a Mercado", "callback_data": f"SELL_{trade_sym}"}],
                    [{"text": "⚪ Mantener", "callback_data": "IGNORE"}]
                ]

                if pnl_pct >= TAKE_PROFIT_PCT:
                    msg = (
                        f"🎯 *TAKE PROFIT ALCANZADO ({p_data['coin']} {direction})*\n\n"
                        f"• *Ganancia:* `+{pnl_pct:.2f}%` 🚀\n"
                        f"• *Entrada:* `${bp}` | *Actual:* `${cur_price}`\n\n"
                        f"¿Cerrar posición y asegurar ganancias?"
                    )
                    send_alert_with_buttons(msg, btn)

                elif pnl_pct <= -STOP_LOSS_PCT:
                    msg = (
                        f"⚠️ *STOP LOSS ALCANZADO ({p_data['coin']} {direction})*\n\n"
                        f"• *Pérdida:* `{pnl_pct:.2f}%` 🔻\n"
                        f"• *Entrada:* `${bp}` | *Actual:* `${cur_price}`\n\n"
                        f"¿Cortar pérdida ahora?"
                    )
                    send_alert_with_buttons(msg, btn)

            # Nuevas entradas
            if len(open_pos) < MAX_OPEN_POSITIONS:
                bal = get_balances_dict()
                if top_long_candidate and highest_score >= BUY_THRESHOLD:
                    coin = top_long_candidate["symbol"].replace("USDT", "")
                    sym_trade = f"{coin}_USDT"
                    if sym_trade not in open_pos:
                        btn = [
                            [{"text": f"🟢 Comprar 5 USDT en {coin} (LONG)", "callback_data": f"BUY_{sym_trade}_LONG_{top_long_candidate['symbol']}"}],
                            [{"text": "⚪ Ignorar", "callback_data": "IGNORE"}]
                        ]
                        msg = (
                            f"🟢 *SEÑAL ALCISTA (LONG)*\n\n"
                            f"• *Par:* `{coin}/USDT`\n"
                            f"• *Precio:* `${top_long_candidate['price']}`\n"
                            f"• *Puntaje:* `{highest_score}/100`\n"
                            f"• *USDT disponible:* `${bal['USDT']:.2f}`\n\n"
                            f"*Condiciones:*\n" + "\n".join([f"• {s}" for s in top_long_candidate["signals"]])
                        )
                        send_alert_with_buttons(msg, btn)

                elif top_short_candidate and lowest_score <= SELL_SHORT_THRESHOLD:
                    coin_base = top_short_candidate["symbol"].replace("USDT", "")
                    token_short = f"{coin_base}3S_USDT" if coin_base in ["BTC", "ETH", "SOL"] else "BTC3S_USDT"
                    if token_short not in open_pos:
                        btn = [
                            [{"text": f"🔴 Comprar 5 USDT en {token_short} (SHORT)", "callback_data": f"BUY_{token_short}_SHORT_{top_short_candidate['symbol']}"}],
                            [{"text": "⚪ Ignorar", "callback_data": "IGNORE"}]
                        ]
                        msg = (
                            f"🔴 *SEÑAL BAJISTA FUERTE (OPORTUNIDAD SHORT)*\n\n"
                            f"• *Mercado:* `{coin_base}/USDT` agotado al alza\n"
                            f"• *Token Inverso:* `{token_short}` (Sube si el mercado cae)\n"
                            f"• *Puntaje:* `{lowest_score}/100`\n"
                            f"• *USDT disponible:* `${bal['USDT']:.2f}`\n\n"
                            f"*Condiciones:*\n" + "\n".join([f"• {s}" for s in top_short_candidate["signals"]])
                        )
                        send_alert_with_buttons(msg, btn)

        time.sleep(1)

# Iniciar el radar apenas el archivo se cargue en Render
radar_thread = threading.Thread(target=run_radar, daemon=True)
radar_thread.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
