from __future__ import annotations

import io
import logging
from typing import Any, List, Optional

from src.domain.candle import Candle

CHART_CANDLES = 60
BULL_COLOR = "#26a69a"
BEAR_COLOR = "#ef5350"
BACKGROUND = "#131722"
PANEL = "#1e222d"
TEXT = "#d1d4dc"
GRID = "#2a2e39"


class ChartRenderer:
    """Render a dark-theme candlestick PNG with entry/SL/TP levels and the
    triggering zone for Telegram alerts. Fully headless (Agg backend)."""

    def render_signal_chart(
        self,
        candles: List[Candle],
        signal: Any,
        zone: Optional[dict[str, Any]] = None,
    ) -> Optional[bytes]:
        if not isinstance(candles, list) or len(candles) < 2:
            return None

        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception as exc:
            logging.info("Chart rendering unavailable (matplotlib): %s", exc)
            return None

        try:
            window = candles[-CHART_CANDLES:]
            entry = float(getattr(signal, "entry_price", 0.0))
            sl = float(getattr(signal, "sl_price", 0.0))
            tp1 = float(getattr(signal, "tp1_price", 0.0))
            tp2 = float(getattr(signal, "tp2_price", 0.0))
            direction = str(getattr(signal, "signal_type", "")).upper()
            order_type = str(getattr(signal, "order_type", "LIMIT")).upper()
            strategy = str(getattr(signal, "strategy", None) or "SMC").replace("_", " ").title()
            score = getattr(signal, "score", "")

            fig, ax = plt.subplots(figsize=(10, 6), dpi=140)
            fig.patch.set_facecolor(BACKGROUND)
            ax.set_facecolor(PANEL)

            for index, candle in enumerate(window):
                open_p = float(candle.open)
                close_p = float(candle.close)
                color = BULL_COLOR if close_p >= open_p else BEAR_COLOR
                ax.plot(
                    [index, index],
                    [float(candle.low), float(candle.high)],
                    color=color,
                    linewidth=0.9,
                    zorder=2,
                )
                body_bottom = min(open_p, close_p)
                body_height = max(abs(close_p - open_p), 0.01)
                ax.bar(
                    index,
                    body_height,
                    bottom=body_bottom,
                    width=0.65,
                    color=color,
                    edgecolor=color,
                    linewidth=0.5,
                    zorder=3,
                )

            if zone:
                try:
                    zone_top = float(zone.get("price_top"))
                    zone_bottom = float(zone.get("price_bottom"))
                    ax.axhspan(
                        zone_bottom,
                        zone_top,
                        color="#f5b942",
                        alpha=0.12,
                        zorder=1,
                    )
                except (TypeError, ValueError):
                    pass

            levels = (
                (entry, "#4da6ff", f"ENTRY {entry:.2f}"),
                (sl, BEAR_COLOR, f"SL {sl:.2f}"),
                (tp1, BULL_COLOR, f"TP1 {tp1:.2f}"),
                (tp2, BULL_COLOR, f"TP2 {tp2:.2f}"),
            )
            last_index = len(window) - 1
            for price, color, label in levels:
                if price <= 0:
                    continue
                # Lines end before the label gutter so text never overlaps dashes.
                ax.hlines(
                    price,
                    xmin=-1,
                    xmax=last_index + 1.4,
                    color=color,
                    linewidth=1.1,
                    linestyle="--",
                    zorder=4,
                )
                ax.annotate(
                    label,
                    xy=(last_index + 1.9, price),
                    xytext=(last_index + 1.9, price),
                    color=color,
                    fontsize=9,
                    fontweight="bold",
                    va="center",
                    annotation_clip=False,
                )

            marker = "^" if direction == "LONG" else "v"
            marker_color = BULL_COLOR if direction == "LONG" else BEAR_COLOR
            ax.scatter(
                [last_index],
                [entry],
                marker=marker,
                s=160,
                color=marker_color,
                edgecolors="white",
                linewidths=0.8,
                zorder=5,
            )

            all_prices = (
                [float(c.low) for c in window]
                + [float(c.high) for c in window]
                + [p for p in (entry, sl, tp1, tp2) if p > 0]
            )
            pad = (max(all_prices) - min(all_prices)) * 0.06 or 1.0
            ax.set_ylim(min(all_prices) - pad, max(all_prices) + pad)
            ax.set_xlim(-1, last_index + 9)

            ax.grid(color=GRID, linewidth=0.5, alpha=0.6)
            ax.tick_params(colors=TEXT, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(GRID)
            ax.set_xticks([])

            symbol = str(getattr(signal, "symbol", "XAUUSD"))
            timeframe = str(window[-1].timeframe)
            ax.set_title(
                f"{symbol} {timeframe}  |  {direction} {order_type}  |  {strategy}  |  Score {score}",
                color=TEXT,
                fontsize=11,
                fontweight="bold",
                loc="left",
                pad=10,
            )

            buffer = io.BytesIO()
            fig.savefig(
                buffer,
                format="png",
                bbox_inches="tight",
                facecolor=BACKGROUND,
            )
            plt.close(fig)
            return buffer.getvalue()
        except Exception as exc:
            logging.info("Chart rendering failed: %s", exc)
            try:
                plt.close("all")
            except Exception:
                pass
            return None
