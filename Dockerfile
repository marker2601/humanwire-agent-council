FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8080

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir ".[google,decisionos]"
RUN addgroup --system humanwire && adduser --system --ingroup humanwire humanwire

USER humanwire
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "google_service:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
