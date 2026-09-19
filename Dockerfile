FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

ARG CTCC_SOURCE_COMMIT=unverified
ARG CTCC_SOURCE_TREE=unverified
LABEL org.opencontainers.image.revision=$CTCC_SOURCE_COMMIT \
    org.ctcc.source.tree=$CTCC_SOURCE_TREE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system ctcc && adduser --system --ingroup ctcc ctcc

COPY pyproject.toml ./
COPY .github ./.github
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs
COPY README.md Dockerfile MANIFEST.sha256 compose.yaml .env.example .gitignore .dockerignore .gitattributes ./
COPY config ./config
COPY requirements ./requirements

RUN python scripts/manifest.py --check \
    && python -m pip install --require-hashes --only-binary=:all: -r requirements/validation-linux-py312.lock \
    && python -m pip install --no-deps --no-build-isolation ".[test,validation]" \
    && python -m pip check \
    && python -m scripts.verify_dependency_lock --target linux

USER ctcc
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
