# Signature updates

The detection engines of a kiosk only use a **signed signature set**,
verified at each start. A set is installed from a **signature update
device** (a USB key holding a set, for kiosks without network) or, when it is
enabled, **online** from a published set.

## Signature sets

A signature set is a folder holding the signature files of the engines and a
signed list of them (`src/pandora_box/sigsets.py`):

```
manifest.json          serial, creation date, and the SHA-256 and size of every file
manifest.json.sig      Ed25519 signature of manifest.json
clamav/main.cvd        ClamAV databases (clamd reads this folder)
clamav/daily.cvd
malwarebazaar/malwarebazaar.sha256.bin
hashlookup/hashlookup-full.bloom
yara/yara-forge/yara-rules-core.yar
```

The paths are those of the default configuration
(`/var/lib/pandora-box-signatures/current/...`). The `manifest.json` of a
subfolder (written by `pandora-box-signatures publish`) gives the source,
version and date of its files; without it, the date of a file is the
creation date of the set, used for the signature freshness warnings.

## Trust

- The kiosk trusts the Ed25519 public keys (`*.pem`) of `signatures.keys`,
  `/usr/share/pandora-box/keys/` in the image: they are part of the root
  filesystem, protected by dm-verity and Secure Boot. The update key is
  separate from the image signing key: sets are signed far more often.
- The signature is verified with `openssl`, on the exact bytes of the
  manifest, before anything else of the set is read.
- A set is only installed when its serial is higher than the installed one:
  an older set, even validly signed, is refused (no rollback).
- Every file is copied with its size and SHA-256 checked against the
  manifest; files not listed are ignored; paths are plain names (no `..`,
  no hidden name) and links are never followed.
- The set is installed next to the current one, then a `current` link is
  switched atomically: an interrupted update changes nothing. The previous
  set is kept.
- At each start, the kiosk verifies the installed set again (signature, then
  every file) and refuses engine files that are not part of it
  (`signatures.verify`). Without a valid set, **it scans nothing**: it shows
  `NO VALID SIGNATURES` and only accepts a signature update device. A new
  kiosk therefore starts by asking for one.

ClamAV databases (`.cvd`) are also signed by Cisco Talos and verified by
clamd. clamd reads the `clamav` folder of the installed set
(`DatabaseDirectory`), and is restarted when it changes.

## Updating a kiosk

Copy a signed set into a `pandora-box-signatures` folder at the root of a
USB key (vfat, exFAT, NTFS or ext4) and insert it into the kiosk. The kiosk
verifies and installs the set, reloads its engines, then asks to remove the
key: a signature update device is never scanned, so do not use it to carry
other files.

The kiosk shows and logs the result (`signatures_installed`,
`signatures_refused` with the reason, or `signatures_not_newer`).

## Online updates

A kiosk works offline by default. An image built with the **profile
`online`** (`image/build.sh --profile online`) updates its signatures
itself, **from their sources**: ClamAV (freshclam), YARA Forge, MalwareBazaar
and Hashlookup, the sources of `pandora-box-signatures publish`. The profile
brings DHCP on the wired network (IPv4), DNS (systemd-resolved, without LLMNR
or mDNS), freshclam, the `pandora-box-update` timer (every 6 hours, 5 minutes
after boot), and a firewall that only lets out the update service (TCP),
DHCP and DNS. Nothing comes in but the answers to these connections. It
enables the `[updates]` section of the configuration with every source:

```toml
[updates]
enabled = true
sources = ["clamav", "yara-forge", "malwarebazaar", "hashlookup"]
# Optional: a company mirror in place of a source ("clamav": a freshclam
# private mirror), and a proxy
# mirrors = { hashlookup = "https://mirror.example.org/hashlookup-full.bloom" }
# proxy = "http://proxy.example.org:3128"
```

### Credentials

