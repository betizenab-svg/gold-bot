from typing import List
import numpy as np

from config.settings import (
    FSR_HIGH_MOMENTUM_THRESHOLD,
    FSR_MEAN_REVERSION_THRESHOLD,
)


class FSREngine:
    def _normalize(self, series: List[float]) -> List[float]:
        """
        Normalize an array strictly between 0 and 1 (Min-Max Scaling)
        to compare slopes dynamically.
        """
        # Trigger linter
        if not series:
            return []
            
        min_val = min(series)
        max_val = max(series)
        range_val = max_val - min_val
        
        if range_val == 0:
            return [0.0] * len(series)
            
        return [(val - min_val) / range_val for val in series]

    def _calculate_slope(self, data_series: List[float]) -> float:
        """
        Calculate the linear regression slope of a given series.
        Returns 0.0 if the series has fewer than 2 items.
        """
        if not data_series or len(data_series) < 2:
            return 0.0
            
        x = np.arange(len(data_series))
        y = np.array(data_series)
        slope, _ = np.polyfit(x, y, 1)
        return float(slope)

    def calculate_fsr(self, price_series: List[float], surprise_series: List[float]) -> float:
        """
        Normalize both series and calculate the divergence between their slopes.
        Formula: FSR = price_slope - surprise_slope
        """
        if not price_series or not surprise_series:
            return 0.0
            
        if len(price_series) < 2 or len(surprise_series) < 2:
            return 0.0

        norm_price = self._normalize(price_series)
        norm_surprise = self._normalize(surprise_series)

        price_slope = self._calculate_slope(norm_price)
        surprise_slope = self._calculate_slope(norm_surprise)

        return price_slope - surprise_slope

    def evaluate_fsr_state(self, fsr_value: float) -> str:
        """
        Map the FSR value to a qualitative momentum state based on configured thresholds.
        """
        if fsr_value > FSR_HIGH_MOMENTUM_THRESHOLD:
            return 'HIGH_MOMENTUM'
        elif fsr_value < FSR_MEAN_REVERSION_THRESHOLD:
            return 'MEAN_REVERSION'
        else:
            return 'EQUILIBRIUM'
