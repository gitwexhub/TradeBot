from datetime import datetime, timedelta, timezone

import pytest

from src.strategy.signals import SignalEvaluator


def make_market(
    ticker="TEST-A",
    yes_ask=75,
    no_ask=27,
    hours_ahead=24.0,
    status="open",
):
    close_time = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    return {
        "ticker": ticker,
        "title": f"Test market {ticker}",
        "status": status,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "close_time": close_time.isoformat(),
    }


def test_yes_signal(strategy_config):
    ev = SignalEvaluator(strategy_config)
    market = make_market(yes_ask=75, no_ask=27, hours_ahead=12)
    sig = ev._evaluate_one(market)
    assert sig is not None
    assert sig.side == "yes"
    assert sig.price_cents == 75
    assert sig.contracts == 13  # floor(1000 / 75)
    assert sig.cost_cents == 13 * 75


def test_no_signal(strategy_config):
    ev = SignalEvaluator(strategy_config)
    market = make_market(yes_ask=25, no_ask=77, hours_ahead=12)
    sig = ev._evaluate_one(market)
    assert sig is not None
    assert sig.side == "no"
    assert sig.price_cents == 77


def test_no_signal_for_midrange(strategy_config):
    ev = SignalEvaluator(strategy_config)
    market = make_market(yes_ask=55, no_ask=47, hours_ahead=12)
    sig = ev._evaluate_one(market)
    assert sig is None


def test_no_signal_outside_window(strategy_config):
    ev = SignalEvaluator(strategy_config)
    market = make_market(yes_ask=80, hours_ahead=72)  # beyond 48h window
    sig = ev._evaluate_one(market)
    assert sig is None


def test_skips_closed_market(strategy_config):
    ev = SignalEvaluator(strategy_config)
    market = make_market(yes_ask=80, status="closed")
    sig = ev._evaluate_one(market)
    assert sig is None


def test_skips_invalid_price(strategy_config):
    ev = SignalEvaluator(strategy_config)
    market = make_market(yes_ask=0, no_ask=100)
    sig = ev._evaluate_one(market)
    assert sig is None


def test_respects_position_limit(strategy_config):
    ev = SignalEvaluator(strategy_config)
    markets = [make_market(ticker=f"MKT-{i}", yes_ask=80, no_ask=22) for i in range(10)]
    signals = ev.evaluate(markets, held_tickers=set(), current_open_count=3)
    assert len(signals) <= 2  # 5 max - 3 open = 2 slots


def test_skips_held_tickers(strategy_config):
    ev = SignalEvaluator(strategy_config)
    markets = [make_market(ticker="HELD-1", yes_ask=80, no_ask=22)]
    signals = ev.evaluate(markets, held_tickers={"HELD-1"}, current_open_count=0)
    assert len(signals) == 0


def test_confidence_higher_closer_to_resolution(strategy_config):
    ev = SignalEvaluator(strategy_config)
    m_far = make_market(ticker="FAR", yes_ask=80, hours_ahead=47)
    m_near = make_market(ticker="NEAR", yes_ask=80, hours_ahead=1)
    sig_far = ev._evaluate_one(m_far)
    sig_near = ev._evaluate_one(m_near)
    assert sig_near.confidence > sig_far.confidence


def test_signals_sorted_by_confidence(strategy_config):
    ev = SignalEvaluator(strategy_config)
    markets = [
        make_market(ticker="A", yes_ask=71, hours_ahead=47),
        make_market(ticker="B", yes_ask=85, hours_ahead=2),
        make_market(ticker="C", yes_ask=90, hours_ahead=10),
    ]
    signals = ev.evaluate(markets, held_tickers=set(), current_open_count=0)
    confidences = [s.confidence for s in signals]
    assert confidences == sorted(confidences, reverse=True)
