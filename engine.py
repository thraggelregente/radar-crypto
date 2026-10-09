"""Cálculo de indicadores y puntaje por par.

IMPORTANTE: la lógica de puntaje (pesos y umbrales) NO está validada con datos históricos.
Con estos pesos, llegar a 50 exige RSI < 30 Y EMA 9 > EMA 21 a la vez, algo poco frecuente:
es esperable que casi no haya señales. Antes de operar con plata real hay que probarla (backtest).
"""
import numpy as np
import pandas as pd

from data_sources import get_klines, get_live_price


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder (el mismo que muestran los gráficos)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    rsi[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return rsi


def evaluate_strategy(symbol="SOLUSDT", interval="15m"):
    """Devuelve {'ok': False, ...} si no hay datos suficientes; nunca inventa velas."""
    df = get_klines(symbol, interval, limit=100)
    if len(df) < 40:
        return {"ok": False, "symbol": symbol, "reason": "datos insuficientes"}

    closed = df.iloc[:-1].copy()          # la última vela está en formación: las señales usan velas cerradas
    closed["rsi"] = calculate_rsi(closed["close"], 14)
    ema_fast = closed["close"].ewm(span=9, adjust=False).mean().iloc[-1]
    ema_slow = closed["close"].ewm(span=21, adjust=False).mean().iloc[-1]
    last_rsi = closed["rsi"].iloc[-1]
    if pd.isna(last_rsi):
        return {"ok": False, "symbol": symbol, "reason": "RSI no calculable"}
    last_rsi = round(float(last_rsi), 2)

    avg_volume = closed["volume"].iloc[-21:-1].mean()
    last_volume = closed["volume"].iloc[-1]
    volume_ratio = round(float(last_volume / avg_volume), 2) if avg_volume > 0 else 1.0

    price = get_live_price(symbol) or float(df["close"].iloc[-1])

    score, signals = 0, []
    if last_rsi < 30:
        score += 35
        signals.append(f"RSI sobreventa ({last_rsi})")
    elif last_rsi > 70:
        score -= 35
        signals.append(f"RSI sobrecompra ({last_rsi})")
    else:
        signals.append(f"RSI neutro ({last_rsi})")

    bullish = ema_fast > ema_slow
    if bullish:
        score += 20
        signals.append("EMA 9 > EMA 21 (alcista)")
    else:
        score -= 20
        signals.append("EMA 9 < EMA 21 (bajista)")

    if volume_ratio > 1.3 and bullish:
        score += 15
        signals.append(f"Volumen fuerte ({volume_ratio}x la media)")

    return {
        "ok": True,
        "symbol": symbol,
        "price": float(price),
        "rsi": last_rsi,
        "ema_fast": float(ema_fast),
        "ema_slow": float(ema_slow),
        "volume_ratio": volume_ratio,
        "score": score,
        "signals": signals,
    }
