"""Cliente Pionex: Soporte para Futuros Perpetuos (LONG y SHORT) y balances."""
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

RULES = {}
_time_offset = {"value": 0, "checked": 0.0}

def _now_ms() -> int:
    if time.time() - _time_offset["checked"] > 600:
        try:
            r = requests.get(f"{BASE_URL}/api/v1/common/timestamp", timeout=5).json()
            server = r.get("timestamp") or (r.get("data") or {}).get("timestamp")
            if server:
                _time_offset["value"] = int(server) - int(time.time() * 1000)
        except Exception:
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
    headers = {
        "PIONEX-KEY": API_KEY,
        "PIONEX-SIGNATURE": signature,
        "Content-Type": "application/json"
    }
    url = f"{BASE_URL}{path}?{query}"
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=10)
        else:
            r = requests.post(url, data=body_str, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"result": False, "message": str(e)}

def get_futures_balance() -> float:
    """Consulta saldo USDT disponible en la cuenta de futuros."""
    # 1. Consulta balance de futuros directo
    res = _signed_request("GET", "/api/v1/futures/account")
    if res.get("result"):
        data = res.get("data") or {}
        return float(data.get("availableMargin") or data.get("totalWalletBalance") or 0.0)

    # 2. Respaldo general de balances
    res_spot = _signed_request("GET", "/api/v1/account/balances")
    if res_spot.get("result"):
        for b in (res_spot.get("data") or {}).get("balances") or []:
            if b.get("coin") == "USDT":
                return float(b.get("free") or 0.0)
    return 0.0

def floor_to(x: float, decimals: int) -> float:
    f = 10 ** decimals
    return math.floor(x * f + 1e-9) / f

def size_decimals(symbol: str) -> int:
    d = (RULES.get(symbol) or {}).get("base_precision")
    return d if isinstance(d, int) and d >= 0 else 2

def place_futures_order(symbol: str, side: str, amount_usdt: float, price_ref: float, client_order_id=None) -> dict:
    """
    side: 'BUY' (para abrir Long o cerrar Short) | 'SELL' (para abrir Short o cerrar Long)
    """
    sym = symbol if "_" in symbol else f"{symbol[:-4]}_{symbol[-4:]}"
    dec = size_decimals(sym)
    size = floor_to(amount_usdt / price_ref, dec) if price_ref > 0 else 0.0
    if size <= 0:
        return {"result": False, "message": "Tamaño calculado menor al permitido."}

    body = {
        "symbol": sym,
        "side": side.upper(),
        "type": "MARKET",
        "size": f"{size:.{dec}f}"
    }
    if client_order_id:
        body["clientOrderId"] = re.sub(r"[^A-Za-z0-9-]", "-", client_order_id)[:64]

    # Envío a endpoint de contratos futuros
    res = _signed_request("POST", "/api/v1/futures/order", body=body)
    if not res.get("result"):
        # Intento con fallback a endpoint spot/margin si la cuenta usa Unified Account
        body_spot = {
            "symbol": sym,
            "side": side.upper(),
            "type": "MARKET",
            "amount": f"{amount_usdt:.2f}" if side.upper() == "BUY" else None,
            "size": f"{size:.{dec}f}" if side.upper() == "SELL" else None
        }
        body_spot = {k: v for k, v in body_spot.items() if v is not None}
        return _signed_request("POST", "/api/v1/trade/order", body=body_spot)
    return res

def close_futures_position(symbol: str, side_to_close: str, size: float, client_order_id=None) -> dict:
    """Cierra la posición enviando una orden inversa."""
    sym = symbol if "_" in symbol else f"{symbol[:-4]}_{symbol[-4:]}"
    dec = size_decimals(sym)
    clean_size = floor_to(size, dec)
    body = {
        "symbol": sym,
        "side": side_to_close.upper(),
        "type": "MARKET",
        "size": f"{clean_size:.{dec}f}"
    }
    if client_order_id:
        body["clientOrderId"] = re.sub(r"[^A-Za-z0-9-]", "-", client_order_id)[:64]
    return _signed_request("POST", "/api/v1/futures/order", body=body)
