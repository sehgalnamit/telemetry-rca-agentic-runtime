# Minimal, single-stage image for the RCA runtime control-plane demo.
# The "control plane" (agent runtime, rule engine, jury evaluator, audit
# log) is what actually runs here; tenant telemetry stores are plain
# DuckDB files mounted as a volume, modeling each tenant's own storage
# living outside the control plane's image/container.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/tenants

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "scripts/demo.py", "--all", "--cross-tenant-federation-demo"]
