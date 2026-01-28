from config.database import get_connection
from src.ingestion.oanda import OandaClient
from src.persistence.repository import Repository
from src.persistence.schema import SchemaInitializer


def main() -> int:
    connection = get_connection()
    try:
        SchemaInitializer(connection).initialize()
        repository = Repository(connection)
        client = OandaClient(repository)

        symbol = "XAUUSD"
        timeframe = "H1"

        candles = client.fetch_latest_candles(symbol, timeframe)
        if candles:
            repository.save_candles(candles)
            latest_timestamp = max(candle.timestamp for candle in candles)
            repository.update_watermark(symbol, timeframe, latest_timestamp)
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
