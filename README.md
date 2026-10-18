# Sensor Metadata Management Platform

Metadata catalog for measurements, physical devices, network elements, controlled vocabularies, Excel exchange, and geospatial records. The application does not ingest or store sensor readings or time-series data.

## Local start

The recommended stack is Docker Compose with PostgreSQL 16 + PostGIS 3.4, FastAPI, and the React/Vite UI. Copy `.env.example` to `.env`, set unique local secrets, then run:

```sh
docker compose up --build
```

The UI is at http://localhost:5173 and the API docs at http://localhost:8000/docs. The database uses a named persistent volume. The API runs the Alembic migration before startup.

On first run, create the initial account and synthetic example data. Use a password of at least 12 characters:

```sh
curl -X POST http://localhost:8000/api/setup \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"replace-with-a-long-local-password","display_name":"Local Admin","demo":true}'
```

This one-time bootstrap is enabled by default for local use and returns the created department ID. It refuses to run once a user exists. After initial setup, set `ALLOW_SETUP=false` in `.env` and restart the API. Never use example passwords or the default Compose values outside an isolated local environment. For production, set a random `SESSION_SECRET`, a strong database password, `HTTPS_ONLY=true`, and exact trusted `CORS_ORIGINS`; terminate TLS at a trusted reverse proxy.

The same setup is available from `backend/seed.py` after the migration, for example `python seed.py --username admin --password 'a-long-local-password' --demo`.

To run without Docker, use Python 3.12+, Node 22+, and a PostgreSQL/PostGIS database:

```sh
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql+psycopg://user:password@localhost:5432/sensor_metadata'
export SESSION_SECRET='use-a-long-random-value'
alembic -c alembic.ini upgrade head
uvicorn main:app --reload
```

In a second terminal:

```sh
cd frontend
npm ci
npm run dev
```

Set `VITE_API_URL` before the frontend build if the API is not at `http://localhost:8000`. Without `DATABASE_URL`, the backend uses a local SQLite file to support development and automated tests; SQLite does not provide PostGIS geometry operations.

## Data model and access policy

The backend has organizations, departments, users, roles, memberships, globally shared controlled lists, measurements, physical devices, network elements, one-to-one collection configuration, API keys, and safe activity events. UUIDs identify records; external operational identifiers remain strings. Measurements have required device and network element links with composite same-department database constraints. Devices and network elements inactivate instead of being deleted, and inactivation is blocked while active dependent records exist. The spatial columns use PostGIS `geometry(Point,4326)` for devices and `geometry(Geometry,4326)` for network elements. GeoJSON output uses longitude, latitude order and omits records without locations.

Roles are scoped per department (Viewer, Editor, Department Administrator). System Administrator is global. Department Administrators can manage department users and shared controlled values; changes to shared values affect all departments. API keys are shown once, stored as SHA-256 hashes, scoped to one department, and read-only. This role scope and global list behavior are safe defaults that should be confirmed with the platform owner.

The synthetic demo seed includes OrgX, NE_000062, NE_000235, RTUXXX, RTUXX2 and LLWD_000286, LLWD_000287, LLWD_000665. Example credentials/IPs were changed to non-production synthetic values: `192.0.2.10` / `.11` are documentation-only addresses and the IOT URL uses the reserved `.invalid` domain. No intentionally invalid sample IP is inserted.

## Excel contract

Import accepts these operational worksheet names and exact headers/order: `Measurements` (23), `NetworkElements` (11), and `Physical_devices` (7). A blank eighth trailing `Physical_devices` column is tolerated. Supporting sheets such as `Statistics`, `LOVs`, and `Structure` are ignored. JSON fields are JSON objects in the database and valid JSON strings in workbooks. The import commit validates references, same-department links, duplicates, JSON, coordinates, controlled values, and provided derived values before inserting all rows in one transaction. Conflicts never overwrite existing records. Export writes all three operational sheets in the same order.

## Tests and checks

```sh
cd backend
python -m pytest -q
alembic -c alembic.ini upgrade head
alembic -c alembic.ini downgrade -1  # roll back one revision
```

`alembic -c alembic.ini downgrade base` removes the application schema; take a database backup before using it.

```sh
cd frontend
npm run lint
npm run build
```

API smoke test: bootstrap a local admin, sign in at the UI, switch departments, add a network element and physical device, create a measurement with both links, then export and re-import the workbook. To confirm persistence, restart the Compose services and verify the records remain. For backups, run `docker compose exec -T db pg_dump -U sensor sensor_metadata > sensor-metadata.sql`; restore into an empty database with `docker compose exec -T db psql -U sensor sensor_metadata < sensor-metadata.sql`.

## Current implementation limits

- The Administration screen supports department user creation and API key creation/revocation. Organization and department creation, membership assignment/role changes/removal, and cross-department administration are API-only. Account password-reset workflows are not implemented.
- Controlled values can be added, edited, and inactivated. The UI has catalog search and server pagination, but does not yet expose all API filters/sort controls, column customization, unsaved-change warnings, or inline related-record creation modals. Collection JSON uses a general editor rather than protocol-specific ODBC/IOT editors.
- GIS supports stored point, line, and polygon GeoJSON and map display. The UI does not yet provide drawing/editing tools or map layer filtering. OSM tiles require network access in the browser.
- Entity payloads are validated by the API, but the generic entity endpoints use a permissive Pydantic input envelope rather than fully enumerated request/response models for each operation.
- Sessions use signed HttpOnly cookies with SameSite=Lax and an eight-hour expiry. A separate CSRF token endpoint is not implemented; deploy behind same-origin TLS and retain strict trusted origins.
- The basic test suite exercises login, department scoping, dashboard aggregation, IP validation, required links, parent-inactivation blocking, and missing-location behavior. Comprehensive 10,000-record timing, 1,000-row import timing, full rollback matrix, session-expiry, and API-key lifecycle tests still need to be run against a live PostGIS deployment.
- Docker Compose configuration was checked, but PostGIS container startup has not been verified in this environment because Docker Desktop/the Docker daemon is unavailable.
- Initial schema migration creates tables from the SQLAlchemy model metadata. For future schema changes, add explicit forward and reverse Alembic revisions and test upgrades against a copy of production data.

No Git commit or push was performed.
