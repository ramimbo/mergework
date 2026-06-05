FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MERGEWORK_DATABASE_URL=sqlite:////srv/mergework/data/mergework.sqlite3

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mergework \
    && mkdir -p /srv/mergework/data \
    && chown -R mergework:mergework /srv/mergework

USER mergework
EXPOSE 8000
CMD ["sh", "-c", "python scripts/check_deploy_ready.py && uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-server-header"]
