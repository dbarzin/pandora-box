#!/bin/sh
# Build the PandoraBox image with mkosi, in a Debian 13 container.
#   image/build.sh                 production image
#   image/build.sh --profile test  test image (test signatures, root shell on hvc0)
# Other arguments are passed to mkosi. An existing image is replaced. The
# image is written to image/mkosi.output/. Its version is the date and time
# of the build (UTC, YYYYMMDDHHMMSS, like the serial of a signature set),
# unless --image-version=N is given: a kiosk only installs a newer version.
# The image is signed with image/mkosi.key and image/mkosi.crt, and trusts the
# signature sets signed with image/update.key (public key image/update.pem):
# development key pairs are generated when they do not exist (never use them
# for a kiosk).
set -eu

cd "$(dirname "$0")"
case " $* " in
*" --image-version="*) ;;
*) set -- --image-version="$(date -u +%Y%m%d%H%M%S)" "$@" ;;
esac
# Windows line endings (a clone made on Windows, or core.autocrlf=true)
# break the configuration of the image: USBGuard rejects its rules and
# blocks every USB device. Refuse to build an image from such files.
crlf=$(grep -rlI --exclude-dir=mkosi.output --exclude-dir=work \
    --exclude-dir=__pycache__ "$(printf '\r')" . ../packaging ../src || true)
if [ -n "$crlf" ]; then
    echo "error: files with Windows line endings (CRLF):" >&2
    echo "$crlf" | sed 's/^/  /' >&2
    echo "Clone the repository again with LF line endings (git config --global core.autocrlf input)." >&2
    exit 1
fi
docker build -q -t pandora-box-builder . >/dev/null
mkdir -p mkosi.output
# Privileged: mkosi creates namespaces and mounts to build the image. Its
# workspace and caches (packages, incremental build) are in a volume:
# overlayfs cannot use the container overlay filesystem. The image is given
# back to the calling user.
exec docker run --rm --privileged \
    -v "$PWD/..:/src" -w /src/image \
    -v pandora-box-mkosi:/var/tmp \
    -e HOST_IDS="$(id -u):$(id -g)" -e KEY_NAME="PandoraBox development key ($(id -un)@$(hostname))" \
    pandora-box-builder sh -c '
        mkdir -p /var/tmp/cache/images /var/tmp/cache/packages
        if [ ! -e mkosi.key ] && [ ! -e mkosi.crt ]; then
            mkosi --genkey-common-name="$KEY_NAME" --genkey-valid-days=3650 genkey
            chown "$HOST_IDS" mkosi.key mkosi.crt
            chmod 0600 mkosi.key
        fi
        if [ ! -e update.key ] && [ ! -e update.pem ]; then
            openssl genpkey -algorithm ed25519 -out update.key
            openssl pkey -in update.key -pubout -out update.pem
            chown "$HOST_IDS" update.key update.pem
            chmod 0600 update.key
        fi
        status=0
        mkosi --force --cache-directory=/var/tmp/cache/images \
            --package-cache-dir=/var/tmp/cache/packages "$@" build || status=$?
        chown -R "$HOST_IDS" mkosi.output
        exit $status' \
    mkosi "$@"
