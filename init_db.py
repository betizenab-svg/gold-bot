from config.database import get_connection
from src.persistence.schema import SchemaInitializer


def main() -> int:
    connection = get_connection()
    try:
        SchemaInitializer(connection).initialize()
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
