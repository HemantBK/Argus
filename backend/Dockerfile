FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY argus_api ./argus_api
ENV PORT=8080
CMD exec uvicorn argus_api.main:app --host 0.0.0.0 --port ${PORT}
