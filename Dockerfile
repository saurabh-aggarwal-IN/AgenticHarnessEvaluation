# The orchestrator needs the Docker CLI and git, not a Docker daemon. Mount the
# host socket (or Podman socket) at /var/run/docker.sock when running this image.
FROM docker:27-cli

RUN apk add --no-cache \
        python3 \
        py3-pip \
        git \
        tini \
        ca-certificates

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir --break-system-packages \
        -r /app/requirements.txt

COPY harness_compare /app/harness_compare

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).read()"]

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["python3", "-m", "harness_compare.api"]
