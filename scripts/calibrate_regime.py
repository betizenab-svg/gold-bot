"""
Dynamic Regime Calibration Script for tracking historical U.S. Real Interest Rate
(TIPS) vs Gold decoupling metrics.

Run manually via terminal `python scripts/calibrate_regime.py`.
"""

import os
import datetime
import pandas as pd
import yfinance as yf


def get_data(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch and align Gold Close values against FRED 10Y TIPS Yield."""
    print(f"Fetching data from {start_date} to {end_date}...")
    
    # Gold Continuous Contract (GC=F) via yfinance
    gold_df = yf.download(
        "GC=F",
        start=start_date,
        end=end_date,
        interval="1d",
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if isinstance(gold_df.columns, pd.MultiIndex):
        gold_df.columns = gold_df.columns.get_level_values(0)
    
    gold_close = gold_df[["Close"]].rename(columns={"Close": "Gold_Close"})
    
    # Normalize gold index datetimes
    if hasattr(gold_close.index, "tz") and gold_close.index.tz is not None:
        gold_close.index = gold_close.index.tz_localize(None)
    gold_close.index = pd.DatetimeIndex(pd.to_datetime(gold_close.index).floor("D"))

    # US 10-Year TIPS Yield (DFII10) natively bypassing pandas_datareader due to deprecation bugs
    fred_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10&cosd={start_date}&coed={end_date}"
    tips_raw = pd.read_csv(fred_url, na_values=".", header=0, names=["DATE", "DFII10"], skiprows=1)
    
    # Pre-process numeric formats
    tips_raw["DATE"] = pd.to_datetime(tips_raw["DATE"])
    tips_df = tips_raw.set_index("DATE")
    
    # Isolate the column directly before renaming to prevent index duplication KeyError
    tips_df = tips_df[["DFII10"]].rename(columns={"DFII10": "TIPS_Yield"})
    tips_df["TIPS_Yield"] = pd.to_numeric(tips_df["TIPS_Yield"], errors="coerce")

    # Normalize FRED datetimes
    if hasattr(tips_df.index, "tz") and tips_df.index.tz is not None:
        tips_df.index = tips_df.index.tz_localize(None)
    tips_df.index = pd.DatetimeIndex(pd.to_datetime(tips_df.index).floor("D"))


    # Merge via Inner Join and sanitize NaNs
    df = pd.merge(gold_close, tips_df, left_index=True, right_index=True, how="inner")
    
    # Forward-fill and drop remaining NaNs where data wasn't available
    df = df.ffill().dropna()
    return df


def evaluate_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate the 60d rolling correlation and classify the regime."""
    print("Calculating 60-day rolling correlation...")
    
    # Note: Requires minimum 60 periods for the initial calculation
    df["60d_Correlation"] = df["Gold_Close"].rolling(window=60).corr(df["TIPS_Yield"])
    
    def classify(val: float) -> str:
        if pd.isna(val):
            return "NaN"
        if val < -0.5:
            return "REGIME_NORMAL"
        if val > -0.2:
            return "REGIME_DECOUPLED"
        return "REGIME_TRANSITION"
        
    df["Regime"] = df["60d_Correlation"].apply(classify)
    
    # Drop the first 59 rows containing NaN correlations
    df = df.dropna(subset=["60d_Correlation"]).copy()
    return df


def generate_report(df: pd.DataFrame):
    """Save the results and print the yearly terminal statistics."""
    # Ensure data directory exists
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    
    # Export cleanly
    output_path = os.path.join(data_dir, "calibration_report.csv")
    df.index.name = "Date"
    df.to_csv(output_path)
    print(f"\nWritten {len(df)} lines to {output_path}")
    
    print("\n==================================")
    print("Annual Regime Decoupling Breakdown")
    print("==================================")
    
    # Dynamic terminal year grouping mapping % Decoupled
    decoupled_mask = pd.Series(df["Regime"] == "REGIME_DECOUPLED", index=df.index)
    yearly_pct = (decoupled_mask.groupby(df.index.year).mean() * 100).round(2)
    
    for year, pct in yearly_pct.items():
        print(f"{year}: {pct}% decoupled")


if __name__ == "__main__":
    start = "2022-01-01"
    end = datetime.date.today().strftime("%Y-%m-%d")
    
    df_raw = get_data(start, end)
    df_eval = evaluate_regimes(df_raw)
    generate_report(df_eval)
