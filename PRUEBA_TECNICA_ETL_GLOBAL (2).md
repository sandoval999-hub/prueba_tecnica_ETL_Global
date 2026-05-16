# Prueba Técnica — Junior Data Engineer (Pipeline ETL Global)

**Empresa:** Atlas Reinsurance Analytics (ficticia)
**Posición:** Junior Data Engineer — Equipo de Riesgo Sísmico
**Tiempo estimado:** 12 – 18 horas
**Fecha de entrega:** 12 días naturales desde la recepción

---

## Contexto del negocio

Atlas Reinsurance Analytics es una empresa de reaseguros con sede en Zúrich que opera en 40 países. Nuestro negocio consiste en asegurar a las aseguradoras: cuando un terremoto destruye una ciudad y las compañías locales no pueden cubrir todos los reclamos, nosotros absorbemos el exceso.

Para calcular nuestras primas de riesgo necesitamos monitorear la actividad sísmica global en tiempo real y mantener un repositorio histórico que alimente nuestros modelos actuariales. Actualmente dependemos de un proveedor externo que nos envía reportes en PDF cada semana. Esto es inaceptable: cuando ocurre un terremoto significativo, necesitamos saberlo en minutos, no en días.

Tu misión es construir un **pipeline ETL automatizado** que extraiga datos sísmicos de la API pública del USGS (United States Geological Survey), los transforme y enriquezca con cálculos de riesgo, y los cargue en una base de datos MySQL que nuestro equipo de actuarios pueda consultar directamente.

---

## Fuente de datos

