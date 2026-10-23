# Niyantrak public demo — read-only, fictional sample data, no secrets.
# Pinned: base image by digest, the free edition by commit (tag v0.1.0), build-only packages by version.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

FROM ${PYTHON_IMAGE} AS build
ARG NIYANTRAK_REPO=https://github.com/vigyanbytes/niyantrak.git
ARG NIYANTRAK_COMMIT=48f8727373817fd39e6a57fe1d87839941d621c4
ARG DEMO_END=
# build stage only (discarded): git to fetch the pinned free edition
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates
RUN git init -q /src && cd /src \
 && git fetch -q --depth 1 "$NIYANTRAK_REPO" "$NIYANTRAK_COMMIT" && git checkout -q FETCH_HEAD \
 && test "$(git rev-parse HEAD)" = "$NIYANTRAK_COMMIT"
COPY requirements-build.txt /tmp/
RUN pip install --no-cache-dir --disable-pip-version-check --timeout 120 --retries 5 -r /tmp/requirements-build.txt
COPY demo/seed.py /demo/seed.py
RUN python /demo/seed.py --src /src --out /site ${DEMO_END:+--end "$DEMO_END"}

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.source="https://github.com/vigyanbytes/niyantrak-demo" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="Niyantrak read-only public demo (fictional sample data)"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
RUN useradd --system --uid 10001 --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin demo
WORKDIR /app
# runtime needs only the recognizers (standard library); nothing is installed with pip
COPY --from=build /src/niyantrak/__init__.py /src/niyantrak/recognizers.py /src/niyantrak/cli.py /src/niyantrak/paths.py /app/niyantrak/
COPY --from=build /src/LICENSE /src/NOTICE /app/niyantrak-license/
COPY --from=build /site /app/site
COPY demo/server.py demo/config.json /app/
RUN python -m compileall -q /app && chmod -R a+rX,go-w /app
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
CMD ["python", "/app/server.py"]
