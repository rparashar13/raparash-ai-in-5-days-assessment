FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    "google-adk>=2.9.2" \
    "google-cloud-aiplatform>=1.156.0" \
    "google-auth>=2.35.0" \
    "requests>=2.32.0"

COPY main.py /app/main.py

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
