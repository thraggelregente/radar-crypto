"""Cliente mínimo de Pionex Spot (balances y órdenes a mercado).

Cambios clave respecto a la versión anterior:
- Los parámetros de la query van ordenados alfabéticamente (como exige la firma de Pionex).
- La venta a mercado usa 'size' (cantidad de la moneda) y la compra 'amount' (USDT), según la documentación.
- clientOrderId evita órdenes duplicadas si se repite un clic.
- Se puede consultar una orden para saber a qué precio y cantidad se ejecutó de verdad.
"""
import hashlib
import hmac
import json
import math
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()


def _clean(v):
    return (v or "").strip().strip('"').strip("'")


API_KEY = _clean(os.getenv("PIONEX_API_KEY"))
API_SECRET = _clean(os.getenv("PIONEX_API_SECRET"))
BASE_URL = "https://api.pionex.com"

# Respaldo si no se pueden consultar las reglas reales del par
DECIMALS = {
    "SOL_USDT": 4, "ETH_USDT": 4, "NEAR_USDT": 2, "SUI_USDT": 2, "AVAX_USDT": 3, "BTC_USDT": 6,
}
RULES = {}   # lo completa main.py al arrancar con las reglas reales de cada par

_time_offset = {"value": 0, "checked": 0.0}


def _now_ms() -> int:
    """Hora del servidor de Pionex (se consulta cada 10 minutos, no en cada pedido)."""
    if time.time() - _time_offset["checked"] > 600:
        try:
            r = requests.get(f"{BASE_URL}/api/v1/common/timestamp", timeout=5).json()
            server = r.get("timestamp") or (r.get("data") or {}).get("timestamp")
            if server:
                _time_offset["value"] = int(server) - int(time.time() * 1000)
        except Exception:  # noqa: BLE001
            pass
        _time_offset["checked"] = time.time()
    return int(time.time() * 1000) + _time_offset["value"]


def _signed_request(method: str, path: str, params=None, body=None) -> dict:
    params = dict(params or {})
    params["timestamp"] = _now_ms()
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    body_str = json.dumps(body, separators=(",", ":")) if body else ""
    payload = f"{method}{path}?{query}{body_str}"
    signature = hmac.new(API_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    headers = {"PIONEX-KEY": API_KEY, "PIONEX-SIGNATURE": signature, "Content-Type": "application/json"}
    url = f"{BASE_URL}{path}?{query}"
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=10)
        else:
            r = requests.post(url, data=body_str, headers=headers, timeout=10)
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"result": False, "message": str(e)}


def get_balances_dict():
    """{'USDT': libre, 'SOL': libre, ...} o None si la consulta falló (no se confunde con saldo 0)."""
    res = _signed_request("GET", "/api/v1/account/balances")
    if not res.get("result"):
        print(f"[pionex] No pude leer saldos: {res.get('code') or ''} {res.get('message') or ''}", flush=True)
        return None
    balances = {"USDT": 0.0}
    for b in (res.get("data") or {}).get("balances") or []:
        try:
            balances[b.get("coin")] = float(b.get("free") or 0.0)
        except (TypeError, ValueError):
            continue
    return balances


def floor_to(x: float, decimals: int) -> float:
    """Redondea HACIA ABAJO (redondear al más cercano puede pedir vender más de lo que tenés)."""
    f = 10 ** decimals
    return math.floor(x * f + 1e-9) / f


def size_decimals(symbol: str) -> int:
    d = (RULES.get(symbol) or {}).get("base_precision")
    return d if isinstance(d, int) and d >= 0 else DECIMALS.get(symbol, 2)


def place_market_order(symbol: str, side: str, amount_usdt=None, size_coin=None, client_order_id=None) -> dict:
    """Compra a mercado: se indica 'amount_usdt'. Venta a mercado: se indica 'size_coin'."""
    side = side.upper()
    body = {"symbol": symbol, "side": side, "type": "MARKET"}
    if side == "BUY":
        if not amount_usdt or amount_usdt <= 0:
            return {"result": False, "message": "Falta amount_usdt para la compra"}
        body["amount"] = f"{amount_usdt:.4f}".rstrip("0").rstrip(".")
    elif side == "SELL":
        dec = size_decimals(symbol)
        size = floor_to(size_coin or 0.0, dec)
        if size <= 0:
            return {"result": False, "message": "Cantidad a vender inválida (size <= 0)"}
        body["size"] = f"{size:.{dec}f}"
    else:
        return {"result": False, "message": f"Lado inválido: {side}"}
    if client_order_id:
        body["clientOrderId"] = re.sub(r"[^A-Za-z0-9-]", "-", client_order_id)[:64]
    return _signed_request("POST", "/api/v1/trade/order", body=body)


def get_order(symbol: str, order_id) -> dict:
    return _signed_request("GET", "/api/v1/trade/order", params={"orderId": order_id, "symbol": symbol})


def parse_fill(order_res: dict, base_coin: str):
    """Cantidad neta y precio promedio realmente ejecutados, o None si todavía no hay datos."""
    d = (order_res or {}).get("data") or {}
    try:
        size = float(d.get("filledSize") or 0)
        amount = float(d.get("filledAmount") or 0)
        fee = float(d.get("fee") or 0)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return None
    fee_coin = str(d.get("feeCoin") or "").upper()
    net = size - fee if fee_coin == base_coin.upper() else size
    return {
        "size": net,
        "gross_size": size,
        "avg_price": (amount / size) if amount > 0 else None,
        "status": d.get("status"),
    }
