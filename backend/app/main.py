from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.controllers.forecast_controller import router as forecast_router

app = FastAPI(
    title="Coffee Shop Sales Forecast API",
    description="API para analizar y predecir ventas diarias con SARIMAX, y la jornada de clientela.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, reemplazar por los dominios permitidos.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(forecast_router, prefix="/api")

@app.get("/health", tags=["Sistema"])
def health():
    return {"status": "ok"}

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
