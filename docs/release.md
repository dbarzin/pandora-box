# Releases

A release of PandoraBox is a ready-to-write image published on
[GitHub](https://github.com/sourcentis/pandora-box/releases), signed with the
release keys of the project. A kiosk installed from it only starts images
signed with the same Secure Boot key, and only installs the signature sets,
image updates and maintenance requests signed with the same update key.

An organization that wants its own keys builds its image itself
([Building the image](build.md)): its kiosks then trust its keys, not those of
the project.

## Release keys

| Key | Algorithm | YubiKey slot | Signs | Public part |
|---|---|---|---|---|
| Secure Boot key | RSA 2048 | 9c | systemd-boot, the unified kernel image, the dm-verity root hash; enrolled in the firmware of the kiosk | `image/release/secure-boot.crt` |
| Update key | Ed25519 | 82 | signature sets, image updates, maintenance requests, checksums of a release | `image/release/update.pem` |

The private keys never leave a **YubiKey 5** (PIV application, firmware 5.7
or newer for Ed25519). The build container uses them through `ykcs11` and the
OpenSSL PKCS#11 provider (`image/pkcs11.sh`). It reaches the YubiKey through
the PC/SC daemon of the build computer when one runs (`/run/pcscd`), else it
starts its own.

The public parts are committed in `image/release/`. Their fingerprints are
also published here, so that a release can be checked against a source other
than itself:

```sh
openssl x509 -in image/release/secure-boot.crt -noout -fingerprint -sha256
openssl pkey -pubin -in image/release/update.pem -outform DER | sha256sum
```

> [!IMPORTANT]
> The release keys are not created yet: the fingerprints will be given here.

### Creating the keys

Once, on an offline computer. The keys are created in memory, imported into
**two** YubiKeys, the second one kept in a safe, then erased. A lost Secure
Boot key means that the kiosks can only be installed again, by hand; a lost
update key, that they get no more signature sets.

In the build container, with the files in memory only:

```sh
docker run --rm -it -v /run/pcscd:/run/pcscd -v "$PWD/image/release:/release" \
    --tmpfs /keys:mode=0700 pandora-box-builder bash
cd /keys
openssl req -new -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=PandoraBox release Secure Boot key/" \
    -keyout secure-boot.key -out secure-boot.crt
openssl genpkey -algorithm ed25519 -out update.key
openssl req -new -x509 -key update.key -days 3650 -subj "/CN=PandoraBox release update key/" -out update.crt
openssl pkey -in update.key -pubout -out update.pem
```

Then for each YubiKey (`-k` asks for its management key; change the default
PIN, PUK and management key of a new YubiKey first):

```sh
yubico-piv-tool -k -a import-key -s 9c -i secure-boot.key --pin-policy=once --touch-policy=never
yubico-piv-tool -k -a import-certificate -s 9c -i secure-boot.crt
yubico-piv-tool -k -a import-key -s 82 -i update.key --pin-policy=once --touch-policy=never
yubico-piv-tool -k -a import-certificate -s 82 -i update.crt
pkcs11-tool --module /usr/lib/x86_64-linux-gnu/libykcs11.so --login --list-objects
```

`ykcs11` only shows the key of a slot that holds a certificate. The PIN policy
`once` lets one build sign every part of the image with a single PIN; the
touch policy `never`, without touching the YubiKey at each signature.

Finally, publish the public keys and leave the container, which erases the
private keys:

```sh
cp secure-boot.crt update.pem /release/
exit
git add image/release/secure-boot.crt image/release/update.pem
```

With other slots or another token, give the PKCS#11 URIs of the keys and the
module: `PANDORA_BOX_SECURE_BOOT_KEY`, `PANDORA_BOX_UPDATE_KEY` and
`PANDORA_BOX_PKCS11_MODULE` (see `image/pkcs11.sh`).

## Making a release

On the build computer, with the YubiKey plugged in and
[gh](https://cli.github.com/) logged in to GitHub:

```sh
git tag -a v1.0.0 -m "PandoraBox 1.0.0"
git push origin v1.0.0
image/release.sh v1.0.0
```

`image/release.sh` asks for the PIN of the YubiKey once, then:

1. extracts the files of the tag only (`git archive`) to `release/v1.0.0/src/`:
   no local theme, credentials or development keys get into the image;
2. builds the image without profile, signed with the Secure Boot key
   (`image/build.sh --release`), trusting `image/release/update.pem`;
3. checks the signature of the unified kernel image, builds the signed image
   update, compresses the disk image, signs the checksums with the update key,
   and checks every signature with the public keys of the tag;
4. creates a **draft** release of the tag on GitHub with the files of
   `release/v1.0.0/assets/`.

| File | Content |
|---|---|
| `pandora-box-v1.0.0.raw.xz` | disk image, to write to the disk of the kiosk |
| `pandora-box-image-v1.0.0.tar` | signed image update: `pandora-box-image/`, to extract at the root of a USB key ([System updates](update.md)) |
| `pandora-box-v1.0.0.manifest` | packages of the image |
| `secure-boot.crt`, `update.pem` | public release keys |
| `SHA256SUMS`, `SHA256SUMS.sig` | checksums, signed with the update key |

Check the draft on GitHub, then publish it. `--no-publish` stops before
GitHub. The image of a release has no profile: the profile `online` holds the
credentials and settings of an organization, and is not published.

### Testing without a YubiKey

`image/softhsm-keys.sh` creates a SoftHSM token, a folder, with test keys in
the slots and labels of the YubiKey:

```sh
image/softhsm-keys.sh /tmp/token
cp /tmp/token/secure-boot.crt /tmp/token/update.pem image/release/   # in a test clone only
echo 123456 | PANDORA_BOX_SOFTHSM=/tmp/token image/release.sh v0.0.0-test --no-publish
```

## Verifying a release

Download the files of the release, then check the signature of the checksums
with the update key of the project, and the files with the checksums:

```sh
openssl pkeyutl -verify -pubin -inkey update.pem -rawin -in SHA256SUMS -sigfile SHA256SUMS.sig
sha256sum -c SHA256SUMS
openssl pkey -pubin -in update.pem -outform DER | sha256sum
```

The last fingerprint must be the one given [above](#release-keys): the
`update.pem` of a release alone proves nothing, as anyone publishing a fake
release would give their own. Then write the image
([Initial installation](install.md#writing-the-image)):

```sh
xz -dc pandora-box-v1.0.0.raw.xz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
```

At its first boot, the kiosk enrolls `secure-boot.crt` in its firmware. A
kiosk installed with development keys does not accept the updates of a
release: write the release image to its disk again.
