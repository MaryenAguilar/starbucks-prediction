# Coffee Sales Forecast API

Servidor FastAPI que expone el análisis de ventas y la predicción SARIMAX del script original.

## Estructura MVC

- `app/models/`: descarga y carga el dataset.
- `app/controllers/`: endpoints HTTP.
- `app/services/`: lógica de análisis, entrenamiento y gráficos.
- `app/schemas/`: validación de solicitudes.
- `app/utils/`: excepciones de la aplicación.

## Instalación

Requiere Python 3.10 o superior.

```bash
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\activate
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Instalar dependencias:

```bash
pip install -r requirements.txt
```

## Kaggle

El servidor descarga el dataset con `kagglehub` la primera vez que se consulta un endpoint que necesita datos. Configura tus credenciales de Kaggle si el dataset o tu cuenta lo requieren. La descarga queda cacheada por `kagglehub` y el DataFrame se mantiene en memoria durante la vida del proceso.

## Ejecutar

Desde la carpeta raíz:

```bash
uvicorn app.main:app --reload
```

Abrir la documentación interactiva:

- http://127.0.0.1:8000/docs

## Endpoints

### Estado

`GET /health`

### Tiendas disponibles

`GET /api/stores`

### Productos disponibles

Global:

`GET /api/products`

Por tienda:

`GET /api/products?store_id=3`

### Ejecutar análisis y predicción

`POST /api/forecast`

Analizar todas las tiendas:

```json
{
  "store_id": null,
  "product_id": 10
}
```

Analizar una tienda específica:

```json
{
  "store_id": 3,
  "product_id": 10
}
```

La respuesta contiene estadísticas, MAE, RMSE, tabla de validación, predicción del siguiente día e imágenes PNG codificadas en Base64.

Para mostrar una imagen en un frontend, se puede usar:

```javascript
const imageUrl = `data:image/png;base64,${response.charts.historical_png_base64}`;
```

## Consideraciones

- Se eliminó `input()` porque los parámetros ahora llegan por HTTP.
- Se eliminó `exit()` y se reemplazó por errores HTTP 404/400/500.
- Se reemplazó `plt.show()` por gráficos PNG en Base64.
- El dataset se carga una sola vez por proceso. Si ejecutas varios workers, cada worker tendrá su propia copia en memoria.
- El endpoint de predicción es síncrono porque pandas y SARIMAX realizan trabajo de CPU. FastAPI ejecuta las rutas `def` en su thread pool.
- `seasonal_decompose`, ACF, PACF y otras importaciones del script original se omitieron porque no intervenían en el resultado calculado.

uvicorn app.main:app --reload