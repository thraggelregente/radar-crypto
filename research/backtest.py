import os
import json
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import vectorbt as vbt


DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def safe_float(value, default=0.0):
    """Convert a stat to float and guard NaN/Inf/None values."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(val):
        return default
    return val


def fetch_ohlcv(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 2000):
    """Download OHLCV from several exchanges with fallback.

    Order of attempts:
      1) Binance public data-api (data-api.binance.vision)
      2) Binance normal
      3) KuCoin
      4) OKX
      5) Local CSV (if present)

    Returns a pandas.DataFrame or raises the last exception if all fail.
    """
    file_path = DATA_DIR / f"{symbol.replace('/', '_').lower()}_{timeframe}.csv"
    if os.getenv("BACKTEST_FORCE_LOCAL", "0") == "1" and file_path.exists():
        print(f"[fetch_ohlcv] BACKTEST_FORCE_LOCAL=1 -> using local file {file_path}")
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    if file_path.exists():
        print(f"[fetch_ohlcv] Found cached file {file_path}, using it.")
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    attempts = [
        ("binance (data-api)", "binance", {"urls": {"api": {"public": "https://data-api.binance.vision/api/v3"}}}),
        ("binance (default)", "binance", {}),
        ("kucoin", "kucoin", {}),
        ("okx", "okx", {}),
    ]

    last_exc = None
    for name, ex_id, params in attempts:
        try:
            print(f"[fetch_ohlcv] Attempting {name} for symbol={symbol} timeframe={timeframe} limit={limit}")
            exchange_cls = getattr(ccxt, ex_id)
            ex_opts = {"enableRateLimit": True}
            ex_opts.update(params or {})
            exchange = exchange_cls(ex_opts)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

            df = pd.DataFrame(ohlcv, columns=["Open time", "Open", "High", "Low", "Close", "Volume"])
            df["Open time"] = pd.to_datetime(df["Open time"], unit="ms")
            df = df.set_index("Open time")
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna().sort_index()
            df.to_csv(file_path)
            print(f"[fetch_ohlcv] Success from {name} — saved to {file_path}")
            return df
        except Exception as e:
            print(f"[fetch_ohlcv] {name} failed: {type(e).__name__}: {e}")
            last_exc = e

    if file_path.exists():
        print(f"[fetch_ohlcv] Remote sources failed but found local file {file_path}, using it as fallback.")
        return pd.read_csv(file_path, index_col=0, parse_dates=True)

    print("[fetch_ohlcv] All remote exchanges failed and no local file present. Raising last exception.")
    raise last_exc or RuntimeError("Failed to fetch OHLCV from any source and no local file found.")


def rsi(series: pd.Series, period: int = 14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    out[(avg_loss == 0) & (avg_gain == 0)] = 50.0
    return out


def ema(series: pd.Series, span: int):
    return series.ewm(span=span, adjust=False).mean()


def backtest_strategy(df: pd.DataFrame, strategy_name: str):
    price = df["Close"]
    rsi14 = rsi(price, 14)
    ema_fast = ema(price, 9)
    ema_slow = ema(price, 21)

    if strategy_name == "ema_cross":
        entry = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
        exit_ = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))
    elif strategy_name == "rsi_ema":
        entry = (rsi14 < 35) & (ema_fast > ema_slow)
        exit_ = (rsi14 > 70) | (ema_fast < ema_slow)
    elif strategy_name == "pullback":
        entry = (rsi14 < 45) & (ema_fast > ema_slow) & (price > ema_fast)
        exit_ = (rsi14 > 65) | (ema_fast < ema_slow)
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")

    entries = entry.to_numpy()
    exits = exit_.to_numpy()
    pf = vbt.Portfolio.from_signals(
        price,
        entries=entries,
        exits=exits,
        init_cash=10000,
        fees=0.001,
        slippage=0.0005,
        freq="1h",
    )

    stats = pf.stats()
    trade_count = int(stats.get("Total Trades", 0) or 0)
    total_return = safe_float(stats.get("Total Return [%]", 0.0))
    cagr = safe_float(stats.get("CAGR [%]", 0.0))
    sharpe = safe_float(stats.get("Sharpe Ratio", 0.0))
    max_dd = safe_float(stats.get("Max Drawdown [%]", 0.0))
    win_rate = safe_float(stats.get("Win Rate [%]", 0.0))
    profit_factor = safe_float(stats.get("Profit Factor", 0.0))

    # Guard against false metrics when there are no trades
    if trade_count == 0:
        sharpe = 0.0
        max_dd = 0.0
        win_rate = 0.0
        profit_factor = 0.0

    return {
        "strategy": strategy_name,
        "total_return": total_return,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "trades": trade_count,
        "profit_factor": profit_factor,
    }


def walkforward_backtest(df: pd.DataFrame, strategy_name: str, folds: int = 4):
    """Evaluate a strategy in rolling train/test windows."""
    n = len(df)
    if n < 500:
        raise ValueError(f"Not enough rows for walk-forward validation: {n} < 500")

    test_size = max(200, n // 6)
    test_size = min(test_size, n // 3)
    if n - test_size <= 0:
        raise ValueError("Not enough data to build a test window")

    base_start = int(n * 0.35)
    end_anchor = n - test_size
    if end_anchor <= base_start:
        end_anchor = n - test_size

    step = max(1, (end_anchor - base_start) // max(1, folds - 1))
    folds_out = []

    for i in range(folds):
        train_end = min(base_start + i * step, end_anchor)
        test_start = train_end
        test_end = min(test_start + test_size, n)
        if test_end <= test_start:
            continue

        train = df.iloc[:train_end]
        test = df.iloc[test_start:test_end]
        train_res = backtest_strategy(train, strategy_name)
        test_res = backtest_strategy(test, strategy_name)

        folds_out.append({
            "fold": i + 1,
            "train_size": len(train),
            "test_size": len(test),
            "train": train_res,
            "test": test_res,
        })

    if not folds_out:
        raise ValueError("No valid walk-forward windows could be created from the given data")

    return folds_out


def summarize_walkforward(results):
    total_returns = [item["test"]["total_return"] for item in results]
    sharpe_vals = [item["test"]["sharpe"] for item in results]
    drawdowns = [item["test"]["max_drawdown"] for item in results]
    win_rates = [item["test"]["win_rate"] for item in results]
    profit_factors = [item["test"]["profit_factor"] for item in results]

    positive_windows = sum(1 for r in total_returns if r > 0)
    avg_return = sum(total_returns) / len(total_returns) if total_returns else 0.0
    avg_sharpe = sum(sharpe_vals) / len(sharpe_vals) if sharpe_vals else 0.0
    avg_drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else 0.0
    avg_win = sum(win_rates) / len(win_rates) if win_rates else 0.0
    avg_profit_factor = sum(profit_factors) / len(profit_factors) if profit_factors else 0.0

    return {
        "windows": len(results),
        "positive_windows": positive_windows,
        "negative_windows": len(results) - positive_windows,
        "avg_total_return": avg_return,
        "avg_sharpe": avg_sharpe,
        "avg_max_drawdown": avg_drawdown,
        "avg_win_rate": avg_win,
        "avg_profit_factor": avg_profit_factor,
    }


def main():
    df = fetch_ohlcv("BTC/USDT", "1h", 2000)
    strategies = ["ema_cross", "rsi_ema", "pullback"]
    out = {}

    for strategy_name in strategies:
        folds = walkforward_backtest(df, strategy_name, folds=4)
        out[strategy_name] = {
            "walkforward": folds,
            "summary": summarize_walkforward(folds),
        }

    output_path = Path(__file__).resolve().parent / "results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    for strategy_name, payload in out.items():
        summary = payload["summary"]
        print(
            f"{strategy_name}: windows={summary['windows']} | "
            f"positive={summary['positive_windows']} | "
            f"avg_return={summary['avg_total_return']:.2%} | "
            f"avg_sharpe={summary['avg_sharpe']:.2f} | "
            f"avg_dd={summary['avg_max_drawdown']:.2f}%"
        )


if __name__ == "__main__":
    main()
