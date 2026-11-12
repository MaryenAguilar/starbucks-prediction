from functools import lru_cache
from pathlib import Path
import pandas as pd
from app.utils.errors import DatasetError

CSV_FILE = Path(__file__).resolve().parent / "coffee-shop-sales-revenue.csv"


@lru_cache(maxsize=1)
def load_dataset() -> pd.DataFrame:
    """Carga el CSV local una sola vez por proceso."""
    try:
        df = pd.read_csv(CSV_FILE, sep="|")

        required = {"transaction_date", "store_id", "product_id", "unit_price"}
        missing = required.difference(df.columns)

        if missing:
            raise DatasetError(
                f"Faltan columnas obligatorias en el CSV: {sorted(missing)}"
            )

        df["transaction_date"] = pd.to_datetime(
            df["transaction_date"], errors="coerce"
        )

        df = df.dropna(
            subset=["transaction_date", "store_id", "product_id"]
        )

        return df

    except DatasetError:
        raise
    except Exception as exc:
        raise DatasetError(f"No se pudo leer el dataset local: {exc}")