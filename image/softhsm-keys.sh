#!/bin/sh
# Create a SoftHSM token holding test release keys, to test image/release.sh
# without a YubiKey (docs/release.md), in the build container:
#   image/softhsm-keys.sh DIR [PIN]
# DIR: new folder of the token. The keys have the labels of the YubiKey slots
# (ykcs11), so that the default URIs of image/pkcs11.sh find them: the Secure
# Boot key (RSA 2048, with a self-signed certificate) and the update key
# (Ed25519). Their public keys are written to DIR/secure-boot.crt and
# DIR/update.pem. Then: PANDORA_BOX_SOFTHSM=DIR image/release.sh ...
# Test keys only: the token is a folder, the PIN defaults to 123456.
set -eu

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    sed -n '2,10p' "$0" >&2
    exit 2
fi
if [ -e "$1" ]; then
    echo "error: $1 exists" >&2
    exit 1
fi
mkdir -p "$1"
dir=$(realpath "$1")
cd "$(dirname "$0")"
docker build -q -t pandora-box-builder . >/dev/null
printf '%s\n' "${2:-123456}" | docker run --rm -i -v "$dir:/run/softhsm" -v "$PWD:/src/image:ro" \
    -e SOFTHSM2_CONF=/run/softhsm/softhsm2.conf \
    -e HOST_IDS="$(id -u):$(id -g)" --tmpfs /run/pandora-box:mode=0700 \
    pandora-box-builder bash -c '
        set -euo pipefail
        trap "chmod -R a+rwX /run/softhsm; chown -R $HOST_IDS /run/softhsm" EXIT
        IFS= read -r pin
        mkdir /run/softhsm/tokens
        printf "directories.tokendir = /run/softhsm/tokens\nobjectstore.backend = file\n" \
            >"$SOFTHSM2_CONF"
        softhsm2-util --init-token --free --label "PandoraBox test release" \
            --pin "$pin" --so-pin "$pin$pin" >/dev/null
        tool() {
            pkcs11-tool --module /usr/lib/softhsm/libsofthsm2.so --login --pin "$pin" "$@" \
                >/dev/null 2>&1
        }
        tool --keypairgen --key-type rsa:2048 --id 02 --label "Private key for Digital Signature"
        tool --keypairgen --key-type EC:edwards25519 --id 05 --label "Private key for Retired Key 1"
        . /src/image/pkcs11.sh
        pkcs11_setup <<<"$pin"
        openssl req -new -x509 -key "$PANDORA_BOX_SECURE_BOOT_KEY" -days 3650 \
            -subj "/CN=PandoraBox test release key/" -out /run/softhsm/secure-boot.crt
        openssl pkey -in "$PANDORA_BOX_UPDATE_KEY" \
            -pubout -out /run/softhsm/update.pem'
echo "test token in $dir: secure-boot.crt, update.pem"