Usarás la **API del USGS Earthquake Hazards Program** (https://earthquake.usgs.gov/fdsnws/event/1/), una API pública del gobierno de Estados Unidos que proporciona datos sísmicos globales en tiempo real. No requiere autenticación ni API key.

### Endpoint principal

```
GET https://earthquake.usgs.gov/fdsnws/event/1/query
```

### Parámetros clave

| Parámetro | Descripción | Ejemplo |
|---|---|---|
| `format` | Formato de respuesta | `geojson` |
| `starttime` | Fecha inicio (ISO 8601) | `2024-01-01` |
| `endtime` | Fecha fin (ISO 8601) | `2024-01-31` |
| `minmagnitude` | Magnitud mínima | `2.5` |
| `maxmagnitude` | Magnitud máxima | `10` |
| `minlatitude` | Latitud mínima del bounding box | `-90` |
| `maxlatitude` | Latitud máxima del bounding box | `90` |
| `minlongitude` | Longitud mínima | `-180` |
| `maxlongitude` | Longitud máxima | `180` |
| `orderby` | Ordenamiento | `time`, `magnitude` |
| `limit` | Máximo de resultados | `20000` |

### Regiones de monitoreo

Nuestros clientes operan en las zonas sísmicas más activas del mundo. Tu pipeline debe cubrir estas 8 regiones:

| Región | Nombre interno | Lat min | Lat max | Lon min | Lon max |
|---|---|---|---|---|---|
| Pacífico Noroeste (EE.UU.) | `pacific_northwest` | 41.0 | 49.0 | -130.0 | -116.0 |
| California | `california` | 32.0 | 42.0 | -125.0 | -114.0 |
| Japón | `japan` | 30.0 | 46.0 | 128.0 | 146.0 |
| Indonesia | `indonesia` | -11.0 | 6.0 | 95.0 | 141.0 |
| Chile – Perú | `south_america_west` | -56.0 | -5.0 | -82.0 | -66.0 |
| Mediterráneo (Turquía – Grecia – Italia) | `mediterranean` | 34.0 | 45.0 | 10.0 | 45.0 |
| Himalaya (Nepal – India) | `himalaya` | 24.0 | 36.0 | 72.0 | 96.0 |
| Nueva Zelanda | `new_zealand` | -50.0 | -34.0 | 165.0 | 180.0 |

### Ejemplo de llamada a la API

**Terremotos en Japón del último mes, magnitud ≥ 2.5:**
```
https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&starttime=2025-04-01&endtime=2025-05-01&minmagnitude=2.5&minlatitude=30.0&maxlatitude=46.0&minlongitude=128.0&maxlongitude=146.0&orderby=time
```

Pega esta URL en tu navegador para ver la respuesta. Estudia la estructura GeoJSON antes de codificar.

### Estructura de la respuesta (GeoJSON)

```json
{
  "type": "FeatureCollection",
  "metadata": {
    "generated": 1714500000000,
    "count": 152,
    "title": "USGS Earthquakes",
    "status": 200
  },
  "features": [
    {
      "type": "Feature",
      "properties": {
        "mag": 5.4,
        "place": "28 km SSW of Shizunai, Japan",
        "time": 1714480000000,
        "updated": 1714490000000,
        "tz": null,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000...",
        "detail": "https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=...",
        "felt": 120,
        "cdi": 4.2,
        "mmi": 5.1,
        "alert": "green",
        "status": "reviewed",
        "tsunami": 0,
        "sig": 449,
        "net": "us",
        "code": "7000m4vf",
        "nst": 98,
        "dmin": 0.832,
        "rms": 0.71,
        "gap": 28,
        "magType": "mww",
        "type": "earthquake"
      },
      "geometry": {
        "type": "Point",
        "coordinates": [142.5, 41.8, 35.0]
      },
      "id": "us7000m4vf"
    }
  ]
}
```

**Campos críticos para tu pipeline:**

| Campo | Ubicación en JSON | Descripción |
|---|---|---|
| `id` | `features[].id` | Identificador único del evento |
| `mag` | `properties.mag` | Magnitud del terremoto |
| `place` | `properties.place` | Descripción textual de la ubicación |
| `time` | `properties.time` | Timestamp UNIX en milisegundos |
| `felt` | `properties.felt` | Número de reportes "lo sentí" de la comunidad (puede ser `null`) |
| `cdi` | `properties.cdi` | Intensidad comunitaria reportada (puede ser `null`) |
| `mmi` | `properties.mmi` | Intensidad instrumental máxima (puede ser `null`) |
| `alert` | `properties.alert` | Nivel de alerta PAGER: `green`, `yellow`, `orange`, `red` (puede ser `null`) |
| `tsunami` | `properties.tsunami` | 1 si hay alerta de tsunami, 0 si no |
| `sig` | `properties.sig` | Significancia del evento (0–1000+). Combina magnitud, impacto y reportes |
| `magType` | `properties.magType` | Tipo de magnitud: `ml`, `md`, `mb`, `ms`, `mww`, `mwc`, etc. |
| `type` | `properties.type` | Tipo de evento: `earthquake`, `quarry blast`, `explosion`, etc. |
| `status` | `properties.status` | Estado de revisión: `automatic`, `reviewed`, `deleted` |
| `longitude` | `geometry.coordinates[0]` | Longitud |
| `latitude` | `geometry.coordinates[1]` | Latitud |
| `depth` | `geometry.coordinates[2]` | Profundidad en km |

---

## Requisitos del pipeline

### Fase 1: Extracción (Extract)

- Extraer datos sísmicos de las **8 regiones** definidas.
- **Modo diario (forecast):** extraer los eventos de las últimas 24 horas para todas las regiones, con magnitud ≥ 1.0.
- **Modo histórico (backfill):** extraer datos de un rango de fechas personalizado con magnitud ≥ 2.5. **Restricción importante**: la API del USGS no permite rangos de más de ~30 días cuando el número de resultados supera 20,000. Tu código debe segmentar automáticamente rangos largos en bloques de 30 días.
- **Modo alerta (alert):** extraer solo los eventos de la última hora con magnitud ≥ 4.5 globalmente (sin filtro de región). Este es el modo que ejecutaríamos cada 5 minutos en producción.
- Implementar **retry con backoff exponencial**: reintentar hasta 3 veces ante errores HTTP (5xx, timeout) con espera creciente (2s, 4s, 8s).
- Registrar en log cada request: región, rango de fechas, status code, cantidad de eventos recibidos, tiempo de respuesta.
- **Filtrar solo eventos tipo `earthquake`**: la API también devuelve explosiones en canteras (`quarry blast`), pruebas nucleares (`nuclear explosion`), etc. Esos no nos interesan.

### Fase 2: Transformación (Transform)

- **Conversión de timestamps:** la API devuelve timestamps UNIX en milisegundos. Convertirlos a `DATETIME` UTC para MySQL.
- **Clasificación de magnitud:** agregar una columna `magnitud_clase` basada en la escala de Richter estándar:

  | Magnitud | Clase | Descripción |
  |---|---|---|
  | < 2.0 | `micro` | Generalmente no sentido |
  | 2.0 – 3.9 | `minor` | Sentido levemente |
  | 4.0 – 4.9 | `light` | Daños menores posibles |
  | 5.0 – 5.9 | `moderate` | Daños a estructuras débiles |
  | 6.0 – 6.9 | `strong` | Daños significativos en área poblada |
  | 7.0 – 7.9 | `major` | Daños graves en área amplia |
  | ≥ 8.0 | `great` | Devastador, potencialmente global |

- **Clasificación de profundidad:** agregar una columna `profundidad_clase`:

  | Profundidad | Clase |
  |---|---|
  | 0 – 70 km | `shallow` (superficial, más destructivo) |
  | 70 – 300 km | `intermediate` |
  | > 300 km | `deep` (profundo, generalmente menos daño en superficie) |

- **Cálculo de energía liberada:** agregar columna `energia_joules` usando la fórmula de Gutenberg-Richter:
  ```
  log10(E) = 1.5 * M + 4.8
  E = 10^(1.5 * M + 4.8)
  ```
  Donde M es la magnitud y E es la energía en joules.

- **Cálculo del índice de riesgo compuesto:** crear una métrica propietaria `risk_score` (0–100) que combine:
  ```
  risk_score = (mag_norm * 0.40) + (depth_norm * 0.25) + (sig_norm * 0.20) + (pop_proxy * 0.15)
  ```
  Donde:
  - `mag_norm` = (magnitud / 10) × 100, cap en 100
  - `depth_norm` = max(0, (1 - profundidad/700)) × 100 (más superficial = más riesgo)
  - `sig_norm` = min(significancia / 10, 100)
  - `pop_proxy` = si `felt` es `null` → 0; si no, min(felt / 100, 100). Nota: `felt` > 0 indica que personas lo reportaron, lo que sugiere cercanía a una zona poblada.

- **Asignación de región:** cada evento debe asociarse a la región de monitoreo donde cayó (basado en sus coordenadas y los bounding boxes definidos). Si un evento no cae en ninguna región monitoreada (solo posible en modo `alert`), asignar `"global_other"`.

- **Validación de datos:**
  - Magnitud: descartar valores negativos o `null` (eventos sin magnitud calculada)
  - Profundidad: descartar valores negativos (error de cálculo del USGS, ocurre raramente)
  - Coordenadas: latitud entre -90 y 90, longitud entre -180 y 180
  - Eventos con `status = "deleted"`: descartar
  - Registrar cada descarte en un log de calidad

- **Deduplicación:** la API puede devolver el mismo evento en dos llamadas distintas (si cae en el borde de dos regiones, o si el pipeline se ejecuta más de una vez). Usar el campo `id` como clave única.

### Fase 3: Carga (Load)

Diseñar e implementar el siguiente esquema en MySQL:

- **`regiones`** — catálogo de las 8 regiones de monitoreo con sus bounding boxes.
- **`terremotos`** — tabla principal con un registro por evento sísmico. Clave primaria: `event_id` (el `id` del USGS).
- **`alertas_tsunami`** — tabla separada para eventos con `tsunami = 1`, con timestamp de detección y región.
- **`estadisticas_diarias`** — tabla agregada (calculada por tu pipeline) con resumen por región por día:
  - Total de eventos
  - Magnitud máxima
  - Magnitud promedio
  - Profundidad promedio
  - Cantidad de eventos por clase de magnitud
  - `risk_score` máximo del día
- **`log_ejecuciones`** — registro de cada ejecución del pipeline.
- **`log_calidad_datos`** — registro de eventos descartados con razón.

**Requisitos de carga:**
- Usar `INSERT ... ON DUPLICATE KEY UPDATE` (UPSERT) para que el pipeline sea idempotente.
- Cargar en batches de 200 registros.
- Las `estadisticas_diarias` deben recalcularse al final de cada ejecución (no acumularse).
- Crear índices apropiados para consultas frecuentes: por fecha, por región, por magnitud, por risk_score.

---

## Requisitos técnicos

### Obligatorios

- **Lenguaje:** Python 3.9+
- **Librerías permitidas:** `requests`, `pandas`, `mysql-connector-python` o `pymysql` o `SQLAlchemy`, `logging`, `argparse`, `math`, `time`, `datetime`, `pathlib`, `json`, `pyyaml`. Cualquier librería estándar.
- **Base de datos:** MySQL 8.0+
- **CLI:**
  ```bash
  # Extracción diaria: últimas 24h, todas las regiones, mag ≥ 1.0
  python main.py --mode daily

  # Extracción en modo alerta: última hora, global, mag ≥ 4.5
  python main.py --mode alert

  # Backfill histórico: rango personalizado
  python main.py --mode historical --start-date 2024-01-01 --end-date 2024-06-30

  # Solo una región específica
  python main.py --mode daily --region japan

  # Dry-run: extrae y transforma pero no carga
  python main.py --mode daily --dry-run

  # Verbose logging
  python main.py --mode daily --verbose
  ```
- **Configuración externalizada:** credenciales de MySQL en `.env`, regiones y parámetros del pipeline en `config.yaml`.
- **Logging:** módulo `logging` de Python, logs a consola y archivo. Formato: `[2025-05-12 14:32:01] [INFO] [extractor] Región japan: 47 eventos extraídos (200ms)`.
- **Manejo de errores:** si una región falla, el pipeline continúa con las demás y reporta al final.
- **Script SQL de inicialización:** `sql/init_db.sql` que cree toda la estructura.
- **No hardcodear:** ni credenciales, ni URLs base, ni nombres de regiones en el código principal.

### Deseables (suman puntos)

- Tests unitarios con `pytest` para: clasificación de magnitud, cálculo de energía, cálculo de risk_score, asignación de región.
- Type hints en todas las funciones.
- `docker-compose.yml` que levante MySQL + ejecute el pipeline.
- Generación de un mini-reporte Markdown al final de cada ejecución con:
  - Resumen global (total de eventos, máxima magnitud, alertas de tsunami).
  - Tabla de eventos con risk_score > 70.
  - Desglose por región.
- Al menos 2 gráficos con `matplotlib`:
  - Mapa de dispersión (scatter plot) de eventos coloreados por magnitud (lat/lon).
  - Barras de eventos por región.
- Diagrama ER del esquema de base de datos.
- Un endpoint de verificación de salud: un script `health_check.py` que confirme que MySQL está accesible y que la última ejecución fue exitosa.

### No queremos ver

- Credenciales en el código o en commits de Git.
- Un archivo monolítico de 600+ líneas.
- Queries SQL con string concatenation (`f"SELECT * FROM x WHERE id = {user_input}"`). Usa parámetros.
- `except: pass` o `except Exception: continue` sin logging.
- Variables con nombres como `x`, `df2`, `temp`, `data2`.
- Timestamps almacenados como strings en MySQL. Usa el tipo `DATETIME`.
- Mezcla inconsistente de inglés y español en el código.

---

## Estructura sugerida del repositorio

```
seismic-etl-pipeline/
├── README.md
├── requirements.txt
├── config/
│   ├── config.yaml              # Regiones, parámetros, umbrales
│   └── .env.example             # Template de credenciales
├── sql/
│   ├── init_db.sql              # Creación de BD y tablas
│   └── useful_queries.sql       # Queries de ejemplo para actuarios (deseable)
├── src/
│   ├── __init__.py
│   ├── extractor.py             # Llamadas a la API del USGS
│   ├── transformer.py           # Limpieza, clasificación, cálculos
│   ├── loader.py                # Carga a MySQL
│   ├── aggregator.py            # Cálculo de estadísticas diarias
│   ├── models.py                # Dataclasses / esquemas
│   └── utils.py                 # Rate limiter, retry, helpers
├── tests/
│   ├── test_transformer.py
│   ├── test_extractor.py
│   └── fixtures/                # JSONs de ejemplo para tests
│       └── sample_response.json
├── logs/
│   └── .gitkeep
├── output/
│   └── .gitkeep
├── main.py                      # Punto de entrada CLI
├── health_check.py              # (deseable) Verificación de salud
├── Makefile                     # (deseable) Comandos de conveniencia
├── docker-compose.yml           # (deseable) MySQL + pipeline
└── .gitignore
```

---

## Criterios de evaluación

| Dimensión | Peso | Qué evaluamos |
|---|---|---|
| Pipeline funcional end-to-end | 25% | ¿Los 3 modos (daily, alert, historical) extraen, transforman y cargan sin intervención manual? |
| Diseño del esquema de BD | 20% | Normalización, tipos de datos correctos, índices, claves primarias compuestas donde corresponde, UPSERT funcional |
| Robustez y manejo de errores | 20% | ¿Qué pasa si la API responde con error? ¿Si una región no tiene datos? ¿Si el pipeline se ejecuta 3 veces seguidas? ¿Si el backfill pide 6 meses y la API limita a 30 días? |
| Calidad del código | 15% | Modularización clara (E-T-L separados), nombres descriptivos, sin código muerto, responsabilidad única por función |
| Documentación | 10% | README completo con instrucciones que funcionen, diagrama ER, decisiones justificadas |
| Extras | 10% | Tests, Docker, gráficos, health check, reporte automático |

---

## Guía de instalación de MySQL

### Opción recomendada: Docker
```bash
docker run --name mysql-seismic \
  -e MYSQL_ROOT_PASSWORD=atlas2025 \
  -e MYSQL_DATABASE=seismic_db \
  -p 3306:3306 \
  -d mysql:8.0
```

Verificar conexión:
```bash
mysql -h 127.0.0.1 -u root -patlas2025 seismic_db
```

---

## Pistas y consejos

1. **Empieza con el modo `alert` — es el más simple.** Una sola request global, pocos datos, sin segmentación. Haz que el pipeline funcione end-to-end para este modo primero. Después expande a `daily` y luego a `historical`.

2. **Estudia la respuesta GeoJSON a fondo.** Abre las URLs en tu navegador. Nota que `geometry.coordinates` es `[longitud, latitud, profundidad]`, no `[latitud, longitud]` — este es un error clásico que el 50% de los candidatos cometen.

3. **La segmentación de rangos largos es un problema real.** Si pides 6 meses de datos globales, la API va a devolver un error porque el resultado excede 20,000 eventos. Tu código debe detectar esto y dividir automáticamente el rango en bloques de 30 días. Esto es un patrón que usarás constantemente como data engineer.

4. **Cuidado con los `null`.** Los campos `felt`, `cdi`, `mmi` y `alert` son frecuentemente `null` (especialmente para eventos pequeños o en zonas remotas). Tu código debe manejar esto sin crashear. En MySQL, almacénalos como `NULL`, no como 0 o strings vacíos.

5. **El risk_score es inventado por nosotros, no por el USGS.** Esto es intencional: en el mundo real, un data engineer frecuentemente debe implementar lógica de negocio personalizada sobre datos crudos. Documenta la fórmula en tu README.

6. **El campo `sig` (significance) ya combina varios factores**, pero no incluye profundidad ni cercanía a población de la forma que nosotros necesitamos. Por eso creamos nuestro propio `risk_score`.

7. **Para los tests, usa fixtures.** Guarda un JSON de ejemplo de la API en `tests/fixtures/sample_response.json` y usa ese archivo en tus tests en lugar de llamar a la API real. Nunca hagas llamadas HTTP en tests unitarios.

8. **`INSERT ... ON DUPLICATE KEY UPDATE` con el `event_id` del USGS como clave primaria** es tu mecanismo de idempotencia. El USGS actualiza los eventos (cambia la magnitud, agrega el campo `alert`, actualiza `felt`), así que tu UPSERT debe sobreescribir los datos cuando el evento ya existe pero vino con valores actualizados.

---

## Queries de ejemplo para los actuarios

Cuando tu pipeline esté funcionando, los actuarios deberían poder ejecutar consultas como estas. Inclúyelas en `sql/useful_queries.sql` como referencia:

```sql
-- ¿Cuántos terremotos fuertes (mag ≥ 6.0) hubo en Japón en los últimos 90 días?

-- ¿Cuál es la región con mayor risk_score promedio este mes?

-- ¿Cuántas alertas de tsunami se han generado por región en el último año?

-- Top 10 terremotos más significativos de los últimos 30 días globalmente

-- Promedio de eventos diarios por región (para calcular frecuencia base de las primas)
```

No te damos las queries resueltas — las vas a escribir tú como parte de la entrega. Esto demuestra que entiendes tanto SQL como el dominio del negocio.

---

## Reglas

- Puedes usar IA generativa para acelerar tu trabajo, pero **debes entender y poder explicar cada línea de código, cada decisión de diseño, y cada query SQL**. En la entrevista simularemos una situación donde necesitas modificar el pipeline en vivo (por ejemplo: "agrega una nueva región" o "cambia la fórmula del risk_score").
- Si tienes dudas sobre los requisitos, documenta tu interpretación en el README.
- Entrega lo que funcione. Un pipeline que procesa 3 regiones correctamente con buen manejo de errores vale más que uno que intenta 8 pero falla silenciosamente.
- **No incluyas credenciales reales en tu repositorio.**

## Contacto

Envía el link de tu repositorio público de GitHub a `data-engineering@atlas-reinsurance.example.com` antes de la fecha límite.

---

*Los terremotos no esperan. Tu pipeline tampoco debería.*

**— Equipo de Data Engineering, Atlas Reinsurance Analytics**
