"""Jornada de clientela a partir del mismo SARIMAX del repo orus.

La serie de clientes es el conteo diario de tickets de la tienda.
Un ticket es una marca de tiempo distinta: en este dataset cada fila es
un producto, y varias filas con la misma hora pertenecen a la misma visita.
"""

from __future__ import annotations

import math
import threading
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from app.models.dataset_model import load_dataset
from app.utils.errors import AnalysisError, SelectionError

MORNING_HOURS = list(range(7, 14))
PILOT_NAME = "Tienda San Isidro"
ORDER = (1, 1, 1)
SEASONAL_ORDER = (0, 0, 0, 0)

# El CSV no trae leche como SKU. La demanda de leche sigue a las bebidas
# que la llevan. El café en grano se mide con los cafés servidos, que son
# los que consumen grano en barra. La bollería es la categoría Bakery.
CATEGORY_RULES = {
    "leche": lambda df: df["product_type"].isin(
        ["Barista Espresso", "Hot chocolate", "Brewed Chai tea"]
    ),
    "cafe": lambda df: df["product_category"].eq("Coffee"),
    "bolleria": lambda df: df["product_category"].eq("Bakery"),
}
CATEGORY_NAMES = {
    "leche": "leche entera",
    "cafe": "café en grano",
    "bolleria": "bollería",
}

_lock = threading.Lock()
_cache: dict[int, dict] = {}


def build_day_plan(store_id: int = 3) -> dict:
    if store_id in _cache:
        return _cache[store_id]
    with _lock:
        if store_id in _cache:
            return _cache[store_id]
        plan = _build_day_plan(store_id)
        _cache[store_id] = plan
        return plan


def _build_day_plan(store_id: int) -> dict:
    full = load_dataset()
    if store_id not in set(full["store_id"].unique()):
        raise SelectionError(f"La tienda {store_id} no existe.")

    df = full[full["store_id"] == store_id].copy()
    if df.empty:
        raise SelectionError(f"La tienda {store_id} no tiene transacciones.")

    df["transaction_qty"] = pd.to_numeric(df["transaction_qty"], errors="coerce").fillna(0)
    location = str(df["store_location"].iloc[0])

    tickets = df.drop_duplicates(["transaction_date", "transaction_time"])
    daily = tickets.groupby("transaction_date").size().rename("customers").to_frame()
    daily["unit_price"] = df.groupby("transaction_date")["unit_price"].mean()
    daily = _regularize(daily, "customers")

    hour_tickets = _hour_counts(tickets)
    customer_forecast = _forecast_next(daily, "customers", with_metrics=True)

    categories = {}
    for key, rule in CATEGORY_RULES.items():
        subset = df.loc[rule(df)]
        series = subset.groupby("transaction_date")["transaction_qty"].sum().rename("sales").to_frame()
        series["unit_price"] = subset.groupby("transaction_date")["unit_price"].mean()
        series = _regularize(series, "sales")
        categories[key] = {
            "forecast": _forecast_next(series, "sales", with_metrics=False),
            "hours": _hour_quantities(subset),
        }

    morning = _morning_customers(customer_forecast["predicted"], hour_tickets)
    product_levels = {
        key: _levels(
            categories[key]["forecast"]["predicted"],
            categories[key]["hours"],
        )
        for key in CATEGORY_RULES
    }

    hours = []
    for index, start in enumerate(MORNING_HOURS):
        products = {key: product_levels[key][index] for key in CATEGORY_RULES}
        hours.append(
            {
                "label": _slot(start, with_period=False),
                "period_label": _slot(start, with_period=True),
                "axis": f"{start:02d}:00",
                "customers": morning[index],
                "baristas": _baristas(morning[index]),
                "peak": False,
                "products": products,
            }
        )

    peak_index = max(range(len(hours)), key=lambda i: hours[i]["customers"])
    hours[peak_index]["peak"] = True
    peak = hours[peak_index]

    return {
        "store_id": store_id,
        "store_location": location,
        "pilot_name": PILOT_NAME,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "series_end": daily.index[-1].strftime("%Y-%m-%d"),
        "target_date": customer_forecast["target_date"],
        "model": {
            "name": "SARIMAX",
            "order": list(ORDER),
            "seasonal_order": list(SEASONAL_ORDER),
            "exog": ["unit_price", "is_weekend"],
            "target": "tickets por día",
            "mae": customer_forecast["mae"],
            "rmse": customer_forecast["rmse"],
            "fallback": customer_forecast["fallback"],
            "full_day_customers": round(customer_forecast["predicted"], 2),
        },
        "total_customers": int(sum(slot["customers"] for slot in hours)),
        "peak": {
            "label": peak["label"],
            "period_label": peak["period_label"],
            "customers": peak["customers"],
            "baristas": peak["baristas"],
        },
        "hours": hours,
        "priority": {
            "label": peak["label"],
            "baristas": peak["baristas"],
            "text": _priority_text(peak),
        },
    }


