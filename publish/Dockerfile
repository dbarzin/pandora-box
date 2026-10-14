# Publication of signature sets (docs/signatures.md): freshclam and clamscan
# for the ClamAV databases, and pandora-box with its engines to check a set
# before it is signed. Run with publish/publish.sh.
FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv libmagic1 clamav clamav-freshclam openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY image/requirements.txt /src/requirements.txt
RUN python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --require-hashes --only-binary :all: \
        -r /src/requirements.txt
COPY pyproject.toml README.md LICENSE /src/
COPY src /src/src
RUN /venv/bin/pip install --no-cache-dir --no-deps /src

ENTRYPOINT ["/venv/bin/pandora-box-signatures", "publish"]
