from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from typing import Dict, Any, Callable
from pathlib import Path
from datetime import datetime

from server.models import RunCreateRequest, RunInfo, RunList
from server.storage import ensure_run_dirs, results_json_path, summary_txt_path, run_dir, DATA_ROOT
from run_workflow import WorkflowRunner


app = FastAPI(title="ANOLE API", version="0.1.0")

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "*",  # relax for container network; tighten later
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_RUNS: Dict[str, RunInfo] = {}
_SUBSCRIBERS: Dict[str, list[Callable[[Dict[str, Any]], None]]] = {}


def _append_log(run_id: str, event: Dict[str, Any]):
    log_path = run_dir(run_id) / "logs" / "logs.ndjson"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        import json
        f.write(json.dumps(event) + "\n")
    # fan-out to in-memory subscribers
    for cb in _SUBSCRIBERS.get(run_id, []):
        try:
            cb(event)
        except Exception:
            pass


def _run_workflow_background(run_id: str, prompt: str, max_iterations: int | None, batch_size: int | None):
    runner = WorkflowRunner(checkpoint_dir=str(run_dir(run_id) / "checkpoints"))

    def emit(event: Dict[str, Any]):
        _append_log(run_id, event)

    state = runner.run(user_prompt=prompt, checkpoint_path=None, save_checkpoints=True, event_callback=emit)
    # Optionally override max_iterations/batch_size
    if max_iterations is not None:
        state.max_iterations = max_iterations
    if batch_size is not None and state.bo_config:
        state.bo_config.batch_size = batch_size

    # Export results
    results_path = results_json_path(run_id)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    runner.export_results(state, str(results_path))

    # Write summary
    if state.summary:
        s_path = summary_txt_path(run_id)
        s_path.write_text(state.summary)

    # Update run info
    info = _RUNS.get(run_id)
    if info:
        info.status = str(state.status)
        info.exit_reason = state.exit_reason
        info.updated_at = datetime.now()
        info.summary_available = state.summary is not None
        info.results_available = results_path.exists()
        _append_log(run_id, {"ts": datetime.now().isoformat(), "event": "run_completed", "status": info.status})


@app.post("/runs", response_model=RunInfo)
def create_run(req: RunCreateRequest, background: BackgroundTasks):
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    paths = ensure_run_dirs(run_id)
    (Path(paths["inputs"]) / "prompt.txt").write_text(req.prompt)

    info = RunInfo(
        id=run_id,
        status="running",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        paths=paths,
    )
    _RUNS[run_id] = info

    background.add_task(_run_workflow_background, run_id, req.prompt, req.max_iterations, req.batch_size)
    return info


@app.get("/runs", response_model=RunList)
def list_runs():
    return RunList(runs=sorted(_RUNS.values(), key=lambda r: r.created_at, reverse=True))


@app.get("/runs/{run_id}", response_model=RunInfo)
def get_run(run_id: str):
    info = _RUNS.get(run_id)
    if not info:
        raise HTTPException(404, "Run not found")
    return info


@app.get("/runs/{run_id}/events")
def sse_events(run_id: str):
    import queue

    q: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def push(evt: Dict[str, Any]):
        q.put(evt)

    _SUBSCRIBERS.setdefault(run_id, []).append(push)

    def stream():
        try:
            # send a hello event
            yield f"event: hello\ndata: {{\"run_id\": \"{run_id}\"}}\n\n"
            while True:
                evt = q.get()
                import json
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            # remove subscriber
            subs = _SUBSCRIBERS.get(run_id, [])
            if push in subs:
                subs.remove(push)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.delete("/runs/{run_id}")
def delete_run(run_id: str):
    info = _RUNS.pop(run_id, None)
    base = run_dir(run_id)
    if base.exists():
        import shutil
        shutil.rmtree(base)
    return {"deleted": bool(info)}


@app.get("/runs/{run_id}/checkpoints")
def list_checkpoints(run_id: str):
    base = run_dir(run_id) / "checkpoints"
    if not base.exists():
        return []
    items = sorted([p.name for p in base.glob("*")])
    return items


@app.get("/runs/{run_id}/artifacts/results.json")
def get_results(run_id: str):
    p = results_json_path(run_id)
    if not p.exists():
        raise HTTPException(404, "Results not found")
    return FileResponse(str(p), media_type="application/json")


@app.get("/runs/{run_id}/artifacts/summary.txt")
def get_summary(run_id: str):
    p = summary_txt_path(run_id)
    if not p.exists():
        raise HTTPException(404, "Summary not found")
    return FileResponse(str(p), media_type="text/plain")
