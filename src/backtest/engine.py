from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.analysis.position_sizing import LotSizeCalculator
from src.analysis.scoring import ScoringEngine
from src.analysis.signal_factory import SignalFactory
from src.domain.candle import Candle
from src.domain.signal import Signal
from src.strategies.big_bulls_bears import BigBullsBearsStrategy


@dataclass
class SimulatedTrade:
    signal: Signal
    status: str
    risk_pips: float = 0.0
    risk_amount: float = 0.0
    realized_pnl_pips: float = 0.0
    realized_pnl_usd: float = 0.0
    activated_timestamp: Optional[int] = None
    closed_timestamp: Optional[int] = None
    outcome: Optional[str] = None


class BacktestEngine:
    """Offline sequential backtester using production strategy and scoring logic."""

    def __init__(
        self,
        candles: list[Candle],
        strategy: Optional[Any] = None,
        scoring_engine: Optional[ScoringEngine] = None,
        initial_balance: float = 10000.0,
    ) -> None:
        self.candles = list(candles)
        self.strategy = strategy or BigBullsBearsStrategy()
        self.scoring_engine = scoring_engine or ScoringEngine()
        self.signal_factory = SignalFactory()
        self.lot_size_calculator = LotSizeCalculator()
        self.minimum_window = max(getattr(self.strategy, "TREND_PERIOD", 200), 200)
        self.open_trades: list[SimulatedTrade] = []
        self.trade_history: list[SimulatedTrade] = []
        self.current_balance = float(initial_balance)
        self._seen_setup_keys: set[str] = set()

    def run_simulation(self) -> None:
        if not self.candles:
            return

        for index, current_candle in enumerate(self.candles):
            self._process_open_trades(current_candle)

            if index + 1 < self.minimum_window:
                continue

            if self._has_open_trade():
                continue

            window = self.candles[: index + 1]
            setup = self.strategy.detect_setup(window)
            if setup is None:
                continue

            setup_key = self._build_setup_key(setup)
            if setup_key in self._seen_setup_keys:
                continue

            score, classification = self._score_setup(window, setup)
            if classification == "REJECTED":
                continue

            signal = self._build_signal_from_setup(setup, score, current_candle)
            self.open_trades.append(
                SimulatedTrade(
                    signal=signal,
                    status="PENDING",
                )
            )
            self._seen_setup_keys.add(setup_key)

    def generate_report(self) -> dict[str, float]:
        total_trades = len(self.trade_history)
        winning_trades = sum(1 for trade in self.trade_history if trade.realized_pnl_usd > 0)
        win_rate = (winning_trades / total_trades * 100.0) if total_trades else 0.0
        total_pnl_pips = sum(trade.realized_pnl_pips for trade in self.trade_history)

        report = {
            "total_trades": int(total_trades),
            "win_rate_pct": round(win_rate, 2),
            "total_pnl_pips": round(total_pnl_pips, 2),
            "final_balance": round(self.current_balance, 2),
        }

        print(f"Total Trades: {report['total_trades']}")
        print(f"Win Rate (%): {report['win_rate_pct']:.2f}")
        print(f"Total PnL (pips): {report['total_pnl_pips']:.2f}")
        print(f"Final Balance: {report['final_balance']:.2f}")
        return report

    def _process_open_trades(self, current_candle: Candle) -> None:
        remaining_trades: list[SimulatedTrade] = []

        for trade in self.open_trades:
            event_type = self._evaluate_trade(trade, current_candle)
            if event_type is None:
                remaining_trades.append(trade)
                continue

            if event_type == "ACTIVATED":
                trade.status = "ACTIVE"
                trade.activated_timestamp = int(current_candle.timestamp)
                trade.risk_pips = self.lot_size_calculator.calculate_pips(
                    trade.signal.entry_price,
                    trade.signal.sl_price,
                )
                trade.risk_amount = round(self.current_balance * 0.02, 2)
                remaining_trades.append(trade)
                continue

            self._apply_trade_event(trade, event_type, current_candle)
            if trade.status.startswith("CLOSED_"):
                self.trade_history.append(trade)
            else:
                remaining_trades.append(trade)

        self.open_trades = remaining_trades

    def _evaluate_trade(self, trade: SimulatedTrade, current_candle: Candle) -> Optional[str]:
        signal = trade.signal
        status = trade.status
        direction = signal.signal_type.upper()

        if direction == "LONG":
            if status == "PENDING" and float(current_candle.low) <= float(signal.entry_price):
                return "ACTIVATED"
            if status == "ACTIVE" and float(current_candle.high) >= float(signal.tp1_price):
                return "TP1_SMASH"
            if status in {"ACTIVE", "PARTIAL_TP1"} and float(current_candle.high) >= float(
                signal.tp2_price
            ):
                return "TP2_SMASH"
            if status in {"ACTIVE", "PARTIAL_TP1"} and float(current_candle.low) <= float(
                signal.sl_price
            ):
                return "SL_HIT"
            return None

        if direction == "SHORT":
            if status == "PENDING" and float(current_candle.high) >= float(signal.entry_price):
                return "ACTIVATED"
            if status == "ACTIVE" and float(current_candle.low) <= float(signal.tp1_price):
                return "TP1_SMASH"
            if status in {"ACTIVE", "PARTIAL_TP1"} and float(current_candle.low) <= float(
                signal.tp2_price
            ):
                return "TP2_SMASH"
            if status in {"ACTIVE", "PARTIAL_TP1"} and float(current_candle.high) >= float(
                signal.sl_price
            ):
                return "SL_HIT"
            return None

        return None

    def _apply_trade_event(
        self,
        trade: SimulatedTrade,
        event_type: str,
        current_candle: Candle,
    ) -> None:
        signal = trade.signal
        previous_status = trade.status
        tp1_pips = abs(float(signal.tp1_price) - float(signal.entry_price)) * 10.0
        tp2_pips = abs(float(signal.tp2_price) - float(signal.entry_price)) * 10.0

        if event_type == "TP1_SMASH":
            trade.status = "PARTIAL_TP1"
            trade.realized_pnl_pips += round(tp1_pips * 0.5, 2)
            trade.realized_pnl_usd += round(trade.risk_amount * 0.75, 2)
            self.current_balance = round(self.current_balance + (trade.risk_amount * 0.75), 2)
            trade.outcome = "PARTIAL_TP1"
            return

        if event_type == "TP2_SMASH":
            trade.status = "CLOSED_TP2"
            trade.closed_timestamp = int(current_candle.timestamp)
            trade.realized_pnl_pips += round(tp2_pips * 0.5, 2)
            trade.realized_pnl_usd += round(trade.risk_amount * 1.5, 2)
            self.current_balance = round(self.current_balance + (trade.risk_amount * 1.5), 2)
            trade.outcome = "WIN"
            return

        if event_type == "SL_HIT":
            trade.status = "CLOSED_SL"
            trade.closed_timestamp = int(current_candle.timestamp)
            if previous_status == "PARTIAL_TP1":
                sl_pips = trade.risk_pips * 0.5
                sl_usd = trade.risk_amount * 0.5
            else:
                sl_pips = trade.risk_pips
                sl_usd = trade.risk_amount

            trade.realized_pnl_pips -= round(sl_pips, 2)
            trade.realized_pnl_usd -= round(sl_usd, 2)
            self.current_balance = round(self.current_balance - sl_usd, 2)
            trade.outcome = "WIN" if trade.realized_pnl_usd > 0 else "LOSS"
            return

        raise ValueError(f"Unsupported backtest event: {event_type}")

    def _score_setup(
        self,
        window: list[Candle],
        setup: dict[str, Any],
    ) -> tuple[int, str]:
        trade_direction = str(setup["trade_direction"]).upper()
        latest_close = float(window[-1].close)
        trend_sma = self._calculate_sma(window, min(self.minimum_window, len(window)))
        current_structure = "BULLISH" if latest_close >= trend_sma else "BEARISH"
        macro_bias = "BIAS_LONG" if trade_direction == "LONG" else "BIAS_SHORT"
        total_score = self.scoring_engine.calculate_total_score(
            trade_direction=trade_direction,
            macro_bias=macro_bias,
            current_structure=current_structure,
            zone_dict={"status": "ACTIVE"},
            has_recent_sweep=False,
        )
        return total_score, self.scoring_engine.classify_score(total_score)

    def _build_signal_from_setup(
        self,
        setup: dict[str, Any],
        score: int,
        current_candle: Candle,
    ) -> Signal:
        stop_loss = setup.get("sl_price", setup.get("stop_loss"))
        if stop_loss is None:
            raise ValueError("Backtest setup missing stop loss")

        signal_context = {
            "id": int(setup.get("timestamp", current_candle.timestamp)),
            "status": "ACTIVE",
            "type": str(setup.get("strategy", "BACKTEST_SETUP")),
            "strategy": str(setup.get("strategy", "BACKTEST_SETUP")),
            "entry_price": float(setup["entry_price"]),
            "sl_price": float(stop_loss),
        }
        return self.signal_factory.build_signal(
            symbol=str(setup.get("symbol", current_candle.symbol)),
            trade_direction=str(setup["trade_direction"]),
            zone_dict=signal_context,
            atr=0.0,
            score=int(score),
            timestamp=int(current_candle.timestamp),
        )

    def _has_open_trade(self) -> bool:
        return any(trade.status in {"PENDING", "ACTIVE", "PARTIAL_TP1"} for trade in self.open_trades)

    @staticmethod
    def _build_setup_key(setup: dict[str, Any]) -> str:
        return "|".join(
            [
                str(setup.get("strategy", "BACKTEST")),
                str(setup.get("trade_direction", "")),
                str(setup.get("timestamp", "")),
            ]
        )

    @staticmethod
    def _calculate_sma(candles: list[Candle], period: int) -> float:
        window = candles[-period:]
        closes = [float(candle.close) for candle in window]
        return sum(closes) / float(period)
