#!/bin/sh
# Build and run the end-to-end tests in a privileged container.
# The host kernel must provide loop devices and the tested filesystems:
#   sudo modprobe -a loop vfat exfat ext4
set -eu

cd "$(dirname "$0")/../.."
docker build -f tests/e2e/Dockerfile -t pandora-box-e2e .
docker run --rm --privileged pandora-box-e2e "$@"
