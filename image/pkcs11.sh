# Release keys in a PKCS#11 token (docs/release.md), sourced by
# image/build.sh --release and image/release.sh.
#   pkcs11_docker_options  on the build computer: options of docker run
#   pkcs11_read_pin        on the build computer: PIN of the token
#   pkcs11_setup           in the build container: PIN, key URIs, OpenSSL
#
# The keys are in a YubiKey 5 (PIV), used through ykcs11 and the OpenSSL
# PKCS#11 provider: the Secure Boot key in slot 9c, the update key in slot 82.
# Environment, all optional:
#   PANDORA_BOX_SECURE_BOOT_KEY  PKCS#11 URI of the Secure Boot key
#   PANDORA_BOX_UPDATE_KEY       PKCS#11 URI of the update key
#   PANDORA_BOX_PKCS11_MODULE    another PKCS#11 module than ykcs11
#   PANDORA_BOX_SOFTHSM          folder of a SoftHSM token, to test without a
#                                YubiKey (image/softhsm-keys.sh)

# Host side (POSIX sh). The PIN is read from the standard input of the
# container (docker run -i), never passed in its environment or arguments.
pkcs11_docker_options() {
    options="-i --tmpfs /run/pandora-box:mode=0700"
    options="$options -e PANDORA_BOX_SECURE_BOOT_KEY -e PANDORA_BOX_UPDATE_KEY"
    options="$options -e PANDORA_BOX_PKCS11_MODULE"
    # The YubiKey is reached through the PC/SC daemon of the build computer
    # when it runs one (it holds the reader); else the container starts its own
    if [ -S /run/pcscd/pcscd.comm ]; then
        options="$options -v /run/pcscd:/run/pcscd"
    fi
    if [ -n "${PANDORA_BOX_SOFTHSM:-}" ]; then
        options="$options -v $(realpath "$PANDORA_BOX_SOFTHSM"):/run/softhsm"
        options="$options -e SOFTHSM2_CONF=/run/softhsm/softhsm2.conf"
    fi
    echo "$options"
}

# Ask for the PIN of the token on the terminal, then write it on the standard
# output (piped to docker run -i)
pkcs11_read_pin() {
    if [ -t 0 ]; then
        printf 'PIN of the release token: ' >&2
        stty -echo
        IFS= read -r pin || true
        stty echo
        printf '\n' >&2
    else
        IFS= read -r pin || true
    fi
    printf '%s\n' "$pin"
}

# Container side (bash). mkosi runs its signing tools in a sandbox that only
# shares /usr and /run with the container, and only gives its Environment= to
# the tools of the image, not to those of the initrd, which share its keys: so
# the OpenSSL PKCS#11 provider finds the module through p11-kit, from
# /usr/share/p11-kit/modules, and the PIN from the key URI (pin-source, a file
# in /run). The PC/SC socket is in /run too.
pkcs11_setup() {
    local pin module=/usr/share/p11-kit/modules/pandora-box.module openssl_module=
    IFS= read -r pin || true
    if [ -z "$pin" ]; then
        echo "error: no PIN for the release token" >&2
        return 1
    fi
    (umask 077 && printf '%s' "$pin" >/run/pandora-box/pin)
    : "${PANDORA_BOX_SECURE_BOOT_KEY:=pkcs11:object=Private%20key%20for%20Digital%20Signature;type=private}"
    : "${PANDORA_BOX_UPDATE_KEY:=pkcs11:object=Private%20key%20for%20Retired%20Key%201;type=private}"
    PANDORA_BOX_SECURE_BOOT_KEY=$(_pkcs11_with_pin "$PANDORA_BOX_SECURE_BOOT_KEY")
    PANDORA_BOX_UPDATE_KEY=$(_pkcs11_with_pin "$PANDORA_BOX_UPDATE_KEY")
    export PANDORA_BOX_SECURE_BOOT_KEY PANDORA_BOX_UPDATE_KEY
    # The only module of p11-kit: a module that fails to start (ykcs11
    # without a reader) makes every token unusable
    if [ -n "${SOFTHSM2_CONF:-}" ]; then
        # SoftHSM reads its configuration from SOFTHSM2_CONF or /etc, which
        # the sandbox does not have: a p11-kit server in the container gives
        # its token through a socket in /run
        p11-kit server --provider /usr/lib/softhsm/libsofthsm2.so \
            --name /run/pandora-box/softhsm.sock "pkcs11:" >/dev/null
        echo "remote: unix:path=/run/pandora-box/softhsm.sock" >"$module"
        # The openssl command of the container loads SoftHSM itself: the
        # p11-kit server does not pass Ed25519 signatures. no-deinit: SoftHSM
        # crashes at the exit of the process otherwise.
        openssl_module="pkcs11-module-path = /usr/lib/softhsm/libsofthsm2.so
pkcs11-module-quirks = no-deinit no-operation-state"
    else
        echo "module: ${PANDORA_BOX_PKCS11_MODULE:-/usr/lib/x86_64-linux-gnu/libykcs11.so}" \
            >"$module"
        if [ ! -S /run/pcscd/pcscd.comm ]; then
            pcscd
        fi
    fi
    # The openssl command of the container loads the provider too
    cat >/etc/ssl/openssl.cnf <<EOF
openssl_conf = openssl_init

[openssl_init]
providers = provider_sect

[provider_sect]
default = default_sect
pkcs11 = pkcs11_sect

[default_sect]
activate = 1

[pkcs11_sect]
module = /usr/lib/x86_64-linux-gnu/ossl-modules/pkcs11.so
$openssl_module
activate = 1
EOF
}

# A PKCS#11 URI with the PIN file of the token, unless it gives a PIN
_pkcs11_with_pin() {
    case "$1" in
    *pin-source=* | *pin-value=*) printf '%s' "$1" ;;
    *\?*) printf '%s&pin-source=file:/run/pandora-box/pin' "$1" ;;
    *) printf '%s?pin-source=file:/run/pandora-box/pin' "$1" ;;
    esac
}
