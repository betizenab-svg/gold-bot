"""Fail if a real-looking secret is about to be committed.

    python scripts/secret_scan.py            # scan files tracked by git
    python scripts/secret_scan.py --history  # also scan every past commit

Values are never printed in full - only the file and a masked preview.
"""

from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = {
    "Telegram bot token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"),
    "Cloudflare/API token": re.compile(r"(?i)(?:api[_-]?key|api[_-]?token|secret)\s*[=:]\s*['\"]?([0-9a-f]{32,})\b"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
SKIP = re.compile(r"\.(?:db|png|jpg|jpeg|gif|pdf|ico|woff2?|ttf|zip|json)$|^scripts/secret_scan\.py$")


def _mask(text: str) -> str:
    return text[:6] + "…(hidden)"


def scan_text(name: str, text: str) -> list[str]:
    findings = []
    for label, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(f"{name}: {label} {_mask(match.group(0))}")
    return findings


def scan_tracked() -> list[str]:
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    findings: list[str] = []
    for name in files:
        if SKIP.search(name):
            continue
        try:
            with open(name, encoding="utf-8", errors="ignore") as handle:
                findings += scan_text(name, handle.read())
        except OSError:
            continue
    return findings


def scan_history() -> list[str]:
    log = subprocess.run(
        ["git", "log", "-p", "--all", "--no-color"], capture_output=True, text=True, errors="ignore"
    ).stdout
    added = "\n".join(line for line in log.splitlines() if line.startswith("+"))
    return scan_text("git history", added)


def main() -> int:
    findings = scan_tracked()
    if "--history" in sys.argv:
        findings += scan_history()
    for line in sorted(set(findings)):
        print(line)
    if findings:
        print("Possible secrets found. Remove them and change (rotate) the key.")
        return 1
    print("No secrets found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
