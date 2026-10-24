import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import MODELS, ROOT, model_name, set_model, state_root
from .research import package, research
from .runner import hosted_play
from .simulation import OPPONENTS, local_cases, local_play
from .store import clean, get_run, list_runs, new_run, save_run

app = FastAPI(title="AEIOU Negotiation Workbench", docs_url="/api/docs", redoc_url=None)
# Built from frontend/ with `npm run build`; the output is committed so no Node is needed to serve it.
WEB = ROOT / "src" / "aeiou" / "web"
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")
POOL = ThreadPoolExecutor(max_workers=4)
JOBS, JOB_LOCK = {}, threading.Lock()


@app.middleware("http")
async def local_origin_guard(request: Request, call_next):
    # This dashboard controls paid API calls. Cross-origin sites cannot submit localhost jobs.
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Cross-origin requests are not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@lru_cache
def pkg():
    return package()


def hosted_cases():
    file = state_root() / "hosted-cases.json"
    return json.loads(file.read_text()) if file.is_file() else list(pkg().cases.values())


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/status")
def status():
    return {
        "model": model_name(),
        "models": [{"id": k, "label": v} for k, v in MODELS.items()],
        "claude_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "arena_configured": bool(os.environ.get("ARENA_API_KEY")),
        "facts": len(pkg().facts),
        "tables": pkg().tables(),
        "sunday_release": pkg().release is not None,
        "data_notes": "Frozen buyer world: 30 September 2026. Refresh Sunday release before final.",
        "jobs": JOBS,
    }


@app.get("/api/cases")
def cases():
    return {"local": list(local_cases().values()), "hosted": hosted_cases(), "opponents": OPPONENTS}


@app.get("/api/runs")
def runs():
    return list_runs()


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    try:
        return get_run(run_id)
    except FileNotFoundError as e:
        raise HTTPException(404, "Run not found") from e


@app.get("/api/research/{case_id}")
def dossier(case_id: str):
    case = next((c for c in [*local_cases().values(), *hosted_cases()] if c["case_id"] == case_id), None)
    if case is None:
        raise HTTPException(404, "Case not found")
    return clean({"case": case, "research": research(case, pkg())})


@app.get("/api/benchmarks")
def benchmarks():
    files = sorted((ROOT / "reports").glob("*.json"), reverse=True)
    return [json.loads(p.read_text()) for p in files if p.name not in {"latest.json", "hosted-practice.json"}][:12]


@app.get("/api/hosted-evaluation")
def hosted_report():
    from .hosted_evaluation import hosted_evaluation

    report = hosted_evaluation()
    snapshot = ROOT / "reports" / "hosted-practice.json"
    # Hosted transcripts stay on the machine that played them; fall back to the committed report.
    if not report["cases"] and snapshot.is_file():
        return {**json.loads(snapshot.read_text()), "source": "snapshot"}
    return {**report, "source": "live"}


@app.get("/api/pipeline")
def deal_pipeline():
    from .pipeline import pipeline

    return pipeline({c["case_id"]: c for c in [*local_cases().values(), *hosted_cases()]})


class ModelRequest(BaseModel):
    model: str = Field(max_length=100)


@app.post("/api/settings/model")
def choose_model(body: ModelRequest):
    try:
        set_model(body.model)
    except ValueError as e:
        raise HTTPException(422, "Unknown model") from e
    return {"model": model_name()}


@app.get("/api/prompts")
def prompts():
    from .prompt_protocol import TRANSPORT, prompt_specs

    return {
        "transport": TRANSPORT,
        "variants": {name: {**spec.metadata(), "strategy": spec.strategy} for name, spec in prompt_specs().items()},
    }


@app.get("/api/prompt-comparisons")
def prompt_comparisons():
    files = sorted((ROOT / "reports/prompt-comparisons").glob("*/report.json"), reverse=True)
    reports = []
    for path in files[:12]:
        try:
            reports.append(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue  # A running comparison can be updating its checkpoint.
    return reports


class PlayRequest(BaseModel):
    case_id: str = Field(max_length=100)
    mode: str = Field(pattern="^(local|hosted)$")
    agent: str = Field(default="claude", pattern="^(baseline|guarded|adaptive|claude)$")
    opponent: str = Field(default="scripted", max_length=50)
    # Seconds between local exchanges, so a demo audience can watch the pipeline move.
    pace: float = Field(default=0, ge=0, le=5)


def dispatch(job_id, fn):
    try:
        result = fn()
        JOBS[job_id].update(status="completed", result_id=result.get("id"))
    except Exception as e:
        JOBS[job_id].update(status="failed", error=type(e).__name__)


@app.post("/api/play", status_code=202)
def launch(body: PlayRequest):
    if body.agent == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(409, "Set ANTHROPIC_API_KEY in .env")
    if body.mode == "hosted" and not os.environ.get("ARENA_API_KEY"):
        raise HTTPException(409, "Set ARENA_API_KEY in .env")
    choices = local_cases() if body.mode == "local" else {c["case_id"]: c for c in hosted_cases() if c.get("hosted")}
    if body.case_id not in choices or body.opponent not in OPPONENTS:
        raise HTTPException(422, "Unknown or unavailable case / opponent")
    run = new_run(body.case_id, body.mode, body.agent)
    with JOB_LOCK:
        if sum(j["status"] == "running" for j in JOBS.values()) >= 3:
            raise HTTPException(429, "Three jobs are already running")
        JOBS[run["id"]] = {"status": "running", "kind": "negotiation", "case_id": body.case_id}
    # Persist the placeholder now so the returned id resolves before the job writes its first step.
    save_run({**run, "opponent": body.opponent} if body.mode == "local" else run)
    fn = (
        (lambda: hosted_play(body.case_id, body.agent, run))
        if body.mode == "hosted"
        else (lambda: local_play(body.case_id, body.agent, body.opponent, run=run, pace=body.pace))
    )
    POOL.submit(dispatch, run["id"], fn)
    return {"id": run["id"]}


@app.post("/api/benchmark", status_code=202)
def launch_benchmark():
    from .benchmark import benchmark

    with JOB_LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise HTTPException(409, "Wait for the current job before starting the benchmark")
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "running", "kind": "benchmark"}
    POOL.submit(dispatch, job_id, lambda: benchmark("adaptive", 3, "dashboard"))
    return {"id": job_id}
