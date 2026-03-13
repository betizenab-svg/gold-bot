from typing import List
import logging
import random

class CotClient:
    """Commitment of Traders (COT) Data Client."""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def fetch_historical_net_positions(self) -> List[float]:
        """Fetch historical Non-Commercial Net Positions for Gold.
        
        Currently returns a mock list of 26 weeks of float values to simulate
        CFTC speculative net positions (Longs - Shorts). This avoids complex 
        CFTC zip file parsing for the initial Sprint 12 implementation.
        """
        self.logger.info("Fetching mock historical COT net positions")
        
        # We need exactly COT_LOOKBACK_WEEKS (26) data points.
        # Generating a realistic looking random walk for speculative net positions.
        # Values typically range between 50,000 and 300,000 contracts for Gold.
        
        # Start at a random base value
        base_value = random.uniform(100000.0, 200000.0)
        
        positions = []
        current_val = base_value
        
        for _ in range(26):
            # Random walk step
            step = random.uniform(-15000.0, 15000.0)
            current_val += step
            # Keep reasonably bounded
            current_val = max(0.0, min(400000.0, current_val))
            positions.append(round(current_val, 2))
            
        return positions
