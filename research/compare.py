import json
from pathlib import Path

from backtest import fetch_ohlcv, walkforward_backtest, summarize_walkforward, trend_breakout_strategy


OUT = Path(__file__).resolve().parent / "results_v3.json"


def run_all():
    df = fetch_ohlcv("BTC/USDT", "1h", 2000)
    folds = walkforward_backtest(df, trend_breakout_strategy, folds=4)
    payload = {
        "strategy": "4h_trend_1h_breakout",
        "walkforward": folds,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run_all()
