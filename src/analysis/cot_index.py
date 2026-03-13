import logging
from typing import List

from config.settings import (
    COT_OVERCROWDED_THRESHOLD,
    COT_CAPITULATION_THRESHOLD,
)

class CotAnalyzer:
    """Commitment of Traders (COT) Index Analyzer.
    
    Evaluates herd positioning using the Speculative (Non-Commercial) net position
    data over a configured rolling window.
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def calculate_index(self, current_net: float, historical_nets: List[float]) -> float:
        """Calculate a 0-100 normalized COT index.
        
        Formula: 100 * ((Current - Min) / (Max - Min))
        
        Args:
            current_net: The current week's net position.
            historical_nets: List of net positions over the lookback window.
            
        Returns:
            A float between 0.0 and 100.0. Returns 50.0 if zero division occurs
            or if the historical list is empty.
        """
        if not historical_nets:
            self.logger.warning("No historical COT data provided. Defaulting to Neutral (50.0).")
            return 50.0

        min_net = min(historical_nets)
        max_net = max(historical_nets)

        if max_net == min_net:
            self.logger.warning("Historical COT max equals min. Defaulting to Neutral (50.0).")
            return 50.0

        index_val = 100.0 * ((current_net - min_net) / (max_net - min_net))
        
        # Guard against floats slightly outside 0-100 due to floating point precision
        return max(0.0, min(100.0, index_val))

    def evaluate_positioning(self, index_value: float) -> str:
        """Map the calculated COT index to a positioning state string.
        
        Args:
            index_value: The normalized 0-100 COT index value.
            
        Returns:
            'OVERCROWDED_LONG', 'CAPITULATION_SHORT', or 'NEUTRAL'.
        """
        if index_value > COT_OVERCROWDED_THRESHOLD:
            return "OVERCROWDED_LONG"
        elif index_value < COT_CAPITULATION_THRESHOLD:
            return "CAPITULATION_SHORT"
        
        return "NEUTRAL"
