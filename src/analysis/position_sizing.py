from __future__ import annotations


class LotSizeCalculator:
    """Dynamic XAUUSD position sizing using a fixed-risk model."""

    ACCOUNT_BALANCES = [50, 100, 200, 500, 700, 1000, 2000, 5000, 10000, 50000]
    RISK_PERCENT = 0.02
    BASELINE_BALANCE = 100
    PIP_MULTIPLIER = 10.0
    STANDARD_LOT_PIP_VALUE = 10.0
    BASELINE_NOTE = "Baseline recommendation: $100 balance is the baseline assumption for these lot sizes."
    LEGACY_BASELINE_TEXT = "Assumed baseline balance for this signal is $100."

    def calculate_pips(self, entry_price: float, sl_price: float) -> float:
        price_difference = abs(float(entry_price) - float(sl_price))
        return round(price_difference * self.PIP_MULTIPLIER, 2)

    def calculate_lot_size(
        self,
        balance: float,
        pips: float,
        risk_pct: float = 0.02,
    ) -> float:
        pip_distance = float(pips)
        if pip_distance <= 0:
            raise ValueError("pips must be greater than zero")

        risk_amount = float(balance) * float(risk_pct)
        lot_size = risk_amount / (pip_distance * self.STANDARD_LOT_PIP_VALUE)
        return round(max(lot_size, 0.01), 2)

    def generate_table(self, entry_price: float, sl_price: float) -> str:
        pips = self.calculate_pips(entry_price, sl_price)
        if pips == 0:
            return "<b>Lot size unavailable:</b> <code>SL distance is zero.</code>"
        return self._build_table_from_pips(pips)

    def calculate_table(self, sl_distance_pips: float) -> str:
        pip_distance = float(sl_distance_pips)
        if pip_distance <= 0:
            raise ValueError("sl_distance_pips must be greater than zero")
        return self._build_table_from_pips(pip_distance)

    def _build_table_from_pips(self, pips: float) -> str:
        pre_lines_before_baseline = [
            "Balance   Lot Size",
            "-------   --------",
        ]
        pre_lines_from_baseline: list[str] = []

        for balance in self.ACCOUNT_BALANCES:
            lot_size = self.calculate_lot_size(balance=balance, pips=pips, risk_pct=self.RISK_PERCENT)
            row = f"${balance:<7} {lot_size:.2f}"
            if balance < self.BASELINE_BALANCE:
                pre_lines_before_baseline.append(row)
            else:
                pre_lines_from_baseline.append(row)

        before_block = "<pre>" + "\n".join(pre_lines_before_baseline) + "</pre>"
        baseline_block = "<pre>" + "\n".join(pre_lines_from_baseline) + "</pre>"
        return (
            f"{self.LEGACY_BASELINE_TEXT}\n"
            "<i>2% risk model for XAUUSD | 1.00 lot = $10 per pip</i>\n"
            f"{before_block}\n"
            f"{self.BASELINE_NOTE}\n"
            f"{baseline_block}"
        )