def _regularize(daily: pd.DataFrame, value_col: str) -> pd.DataFrame:
    daily = daily.sort_index()
    daily = daily.asfreq("D")
    daily[value_col] = daily[value_col].fillna(0)
    if "unit_price" not in daily.columns:
        daily["unit_price"] = np.nan
    mean_price = daily["unit_price"].mean()
    if pd.isna(mean_price):
        mean_price = 0.0
    daily["unit_price"] = daily["unit_price"].fillna(mean_price)
    return daily


def _hour_counts(tickets: pd.DataFrame) -> dict[int, float]:
    hours = pd.to_datetime(tickets["transaction_time"], format="%H:%M:%S", errors="coerce").dt.hour
    counts = hours.value_counts()
    return {int(hour): float(counts[hour]) for hour in counts.index if pd.notna(hour)}


def _hour_quantities(subset: pd.DataFrame) -> dict[int, float]:
    if subset.empty:
        return {}
    hours = pd.to_datetime(subset["transaction_time"], format="%H:%M:%S", errors="coerce").dt.hour
    grouped = subset.groupby(hours)["transaction_qty"].sum()
    return {int(hour): float(qty) for hour, qty in grouped.items() if pd.notna(hour)}


def _forecast_next(daily: pd.DataFrame, value_col: str, with_metrics: bool = False) -> dict:
    y = daily[value_col].astype(float)
    exog = pd.DataFrame(
        {
            "unit_price": daily["unit_price"].astype(float),
            "is_weekend": daily.index.dayofweek.isin([5, 6]).astype(int),
        },
        index=daily.index,
    )
    if len(y) < 10:
        raise AnalysisError("La serie temporal tiene menos de 10 días; no hay datos suficientes.")

    target = y.index[-1] + pd.Timedelta(days=1)
    metrics = _validation_metrics(y, exog) if with_metrics else {"mae": None, "rmse": None}
    fallback = None
    try:
        predicted = _sarimax_one_step(y, exog, target)
        if not math.isfinite(predicted) or predicted > float(y.max()) * 5:
            raise AnalysisError("La predicción SARIMAX quedó fuera de rango.")
    except Exception as exc:
        fallback = str(exc)
        same_day = y[y.index.dayofweek == target.dayofweek]
        base = same_day.tail(8) if len(same_day) else y.tail(14)
        predicted = float(base.mean()) if len(base) else 0.0

    return {
        "predicted": max(0.0, predicted),
        "target_date": target.strftime("%Y-%m-%d"),
        "mae": metrics["mae"],
        "rmse": metrics["rmse"],
        "fallback": fallback,
    }


