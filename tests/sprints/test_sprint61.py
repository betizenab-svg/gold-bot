"""Sprint 61 (roadmap milestone 6): the new signal card, Ethiopian time,
Amharic wording, the morning briefing, pause notices, the weekly results card
and lesson, and the public track record the command bot reads."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from config import settings
from src.alerting.formatter import SignalFormatter, signal_code
from src.alerting.i18n import t, why_this_trade
from src.alerting.public_posts import (
    build_morning_briefing,
    lesson_for_week,
    render_weekly_card,
    results_week_start,
    weekly_caption,
    weekly_results,
)
from src.alerting.timefmt import eat_time
from src.domain.signal import Signal
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def _ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def _repo(tmp_path: Path, name: str = "m6.db") -> Repository:
    connection = sqlite3.connect(str(tmp_path / name))
    SchemaInitializer(connection).initialize()
    return Repository(connection)


def _signal(**overrides) -> Signal:
    values = dict(
        symbol="XAUUSD", signal_type="LONG", entry_price=4196.4, sl_price=4187.86, tp1_price=4209.21,
        tp2_price=4222.02, score=82, reasoning="x", timestamp=_ts(2026, 10, 6, 13, 0), signal_hash="h1",
        strategy="INSIDE_BAR_TRAP", id=142, risk_pct=1.0,
    )
    values.update(overrides)
    return Signal(**values)


# --- The signal card -------------------------------------------------------------------

def test_card_has_code_copyable_prices_money_and_reason() -> None:
    card = SignalFormatter().format_initial_signal(_signal())
    assert "<b>BUY Gold</b> (XAUUSD) \u00b7 <b>#G142</b>" in card
    for line in ("Entry @ <code>4196.40</code>", "Stop @ <code>4187.86</code>",
                 "Target 1 @ <code>4209.21</code>", "Target 2 @ <code>4222.02</code>"):
        assert line in card
    assert "-85 pips = $8.54 per 0.01 lot" in card
    assert "Reward to risk: 1.5 : 1 \u2192 3.0 : 1" in card
    assert "trapped sellers" in card
    assert f"Cancel it if it has not opened by {eat_time(_signal().timestamp + 90 * 60)}" in card
    assert "$100 \u2192 0.01 \u26a0 8.5%" in card and "EAT" in card


def test_market_orders_and_trials_read_differently() -> None:
    card = SignalFormatter().format_initial_signal(_signal(order_type="MARKET", status="ACTIVE"))
    assert "Buy now at the market price." in card and "Cancel it" not in card
    sell = SignalFormatter().format_initial_signal(
        _signal(signal_type="SHORT", sl_price=4205.0, tp1_price=4183.5, tp2_price=4170.6, trial=True)
    )
    assert "<b>SELL Gold</b>" in sell and "TRIAL" in sell
    assert signal_code({"symbol": "EURUSD", "id": 7}) == "#E7"
    assert signal_code({"symbol": "EURUSD"}) == ""


def test_amharic_wording_can_be_switched_on(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_LANGUAGE", "am")
    card = SignalFormatter().format_initial_signal(_signal())
    assert "ግዛ" in card and "መግቢያ @ <code>4196.40</code>" in card
    monkeypatch.setenv("MESSAGE_LANGUAGE", "both")
    assert t("entry") == "Entry / መግቢያ"
    assert "\n" in t("order_limit_buy")  # long sentences: English line, then Amharic line
    monkeypatch.setenv("MESSAGE_LANGUAGE", "en")
    assert why_this_trade("NO_SUCH", "LONG").startswith("The trend")


def test_lifecycle_replies_use_ethiopian_time() -> None:
    _alert, explanation = SignalFormatter().format_lifecycle_update("TP1_SMASH", "Price hit TP1 at 4209.21.")
    assert "<b>Reason:</b>" in explanation and "EAT" in explanation and "UTC" not in explanation


# --- Morning briefing, pauses, weekly posts -----------------------------------------------------

def test_morning_briefing(tmp_path: Path) -> None:
    from src.domain.candle import Candle

    repository = _repo(tmp_path)
    monday = _ts(2026, 10, 5)
    repository.save_candles(
        [Candle("XAUUSD", "M5", monday + 300 * i, 4100 + i * 0.1, 4101 + i * 0.1, 4099 + i * 0.1, 4100.5 + i * 0.1, 0)
         for i in range(288)]
    )
    news_time = _ts(2026, 10, 6, 12, 30)  # 15:30 in Ethiopia
    repository.set_kv("upcoming_news_events_json", json.dumps(
        [{"timestamp": news_time, "label": "US CPI", "currency": "USD"},
         {"timestamp": news_time, "label": "Japan data", "currency": "JPY"}]
    ))
    repository.set_kv("current_structure_state", "BULLISH")
    text = build_morning_briefing(repository, _ts(2026, 10, 6, 4, 5))
    assert "Good morning" in text and "Tue 6 Oct 2026" in text
    assert "high <code>4129.70</code>" in text and "low <code>4099.00</code>" in text
    assert "15:30 US CPI (USD)" in text
    assert "buyers in control" in text
    repository.close()


def test_news_pause_is_posted_once_per_release_time(tmp_path: Path, monkeypatch) -> None:
    from src.core.orchestrator import PulseOrchestrator

    repository = _repo(tmp_path)
    monkeypatch.setattr(settings, "PAUSE_NOTICES_ENABLED", True)
    soon = int(time.time()) + 20 * 60
    repository.set_kv("upcoming_news_events_json", json.dumps(
        [{"timestamp": soon, "label": "US CPI", "currency": "USD"},
         {"timestamp": soon, "label": "US core CPI", "currency": "USD"},
         {"timestamp": soon + 7200, "label": "later", "currency": "USD"}]
    ))
    telegram = MagicMock()
    telegram.chat_id = "public"
    telegram.send_message.return_value = 1
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    orchestrator._maybe_post_news_pauses(repository)
    orchestrator._maybe_post_news_pauses(repository)
    assert telegram.send_message.call_count == 1
    text = telegram.send_message.call_args.args[0]
    assert "No new trades" in text and "US CPI + US core CPI" in text and "EAT" in text
    repository.close()


def test_brake_notice_once_and_never_for_trials(tmp_path: Path, monkeypatch) -> None:
    from src.core.orchestrator import PulseOrchestrator

    repository = _repo(tmp_path)
    monkeypatch.setattr(settings, "PAUSE_NOTICES_ENABLED", True)
    telegram = MagicMock()
    telegram.chat_id = "public"
    telegram.send_message.return_value = 1
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    reason = "Risk governor: daily loss limit reached (-3.00R); no more signals today"
    orchestrator._maybe_post_brake_notice(repository, reason, "XAUUSD", {"strategy": "PIN_BAR"})
    orchestrator._maybe_post_brake_notice(repository, reason, "XAUUSD", {"strategy": "PIN_BAR"})
    orchestrator._maybe_post_brake_notice(repository, reason, "US100", {"strategy": "PIN_BAR"})
    assert telegram.send_message.call_count == 1
    assert "No more signals today" in telegram.send_message.call_args.args[0]
    repository.close()


def test_weekly_results_card(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    monday = _ts(2026, 9, 28)
    for index, value in enumerate([2.25, -1.0, 0.0, 0.75, -1.0]):
        repository.save_signal(_signal(signal_hash=f"w{index}", id=None, timestamp=monday + index * 86400))
        repository.update_signal_closure(f"w{index}", "x", "CLOSED_SL" if value < 0 else "CLOSED_TP2",
                                         realized_r=value, closed_at=monday + index * 86400 + 3600)
    trial = _signal(signal_hash="trial", id=None, timestamp=monday, trial=True)
    repository.save_signal(trial)
    repository.update_signal_closure("trial", "x", "CLOSED_TP2", realized_r=5.0, closed_at=monday + 7200)
    assert results_week_start(_ts(2026, 10, 3, 9)) == monday  # Saturday: the week just ended
    stats = weekly_results(repository, monday)
    assert (stats["trades"], stats["wins"], stats["losses"], stats["flat"], stats["net_r"]) == (5, 2, 2, 1, 1.0)
    caption = weekly_caption(stats)
    assert "+1.00R" in caption and "2 lost" in caption and "losses included" in caption
    png = render_weekly_card(stats, size=(540, 540))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert "No finished trades" in weekly_caption(weekly_results(repository, monday + 14 * 86400))
    repository.close()


def test_weekly_lessons_rotate() -> None:
    assert lesson_for_week(1) != lesson_for_week(2)
    assert lesson_for_week(1) == lesson_for_week(17)


# --- Public track record -------------------------------------------------------------------------

def test_ledger_is_append_only_and_hash_chained(tmp_path: Path) -> None:
    from scripts.publish_site_data import main as publish, verify_ledger

    repository = _repo(tmp_path, "live.db")
    repository.save_signal(_signal(signal_hash="a", id=None))
    repository.save_signal(_signal(signal_hash="secret-trial", id=None, trial=True))
    repository.close()
    out = tmp_path / "site"
    publish(["--db", str(tmp_path / "live.db"), "--out", str(out)])
    first = (out / "ledger.jsonl").read_text().splitlines()
    assert len(first) == 1 and json.loads(first[0])["backfilled"] is True

    repository = Repository(sqlite3.connect(str(tmp_path / "live.db")))
    repository.update_signal_closure("a", "x", "CLOSED_TP2", realized_r=2.25, closed_at=_ts(2026, 10, 6, 15))
    repository.save_signal(_signal(signal_hash="b", id=None, timestamp=_ts(2026, 10, 7, 9)))
    repository.close()
    publish(["--db", str(tmp_path / "live.db"), "--out", str(out)])
    lines = (out / "ledger.jsonl").read_text().splitlines()
    assert lines[0] == first[0]  # never rewritten
    events = [json.loads(line) for line in lines]
    assert [(e["code"], e["event"], e["backfilled"]) for e in events] == [
        ("#G1", "OPEN", True), ("#G1", "CLOSE", False), ("#G3", "OPEN", False)
    ]
    assert events[1]["result_r"] == 2.25 and verify_ledger(out / "ledger.jsonl")
    assert all("secret-trial" not in line and e["id"] != 2 for line, e in zip(lines, events))

    public = json.loads((out / "public.json").read_text())
    assert public["totals"]["trades"] == 1 and public["totals"]["net_r"] == 2.25
    assert public["totals"]["net_r_after_costs"] < 2.25
    assert [o["code"] for o in public["open"]] == ["#G3"]
    stamp = public["updated"]
    publish(["--db", str(tmp_path / "live.db"), "--out", str(out)])
    assert json.loads((out / "public.json").read_text())["updated"] == stamp  # no change, no rewrite

    tampered = lines[:]
    tampered[0] = tampered[0].replace("4196.4", "4190.0")
    (out / "ledger.jsonl").write_text("\n".join(tampered) + "\n")
    assert not verify_ledger(out / "ledger.jsonl")


def test_signal_card_carries_its_number(tmp_path: Path) -> None:
    from src.core.orchestrator import PulseOrchestrator
    from src.domain.candle import Candle

    repository = _repo(tmp_path)
    telegram = MagicMock()
    telegram.chat_id = "public"
    telegram.send_message.return_value = 11
    orchestrator = PulseOrchestrator(telegram_client_factory=lambda: telegram)
    candles = [Candle("XAUUSD", "M5", _ts(2026, 10, 7, 9) + 300 * i, 4196.0, 4199.0, 4193.0, 4197.0, 0) for i in range(20)]
    setup = {"trade_direction": "LONG", "strategy": "PIN_BAR_REJECTION", "entry_price": 4196.4, "sl_price": 4187.86}
    orchestrator._persist_actionable_signal(repository, setup, candles, candles[-1], 82)
    assert "#G1" in telegram.send_message.call_args_list[0].args[0]
    repository.close()
