#!/bin/sh
# Copy to real servers (airgap): an SSH server (SFTP, rsync over SSH) and a
# Samba server (SMB 3 with encryption), in containers, then the tests of
# tests/airgap in a runner container. Needs Docker with Compose.
#   tests/airgap/run.sh [pytest options]
set -eu

cd "$(dirname "$0")"
compose() { docker compose -p pandora-box-airgap -f compose.yml "$@"; }
trap 'compose down -v --remove-orphans >/dev/null 2>&1' EXIT
compose build -q
compose run --rm -T keygen
compose up -d sshd samba
# The SSH server writes known_hosts once its host keys exist
for _ in $(seq 30); do
    if compose exec -T sshd test -s /secrets/known_hosts; then break; fi
    sleep 1
done
compose run --rm -T runner /venv/bin/pytest -m airgap -v tests/airgap "$@"
