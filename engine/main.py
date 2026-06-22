from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy.orm import Session

from engine.config import settings
from engine.db.models import Politician
from engine.db.neo4j_client import close_driver
from engine.db.session import get_session
from engine.pipeline import run_pipeline
from engine.schemas import PoliticianCreate, PoliticianOut, RunRequest, RunResult

app = FastAPI(title="Political Intelligence Engine")


def require_internal_token(x_internal_token: str | None = Header(default=None)):
    """Shared-secret check between `api` and `engine`. Skipped if no token is
    configured, so local quickstart still works without extra setup — but
    INTERNAL_API_TOKEN should always be set outside local dev.
    """
    if settings.internal_api_token and x_internal_token != settings.internal_api_token:
        raise HTTPException(status_code=401, detail="invalid or missing internal token")


@app.on_event("startup")
def on_startup():
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The engine requires it for entity/sentiment/"
            "narrative analysis — set it in .env before starting."
        )
    # Schema is managed by Alembic migrations (engine/alembic/) — run
    # `alembic upgrade head` before starting the engine, rather than relying
    # on create_all, so schema changes have a real upgrade path.


@app.on_event("shutdown")
def on_shutdown():
    close_driver()


@app.post("/politicians", response_model=PoliticianOut, dependencies=[Depends(require_internal_token)])
def create_politician(payload: PoliticianCreate, db: Session = Depends(get_session)):
    politician = Politician(name=payload.name, aliases=payload.aliases, keywords=payload.keywords)
    db.add(politician)
    db.commit()
    db.refresh(politician)
    return politician


@app.get("/politicians/{politician_id}", response_model=PoliticianOut, dependencies=[Depends(require_internal_token)])
def get_politician(politician_id: str, db: Session = Depends(get_session)):
    politician = db.get(Politician, politician_id)
    if not politician:
        raise HTTPException(status_code=404, detail="politician not found")
    return politician


@app.post("/politicians/{politician_id}/runs", response_model=RunResult, dependencies=[Depends(require_internal_token)])
def trigger_run(politician_id: str, payload: RunRequest, db: Session = Depends(get_session)):
    politician = db.get(Politician, politician_id)
    if not politician:
        raise HTTPException(status_code=404, detail="politician not found")

    report = run_pipeline(db, politician, payload.period, payload.window_start, payload.window_end)
    return RunResult(report_id=report.id, payload=report.payload)


@app.get("/reports/{report_id}", dependencies=[Depends(require_internal_token)])
def get_report(report_id: str, db: Session = Depends(get_session)):
    from engine.db.models import IntelligenceReport

    report = db.get(IntelligenceReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    return {"id": report.id, "payload": report.payload}