MalwareBazaar needs an **abuse.ch Auth-Key** (free account:
https://auth.abuse.ch/). It is given at build time in `image/credentials.toml`
(see `image/credentials.toml.example`; git ignores the file), and the profile
installs it on the read-only root filesystem as a **systemd credential**,
`/etc/credstore/pandora-box.abusech-auth-key`, readable by root only: systemd
gives it to the update service alone (`ImportCredential=`). Without it,
MalwareBazaar is not downloaded and its database is kept from the last
signature update device.

An image built with credentials **must not be published**: anyone with the
image or the disk of the kiosk can read them.

### The update service

`pandora-box-update.service` runs in two steps
(`src/pandora_box/online.py`):

1. as the `pandora-box-update` user, the only one the firewall lets out,
   without privileges and in a systemd sandbox, it downloads the sources
   (with an HTTP cache, `/var/lib/pandora-box-update/staging-sources`: an
   unchanged source is not downloaded again; freshclam downloads the daily
   differences of ClamAV), checks that the engines load them, and builds a
   set in `/var/lib/pandora-box-update/staging` when they changed. The files
   of the sources it does not download are taken from the installed set;
2. as root, without network, it signs the staged set with the **key of the
   kiosk** and installs it (`install --staged`): every check of a signature
   update device applies, the unchanged files are taken from the installed
   set.

The key of the kiosk is an Ed25519 key generated on the kiosk the first time
(`/var/lib/pandora-box-signatures/local-key/`, the private key readable by
root only). The kiosk trusts it besides the keys of its image: it verifies
the installed set at each start, whoever signed it. Signature update devices
signed with the update key are still accepted (a kiosk without network, or
signatures newer than the last download).

The kiosk loads the new set the next time it is idle (between two devices):
a scan is never interrupted. It logs `signatures_changed` and shows `New
signatures installed`.

The downloads are HTTPS (a mirror configured in `updates.mirrors` may be
HTTP); the ClamAV databases carry the signature of Cisco Talos, verified by
freshclam. freshclam does not load the new databases to test them
(`TestDatabases no`): clamd already holds them in memory, a kiosk has 2 GB.
Redirects are only followed to HTTPS URLs, and the proxy of the environment
is ignored: only `updates.proxy` is used.

### A published set instead

A kiosk can also download a signed set published by
`pandora-box-signatures publish` on a web server, in place of the sources:
`url` (the folder of the set) instead of `sources`. It then needs no
credential, and only installs a set signed with the update key, newer than
the installed one, downloading only the files that changed.

The same service also installs online image updates (`updates.image_url`,
see [image.md](image.md#ab-updates-of-the-image)).

## Publishing a set

`pandora-box-signatures publish` downloads the signatures of every engine
from their sources, checks that the engines of a kiosk can load them, then
builds and signs a set (`src/pandora_box/publish.py`):

| Source | Files | Notes |
|---|---|---|
| `clamav` | `clamav/main.cvd`, `daily`, `bytecode` | updated by `freshclam` (incremental), which verifies their Cisco Talos signature |
| `yara-forge` | `yara/yara-forge/yara-rules-core.yar` | YARA Forge "core" package, latest GitHub release |
| `signature-base` | `yara/signature-base/*.yar` | opt-in: YARA Forge already includes its rules |
| `malwarebazaar` | `malwarebazaar/malwarebazaar.sha256.bin` | full SHA-256 export, needs a free abuse.ch Auth-Key (`ABUSECH_AUTH_KEY`) |
| `hashlookup` | `hashlookup/hashlookup-full.bloom` | about 1 GB, published monthly |

Downloads are HTTPS only and cached (`--cache`, default
`~/.cache/pandora-box-signatures`): a source that did not change (ETag,
Last-Modified) is not downloaded again. Before signing, the set is loaded
with the engines of the kiosk: the YARA rules must compile with YARA-X, the
MalwareBazaar database and the Hashlookup filter must load, and `clamscan`
must load the ClamAV databases. A set that fails is never signed, and the
previous output folder is kept: a broken set, validly signed, would make
every kiosk that installs it unable to scan. The set must contain the files
of every engine enabled on the kiosks: a kiosk refuses to scan with a set
that misses one.

The publication runs in a container holding freshclam, clamscan and the
engines, for instance every day on the computer that holds the signing key:

```sh
export ABUSECH_AUTH_KEY=...
publish/publish.sh /srv/pandora-box/pandora-box-signatures /secure/update.key
cp -r /srv/pandora-box/pandora-box-signatures /media/KEY/
```

The full set is about 1.2 GB (ClamAV about 115 MB, YARA Forge core 8 MB,
Hashlookup 1 GB). With the full ClamAV databases, clamd uses about 1 GB of
memory on the kiosk (measured in the test virtual machine). Signing uses `openssl`, so that the private key can stay in
a hardware token (OpenSSL provider). The release key of the project and its
storage are not defined yet.

The command also works on sets built by other means:

```sh
pandora-box-signatures build FOLDER --key update.key   # manifest.json + signature
pandora-box-signatures verify FOLDER --keys KEYS_FOLDER
pandora-box-signatures install FOLDER                   # by hand, on a kiosk
```

`build` and `publish` number the set with its creation date
(`YYYYMMDDHHMMSS`) unless `--serial` is given.

## Development keys

`image/build.sh` generates a development key pair, `image/update.key` and
`image/update.pem` (ignored by git), when they do not exist: the image trusts
`update.pem`, and the test image installs a set signed with `update.key`.
Never use them for a kiosk.

On a development computer, `pandora-box-signatures publish` without `--key`
builds an unsigned set and prints the matching configuration, with
`verify = false` in `[signatures]`.
