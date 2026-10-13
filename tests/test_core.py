"""Offline regression tests, not connection scripts. No messages or live orders."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
import urllib.request

from backtest import historical_report, simulate
from config import Config
from data_sources import Market, validate_candles
from engine import analyze
from execution import Execution
from main import Radar, health_server
from net import APIError
from pionex_client import Pionex, floor_string, parse_fill
from risk import active_orders, evidence_gate, position_size, update_limits
from state import Store

RULE = {"basePrecision": 4, "amountPrecision": 2, "minAmount": 1.0, "minTradeDumping": 0.0001,
        "maxTradeDumping": 1000000.0, "enable": True, "type": "SPOT"}
QUOTE = {"bid": 100.0, "ask": 100.01, "time": 0, "spread": 0.0001}


def signal(symbol="SOL_USDT"):
    return {"symbol": symbol, "bar": 0, "price": 100.0, "stop_pct": 0.02, "target_pct": 0.03,
            "eligible": True, "rank": 1, "breakout_level": 99, "atr": 1}


def bars(n=400, down=False):
    out = []
    for i in range(n):
        price = (100 + i * 0.2) if not down else (300 - i * 0.2)
        out.append({"time": i * 3600, "open": price - 0.1, "high": price + 0.15,
                    "low": price - 0.2, "close": price, "volume": 100})
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(order_cap=10, capital=100)
        self.store = Store(Path(self.tmp.name) / "paper.sqlite3", self.cfg)
        self.api = Mock()
        self.api.submit.side_effect = AssertionError("Una prueba paper no debe enviar órdenes")
        self.ex = Execution(self.store, self.api, self.cfg)

    def paper_buy(self):
        return self.ex.buy(signal(), 10, RULE, QUOTE)

    def real(self):
        cfg = replace(self.cfg, live=True)
        store = Store(Path(self.tmp.name) / "real.sqlite3", cfg)
        api = Mock()
        api.submit.return_value = {"orderId": 123}
        api.balances.return_value = {"SOL": 10, "USDT": 100}
        return cfg, store, api, Execution(store, api, cfg)


class ExecutionTests(Base):
    def test_paper_buy_uses_fees_and_ask(self):
        self.paper_buy()
        s = self.store.read()
        p = s["positions"]["SOL_USDT"]
        self.assertEqual(s["cash"], 90)
        self.assertLess(p["qty"], 0.1)
        self.api.submit.assert_not_called()

    def test_full_auto_paper_entry_and_exit(self):
        tg = types.SimpleNamespace(send=Mock())
        market = Mock()
        market.rules = {"SOL_USDT": RULE}
        market.rules_ts = time.time()
        market.api.balances.side_effect = AssertionError("Paper no consulta saldos")
        market.quote.return_value = dict(QUOTE, time=time.time())
        market.entry_liquidity.return_value = True
        r = Radar(self.cfg, self.store, market, self.ex, tg)
        self.addCleanup(r.pool.shutdown)
        r.bars = {"BTC_USDT": [{"close": 100}]}
        r.snapshot = {"SOL_USDT": dict(signal(), bar=int(time.time() // 3600) * 3600 - 3600)}
        r.alerts()
        self.assertIn("SOL_USDT", self.store.read()["positions"])
        r.quotes = {}
        market.quote.return_value = dict(QUOTE, bid=104, ask=104.01, time=time.time())
        r.exits()
        self.assertEqual(len(self.store.read()["trades"]), 1)
        self.assertGreater(self.store.read()["trades"][0]["pnl"], 0)
        self.api.submit.assert_not_called()

    def test_batch_does_not_erase_new_position(self):
        tg = types.SimpleNamespace(inbox=queue.Queue(), authorized=lambda _: True, send=Mock())
        market = types.SimpleNamespace()
        r = Radar(self.cfg, self.store, market, self.ex, tg)
        self.addCleanup(r.pool.shutdown)
        with self.store.transaction() as s:
            s["signals"]["s1"] = {"symbol": "SOL_USDT"}
        r.buy = lambda symbol, identity=None: self.paper_buy()
        ack = threading.Event()
        tg.inbox.put(([{"update_id": 1, "callback_query": {"data": "B|s1"}},
                       {"update_id": 2, "message": {"text": "/status"}}], ack))
        r.updates()
        self.assertIn("SOL_USDT", self.store.read()["positions"])
        self.assertEqual(self.store.read()["last_update_id"], 2)
        self.assertTrue(ack.is_set())

    def test_pending_write_is_durable_before_post(self):
        cfg, store, api, ex = self.real()
        def submit(order, rule):
            self.assertEqual(store.read()["orders"][order["id"]]["status"], "PREPARED")
            raise APIError("timeout", ambiguous=True)
        api.submit.side_effect = submit
        ex.buy(signal(), 10, RULE, QUOTE)
        self.assertEqual(active_orders(store.read())[0]["status"], "UNKNOWN")

    def test_timeout_restart_never_resends(self):
        cfg, store, api, ex = self.real()
        api.submit.side_effect = APIError("timeout", ambiguous=True)
        ex.buy(signal(), 10, RULE, QUOTE)
        api.order.side_effect = APIError("not found", code="TRADE_ORDER_NOT_FOUND")
        Execution(store, api, cfg).poll({"SOL_USDT": RULE})
        self.assertEqual(api.submit.call_count, 1)
        self.assertEqual(len(active_orders(store.read())), 1)

    def test_fill_applied_exactly_once(self):
        _, store, api, ex = self.real()
        identity = ex.buy(signal(), 10, RULE, QUOTE)
        fill = {"status": "CLOSED", "filledSize": 0.1, "filledAmount": 10, "fee": 0.00005, "feeCoin": "SOL"}
        ex.apply(identity, fill, RULE)
        before = store.read()
        ex.apply(identity, fill, RULE)
        self.assertEqual(before, store.read())

    def test_incremental_buy_fill(self):
        _, store, _, ex = self.real()
        identity = ex.buy(signal(), 10, RULE, QUOTE)
        ex.apply(identity, {"status": "OPEN", "filledSize": .04, "filledAmount": 4}, RULE)
        ex.apply(identity, {"status": "CLOSED", "filledSize": .1, "filledAmount": 10}, RULE)
        self.assertAlmostEqual(store.read()["positions"]["SOL_USDT"]["qty"], .1)
        self.assertAlmostEqual(store.read()["cash"], 90)

    def prepare_sell(self):
        _, store, api, ex = self.real()
        identity = ex.buy(signal(), 10, RULE, QUOTE)
        ex.apply(identity, {"status": "CLOSED", "filledSize": .1, "filledAmount": 10}, RULE)
        pos = store.read()["positions"]["SOL_USDT"]
        sell = ex.sell("SOL_USDT", pos["id"], RULE, QUOTE, "test")
        return store, api, ex, sell

    def test_accepted_sell_keeps_position(self):
        store, _, _, _ = self.prepare_sell()
        self.assertIn("SOL_USDT", store.read()["positions"])

    def test_partial_closed_sell_keeps_remainder(self):
        store, _, ex, identity = self.prepare_sell()
        ex.apply(identity, {"status": "CLOSED", "filledSize": .02, "filledAmount": 2}, RULE)
        self.assertAlmostEqual(store.read()["positions"]["SOL_USDT"]["qty"], .08)
        self.assertEqual(len(store.read()["trades"]), 0)

    def test_repeated_partial_fill_is_not_double_sold(self):
        store, _, ex, identity = self.prepare_sell()
        data = {"status": "OPEN", "filledSize": .02, "filledAmount": 2}
        ex.apply(identity, data, RULE)
        ex.apply(identity, data, RULE)
        self.assertAlmostEqual(store.read()["positions"]["SOL_USDT"]["qty"], .08)

    def test_sell_with_no_fill_keeps_position(self):
        store, _, ex, identity = self.prepare_sell()
        ex.apply(identity, {"status": "CLOSED", "filledSize": 0, "filledAmount": 0}, RULE)
        self.assertAlmostEqual(store.read()["positions"]["SOL_USDT"]["qty"], .1)

    def test_old_position_id_cannot_sell_new_position(self):
        self.paper_buy()
        identity = self.ex.sell("SOL_USDT", "old-id", RULE, QUOTE, "test")
        self.assertIsNone(identity)
        self.assertIn("SOL_USDT", self.store.read()["positions"])

    def test_dust_is_tracked_not_silently_deleted(self):
        self.paper_buy()
        p = self.store.read()["positions"]["SOL_USDT"]
        self.ex.sell("SOL_USDT", p["id"], RULE, QUOTE, "test")
        s = self.store.read()
        self.assertNotIn("SOL_USDT", s["positions"])
        self.assertGreater(s["dust"]["SOL_USDT"]["qty"], 0)
        self.assertEqual(len(s["trades"]), 1)

    def test_expired_signal_cannot_buy(self):
        with self.store.transaction() as s:
            s["signals"]["expired"] = {"used": False, "expires": time.time() - 1}
        self.assertIsNone(self.ex.buy(signal(), 10, RULE, QUOTE, "expired"))

    def test_used_signal_does_not_duplicate(self):
        with self.store.transaction() as s:
            s["signals"]["id"] = {"used": False, "expires": time.time() + 100}
        self.ex.buy(signal(), 10, RULE, QUOTE, "id")
        self.assertIsNone(self.ex.buy(signal(), 10, RULE, QUOTE, "id"))

    def test_corrupt_db_fails_closed(self):
        path = Path(self.tmp.name) / "corrupt"
        path.write_text("not sqlite")
        with self.assertRaises(Exception):
            Store(path, self.cfg)

    def test_rollback_does_not_lose_cash(self):
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as s:
                s["cash"] = 0
                raise RuntimeError("crash")
        self.assertEqual(self.store.read()["cash"], 100)

    def test_strategy_change_preserves_exit_positions(self):
        self.paper_buy()
        changed = Store(self.store.path, replace(self.cfg, order_cap=11))
        s = changed.read()
        self.assertIn("SOL_USDT", s["positions"])
        self.assertTrue(s["halt"])

    def test_legacy_positions_block_entries(self):
        path = Path(self.tmp.name) / "legacy.json"
        path.write_text(json.dumps({"positions": {"SOL_USDT": {"amount_coin": 1}}}))
        self.store.legacy_guard(path)
        self.assertTrue(self.store.read()["halt"])
        self.assertIsNone(self.paper_buy())


class RiskDataTests(Base):
    def test_minimum_does_not_increase_risk(self):
        r = dict(RULE, minAmount=100)
        self.assertEqual(position_size(self.store.read(), signal(), r, self.cfg, {}), 0)

    def test_trade_risk_cap(self):
        amount = position_size(self.store.read(), signal(), RULE, replace(self.cfg, order_cap=100), {})
        self.assertLessEqual(amount * (.02 + .003), .5)

    def test_daily_loss_blocks_without_hiding_positions(self):
        with self.store.transaction() as s:
            update_limits(s, 100, self.cfg, 1700000000)
            update_limits(s, 97, self.cfg, 1700000100)
        self.assertEqual(position_size(self.store.read(), signal(), RULE, self.cfg, {}), 0)

    def test_persistent_drawdown_halt(self):
        with self.store.transaction() as s:
            update_limits(s, 90, self.cfg)
        self.assertTrue(self.store.read()["halt"])

    def test_finite_decimal_floor(self):
        self.assertEqual(floor_string("0.10009999999", 4), "0.1000")
        with self.assertRaises(ValueError):
            floor_string(float("nan"), 4)

    def test_live_without_evidence_is_blocked(self):
        with patch("risk.Path.read_text", side_effect=FileNotFoundError):
            self.assertFalse(evidence_gate(replace(self.cfg, live=True))[0])

    def test_synthetic_evidence_cannot_approve(self):
        series = bars(400)
        data = {s: deepcopy(series) for s in (*self.cfg.watchlist, "BTC_USDT")}
        report = historical_report(data, {s: RULE for s in data}, self.cfg)
        self.assertFalse(report["approved"])

    def test_no_buy_when_only_oversold(self):
        self.assertFalse(analyze("SOL_USDT", bars(down=True), bars(), self.cfg)["eligible"])

    def test_missing_btc_does_not_pass(self):
        self.assertFalse(analyze("SOL_USDT", bars(), [], self.cfg)["eligible"])

    def test_candidate_is_reachable_with_default_filters(self):
        series = bars()
        for b in series:
            b["high"], b["low"] = b["close"] + 2, b["close"] - 2
        series[-1]["close"] += 3
        series[-1]["high"] = series[-1]["close"] + 2
        series[-1]["volume"] = 200
        candidate = analyze("SOL_USDT", series, deepcopy(series), self.cfg)
        self.assertTrue(candidate["eligible"])

    def test_calendar_time_alone_does_not_validate_forward(self):
        from forward_report import report
        with self.store.transaction() as s:
            s["created"] = time.time() - 100 * 86400
        result = report(self.store.path, self.cfg)
        self.assertFalse(result["approved"])
        self.assertEqual(result["observed_days"], 0)

    def test_candle_closure_uses_clock(self):
        rows = [dict(b, time=b["time"] * 1000) for b in bars(3)]
        self.assertEqual(len(validate_candles(rows, now=10800)), 3)
        self.assertEqual(len(validate_candles(rows, now=9000)), 2)

    def test_candle_gap_rejected(self):
        rows = [dict(b, time=b["time"] * 1000) for b in bars(4)]
        with self.assertRaises(ValueError):
            validate_candles([rows[0], rows[2], rows[3]], now=14400)

    def test_stale_candle_rejected(self):
        rows = [dict(b, time=b["time"] * 1000) for b in bars(3)]
        with self.assertRaises(ValueError):
            validate_candles(rows, now=36000)

    def test_unknown_fee_currency_rejected(self):
        with self.assertRaises(ValueError):
            parse_fill({"status": "CLOSED", "filledSize": 1, "filledAmount": 100, "fee": .1, "feeCoin": "OTHER"}, "SOL")

    def test_api_amount_precision_not_hardcoded(self):
        http = Mock()
        http.request.return_value = {"result": True, "data": {"orderId": 1}}
        api = Pionex(self.cfg, http)
        api.clock_checked = time.monotonic()
        api.submit({"id": "abc", "symbol": "SOL_USDT", "side": "BUY", "amount": 1.23456789}, dict(RULE, amountPrecision=6))
        body = json.loads(http.request.call_args.kwargs["body"])
        self.assertEqual(body["amount"], "1.234567")

    def test_unknown_exchange_error_is_ambiguous(self):
        http = Mock()
        http.request.return_value = {"result": False, "code": "NEW_INTERNAL_ERROR"}
        api = Pionex(self.cfg, http)
        api.clock_checked = time.monotonic()
        with self.assertRaises(APIError) as caught:
            api.submit({"id": "abc", "symbol": "SOL_USDT", "side": "BUY", "amount": 10}, RULE)
        self.assertTrue(caught.exception.ambiguous)

    def test_explicit_order_rejection_is_definitive(self):
        http = Mock()
        http.request.return_value = {"result": False, "code": "TRADE_NOT_ENOUGH_MONEY"}
        api = Pionex(self.cfg, http)
        api.clock_checked = time.monotonic()
        with self.assertRaises(APIError) as caught:
            api.submit({"id": "abc", "symbol": "SOL_USDT", "side": "BUY", "amount": 10}, RULE)
        self.assertFalse(caught.exception.ambiguous)

    def test_market_rules_use_market_sell_minimum(self):
        api = Mock()
        row = dict(RULE, symbol="SOL_USDT", minTradeSize="999", minTradeDumping="0.002")
        api.public.return_value = {"symbols": [row]}
        market = Market(api, self.cfg)
        self.assertEqual(market.load_rules()["SOL_USDT"]["minTradeDumping"], .002)

    def test_failed_refresh_clears_old_signal(self):
        api = types.SimpleNamespace(candles=Mock(side_effect=APIError("offline")))
        tg = types.SimpleNamespace(send=Mock())
        radar = Radar(self.cfg, self.store, api, self.ex, tg)
        self.addCleanup(radar.pool.shutdown)
        radar.snapshot = {"SOL_USDT": signal()}
        radar.refresh()
        self.assertEqual(radar.snapshot, {})

    def test_health_503_when_worker_has_no_data(self):
        r = types.SimpleNamespace(cfg=self.cfg, heartbeat=time.time(), error="sin datos", last_market_success=0)
        server = health_server(r, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=2)
            self.assertEqual(caught.exception.code, 503)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_next_bar_fill_has_no_lookahead(self):
        series = bars(340)
        for i in range(300, 340, 5):
            series[i]["volume"] = 200
        for b in series:
            b["open"] = b["close"] - .2
        data = {s: deepcopy(series) for s in (*self.cfg.watchlist, "BTC_USDT")}
        cfg = replace(self.cfg, min_net_rr=0, min_stop=.002)
        result = simulate(data, {s: RULE for s in data}, cfg)
        self.assertTrue(result["trade_log"], "El test debe incluir operaciones; no puede aprobar por muestra vacía")
        for t in result["trade_log"]:
            self.assertEqual(t["entry_bar"] - t["signal_bar"], 3600)

    def test_intrabar_ambiguity_uses_stop_first(self):
        cfg = replace(self.cfg, watchlist=("SOL_USDT",))
        series = bars(302)
        series[300].update(low=140, high=180)
        data = {"SOL_USDT": deepcopy(series), "BTC_USDT": deepcopy(series)}
        def candidate(symbol, history, btc, config):
            return dict(signal(symbol), price=history[-1]["close"], bar=history[-1]["time"], atr=5, breakout_level=150)
        with patch("backtest.analyze", side_effect=candidate):
            result = simulate(data, {s: RULE for s in data}, cfg)
        self.assertEqual(result["closed_trades"][0]["reason"], "stop")
        self.assertLess(result["closed_trades"][0]["pnl"], 0)

    def test_insufficient_book_liquidity_blocks_buy(self):
        api = Mock()
        api.public.return_value = {"asks": [["100", "0.01"]]}
        self.assertFalse(Market(api, self.cfg).entry_liquidity("SOL_USDT", 10, QUOTE))

    def test_price_impact_above_budget_blocks_buy(self):
        api = Mock()
        api.public.return_value = {"asks": [["110", "10"]]}
        self.assertFalse(Market(api, self.cfg).entry_liquidity("SOL_USDT", 10, QUOTE))

    def test_fresh_ample_book_passes(self):
        api = Mock()
        api.public.return_value = {"asks": [["100.01", "10"]]}
        self.assertTrue(Market(api, self.cfg).entry_liquidity("SOL_USDT", 10, QUOTE))

    def test_missing_quote_timestamp_fails(self):
        api = Mock()
        api.public.return_value = {"tickers": [{"symbol": "SOL_USDT", "bidPrice": "100", "askPrice": "100.01"}]}
        with self.assertRaises(KeyError):
            Market(api, self.cfg).quote("SOL_USDT")

    def test_no_network_on_import(self):
        import importlib
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network on import")):
            for name in ("main", "backtest", "download_history", "forward_report"):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
