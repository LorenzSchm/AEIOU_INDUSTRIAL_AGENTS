import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
    MODELS,
    ROOT,
    model_efforts,
    model_name,
    model_provider,
    model_settings,
    provider_configured,
    require_model_key,
    set_model,
    state_root,
)
from .evidence_transport import attachment_text
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
        **model_settings(),
        "models": [{"id": k, "label": v, "provider": model_provider(k), "efforts": model_efforts(k),
                    "default_effort": model_settings(k)["effort"]} for k, v in MODELS.items()],
        "claude_configured": provider_configured("anthropic"),
        "openai_configured": provider_configured("openai"),
        "llm_configured": provider_configured(model_provider(model_name())),
        "providers": {name: {"configured": provider_configured(name)} for name in ("anthropic", "openai")},
        "arena_configured": bool(os.environ.get("ARENA_API_KEY")),
        "facts": len(pkg().facts),
        "tables": pkg().tables(),
        "sunday_release": pkg().release is not None,
        "data_notes": "Frozen buyer world: 30 September 2026. Refresh Sunday release before final.",
        "jobs": JOBS,
    }


@app.get("/api/cases")
def cases():
    from .supplier_profiles import PROFILES

    return {"local": list(local_cases().values()), "hosted": hosted_cases(), "opponents": PROFILES}


@app.get("/api/runs")
def runs():
    return list_runs()


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    try:
        result = get_run(run_id)
        # Presentation only: preserve the stored wire request for exact replay.
        for events in result.get("threads", {}).values():
            for message in events:
                if message.get("from") == "buyer":
                    for attachment in message.get("attachments", []):
                        if "text" not in attachment and (text := attachment_text(attachment)):
                            attachment["text"] = text
        return result
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


@app.get("/api/hosted-benchmarks")
def hosted_benchmarks(archive: bool = False):
    from .hosted_benchmarks import hosted_benchmarks

    return hosted_benchmarks(archive)


class ModelRequest(BaseModel):
    model: str = Field(max_length=100)


@app.post("/api/settings/model")
def choose_model(body: ModelRequest):
    try:
        set_model(body.model)
    except ValueError as e:
        raise HTTPException(422, "Unknown model") from e
    return model_settings()


@app.get("/api/prompts")
def prompts():
    from .prompt_protocol import RESEARCH_SOURCES, TRANSPORT, original_reference, prompt_specs

    return {
        "transport": TRANSPORT,
        "variants": {name: {**spec.metadata(), "strategy": spec.strategy} for name, spec in prompt_specs().items()},
        "reference_original": original_reference(),
        "revision_note": "The active prompt removes the stock-question tactic and buyer-initiated walk-aways, "
        "and requests a concise decision brief instead of internal reasoning. "
        "The three-offer request, "
        "five-repeat rule and preparation structure are retained.",
        "research_sources": RESEARCH_SOURCES,
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


@app.get("/api/local-evaluations")
def local_evaluations():
    reports = []
    for path in sorted((ROOT / "reports/local-evaluations").glob("*/report.json"), reverse=True)[:20]:
        try:
            reports.append(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue
    return reports


@app.get("/api/local-dashboard")
def local_dashboard():
    from .evaluation_data import local_dashboard

    return local_dashboard()


@app.get("/api/eval-suite")
def eval_suite():
    from .eval_suite import suite

    return suite()


class EvalSuiteRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=100000)
    variant_id: Literal["a", "b"] = "a"
    model: str | None = Field(default=None, max_length=100)
    effort: str | None = Field(default=None, max_length=20)


class PracticeEvalRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=100000)
    repeats: int = Field(default=1, ge=1, le=3)
    case_ids: list[str] | None = Field(default=None, min_length=1, max_length=30)
    model: str | None = Field(default=None, max_length=100)
    effort: str | None = Field(default=None, max_length=20)


def validate_buyer(model=None, effort=None):
    model = model or model_name()
    if model not in MODELS:
        raise HTTPException(422, "Unknown buyer model")
    try:
        settings = model_settings(model, effort)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        require_model_key(model)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return settings


@app.get("/api/eval-suite/practice/catalog")
def practice_eval_catalog():
    from .eval_suite import error_text, refresh_practice_catalog

    try:
        return refresh_practice_catalog()
    except Exception as exc:
        raise HTTPException(409, error_text(exc)) from exc


