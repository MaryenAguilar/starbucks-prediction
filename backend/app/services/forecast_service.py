import base64
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
from statsmodels.tsa.statespace.sarimax import SARIMAX

from app.models.dataset_model import load_dataset
from app.utils.errors import DatasetError, SelectionError, AnalysisError

def _get_quantity_column(df: pd.DataFrame) -> str | None:
    for column in ("quantity", "qty", "transaction_qty"):
        if column in df.columns:
            return column
    return None

def get_available_stores():
    df = load_dataset()
    return sorted(int(value) for value in df["store_id"].dropna().unique())

def get_available_products(store_id: int | None = None):
    df = load_dataset()

    if store_id is not None:
        if store_id not in set(df["store_id"].unique()):
            raise SelectionError(f"La tienda {store_id} no existe.")
        df = df[df["store_id"] == store_id]

    products = sorted(int(value) for value in df["product_id"].dropna().unique())
    return {"store_id": store_id, "products": products, "total": len(products)}

def _figure_to_base64(fig) -> str:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("ascii")

def _build_chart(daily_data, train_data, test_data, forecast_mean,
                 forecast_ci, scope_name, product_id):
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(
        daily_data.index, daily_data["sales"],
        color="tab:brown", linewidth=1.5, label="Ventas diarias"
    )
    ax.set_title(f"Evolución histórica - {scope_name} | Producto {product_id}")
    ax.set_xlabel("Fecha")
    ax.set_ylabel("Cantidad vendida")
    ax.legend()
    fig.tight_layout()
    history_chart = _figure_to_base64(fig)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(train_data.index, train_data["sales"],
            label="Entrenamiento (80%)", color="tab:blue")
    ax.plot(test_data.index, test_data["sales"],
            label="Real - Test (20%)", color="tab:green", marker="o")
    ax.plot(test_data.index, forecast_mean,
            label="Predicción SARIMAX", color="tab:orange",
            linestyle="--", marker="x")
    ax.fill_between(
        test_data.index,
        forecast_ci.iloc[:, 0],
        forecast_ci.iloc[:, 1],
        color="tab:orange", alpha=0.2,
        label="Intervalo de confianza (95%)"
    )
    ax.set_title(f"Validación 80/20 - {scope_name} | Producto {product_id}")
    ax.set_xlabel("Fecha")
    ax.set_ylabel("Cantidad vendida")
    ax.legend()
    fig.tight_layout()
    validation_chart = _figure_to_base64(fig)

    return {
        "historical_png_base64": history_chart,
        "validation_png_base64": validation_chart,
    }

