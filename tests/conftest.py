from pathlib import Path
import os


def get_test_db_path() -> Path:
    tests_dir = Path(__file__).resolve().parent
    return tests_dir / "test_trading_engine.db"


def set_test_db_env() -> Path:
    db_path = get_test_db_path()
    os.environ["DB_PATH"] = str(db_path)
    return db_path
