# GTM Hackathon

Backend-first scaffold for the hackathon. The UI can be built separately in Lovable and call this API over HTTP.

## FastAPI backend

### Run locally

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The API runs at `http://127.0.0.1:8000`.

### Useful routes

- `GET /health` - service health check
- `GET /api/v1/status` - API readiness payload for UI smoke tests
- `POST /api/v1/leads` - placeholder lead capture endpoint for the Lovable UI

FastAPI's interactive docs are available at `http://127.0.0.1:8000/docs`.

### Lovable UI connection

When the Lovable app is ready, point its API base URL to:

```text
http://127.0.0.1:8000/api/v1
```

For deployed environments, set `CORS_ORIGINS` to a comma-separated list of allowed UI origins:

```bash
CORS_ORIGINS=https://your-lovable-app.lovable.app,https://yourdomain.com
```
