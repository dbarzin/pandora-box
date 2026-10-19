#!/bin/sh
# Build the PandoraBox image with mkosi, in a Debian 13 container.
#   image/build.sh                 production image
#   image/build.sh --profile test  test image (test signatures, root shell on hvc0)
#   image/build.sh --release       production image signed with the release keys
#   --snapshot=YYYYMMDDTHHMMSSZ    Debian packages of snapshot.debian.org at that
#                                  time, for a reproducible build (image/release.sh)
# Other arguments are passed to mkosi. An existing image is replaced. The
# image is written to image/mkosi.output/. Its version is the date and time
# of the build (UTC, YYYYMMDDHHMMSS, like the serial of a signature set),
# unless --image-version=N is given: a kiosk only installs a newer version.
# The image is signed with image/mkosi.key and image/mkosi.crt, and trusts the
# signature sets signed with image/update.key (public key image/update.pem):
# development key pairs are generated when they do not exist (never use them
# for a kiosk). With --release, the image is signed with the keys of the
# release token instead, and trusts the public keys of image/release/
# (image/pkcs11.sh, docs/release.md): its PIN is asked on the terminal, or
# read from the standard input.
set -eu

cd "$(dirname "$0")"
release=""
snapshot=""
for arg do
    shift
    case "$arg" in
    --release) release=yes ;;
    --snapshot=*) snapshot=${arg#--snapshot=} ;;
    *) set -- "$@" "$arg" ;;
    esac
done
case "$snapshot" in
"" | [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
*)
    echo "error: --snapshot=YYYYMMDDTHHMMSSZ" >&2
    exit 2
    ;;
esac
docker_options=""
pin=""
if [ -n "$release" ]; then
    case " $* " in
    *" --profile"* | *" -p"*)
        echo "error: a release image has no profile" >&2
        exit 2
        ;;
    esac
    for key in release/secure-boot.crt release/update.pem; do
        if [ ! -f "$key" ]; then
            echo "error: no image/$key (docs/release.md)" >&2
            exit 1
        fi
    done
    . ./pkcs11.sh
    docker_options=$(pkcs11_docker_options)
    pin=$(pkcs11_read_pin)
fi
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
docker build -q -t pandora-box-builder --build-arg SNAPSHOT="$snapshot" . >/dev/null
mkdir -p mkosi.output
# Privileged: mkosi creates namespaces and mounts to build the image. Its
# workspace and caches (packages, incremental build) are in a volume:
# overlayfs cannot use the container overlay filesystem. The volume is not
# the device of mkosi.output/: mkosi copies the outputs instead of renaming
# them, and its message about it for each output is filtered out, as those of
# systemctl preset-all about the masked units (masked on purpose, by Debian or
# by the image). The image is given back to the calling user.
# Release: the keys are in the token, the PIN comes on the standard input.
# shellcheck disable=SC2086  # docker_options: several words
printf '%s\n' "$pin" | docker run --rm --privileged $docker_options \
    -v "$PWD/..:/src" -w /src/image \
    -v pandora-box-mkosi:/var/tmp \
    -e HOST_IDS="$(id -u):$(id -g)" -e KEY_NAME="PandoraBox development key ($(id -un)@$(hostname))" \
    -e RELEASE="$release" -e SNAPSHOT="$snapshot" \
    pandora-box-builder bash -c '
        mkdir -p /var/tmp/cache/images /var/tmp/cache/packages
        if [ -n "$SNAPSHOT" ]; then
            # The repositories of mkosi (Repositories= of mkosi.conf) at that
            # time; no incremental build, whose cache holds other packages
            tree=/tmp/snapshot
            mkdir -p "$tree/etc/apt/sources.list.d"
            for archive in "debian trixie trixie-updates" "debian-security trixie-security"; do
                read -r name suites <<<"$archive"
                printf "%s\n" "Types: deb" \
                    "URIs: http://snapshot.debian.org/archive/$name/$SNAPSHOT/" \
                    "Suites: $suites" "Components: main non-free-firmware" \
                    "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg" \
                    "Check-Valid-Until: no" ""
            done >"$tree/etc/apt/sources.list.d/mkosi.sources"
            set -- "$@" --sandbox-tree="$tree" --incremental=no
        fi
        if [ -n "$RELEASE" ]; then
            . ./pkcs11.sh
            pkcs11_setup
            set -- "$@" \
                --secure-boot-key-source=provider:pkcs11 \
                --secure-boot-key="$PANDORA_BOX_SECURE_BOOT_KEY" \
                --secure-boot-certificate=release/secure-boot.crt \
                --verity-key-source=provider:pkcs11 \
                --verity-key="$PANDORA_BOX_SECURE_BOOT_KEY" \
                --verity-certificate=release/secure-boot.crt \
                --sign-expected-pcr-key-source=provider:pkcs11 \
                --sign-expected-pcr-key="$PANDORA_BOX_SECURE_BOOT_KEY" \
                --sign-expected-pcr-certificate=release/secure-boot.crt \
                --environment=UPDATE_PUBLIC_KEY=release/update.pem
        elif [ ! -e mkosi.key ] && [ ! -e mkosi.crt ]; then
            mkosi --genkey-common-name="$KEY_NAME" --genkey-valid-days=3650 genkey
            chown "$HOST_IDS" mkosi.key mkosi.crt
            chmod 0600 mkosi.key
        fi
        if [ -z "$RELEASE" ] && [ ! -e update.key ] && [ ! -e update.pem ]; then
            openssl genpkey -algorithm ed25519 -out update.key
            openssl pkey -in update.key -pubout -out update.pem
            chown "$HOST_IDS" update.key update.pem
            chmod 0600 update.key
        fi
        mkosi --force --cache-directory=/var/tmp/cache/images \
            --package-cache-dir=/var/tmp/cache/packages "$@" build 2>&1 |
            grep --line-buffered -v \
                -e "as they are located on different devices, falling back to copying$" \
                -e "^Unit /buildroot/.* is masked, ignoring\.$" \
                -e "^Failed to preset unit: Unit /buildroot/.* is masked$"
        status=${PIPESTATUS[0]}
        chown -R "$HOST_IDS" mkosi.output
        exit $status' \
    mkosi "$@"
