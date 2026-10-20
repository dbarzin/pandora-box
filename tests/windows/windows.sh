#!/bin/sh
# Copy to a Windows server (airgap), in a QEMU virtual machine of the build
# container (docs/airgap.md, "Windows server in a virtual machine"):
#   tests/windows/windows.sh prepare ISO [--index N]   install Windows (once)
#   tests/windows/windows.sh test                      copy keys to it
# ISO: Windows Server 2025 evaluation, from the Microsoft Evaluation Center.
# The installation can be watched over VNC on localhost:5900.
set -eu

[ $# -ge 1 ] || { sed -n '2,8p' "$0" >&2; exit 2; }
cd "$(dirname "$0")/../.."
docker build -q -t pandora-box-builder image >/dev/null
user="--user $(id -u):$(id -g) -e PYTHONDONTWRITEBYTECODE=1"
kvm=""
if [ -w /dev/kvm ]; then
    kvm="--device /dev/kvm --group-add $(stat -c %g /dev/kvm)"
fi
# The key of the kiosks, accepted by the account of the server (ssh-keygen
# needs a user name, which the container does not have)
mkdir -p tests/windows/work
if [ ! -f tests/windows/work/kiosk ]; then
    ssh-keygen -q -t ed25519 -N "" -C pandora-box-kiosk -f tests/windows/work/kiosk
fi
iso=""
if [ "$1" = prepare ]; then
    [ $# -ge 2 ] || { echo "usage: $0 prepare ISO [--index N]" >&2; exit 2; }
    iso="-v $(realpath "$2"):/windows.iso:ro"
    shift 2
    set -- prepare /windows.iso "$@"
fi
# shellcheck disable=SC2086
exec docker run --rm $user $kvm $iso -v "$PWD:/src" -w /src -p 127.0.0.1:5900:5900 \
    pandora-box-builder python3 tests/windows/windows.py "$@"
