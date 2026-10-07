# Geospatial File Measurement API

Backend service for extracting features and measurements from geospatial files.

## Current progress

Phases 1 through 6 are implemented, including geometry/API tests and a Locust load-test profile. Phase 7 containerization files are in place; the Docker image and Compose stack still need a local build and run.

## Setup

Requires Python 3.10 or newer.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`. Interactive API documentation is available at `/docs`.

To install test and load-testing dependencies and run the unit and API integration tests:

```powershell
py -m pip install -r requirements-dev.txt
py -m unittest discover -s tests -v
```

The suite includes geometry calculation unit tests and API integration tests for uploads, measurements, cache reuse, and large-file job status. The asynchronous job tests mock Redis queue publication; use a running Redis server and RQ worker for a live queue check.

## Container deployment

Docker Compose starts the API, Redis, and one RQ worker. The API and worker share a persistent volume for SQLite results and uploaded files; Redis uses a separate persistent volume. The API runs as an unprivileged user in a multi-stage Python image. Pyogrio's PyPI wheels include GDAL, so the final image does not need GDAL build packages.

```powershell
docker compose up --build
```

Check `http://127.0.0.1:8000/health` and open `http://127.0.0.1:8000/docs`. To change the host port or cache/job threshold, set `API_PORT`, `CACHE_TTL_SECONDS`, or `ASYNC_THRESHOLD_BYTES` before starting Compose. Stop the services with `Ctrl+C`, then run `docker compose down`; persistent data volumes remain available for the next start.

Compose deployment files are `Dockerfile`, `docker-compose.yml`, and `.dockerignore`.

## API

### Health check

`GET /health` returns `200 OK` while the API process is running:

```json
{"status":"ok"}
```

### Upload and process a file

`POST /api/v1/measure` accepts one `multipart/form-data` field named `file`. Supported inputs are `.kml` and `.zip` archives containing matching `.shp`, `.shx`, and `.dbf` components. The default maximum file size is 100 MiB; set `MAX_UPLOAD_SIZE_BYTES` to change it. Uploaded files are stored under `data/uploads` by default; set `UPLOAD_DIRECTORY` to change that location.

Files larger than 5 MiB are queued for background processing and return `202 Accepted`; set `ASYNC_THRESHOLD_BYTES` to change the threshold. Smaller uploads continue to process synchronously.

```powershell
curl.exe -F "file=@survey.kml" http://127.0.0.1:8000/api/v1/measure
```

Successful upload response (`201 Created`):

```json
{"id":"<file-id>","filename":"survey.kml","size_bytes":1234,"feature_count":12,"crs":"EPSG:4326","status":"COMPLETED"}
```

Uploads are SHA-256 hashed while streaming. The API caches processed features and summary metadata in Redis for 24 hours by default (`CACHE_TTL_SECONDS` changes the TTL). An identical upload reuses the measurements while storing a summary for its new file ID and filename; the response includes `"cache_hit": true`. Cache reads and writes are best-effort, so a Redis cache outage falls back to normal synchronous processing. Large uncached uploads still require Redis and an RQ worker.

Unsupported, oversized, empty, malformed, or unreadable files return `400 Bad Request`.

### Large-file job status

`GET /api/v1/measure/{job_id}` returns `QUEUED`, `RUNNING`, `COMPLETED`, or `FAILED`. A completed job response includes the file summary and processed features. A failed job includes a safe error message.

Large-file processing requires a Redis server and at least one RQ worker. Set `REDIS_URL` if Redis is not at `redis://localhost:6379/0`. Start a Redis server, then start the API and worker in separate terminals. On Windows, use RQ's spawn-based worker:

```powershell
$env:REDIS_URL = "redis://localhost:6379/0"
rq worker-pool --url $env:REDIS_URL --num-workers 2 --worker-class rq.worker.SpawnWorker --serializer json geospatial-measurements
```

On Linux or macOS, omit `--worker-class rq.worker.SpawnWorker` to use RQ's default process worker. The API returns `503 Service Unavailable` for large uploads when Redis cannot accept the job; it removes that upload so it can be retried.

### File information

`GET /api/files/{id}/` returns the persisted file summary, including filename, feature count, CRS, and status.

### Feature measurements

`GET /api/files/{id}/measurements/` returns each feature's index, geometry, CRS, properties, and optional measurement. Polygon and MultiPolygon area is in square metres; LineString and MultiLineString length is in metres. Point and other unsupported geometries remain in the response with no measurement and a status. Measurements transform each feature into a local projected CRS selected from its location. KML is treated as EPSG:4326 when the reader does not report its standard CRS. Shapefile features without a declared CRS remain available but have no measurement.

Unknown file IDs return `404 Not Found`.

## Storage and structure

File summaries and feature results are stored in SQLite at `data/geospatial.sqlite3` by default; set `DATABASE_PATH` to change that location.

- `app/main.py` - FastAPI routes and application lifecycle.
- `app/uploads.py` - chunked upload storage and file validation.
- `app/geoprocessing.py` - Pyogrio feature reading and Shapely/PyProj measurements.
- `app/jobs.py` - Redis Queue publishing and measurement job execution.
- `app/cache.py` - Redis-backed, SHA-256 keyed result cache and TTL.
- `app/repository.py` - SQLite persistence for file summaries and features.
- `app/logging_config.py` - JSON logging configuration.
- `tests/test_geoprocessing.py` - geometry and CRS measurement unit tests.
- `locustfile.py` - concurrent KML upload benchmark.

## Load testing

Phase 6 load testing uses Locust. Start the API, then run the benchmark in another terminal:

Create the CSV output directory first, then run the load profile:

```powershell
New-Item -ItemType Directory -Force results | Out-Null
locust -f locustfile.py --host http://127.0.0.1:8000 --headless -u 200 -r 20 -t 2m --csv results/phase6
```

This ramps up to 200 simulated users at 20 users per second for two minutes. Uploads alternate between unique polygon and line KML files so processing is measured without cache hits; health checks run more often. Locust reports request count, failures, throughput, and response-time percentiles, with CSV output under `results/`. Adjust `-u`, `-r`, and `-t` for the machine and run duration. Treat this as a benchmark profile, not a preset service-level guarantee: record the machine, Python/GDAL versions, Redis state, and run settings when comparing results.

