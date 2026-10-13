"""Spot API with durable client IDs supplied by execution.py."""
from decimal import Decimal, ROUND_DOWN
import hashlib
import hmac
import json
import math
import time
from urllib.parse import urlencode
from net import APIError, HTTP


def finite(value, positive=False):
    v = float(value)
    if not math.isfinite(v) or v < 0 or (positive and v <= 0):
        raise ValueError("Número de mercado inválido")
    return v


def floor_string(value, decimals):
    if not isinstance(decimals, int) or not 0 <= decimals <= 18:
        raise ValueError("Precisión inválida")
    n = Decimal(str(value))
    if not n.is_finite() or n < 0:
        raise ValueError("Cantidad inválida")
    return format(n.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN), "f")


class Pionex:
    base = "https://api.pionex.com"

    def __init__(self, config, http=None):
        self.config, self.http = config, http or HTTP()
        self.offset = 0
        self.clock_checked = 0

    def public(self, path, params=None, weight=1):
        result = self.http.request("GET", self.base + path + "?" + urlencode(params or {}), weight=weight)
        if not result.get("result"):
            raise APIError(str(result.get("message", "Consulta rechazada")), code=str(result.get("code", "")))
        return result.get("data") or {}

    def sync_clock(self):
        if time.monotonic() - self.clock_checked < 600:
            return
        before = time.time() * 1000
        res = self.http.request("GET", self.base + "/api/v1/common/timestamp")
        server = res.get("timestamp") or (res.get("data") or {}).get("timestamp")
        if not server:
            raise APIError("Hora Pionex no confirmada")
        self.offset = int(server) - int((before + time.time() * 1000) / 2)
        self.clock_checked = time.monotonic()

    def signed(self, method, path, params=None, body=None):
        self.sync_clock()
        params = dict(params or {}, timestamp=int(time.time() * 1000) + self.offset)
        canonical = "&".join(f"{k}={params[k]}" for k in sorted(params))
        text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        sig = hmac.new(self.config.api_secret.encode(), f"{method}{path}?{canonical}{text}".encode(), hashlib.sha256).hexdigest()
        res = self.http.request(method, f"{self.base}{path}?{urlencode(sorted(params.items()))}", body=text.encode() if text else None,
                                headers={"PIONEX-KEY": self.config.api_key, "PIONEX-SIGNATURE": sig, "Content-Type": "application/json"})
        if res.get("result") is not True:
            code = str(res.get("code", ""))
            explicit_rejections = {"APIKEY_LOST", "SIGNATURE_LOST", "IP_NOT_WHITELISTED", "INVALID_APIKEY",
                "INVALID_SIGNATURE", "APIKEY_EXPIRED", "INVALID_TIMESTAMP", "PERMISSION_DENIED",
                "TRADE_INVALID_SYMBOL", "TRADE_PARAMETER_ERROR", "TRADE_NOT_ENOUGH_MONEY",
                "TRADE_PRICE_FILTER_DENIED", "TRADE_SIZE_FILTER_DENIED", "TRADE_AMOUNT_FILTER_DENIED",
                "TRADE_OPEN_ORDER_EXCEED_LIMIT", "TRADE_OPERATION_DENIED"}
            raise APIError(str(res.get("message", "Consulta rechazada"))[:200], code=code,
                           ambiguous=method != "GET" and code not in explicit_rejections)
        return res.get("data") or {}

    def balances(self):
        rows = self.signed("GET", "/api/v1/account/balances").get("balances")
        if not isinstance(rows, list):
            raise APIError("Saldos inválidos")
        return {r["coin"]: finite(r["free"]) for r in rows}

    def submit(self, order, rules):
        body = {"symbol": order["symbol"], "side": order["side"], "type": "MARKET", "clientOrderId": order["id"]}
        key, value, precision = ("amount", order["amount"], rules["amountPrecision"]) if order["side"] == "BUY" else ("size", order["size"], rules["basePrecision"])
        body[key] = floor_string(value, precision)
        return self.signed("POST", "/api/v1/trade/order", body=body)

    def order(self, order):
        if order.get("exchange_id"):
            return self.signed("GET", "/api/v1/trade/order", {"orderId": order["exchange_id"]})
        return self.signed("GET", "/api/v1/trade/orderByClientOrderId", {"clientOrderId": order["id"]})


def parse_fill(data, coin):
    if data.get("status") not in {"OPEN", "CLOSED"}:
        raise ValueError("Estado no confirmado")
    size, amount, fee = finite(data.get("filledSize", 0)), finite(data.get("filledAmount", 0)), finite(data.get("fee", 0))
    fee_coin = str(data.get("feeCoin") or "").upper()
    if fee and fee_coin not in {coin, "USDT"}:
        raise ValueError("Comisión en tercera moneda: requiere conciliación")
    if bool(size) != bool(amount) or (fee_coin == coin and fee > 0 and fee >= size) or (fee > 0 and size == 0):
        raise ValueError("Ejecución inválida")
    return {"gross": size, "amount": amount, "base_fee": fee if fee_coin == coin else 0.0,
            "quote_fee": fee if fee_coin == "USDT" else 0.0, "closed": data["status"] == "CLOSED"}
