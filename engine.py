import pandas as pd
from data_sources import get_market_data, get_fear_and_greed, get_funding_and_sentiment, check_btc_health

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -1 * delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def evaluate_strategy(symbol="SOLUSDT"):
    df = get_market_data(symbol=symbol, interval="15m", limit=50)
    if df.empty:
        return {"price": 0, "rsi": 50, "score": 0, "signals": ["Error datos"]}

    current_price = df["close"].iloc[-1]
    df["rsi"] = calculate_rsi(df["close"], period=14)
    df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()
    
    last_rsi = round(df["rsi"].iloc[-1], 2)
    ema_fast = df["ema_fast"].iloc[-1]
    ema_slow = df["ema_slow"].iloc[-1]
    
    # Análisis de volumen relativo
    avg_volume = df["volume"].iloc[-20:].mean()
    last_volume = df["volume"].iloc[-1]
    volume_ratio = round(last_volume / avg_volume, 2) if avg_volume > 0 else 1.0

    score = 0
    signals = []

    # 1. RSI
    if last_rsi < 30:
        score += 35
        signals.append(f"RSI sobreventa ({last_rsi})")
    elif last_rsi > 70:
        score -= 35
        signals.append(f"RSI sobrecompra ({last_rsi})")
    else:
        signals.append(f"RSI neutro ({last_rsi})")

    # 2. EMAs
    if ema_fast > ema_slow:
        score += 20
        signals.append("EMA 9 > EMA 21 (Alcista)")
    else:
        score -= 20
        signals.append("EMA 9 < EMA 21 (Bajista)")

    # 3. Volumen
    if volume_ratio > 1.3 and ema_fast > ema_slow:
        score += 15
        signals.append(f"Volumen fuerte ({volume_ratio}x media)")

    # 4. Sentimiento y Derivados
    deriv = get_funding_and_sentiment(symbol)
    score += deriv["score"]
    signals.append(f"Futuros: {deriv['status']} (L/S: {deriv['long_short_ratio']})")

    # 5. Filtro Guardián de BTC
    btc = check_btc_health()
    if not btc["safe"]:
        score -= 40
        signals.append(f"⚠️ Alerta BTC: Cayendo {btc['change_1h']}% en 1h (Freno de emergencia)")

    return {
        "symbol": symbol,
        "price": current_price,
        "rsi": last_rsi,
        "score": score,
        "signals": signals,
        "volume_ratio": volume_ratio
    }