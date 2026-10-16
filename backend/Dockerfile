FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY argus_api ./argus_api
# The RBI library snapshot (build it first with `make corpus`); the API refuses to start without it.
COPY data/corpus/instruments.json data/corpus/paragraphs.json ./data/corpus/
ENV PORT=8080
CMD exec uvicorn argus_api.main:app --host 0.0.0.0 --port ${PORT}
