# Runner of the airgap tests: the kiosk side (pandora_box, rsync, OpenSSH client).
FROM debian:trixie-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv libmagic1 rsync openssh-client openssl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -e ".[dev]"
CMD ["/venv/bin/pytest", "-m", "airgap", "-v", "tests/airgap"]
