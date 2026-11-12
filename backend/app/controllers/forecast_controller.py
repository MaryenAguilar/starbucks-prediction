from fastapi import APIRouter, HTTPException
from app.schemas.forecast_schema import ForecastRequest
from app.services.day_plan_service import build_day_plan
from app.services.forecast_service import (
    get_available_stores,
    get_available_products,
    run_forecast,
)
from app.utils.errors import DatasetError, AnalysisError, SelectionError

router = APIRouter(tags=["Predicción"])

@router.get("/stores")
def stores():
    """Devuelve los IDs de tiendas disponibles."""
    try:
        return {"stores": get_available_stores()}
    except DatasetError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/products")
def products(store_id: int | None = None):
    """Devuelve productos. Sin store_id, considera todas las tiendas."""
    try:
        return get_available_products(store_id)
    except SelectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DatasetError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@router.get("/day-plan")
def day_plan(store_id: int = 3):
    """Predice la clientela de la mañana con SARIMAX y arma la jornada."""
    try:
        return build_day_plan(store_id)
    except SelectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (DatasetError, AnalysisError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/forecast")
def forecast(request: ForecastRequest):
    """Ejecuta validación 80/20 y predice el siguiente día disponible."""
    try:
        return run_forecast(request.store_id, request.product_id)
    except SelectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (DatasetError, AnalysisError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
