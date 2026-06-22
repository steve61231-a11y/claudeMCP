FROM python:3.11-slim

WORKDIR /app

COPY engine/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_sm \
    && python -c "from transformers import pipeline; pipeline('sentiment-analysis', model='distilbert-base-uncased-finetuned-sst-2-english')" \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY engine ./engine
COPY mock-data ./mock-data

ENV PYTHONPATH=/app

CMD ["sh", "-c", "cd engine && alembic upgrade head && cd .. && uvicorn engine.main:app --host 0.0.0.0 --port 8000"]
