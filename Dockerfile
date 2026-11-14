FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV RETURNS_CLOCK=2026-10-25T10:00:00 \
    DATABASE_URL=sqlite:///out/returns.db \
    SCALE=full
EXPOSE 8000
# Generate (seeded, deterministic), load, then serve.
CMD python -m returns_platform generate --scale "$SCALE" --out out/data && \
    python -m returns_platform load-db --data out/data --url "$DATABASE_URL" && \
    python -m returns_platform serve --url "$DATABASE_URL" --host 0.0.0.0 --port 8000