@app.post("/api/eval-suite/practice", status_code=202)
def launch_practice_eval(body: PracticeEvalRequest):
    from .eval_suite import error_text, prepare_evaluation, refresh_practice_catalog, run_evaluation, suite

    if not body.prompt.strip():
        raise HTTPException(422, "Enter a prompt before running negotiations")
    settings = validate_buyer(body.model, body.effort)
    try:
        catalog = refresh_practice_catalog()
        with JOB_LOCK:
            previous = suite()["practice"]["latest"]
            if previous and previous["status"] == "running":
                raise HTTPException(409, "Hosted practice already has a running evaluation")
            if sum(job["status"] == "running" for job in JOBS.values()) >= 4:
                raise HTTPException(429, "Wait for a running job to finish")
            report = prepare_evaluation(body.prompt, settings["model"], "practice", repeats=body.repeats,
                                        case_ids=body.case_ids, catalog=catalog, effort=settings["effort"])
            JOBS[report["id"]] = {"status": "running", "kind": "evaluation", "variant_id": "practice"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(409, error_text(exc)) from exc
    POOL.submit(dispatch, report["id"], lambda: run_evaluation(report))
    return {"id": report["id"], "variant_id": "practice"}


@app.post("/api/eval-suite", status_code=202)
def launch_eval_suite(body: EvalSuiteRequest):
    reports = start_eval_variants([body])
    return {"id": reports[0]["id"], "variant_id": body.variant_id}


@app.post("/api/eval-suite/{evaluation_id}/stop", status_code=202)
def stop_eval_suite(evaluation_id: str):
    from .eval_suite import stop_evaluation

    try:
        return stop_evaluation(evaluation_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Evaluation not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


class EvalComparisonRequest(BaseModel):
    prompt_a: str = Field(min_length=1, max_length=100000)
    prompt_b: str = Field(min_length=1, max_length=100000)
    model_a: str | None = Field(default=None, max_length=100)
    effort_a: str | None = Field(default=None, max_length=20)
    model_b: str | None = Field(default=None, max_length=100)
    effort_b: str | None = Field(default=None, max_length=20)


@app.post("/api/eval-suite/compare", status_code=202)
def launch_eval_comparison(body: EvalComparisonRequest):
    reports = start_eval_variants([
        EvalSuiteRequest(prompt=body.prompt_a, variant_id="a", model=body.model_a, effort=body.effort_a),
        EvalSuiteRequest(prompt=body.prompt_b, variant_id="b", model=body.model_b, effort=body.effort_b),
    ])
    return {"batches": [{"id": report["id"], "variant_id": report["variant_id"]} for report in reports]}


def start_eval_variants(requests):
    from .eval_suite import prepare_evaluation, run_evaluation, suite, validate_hosted_config

    if any(not body.prompt.strip() for body in requests):
        raise HTTPException(422, "Enter a prompt before running negotiations")
    selections = [validate_buyer(body.model, body.effort) for body in requests]
    try:
        validate_hosted_config()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    with JOB_LOCK:
        variants = {variant["id"]: variant["latest"] for variant in suite()["variants"]}
        for body in requests:
            latest = variants[body.variant_id]
            if latest and latest["status"] == "running":
                raise HTTPException(409, f"Prompt {body.variant_id.upper()} already has a running evaluation")
        if sum(job["status"] == "running" for job in JOBS.values()) + len(requests) > 4:
            raise HTTPException(429, "Wait for a running job to finish before starting more evaluations")
        comparison_id = uuid.uuid4().hex if len(requests) > 1 else None
        reports = [prepare_evaluation(body.prompt, selection["model"], body.variant_id, comparison_id,
                                      effort=selection["effort"])
                   for body, selection in zip(requests, selections, strict=True)]
        for report in reports:
            JOBS[report["id"]] = {
                "status": "running", "kind": "evaluation", "variant_id": report["variant_id"],
            }
    for report in reports:
        POOL.submit(dispatch, report["id"], lambda report=report: run_evaluation(report))
    return reports


class PlayRequest(BaseModel):
    case_id: str = Field(max_length=100)
    mode: str = Field(pattern="^(local|hosted)$")
    agent: str = Field(default="original", pattern="^(baseline|guarded|adaptive|claude|original)$")
    opponent: str = Field(default="auto", max_length=50)
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
    if body.mode == "local" or body.agent in {"claude", "original"}:
        validate_buyer()
    if body.mode == "hosted" and not os.environ.get("ARENA_API_KEY"):
        raise HTTPException(409, "Set ARENA_API_KEY in .env")
    choices = local_cases() if body.mode == "local" else {c["case_id"]: c for c in hosted_cases() if c.get("hosted")}
    if body.opponent == "auto" and body.case_id in choices:
        from .supplier_profiles import case_profile

        profile = case_profile(choices[body.case_id])
        body.opponent = profile if profile in OPPONENTS else "private"
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


class EvaluationRequest(BaseModel):
    variant: str = Field(default="original", pattern="^(original|adapted)$")
    profile: str | None = None


@app.post("/api/benchmark", status_code=202)
def launch_benchmark(body: EvaluationRequest | None = None):
    from .local_evaluation import evaluate_local, evaluation_plan
    from .supplier_profiles import PROFILES

    body = body or EvaluationRequest()
    if body.profile is not None and body.profile not in PROFILES:
        raise HTTPException(422, "Unknown supplier profile")
    plan = evaluation_plan(profiles=[body.profile] if body.profile else None)

    validate_buyer()
    with JOB_LOCK:
        if any(j["status"] == "running" for j in JOBS.values()) or any(
            r["status"] == "running" for r in local_evaluations()
        ):
            raise HTTPException(409, "Wait for the current job before starting the benchmark")
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"status": "running", "kind": "benchmark"}
    POOL.submit(
        dispatch,
        job_id,
        lambda: evaluate_local(
            label=f"dashboard-{body.variant}-{len(plan)}",
            parallel=2,
            plan=plan,
            attempt_limit=len(plan),
            variant=body.variant,
        ),
    )
    return {"id": job_id}
