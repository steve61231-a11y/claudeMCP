# Root Dockerfile for Hugging Face Spaces (sdk: docker).
# Same build as engine/Dockerfile.render but listens on HF's port (7860) and
# runs the FULL pipeline (16GB RAM on HF free tier). The engine also serves the
# frontend at "/", so the Space URL is the whole app.
FROM python:3.11-slim

WORKDIR /app

COPY engine/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_sm

COPY engine ./engine
COPY mock-data ./mock-data
COPY web ./web

ENV PYTHONPATH=/app
# HF Spaces default port is 7860; honour $PORT if the platform sets one.
ENV PORT=7860
EXPOSE 7860

# Run DB migrations (against DATABASE_URL / Neon) then start the API+frontend.
CMD ["sh", "-c", "cd engine && alembic upgrade head && cd .. && uvicorn engine.api_server:app --host 0.0.0.0 --port ${PORT:-7860}"]
