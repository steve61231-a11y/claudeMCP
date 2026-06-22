from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from engine.db.models import Politician
from engine.db.session import get_session, init_db
from engine.pipeline import run_pipeline
from engine.schemas import PoliticianCreate, PoliticianOut, RunRequest, RunResult

app = FastAPI(title="Political Intelligence Engine")


@app.on_event("startup")
def on_startup():
    init_db()


@app.post("/politicians", response_model=PoliticianOut)
def create_politician(payload: PoliticianCreate, db: Session = Depends(get_session)):
    politician = Politician(name=payload.name, aliases=payload.aliases, keywords=payload.keywords)
    db.add(politician)
    db.commit()
    db.refresh(politician)
    return politician


@app.get("/politicians/{politician_id}", response_model=PoliticianOut)
def get_politician(politician_id: str, db: Session = Depends(get_session)):
    politician = db.get(Politician, politician_id)
    if not politician:
        raise HTTPException(status_code=404, detail="politician not found")
    return politician


@app.post("/politicians/{politician_id}/runs", response_model=RunResult)
def trigger_run(politician_id: str, payload: RunRequest, db: Session = Depends(get_session)):
    politician = db.get(Politician, politician_id)
    if not politician:
        raise HTTPException(status_code=404, detail="politician not found")

    report = run_pipeline(db, politician, payload.period, payload.window_start, payload.window_end)
    return RunResult(report_id=report.id, payload=report.payload)


@app.get("/reports/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_session)):
    from engine.db.models import IntelligenceReport

    report = db.get(IntelligenceReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    return {"id": report.id, "payload": report.payload}
