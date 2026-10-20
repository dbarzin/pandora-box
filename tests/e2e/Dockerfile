# End-to-end test environment, based on the target system (Debian 13 trixie).
# Run with tests/e2e/run.sh: the container must be privileged to use loop devices.
# clamav-daemon is started by the tests with a test-only signature database.
# The scan workers run in their sandbox (bubblewrap, setpriv, seccomp), the
# FUSE drivers of NTFS, exFAT and ext4 too (pandora_box.fuse).
FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv libmagic1 clamav-daemon \
        dosfstools exfatprogs e2fsprogs mount util-linux \
        bubblewrap libseccomp2 \
        ntfs-3g exfat-fuse fuse2fs gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --no-create-home --shell /usr/sbin/nologin pandora-box-scan

COPY packaging/fuse/premounted.c /tmp/
RUN mkdir -p /usr/lib/pandora-box \
    && gcc -O2 -Wall -Wextra -Werror -shared -fPIC \
        -o /usr/lib/pandora-box/fuse-premounted.so /tmp/premounted.c -ldl

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -e ".[dev]"

CMD ["/venv/bin/pytest", "-m", "e2e", "-v", "tests/e2e"]