def run_forecast(store_id: int | None, product_id: int):
    df = load_dataset()

    if store_id is None:
        df_scope = df.copy()
        scope_name = "Todas las tiendas (Global)"
    else:
        if store_id not in set(df["store_id"].unique()):
            raise SelectionError(f"La tienda {store_id} no existe.")
        df_scope = df[df["store_id"] == store_id].copy()
        scope_name = f"Tienda {store_id}"

    products = set(df_scope["product_id"].dropna().unique())
    if product_id not in products:
        raise SelectionError(
            f"El producto {product_id} no existe en el ámbito seleccionado."
        )

    df_prod = df_scope[df_scope["product_id"] == product_id].copy()
    if df_prod.empty:
        raise SelectionError("No existen transacciones para esa selección.")

    qty_col = _get_quantity_column(df)
    if qty_col:
        daily_data = (
            df_prod.groupby("transaction_date")
            .agg({qty_col: "sum", "unit_price": "mean"})
            .rename(columns={qty_col: "sales"})
        )
    else:
        daily_data = (
            df_prod.groupby("transaction_date")
            .agg({"unit_price": "mean"})
            .rename(columns={"unit_price": "unit_price"})
        )
        daily_data["sales"] = df_prod.groupby("transaction_date").size()

    daily_data = daily_data.sort_index().asfreq("D")
    daily_data["sales"] = daily_data["sales"].fillna(0)
    daily_data["unit_price"] = daily_data["unit_price"].fillna(
        daily_data["unit_price"].mean()
    )

    if daily_data["unit_price"].isna().all():
        raise AnalysisError("No hay precios unitarios válidos para entrenar el modelo.")

    if len(daily_data) < 10:
        raise AnalysisError(
            "La serie temporal tiene menos de 10 días; no hay datos suficientes."
        )

    daily_data["day_of_week"] = daily_data.index.dayofweek
    daily_data["is_weekend"] = daily_data["day_of_week"].isin([5, 6]).astype(int)
    exog_vars = daily_data[["unit_price", "is_weekend"]]

    train_size = int(len(daily_data) * 0.8)
    train_data = daily_data.iloc[:train_size]
    test_data = daily_data.iloc[train_size:]

    if train_data.empty or test_data.empty:
        raise AnalysisError("No fue posible dividir los datos en entrenamiento y prueba.")

    train_sales = train_data["sales"]
    test_sales = test_data["sales"]
    train_exog = exog_vars.iloc[:train_size]
    test_exog = exog_vars.iloc[train_size:]

    try:
        model = SARIMAX(
            train_sales,
            exog=train_exog,
            order=(1, 1, 1),
            seasonal_order=(0, 0, 0, 0),
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        results = model.fit(disp=False)

        forecast_obj = results.get_forecast(steps=len(test_data), exog=test_exog)
        forecast_mean = forecast_obj.predicted_mean.clip(lower=0)
        forecast_ci = forecast_obj.conf_int().clip(lower=0)

        comparison_df = pd.DataFrame({
            "date": test_data.index.strftime("%Y-%m-%d"),
            "actual_sales": test_sales.to_numpy(),
            "predicted_sales": forecast_mean.to_numpy().round(2),
            "lower_95": forecast_ci.iloc[:, 0].to_numpy().round(2),
            "upper_95": forecast_ci.iloc[:, 1].to_numpy().round(2),
        })
        comparison_df["absolute_error"] = (
            comparison_df["actual_sales"] - comparison_df["predicted_sales"]
        ).abs().round(2)

        mae = float(mean_absolute_error(test_sales, forecast_mean))
        rmse = float(np.sqrt(mean_squared_error(test_sales, forecast_mean)))

        last_date = daily_data.index[-1]
        target_date = last_date + pd.Timedelta(days=1)
        future_price = float(daily_data["unit_price"].mean())
        future_is_weekend = int(target_date.dayofweek in [5, 6])
        future_exog = pd.DataFrame({
            "unit_price": [future_price],
            "is_weekend": [future_is_weekend],
        })

        future_forecast = results.get_forecast(steps=1, exog=future_exog)
        predicted_qty = max(0.0, float(future_forecast.predicted_mean.iloc[0]))
        conf_interval = future_forecast.conf_int().clip(lower=0).iloc[0]

        charts = _build_chart(
            daily_data, train_data, test_data, forecast_mean,
            forecast_ci, scope_name, product_id
        )

        return {
            "scope": scope_name,
            "store_id": store_id,
            "product_id": product_id,
            "statistics": {
                "raw_transactions": int(len(df_prod)),
                "days": int(len(daily_data)),
                "historical_units_sold": float(daily_data["sales"].sum()),
                "training_days": int(len(train_data)),
                "test_days": int(len(test_data)),
            },
            "metrics": {"mae": mae, "rmse": rmse},
            "validation": comparison_df.to_dict(orient="records"),
            "next_day_prediction": {
                "date": target_date.strftime("%Y-%m-%d"),
                "estimated_unit_price": round(future_price, 2),
                "is_weekend": bool(future_is_weekend),
                "predicted_quantity": round(predicted_qty, 2),
                "confidence_95": [
                    round(float(conf_interval.iloc[0]), 2),
                    round(float(conf_interval.iloc[1]), 2),
                ],
            },
            "charts": charts,
        }
    except AnalysisError:
        raise
    except Exception as exc:
        raise AnalysisError(f"Falló el entrenamiento o la predicción SARIMAX: {exc}")
