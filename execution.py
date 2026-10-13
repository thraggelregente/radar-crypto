"""Write intent before POST. Apply cumulative fills exactly once, including fees.

A restart never resubmits PREPARED/UNKNOWN orders. Queries recover them by ID.
"""
import time
import uuid
from net import APIError
from pionex_client import floor_string, parse_fill
from risk import active_orders


class Execution:
    def __init__(self, store, api, cfg, notify=lambda text: None):
        self.store, self.api, self.cfg, self.notify = store, api, cfg, notify

    def buy(self, signal, amount, rules, quote, signal_id=None):
        with self.store.transaction() as s:
            if active_orders(s) or signal["symbol"] in s["positions"] or len(s["positions"]) >= self.cfg.max_positions:
                return None
            if s["halt"] or s["manual_pause"] or s.get("daily_block"):
                return None
            if signal_id:
                sig = s["signals"].get(signal_id)
                if not sig or sig["used"] or time.time() > sig["expires"]:
                    return None
                sig["used"] = True
            identity = "rdr-b-" + uuid.uuid4().hex
            pos_id = uuid.uuid4().hex[:12]
            order = {"id": identity, "symbol": signal["symbol"], "side": "BUY", "amount": amount,
                     "size": 0.0, "position_id": pos_id, "created": time.time(), "status": "PREPARED",
                     "stop_pct": signal["stop_pct"], "target_pct": signal["target_pct"], "bar": signal["bar"],
                     "filled_gross": 0.0, "filled_amount": 0.0, "base_fee": 0.0, "quote_fee": 0.0}
            s["orders"][identity] = order
            s["cooldown"][signal["symbol"]] = time.time()
        self.send(order, rules, quote)
        return identity

    def sell(self, symbol, position_id, rules, quote, reason):
        with self.store.transaction() as s:
            p = s["positions"].get(symbol)
            if not p or p["id"] != position_id or any(o["symbol"] == symbol for o in active_orders(s)):
                return None
            size = float(floor_string(p["qty"], rules["basePrecision"]))
            size = min(size, rules["maxTradeDumping"])
            if size < rules["minTradeDumping"]:
                s["halt"] = f"Residuo de {symbol} menor al mínimo; revisión requerida"
                return None
            identity = "rdr-s-" + uuid.uuid4().hex
            order = {"id": identity, "symbol": symbol, "side": "SELL", "amount": 0.0, "size": size,
                     "position_id": position_id, "created": time.time(), "status": "PREPARED", "reason": reason,
                     "filled_gross": 0.0, "filled_amount": 0.0, "base_fee": 0.0, "quote_fee": 0.0}
            s["orders"][identity] = order
        self.send(order, rules, quote)
        return identity

    def send(self, order, rules, quote):
        if not self.cfg.live:
            if order["side"] == "BUY":
                price = quote["ask"] * (1 + self.cfg.slippage)
                gross, amount = order["amount"] / price, order["amount"]
            else:
                price = quote["bid"] * (1 - self.cfg.slippage)
                gross, amount = order["size"], order["size"] * price
            fee_coin = order["symbol"].split("_")[0] if order["side"] == "BUY" else "USDT"
            fee = gross * self.cfg.fee if order["side"] == "BUY" else amount * self.cfg.fee
            self.apply(order["id"], {"status": "CLOSED", "filledSize": gross, "filledAmount": amount,
                                     "fee": fee, "feeCoin": fee_coin}, rules)
            return
        try:
            if order["side"] == "SELL":
                available = self.api.balances().get(order["symbol"].split("_")[0], 0)
                if available + 1e-12 < order["size"]:
                    raise APIError("Saldo difiere de posición; no se vende otra tenencia", code="BALANCE_MISMATCH")
            result = self.api.submit(order, rules)
            # Missing/malformed ID is NOT proof of failure.
            with self.store.transaction() as s:
                o = s["orders"][order["id"]]
                if result.get("orderId"):
                    o["exchange_id"] = str(result["orderId"])
                    o["status"] = "OPEN"
                else:
                    o["status"] = "UNKNOWN"
        except APIError as e:
            with self.store.transaction() as s:
                o = s["orders"][order["id"]]
                o["status"] = "UNKNOWN" if e.ambiguous else "REJECTED"
                o["error"] = str(e)
                if e.code == "BALANCE_MISMATCH":
                    s["halt"] = str(e)
            self.notify(f"Orden {order['side']} {order['symbol']}: {str(e)}. Se conserva seguimiento.")

    def poll(self, rules):
        for order in active_orders(self.store.read()):
            if order["symbol"] not in rules:
                continue
            # Paper intent written immediately before a crash is replayed ONLY as a simulation.
            if not self.cfg.live:
                with self.store.transaction() as s:
                    s["orders"][order["id"]]["status"] = "REJECTED"
                continue
            try:
                data = self.api.order(order)
                if data.get("symbol") != order["symbol"] or data.get("side") != order["side"]:
                    raise ValueError("Respuesta de otra orden")
                self.apply(order["id"], data, rules[order["symbol"]])
            except (APIError, ValueError, TypeError) as e:
                # Even NOT_FOUND after timeout does not authorize another POST.
                with self.store.transaction() as s:
                    s["orders"][order["id"]]["error"] = str(e)
                if time.time() - order["created"] > 120:
                    if time.time() - order.get("last_warning", 0) > 300:
                        self.notify(f"Pendiente de conciliación: {order['symbol']} {order['id']}; nuevas compras bloqueadas.")
                        with self.store.transaction() as s:
                            s["orders"][order["id"]]["last_warning"] = time.time()

    def apply(self, identity, data, rules):
        order = self.store.read()["orders"][identity]
        coin = order["symbol"].split("_")[0]
        fill = parse_fill(data, coin)
        with self.store.transaction() as s:
            o = s["orders"][identity]
            if o["status"] in {"CLOSED", "REJECTED"}:
                return
            keys = {"gross": "filled_gross", "amount": "filled_amount", "base_fee": "base_fee", "quote_fee": "quote_fee"}
            delta = {k: fill[k] - o[v] for k, v in keys.items()}
            if any(v < -1e-10 for v in delta.values()):
                raise ValueError("Ejecuciones acumuladas retrocedieron")
            if o["side"] == "BUY":
                qty = delta["gross"] - delta["base_fee"]
                spent = delta["amount"] + delta["quote_fee"]
                if qty < -1e-12:
                    raise ValueError("Comisión base retroactiva no conciliada")
                if delta["gross"] > 0 or spent > 0:
                    p = s["positions"].setdefault(o["symbol"], {"id": o["position_id"], "qty": 0.0, "cost": 0.0,
                        "opened": o["created"], "stop_pct": o["stop_pct"], "target_pct": o["target_pct"],
                        "realized": 0.0, "total_cost": 0.0, "bar": o["bar"], "mode": "live" if self.cfg.live else "paper"})
                    p["qty"] += qty
                    p["cost"] += spent
                    p["total_cost"] += spent
                    p["entry"] = p["cost"] / p["qty"]
                    p["stop"] = p["entry"] * (1 - p["stop_pct"])
                    p["target"] = p["entry"] * (1 + p["target_pct"])
                    s["cash"] -= spent
            else:
                p = s["positions"].get(o["symbol"])
                if not p or p["id"] != o["position_id"]:
                    raise ValueError("Venta no pertenece a posición activa")
                removed = delta["gross"] + delta["base_fee"]
                if removed > p["qty"] + 1e-10:
                    raise ValueError("Venta/comisión excede posición")
                proceeds = delta["amount"] - delta["quote_fee"]
                cost = p["cost"] * min(1, removed / p["qty"])
                p["qty"] = max(0, p["qty"] - removed)
                p["cost"] -= cost
                p["realized"] += proceeds - cost
                s["cash"] += proceeds
                # Keep dust and partial balances tracked; never silently drop them.
                dust = fill["closed"] and delta["gross"] > 0 and p["qty"] < rules["minTradeDumping"]
                if dust and p["qty"] > 1e-12:
                    residue = s.setdefault("dust", {}).setdefault(o["symbol"], {"qty": 0.0, "cost": 0.0})
                    residue["qty"] += p["qty"]
                    residue["cost"] += p["cost"]
                if p["qty"] <= 1e-12 or dust:
                    s["trades"].append({"id": p["id"], "symbol": o["symbol"], "opened": p["opened"],
                                        "closed": time.time(), "cost": p["total_cost"], "pnl": p["realized"],
                                        "return": p["realized"] / p["total_cost"], "reason": o["reason"], "mode": p["mode"]})
                    del s["positions"][o["symbol"]]
            for key, state_key in keys.items():
                o[state_key] = fill[key]
            o["status"] = "CLOSED" if fill["closed"] else "OPEN"
        if fill["closed"]:
            self.notify(f"{'REAL' if self.cfg.live else 'SIM'} {order['side']} {order['symbol']}: ejecución confirmada; ID {identity}")
