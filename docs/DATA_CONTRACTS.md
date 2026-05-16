# Data Contracts & Gobernanza

Este documento define las reglas estrictas de integridad, validación y calidad de datos (Data Contracts) aplicadas a lo largo del flujo del Atlas RA Seismic ETL Pipeline. Garantiza que cualquier consumidor downstream (Data Analysts, Data Scientists) trabaje con datos certificados.

## Arquitectura Lakehouse (Medallion)

El sistema procesa la información en tres capas bien definidas:

### 1. Capa Bronze (Raw)
- **Origen:** API de USGS GeoJSON.
- **Formato:** Archivos JSON inmutables.
- **Ubicación:** `data/raw/usgs/{mode}/year={YYYY}/month={MM}/day={DD}/`
- **Contrato:** Ninguno. Se ingiere la respuesta HTTP 200 tal cual se recibe para permitir auditoría o reprocesamiento en caso de fallos en el esquema esperado.

### 2. Capa Silver (Transformed & Cleansed)
- **Transformación:** En memoria via `SeismicTransformer`.
- **Contrato Estructural (Pydantic):**
  - Todo registro DEBE contener la propiedad `type: "earthquake"`.
  - Todo registro DEBE contener un arreglo `coordinates` de longitud exacta 3: `[longitude, latitude, depth]`.
  - Las coordenadas DEBEN estar dentro de rangos geográficos válidos (lat: -90 a 90, lon: -180 a 180).
  - El campo `id` no puede estar vacío.
- **Reglas de Calidad (Quarantine & Rejection):**
  - **Rechazo (Quality Log):** Sismos con estado `deleted` o `magnitude = None`. No aportan valor analítico.
  - **Deduplicación:** Registros con `event_id` duplicado en un mismo batch son ignorados.
  - **Cuarentena (Quarantine Table):** Sismos fuera del rango histórico permitido u otras anomalías de negocio (ej. eventos detectados con profundidad anómala extrema que requieren revisión humana).

### 3. Capa Gold (Business & Aggregated)
- **Destino:** Base de datos MySQL (`seismic_db`).
- **Tablas:**
  - `seismic_events`: Única fuente de verdad de eventos sísmicos.
  - `data_quality_log`: Bitácora de rechazos para observabilidad.
  - `quarantine_records`: Eventos en observación.
  - `daily_aggregates`: Tabla analítica materializada pre-agregada.
- **Enriquecimiento (Valor de Negocio):**
  - `magnitude_class`: (micro, minor, light, moderate, strong, major, great).
  - `depth_class`: (shallow, intermediate, deep).
  - `energy_joules`: Cálculo científico derivado.
  - `risk_score`: (0.0 a 100.0) Puntuación compuesta de riesgo.
  - `region_id`: Asignación espacial a polígonos predefinidos.

## Schema Expected (Silver a Gold)

```python
class SeismicEvent(BaseModel):
    event_id: str          # Primary Key
    magnitude: float       # Not null
    magnitude_class: str   # Derivado
    place: str             # Limpio
    event_time: datetime   # UTC
    updated_time: datetime # UTC
    latitude: float        # Validado
    longitude: float       # Validado
    depth_km: float        # Validado
    depth_class: str       # Derivado
    energy_joules: float   # Calculado
    risk_score: float      # (0-100)
    region_id: str         # Georeferenciado
    felt: int | None       
    cdi: float | None      
    mmi: float | None      
    alert_level: str | None
    tsunami: int           # 0 o 1
    significance: int      
    net: str               
    mag_type: str          
    status: str            # 'reviewed', etc
    raw_place: str         
```
