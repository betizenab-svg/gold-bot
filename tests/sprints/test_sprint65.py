"""Milestone 10: safety and upkeep (items 93-97)."""

from __future__ import annotations

import subprocess
import sys

from config.validate import check
from scripts.secret_scan import scan_text


def test_settings_check_catches_typos():
    problems = check({"RISK_PER_TRADE_PCT": "one", "TP1_R": "3", "TP2_R": "2",
                      "ENTRY_MODE": "maybe", "TELEGRAM_VIP_CHAT_ID": "abc",
                      "SYMBOLS": "XAUUSD,NOPE"})
    text = "\n".join(problems)
    for word in ("RISK_PER_TRADE_PCT", "TP1_R", "ENTRY_MODE", "TELEGRAM_VIP_CHAT_ID", "NOPE"):
        assert word in text


def test_settings_check_accepts_good_values():
    assert check({"RISK_PER_TRADE_PCT": "1", "TP1_R": "1.5", "TP2_R": "3", "ENTRY_MODE": "limit",
                  "TELEGRAM_VIP_CHAT_ID": "-1001234567890", "PARTNER_CHAT_IDS": "@a,-100",
                  "SYMBOLS": "XAUUSD,BTCUSD", "WEEKEND_ACTION": "breakeven"}) == []


def test_secret_scan_masks_and_finds():
    fake = "TOKEN 123456789:AA" + "x" * 33 + " TWELVEDATA_API_KEY=" + "ab12" * 8
    found = scan_text("f", fake)
    assert len(found) == 2 and all("hidden" in f for f in found)
    assert "x" * 10 not in " ".join(found)


def test_repository_has_no_secrets_and_no_compiled_files():
    result = subprocess.run([sys.executable, "scripts/secret_scan.py"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout
    assert ".pyc" not in tracked
