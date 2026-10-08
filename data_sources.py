import requests
import pandas as pd

def get_market_data(symbol="SOLUSDT", interval="15m", limit=60):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"
        ])
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df
    except Exception:
        return pd.DataFrame()

def check_btc_health():
    """Verifica si Bitcoin no está en caída libre en la última hora."""
    df_btc = get_market_data("BTCUSDT", interval="15m", limit=5)
    if df_btc.empty:
        return {"safe": True, "change_1h": 0.0}
    
    price_now = df_btc["close"].iloc[-1]
    price_1h_ago = df_btc["close"].iloc[0]
    change_1h = ((price_now - price_1h_ago) / price_1h_ago) * 100
    
    # Si BTC cae más de -1.3% en 1 hora, no es seguro comprar altcoins
    safe = change_1h > -1.3
    return {"safe": safe, "change_1h": round(change_1h, 2)}

def get_fear_and_greed():
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        res = requests.get(url, timeout=5).json()
        value = int(res["data"][0]["value"])
        classification = res["data"][0]["value_classification"]
        return {"value": value, "status": classification}
    except Exception:
        return {"value": 50, "status": "Neutral"}

def get_funding_and_sentiment(symbol="SOLUSDT"):
    """Consulta Funding Rate y Long/Short Ratio de Futuros en Binance."""
    try:
        fr_url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
        fr_res = requests.get(fr_url, timeout=5).json()
        last_funding = float(fr_res[0]["fundingRate"]) if fr_res else 0.0

        ls_url = f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={symbol}&period=15m&limit=1"
        ls_res = requests.get(ls_url, timeout=5).json()
        ls_ratio = float(ls_res[0]["longShortRatio"]) if ls_res else 1.0

        if last_funding < 0 and ls_ratio > 1.2:
            status = "Fuerte Rebote (Squeeze)"
            signal_score = 25
        elif ls_ratio > 1.5 and last_funding > 0.0005:
            status = "Sobrecalentado"
            signal_score = -20
        elif last_funding < 0:
            status = "Sesgo Alcista (Shorts pagando)"
            signal_score = 15
        elif last_funding > 0.0003:
            status = "Sesgo Bajista"
            signal_score = -15
        else:
            status = "Equilibrado"
            signal_score = 0

        return {
            "funding_rate": round(last_funding * 100, 4),
            "long_short_ratio": round(ls_ratio, 2),
            "status": status,
            "score": signal_score
        }
    except Exception:
        return {
            "funding_rate": 0.0,
            "long_short_ratio": 1.0,
            "status": "Neutro",
            "score": 0
        }