from __future__ import annotations

import html
from typing import Any


def build_weekly_report(analysis: dict[str, Any]) -> str:
    """Telegram HTML message summarizing realized performance and the
    evidence-based tuning recommendations. Posted automatically."""
    strategies = analysis.get("strategies", {})
    recommendations = analysis.get("recommendations", [])

    total_trades = sum(int(s.get("trades", 0)) for s in strategies.values())
    net_r = sum(
        float(s.get("expectancy_r", 0.0)) * int(s.get("trades", 0))
        for s in strategies.values()
    )

    lines = [
        "📊 <b>Weekly Performance Report</b>",
        f"Closed trades: <b>{total_trades}</b> | Net result: <b>{net_r:+.2f}R</b>",
        "",
        "<b>Per strategy (realized)</b>",
    ]
    for name, stats in strategies.items():
        profit_factor = stats.get("profit_factor")
        pf_text = f"{profit_factor:.2f}" if isinstance(profit_factor, (int, float)) else "inf"
        lines.append(
            f"• <code>{html.escape(str(name))}</code>: "
            f"{stats.get('trades', 0)} trades, "
            f"expectancy {float(stats.get('expectancy_r', 0.0)):+.2f}R, "
            f"PF {pf_text}"
        )

    if recommendations:
        lines.append("")
        lines.append("<b>What the evidence says</b>")
        for recommendation in recommendations:
            lines.append(f"• {html.escape(str(recommendation))}")

    lines.append("")
    lines.append("<i>Every number above is computed from recorded, timestamped trades.</i>")
    return "\n".join(lines)
