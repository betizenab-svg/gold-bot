import os
import datetime
import pandas as pd
from unittest.mock import patch, MagicMock

from config.settings import BASE_DIR

def test_dynamic_calibration():
    report_path = os.path.join(BASE_DIR, "data", "calibration_report.csv")
    if os.path.exists(report_path):
        os.remove(report_path) # Clean state
        
    with patch("scripts.calibrate_regime.datetime") as mock_datetime, \
         patch("scripts.calibrate_regime.pd.read_csv") as mock_read_csv, \
         patch("scripts.calibrate_regime.yf.download") as mock_yf_download:
         
        # 1. Mock datetime.date to return a fixed future date for today()
        mock_date = MagicMock()
        mock_date.today.return_value = datetime.date(2026, 3, 15)
        mock_datetime.date = mock_date
        mock_datetime.datetime = datetime.datetime

        # Generate an overlapping dummy dataset starting from 2022-01-01 to 2026-03-15
        dates = pd.date_range(start="2022-01-01", end="2026-03-15", freq="D")
        
        # Gold DataFrame (Mocking yf.download)
        gold_df = pd.DataFrame(index=dates, columns=["Close"])
        gold_df["Close"] = [1800.0 + i for i in range(len(dates))] 
        mock_yf_download.return_value = gold_df

        # TIPS Yield DataFrame (Mocking pd.read_csv)
        tips_raw = pd.DataFrame()
        tips_raw["DATE"] = dates
        tips_raw["DFII10"] = [1.0 + i * 0.01 for i in range(len(dates))] 
        mock_read_csv.return_value = tips_raw

        # 3. Execute Calibration
        from scripts.calibrate_regime import get_data, evaluate_regimes, generate_report
        start = "2022-01-01"
        end = mock_date.today().strftime("%Y-%m-%d")
        
        # Assert that requests evaluated strings successfully
        assert end == "2026-03-15", f"Expected '2026-03-15', got {end}"
        
        # Manually execute execution stream
        df_raw = get_data(start, end)
        df_eval = evaluate_regimes(df_raw)
        generate_report(df_eval)

    # 4. Verify Output csv natively bypassing mock overwrites
    assert os.path.exists(report_path), f"File was not created at {report_path}"
    
    df_result = pd.read_csv(report_path, parse_dates=[0])
    max_year = df_result.iloc[:, 0].dt.year.max()
    assert max_year == 2026, f"Expected 2026 max year, got {max_year}"
    
    # Target the generic format avoiding whitespace
    last_regime = df_result.iloc[-1]["Regime"]
    last_regime_clean = str(last_regime).strip()
    assert last_regime_clean == "REGIME_DECOUPLED", f"Failed to map REGIME_DECOUPLED! Row mapped: {last_regime_clean}"
    
    print("Sprint 16 (Revised) Dynamic Calibration Engine Verified")
    os.remove(report_path)

if __name__ == "__main__":
    test_dynamic_calibration()
