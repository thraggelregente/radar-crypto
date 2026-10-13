"""Single trading worker + independent health server. Start: python main.py.

Paper is the default. Both entry modes have automatic risk exits.
"""
from concurrent.futures import ThreadPoolExecutor
try:
    import fcntl
except ImportError:  # Windows local development; Render/Linux uses flock.
    fcntl = None
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import queue
import signal
import threading
import time
import uuid

from config import Config
from data_sources import Market
from engine import analyze
from execution import Execution
from net import APIError
from pionex_client import Pionex
from risk import active_orders, equity, evidence_gate, position_size, update_limits
from state import Store
from telegram_client import Telegram

LOG = logging.getLogger("radar")


class Radar:
    def __init__(self, cfg, store, market, execution, telegram):
        self.cfg, self.store, self.market, self.execution, self.tg = cfg, store, market, execution, telegram
        self.stop = threading.Event()
        self.heartbeat = 0.0
        self.last_market_success = 0.0
        self.snapshot = {}
        self.bars = {}
        self.quotes = {}
        self.error = "iniciando"
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.last_alert = {}
        self.valuation_ok = True
        self.last_reconcile = 0.0

    def notify_once(self, key, text, seconds=300):
        if time.time() - self.last_alert.get(key, 0) >= seconds:
            self.tg.send(text)
            self.last_alert[key] = time.time()

    def refresh(self):
        self.snapshot, self.bars = {}, {}  # Never carry stale signals through a failed refresh.
        syms = tuple(dict.fromkeys((*self.cfg.watchlist, "BTC_USDT")))
        jobs = {s: self.pool.submit(self.market.candles, s) for s in syms}
        for symbol, job in jobs.items():
            try:
                self.bars[symbol] = job.result()
            except (APIError, ValueError, KeyError, TypeError):
                self.notify_once("data:" + symbol, f"Sin velas válidas de {symbol}; no se usa su señal anterior.")
        btc = self.bars.get("BTC_USDT")
        if not btc:
            self.error = "BTC no disponible: entradas bloqueadas"
            return
        for symbol in self.cfg.watchlist:
            if symbol in self.bars:
                self.snapshot[symbol] = analyze(symbol, self.bars[symbol], btc, self.cfg)
        if len(self.snapshot) == len(self.cfg.watchlist):
            self.last_market_success = time.time()
            self.error = ""
        else:
            self.error = "Datos incompletos; pares faltantes excluidos"

    def quote(self, symbol):
        # Only cache within a single tick; callback revalidates against an executable quote.
        if symbol not in self.quotes:
            self.quotes[symbol] = self.market.quote(symbol)
        return self.quotes[symbol]

    def prices(self):
        state = self.store.read()
        symbols = set(state["positions"]) | set(state.get("dust", {}))
        return {s: self.quote(s)["bid"] for s in symbols}

    def reconcile(self):
        if not self.cfg.live or active_orders(self.store.read()):
            return
        balances = self.market.api.balances()
        s = self.store.read()
        for symbol in self.cfg.watchlist:
            coin = symbol.split("_")[0]
            tracked = s["positions"].get(symbol, {}).get("qty", 0) + s.get("dust", {}).get(symbol, {}).get("qty", 0)
            available = balances.get(coin, 0)
            tolerance = 10 ** -(self.market.rules.get(symbol, {}).get("basePrecision", 8))
            reason = ""
            if available + tolerance < tracked:
                reason = f"Saldo de {symbol} menor al registrado; posible operación externa"
            elif available > tracked + tolerance and (available - tracked) * self.quote(symbol)["bid"] >= 1:
                reason = f"Saldo no atribuido de {symbol}; revisar posiciones previas/tenencias externas"
            if reason:
                with self.store.transaction() as state:
                    state["halt"] = reason
                self.notify_once("reconcile:" + symbol, reason + "; no se importan ni venden tenencias externas.")
        self.last_reconcile = time.time()

    def buy(self, symbol, signal_id=None):
        now = time.time()
        if not self.valuation_ok:
            return None
        data = self.snapshot.get(symbol)
        if not data or not data.get("eligible") or data["bar"] != int(now // 3600) * 3600 - 3600:
            return None
        gate, why = evidence_gate(self.cfg, auto=self.cfg.entry_mode == "auto")
        if not gate:
            self.notify_once("gate", why)
            return None
        if now - self.market.rules_ts > 3600 or symbol not in self.market.rules:
            return None
        s = self.store.read()
        if now - s["cooldown"].get(symbol, 0) < self.cfg.cooldown_sec:
            return None
        if signal_id:
            saved = s["signals"].get(signal_id)
            if not saved or saved["symbol"] != symbol or saved["used"] or now > saved["expires"] or saved["bar"] != data["bar"]:
                return None
        quote = self.market.quote(symbol)  # force a fresh execution quote
        # Avoid buying a signal after a large price jump while preserving its breakout.
        if quote["spread"] > self.cfg.max_spread or quote["ask"] < data["breakout_level"] or quote["ask"] > data["price"] + 0.5 * data["atr"]:
            return None
        # BTC must still be tradable and not have crossed the intrahour brake.
        btc_quote = self.market.quote("BTC_USDT")
        if btc_quote["bid"] / self.bars["BTC_USDT"][-1]["close"] - 1 <= self.cfg.btc_brake:
            return None
        available = None
        if self.cfg.live:
            available = self.market.api.balances().get("USDT", 0)
        amount = position_size(s, data, self.market.rules[symbol], self.cfg, self.prices(), available)
        if not amount:
            self.notify_once("minimum:" + symbol, f"{symbol}: no hay importe compatible con mínimos y riesgo. ORDER_USDT no se aumenta.", 3600)
            return None
        if not self.market.entry_liquidity(symbol, amount, quote):
            return None
        return self.execution.buy(data, amount, self.market.rules[symbol], quote, signal_id)

    def exits(self):
        s = self.store.read()
        prices = {}
        for symbol in set(s["positions"]) | set(s.get("dust", {})):
            try:
                prices[symbol] = self.quote(symbol)["bid"]
            except (APIError, ValueError, KeyError):
                self.notify_once("quote:" + symbol, f"Sin precio ejecutable de {symbol}; compras bloqueadas. No se inventa una salida.")
        self.valuation_ok = len(prices) == len(set(s["positions"]) | set(s.get("dust", {})))
        if self.valuation_ok:
            with self.store.transaction() as state:
                value = equity(state, prices, self.cfg)
                update_limits(state, value, self.cfg)
                state["last_equity"] = value
                state["forward_max_drawdown"] = max(state.get("forward_max_drawdown", 0), 1 - value / state["high_water"])
        for symbol, p in s["positions"].items():
            if symbol not in self.market.rules or symbol not in prices:
                continue
            cur = prices[symbol]
            reason = ""
            if cur <= p["stop"]:
                reason = "stop"
            elif cur >= p["target"]:
                reason = "target"
            elif time.time() - p["opened"] >= self.cfg.max_hold_hours * 3600:
                reason = "time"
            if reason:
                self.execution.sell(symbol, p["id"], self.market.rules[symbol], self.quote(symbol), reason)

    def alerts(self):
        now = time.time()
        s = self.store.read()
        if s["halt"] or s["manual_pause"] or s.get("daily_block") or active_orders(s) or len(s["positions"]) >= self.cfg.max_positions:
            return
        # Apply cooldown BEFORE ranking, so another eligible symbol is considered.
        candidates = [d for d in self.snapshot.values() if d.get("eligible") and d["symbol"] not in s["positions"]
                      and now - s["cooldown"].get(d["symbol"], 0) >= self.cfg.cooldown_sec]
        candidates.sort(key=lambda x: (-x["rank"], x["symbol"]))
        for data in candidates:
            if self.cfg.entry_mode == "auto":
                if self.buy(data["symbol"]):
                    break
            else:
                if any(x["symbol"] == data["symbol"] and not x["used"] and now < x["expires"] for x in s["signals"].values()):
                    continue
                gate, why = evidence_gate(self.cfg)
                if not gate:
                    self.notify_once("gate", why)
                    return
                identity = uuid.uuid4().hex[:12]
                with self.store.transaction() as state:
                    state["signals"][identity] = {"symbol": data["symbol"], "bar": data["bar"], "expires": now + self.cfg.signal_ttl, "used": False}
                sent = self.tg.send(f"{'REAL' if self.cfg.live else 'SIM'} {data['symbol']}: ruptura y tendencia confirmadas. "
                                    f"RR neto estimado {data['net_rr']:.2f}; límite {self.cfg.order_cap:g} USDT. Vence en 3 minutos.",
                                    [[{"text": "Comprar", "callback_data": f"B|{identity}"}], [{"text": "Ignorar", "callback_data": f"I|{identity}"}]])
                if not sent:
                    with self.store.transaction() as state:
                        state["signals"].pop(identity, None)

    def updates(self):
        try:
            batch, ack = self.tg.inbox.get_nowait()
        except queue.Empty:
            return
        try:
            for upd in batch:
                if upd["update_id"] <= self.store.read()["last_update_id"]:
                    continue
                # Persist receipt; order intent and fills have separate durable records.
                with self.store.transaction() as s:
                    s["last_update_id"] = upd["update_id"]
                if not self.tg.authorized(upd):
                    continue
                cb = upd.get("callback_query")
                if cb:
                    action, _, identity = (cb.get("data") or "").partition("|")
                    if action == "B":
                        saved = self.store.read()["signals"].get(identity)
                        if not saved or not self.buy(saved["symbol"], identity):
                            self.tg.send("Compra no enviada: señal vencida, usada o condiciones/riesgo cambiaron.")
                    elif action == "I":
                        with self.store.transaction() as s:
                            if identity in s["signals"]:
                                s["signals"][identity]["used"] = True
                else:
                    text = (upd.get("message", {}).get("text") or "").lower().strip()
                    if text == "/status":
                        s = self.store.read()
                        self.tg.send(f"{'REAL' if self.cfg.live else 'SIM'} · entradas {self.cfg.entry_mode} · salidas automáticas\n"
                                     f"Posiciones {len(s['positions'])}; órdenes pendientes {len(active_orders(s))}\n"
                                     f"Capital del bot {s.get('last_equity', s['cash']):.2f} USDT\n"
                                     f"Bloqueo: {s['halt'] or ('pausa' if s['manual_pause'] else 'ninguno')}\n"
                                     f"Datos: {self.error or 'actualizados'}")
                    elif text in {"/pause", "/resume"}:
                        with self.store.transaction() as s:
                            s["manual_pause"] = text == "/pause"
                        self.tg.send("Compras pausadas; salidas siguen activas." if text == "/pause" else "Pausa manual levantada; siguen vigentes validación y límites.")
                    elif text in {"/start", "/help"}:
                        self.tg.send("/status /pause /resume. La pausa no desactiva salidas de riesgo. Paper por defecto.")
        finally:
            ack.set()

    def run(self):
        refresh_at = 0.0
        self.tg.send(f"Radar iniciado: {'REAL' if self.cfg.live else 'SIMULACIÓN'}; entradas {self.cfg.entry_mode}, salidas automáticas.")
        while not self.stop.is_set():
            self.heartbeat = time.time()
            self.quotes = {}
            try:
                if time.time() - self.market.rules_ts > 1800:
                    try:
                        self.market.load_rules()
                    except (APIError, ValueError, KeyError, TypeError):
                        # Previously verified rules can still manage exits. Stale rules cannot authorize buys.
                        self.notify_once("rules", "No se actualizaron reglas; no se habilitan compras con reglas vencidas.")
                self.execution.poll(self.market.rules)
                self.exits()  # Run before slow candle refresh and before new entries.
                if self.cfg.live and time.time() - self.last_reconcile > 60:
                    self.reconcile()
                if time.time() >= refresh_at:
                    refresh_at = time.time() + 60
                    self.refresh()
                self.updates()
                self.alerts()
                with self.store.transaction() as s:
                    s["signals"] = {k: v for k, v in s["signals"].items() if time.time() < v["expires"]}
                    now = time.time()
                    if not self.error and self.valuation_ok and now - self.last_market_success < 180:
                        elapsed = max(0, now - s.get("last_observation_ts", now))
                        s["forward_observed_seconds"] = s.get("forward_observed_seconds", 0) + min(elapsed, 3 * self.cfg.poll_sec)
                    s["last_observation_ts"] = now
            except (APIError, ValueError, KeyError, TypeError) as e:
                self.error = str(e)
                self.snapshot = {}
                LOG.warning("Ciclo degradado: %s", type(e).__name__)
                self.notify_once("cycle", "Ciclo degradado: nuevas compras bloqueadas; revisar salud y Pionex.")
            except Exception:
                self.error = "Fallo crítico del worker; requiere revisión"
                LOG.exception("Worker detenido")
                return  # No silent reset, empty state or second worker.
            self.stop.wait(self.cfg.poll_sec)


def health_server(radar, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in {"/", "/health"}:
                self.send_error(404)
                return
            age = time.time() - radar.heartbeat
            healthy = age < 60 and not radar.error and time.time() - radar.last_market_success < 180
            body = json.dumps({"healthy": healthy, "mode": "live" if radar.cfg.live else "paper",
                               "worker_age_seconds": round(age) if radar.heartbeat else None,
                               "status": radar.error or "running"}).encode()
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.from_env()
    Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
    # A second local worker cannot start; multiple replicas are unsupported.
    lock = open(Path(cfg.data_dir) / "worker.lock", "a", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write("0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit("Ya hay un worker activo")
    store = Store(Path(cfg.data_dir) / ("live.sqlite3" if cfg.live else "paper.sqlite3"), cfg)
    store.legacy_guard(os.getenv("STATE_FILE", "trade_state.json"))
    api = Pionex(cfg)
    tg = Telegram(cfg, store)
    execution = Execution(store, api, cfg, tg.send)
    radar = Radar(cfg, store, Market(api, cfg), execution, tg)
    tg.start()
    worker = threading.Thread(target=radar.run, name="radar", daemon=True)
    worker.start()
    server = health_server(radar, int(os.getenv("PORT", "8080")))

    def shutdown(*_):
        radar.stop.set()
        tg.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        radar.stop.set()
        tg.stop.set()
        worker.join(timeout=40)
        radar.pool.shutdown(wait=True, cancel_futures=True)
        server.server_close()
        lock.close()


if __name__ == "__main__":
    main()
