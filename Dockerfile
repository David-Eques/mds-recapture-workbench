# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.11.19 AS uv-bin

FROM python:3.13-slim-bookworm AS python-builder
COPY --from=uv-bin /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY app ./app
RUN uv sync --frozen --no-dev

FROM python:3.13-slim-bookworm AS cms-fetch
WORKDIR /source
COPY cms-grouper.lock.json ./
COPY scripts/bootstrap_cms_grouper.py ./scripts/bootstrap_cms_grouper.py
RUN python scripts/bootstrap_cms_grouper.py --destination /opt/cms-grouper

FROM eclipse-temurin:17-jdk-jammy AS shim-builder
COPY --from=cms-fetch /opt/cms-grouper /opt/cms-grouper
COPY grouper-jar/RecaptureGrouperShim.java /source/RecaptureGrouperShim.java
RUN mkdir -p /opt/cms-grouper/_shim_build \
 && javac \
      -cp '/opt/cms-grouper/jars/snf-component-2.4.0.0.jar:/opt/cms-grouper/jars/lib/*' \
      -d /opt/cms-grouper/_shim_build \
      /source/RecaptureGrouperShim.java

FROM eclipse-temurin:17-jre-jammy AS jre

FROM python:3.13-slim-bookworm AS final
ENV JAVA_HOME=/opt/java/openjdk \
    PATH="/opt/venv/bin:/opt/java/openjdk/bin:${PATH}" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    CMS_GROUPER_JAR_DIR=/opt/cms-grouper/jars \
    TMPDIR=/tmp/mds-workbench

RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin workbench \
 && mkdir -p /tmp/mds-workbench \
 && chown workbench:workbench /tmp/mds-workbench \
 && chmod 700 /tmp/mds-workbench

WORKDIR /app
COPY --from=jre /opt/java/openjdk /opt/java/openjdk
COPY --from=python-builder /opt/venv /opt/venv
COPY --from=cms-fetch /opt/cms-grouper /opt/cms-grouper
COPY --from=shim-builder /opt/cms-grouper/_shim_build /opt/cms-grouper/_shim_build
COPY app /app/app
COPY cms-grouper.lock.json /app/cms-grouper.lock.json

RUN chmod -R a-w /app /opt/cms-grouper /opt/venv
USER workbench
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
