Run the API locally:

```bash
uvicorn server.app:app --reload --host 0.0.0.0 --port 8000
```

Endpoints:
- POST /runs — start a run
- GET /runs — list runs
- GET /runs/{id} — run info
- GET /runs/{id}/artifacts/results.json — results
- GET /runs/{id}/artifacts/summary.txt — summary