def _sarimax_one_step(y: pd.Series, exog: pd.DataFrame, target: pd.Timestamp) -> float:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(
            y,
            exog=exog,
            order=ORDER,
            seasonal_order=SEASONAL_ORDER,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        results = model.fit(disp=False, maxiter=200)
        future = pd.DataFrame(
            {
                "unit_price": [float(exog["unit_price"].mean())],
                "is_weekend": [int(target.dayofweek in (5, 6))],
            }
        )
        forecast = results.get_forecast(steps=1, exog=future)
        return max(0.0, float(forecast.predicted_mean.iloc[0]))


def _validation_metrics(y: pd.Series, exog: pd.DataFrame) -> dict:
    split = int(len(y) * 0.8)
    train_y, test_y = y.iloc[:split], y.iloc[split:]
    train_exog, test_exog = exog.iloc[:split], exog.iloc[split:]
    if train_y.empty or test_y.empty:
        return {"mae": None, "rmse": None}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = SARIMAX(
                train_y,
                exog=train_exog,
                order=ORDER,
                seasonal_order=SEASONAL_ORDER,
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            results = model.fit(disp=False, maxiter=200)
            predicted = results.get_forecast(steps=len(test_y), exog=test_exog).predicted_mean.clip(lower=0)
            error = test_y.to_numpy() - predicted.to_numpy()
            return {
                "mae": round(float(np.mean(np.abs(error))), 2),
                "rmse": round(float(np.sqrt(np.mean(error ** 2))), 2),
            }
    except Exception:
        return {"mae": None, "rmse": None}


def _morning_customers(full_day: float, hour_tickets: dict[int, float]) -> list[int]:
    weights = [hour_tickets.get(hour, 0.0) for hour in MORNING_HOURS]
    total_weight = sum(hour_tickets.values())
    if total_weight <= 0 or sum(weights) <= 0:
        raise AnalysisError("No hay tickets históricos en las franjas de la mañana.")
    morning_total = full_day * sum(weights) / total_weight
    return _allocate(int(round(morning_total)), weights)


def _allocate(total: int, weights: list[float]) -> list[int]:
    if total <= 0:
        return [0 for _ in weights]
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return [0 for _ in weights]
    raw = [total * weight / weight_sum for weight in weights]
    floors = [math.floor(value) for value in raw]
    leftover = total - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for index in order[:leftover]:
        floors[index] += 1
    return floors


def _baristas(customers: int) -> int:
    """Una persona cubre unos 10 clientes en la hora. Mínimo 2 si hay gente."""
    if customers <= 0:
        return 0
    return max(2, math.ceil(customers / 10))


def _levels(predicted_total: float, hour_qty: dict[int, float]) -> list[str]:
    total_weight = sum(hour_qty.values())
    if predicted_total <= 0 or total_weight <= 0:
        return ["verde" for _ in MORNING_HOURS]
    values = [
        predicted_total * hour_qty.get(hour, 0.0) / total_weight
        for hour in MORNING_HOURS
    ]
    mean = sum(values) / len(values)
    levels = []
    for value in values:
        ratio = value / mean if mean else 0
        if ratio >= 1.12:
            levels.append("rojo")
        elif ratio >= 1.02:
            levels.append("amarillo")
        else:
            levels.append("verde")
    return levels


def _slot(start: int, with_period: bool) -> str:
    label = f"{start:02d}:00–{start + 1:02d}:00"
    if not with_period:
        return label
    period = "a. m." if start < 12 else "p. m."
    return f"{label} {period}"


def _priority_text(peak: dict) -> str:
    names = CATEGORY_NAMES
    reds = [names[key] for key, level in peak["products"].items() if level == "rojo"]
    yellows = [names[key] for key, level in peak["products"].items() if level == "amarillo"]
    sentences = [
        (
            f"Esperamos {peak['customers']} clientes y recomendamos "
            f"{peak['baristas']} baristas. Es la hora con más clientes."
        )
    ]
    if reds:
        sentences.append(f"Revisa primero {_join(reds)} (Rojo).")
    if yellows:
        sentences.append(f"Después haz seguimiento de {_join(yellows)} (Amarillo).")
    if not reds and not yellows:
        sentences.append("Ningún insumo del piloto está en alerta en esa hora.")
    return " ".join(sentences)


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} y {items[1]}"
    return ", ".join(items[:-1]) + f" y {items[-1]}"
