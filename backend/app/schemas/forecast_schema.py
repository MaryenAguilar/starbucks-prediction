from pydantic import BaseModel, Field

class ForecastRequest(BaseModel):
    store_id: int | None = Field(
        default=None,
        description="ID de tienda. Si es null, se analizan todas las tiendas."
    )
    product_id: int = Field(..., description="ID del producto que se analizará.")
