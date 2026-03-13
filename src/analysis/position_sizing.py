from __future__ import annotations


class LotSizeCalculator:
    """Build fixed-balance risk tables for XAUUSD trade planning."""

    ACCOUNT_BALANCES = [50, 100, 200, 500, 700, 1000, 2000, 5000, 10000, 50000]
    RISK_PERCENT = 0.02
    BASELINE_BALANCE = 100

    def calculate_table(self, sl_distance_pips: float) -> str:
        sl_distance = float(sl_distance_pips)
        if sl_distance <= 0:
            raise ValueError("sl_distance_pips must be greater than zero")

        lines = [
            "Lot size table (2% risk per trade)",
            "Assumed baseline balance for this signal is $100.",
        ]
        for balance in self.ACCOUNT_BALANCES:
            risk_amount = balance * self.RISK_PERCENT
            lot_size = risk_amount / sl_distance
            marker = " <- BASELINE ASSUMPTION" if balance == self.BASELINE_BALANCE else ""
            lines.append(f"${balance}: {lot_size:.4f} lots{marker}")
        return "\n".join(lines)
