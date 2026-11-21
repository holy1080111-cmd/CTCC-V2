FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

ARG CTCC_SOURCE_COMMIT=unverified
ARG CTCC_SOURCE_TREE=unverified
LABEL org.opencontainers.image.revision=$CTCC_SOURCE_COMMIT \
    org.ctcc.source.tree=$CTCC_SOURCE_TREE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The source/archive installer tests use real isolated Git repositories. Keep
# the genuine tool available in immutable validation instead of skipping them.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && git --version \
    && dpkg-query -W -f='${Package}\t${Version}\n' > /usr/local/share/ctcc-validation-os-packages.tsv \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system ctcc \
    && adduser --system --ingroup ctcc ctcc \
    && install -d -m 0750 -o ctcc -g ctcc /run/ctcc-analysis \
    && install -m 0640 -o ctcc -g ctcc /dev/null /run/ctcc-analysis/.volume

COPY pyproject.toml ./
COPY .github ./.github
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests ./tests
COPY scripts ./scripts
COPY docs ./docs
COPY README.md Dockerfile MANIFEST.sha256 compose.yaml .env.example .gitignore .dockerignore .gitattributes Open-CTCC.cmd ./
COPY config ./config
COPY requirements ./requirements

RUN python scripts/manifest.py --check \
    && python -m pip install --require-hashes --only-binary=:all: -r requirements/validation-linux-py312.lock \
    && python -m pip install --no-deps --no-build-isolation ".[test,validation]" \
    && python -m pip check \
    && python -m scripts.verify_dependency_lock --target linux

USER ctcc
EXPOSE 8000

CMD ["python", "-B", "-m", "scripts.container_startup_0058_v1"]
