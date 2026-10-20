# Copy to a server (airgap)

A kiosk can act as an inbound station: once a key is scanned and verified,
the kiosk copies its clean files to a server of the organization (rsync over
SSH, SFTP or SMB), where they are picked up from the protected network. The
key itself is not cleaned and leaves with its files.

## In short

1. The user inserts a key; the kiosk scans it.
2. A key that was **not verified** (a file could not be fully scanned) is
   refused: nothing is copied, nothing is written on it.
3. Otherwise the kiosk reads the identifier of the key (the hidden file
   `.pandora-box` at its root), or writes a new one on it.
4. The **clean files** are copied to `<path>/<identifier>/` on the server,
   with the scan report in `<path>/<identifier>/.pandora-box/`. Malicious
   and suspicious files are never copied.
5. When the key comes back, its folder on the server is **synchronized**:
   new and changed files are copied, files that are no longer on the key, or
   no longer clean, are deleted from the server.

```text
/srv/keys/                                   path on the server
└── 0f8fad5b-d9cb-469f-a165-70867728950e/    identifier of the key
    ├── docs/report.pdf                      clean files of the key
    ├── readme.txt
    └── .pandora-box/                        scan reports, one per insertion
        ├── 7c9e6679-7425-40de-944b-e07fc1f90ae7.json
        └── 3f2504e0-4f89-41d3-9a0c-0305e82c3301.json
```

## How it works

- **Identifier of the key.** The serial number of a USB key is missing or
  shared on some keys: at its first insertion, the kiosk writes a random
  identifier (UUID) in `.pandora-box`, at the root of its first filesystem,
  signed with a key of the kiosks of the organization (Ed25519). The file
  comes from the key: it is read as hostile data (size limit, strict format,
  signature checked). A missing, malformed or foreign token is replaced with
  a new one, so the key gets a new folder on the server. The signature
  prevents forging an identifier, not copying the file to another key. This
  file is the only change made to the key.
- **What is copied.** The files whose verdict is clean. Each one is first
  copied by the kiosk into a staging folder (`airgap.staging`) and checked
  against the SHA-256 that was scanned; a file changed since the scan is not
  copied. The staging folder is deleted once the copy is over, whatever its
  result.
- **Who copies.** The kiosk has no network. The service
  `pandora-box-airgap` runs as its own user, without privileges, and is the
  only one besides the update and SIEM services that the firewall lets out.
  It reads the staging folder, and nothing else of the kiosk (not the
  reports, not the quarantine, not the keys). The kiosk asks it to copy a
  key over its Unix socket.
- **Mirror.** The folder of the key on the server is a copy of the clean
  files of its last verified scan: files are compared by size and
  modification time (2 seconds apart at most: vfat), changed files are sent
  to a temporary name, then renamed, so that a reader never sees a half
  written file. The reports of the previous scans are kept.
- **Several partitions.** The files of each partition go into
  `partition-1/`, `partition-2/`...
- **SMB names.** A file whose name an SMB server refuses (`\ : * ? " < > |`,
  a trailing space or dot), or that differs from another one only by its
  case, is not copied and is listed as such.

The screen shows the copy, then **Files copied to the server** with the
files that were not copied (dangerous, suspicious, or refused by the server),
or **Copy to the server failed**: the key is then neither cleaned nor
copied, and can be inserted again later.

## Configuration

`[airgap]` of `packaging/pandora-box.toml`:

```toml
[airgap]
enabled = true
# "rsync" (rsync over SSH), "sftp" or "smb"
protocol = "sftp"
host = "files.example.org"
port = 0                    # 22 for rsync and sftp, 445 for smb
user = "kiosk"
path = "/srv/keys"          # smb: a folder of the share
share = ""                  # smb: the share
domain = ""                 # smb: the domain of the account
known_hosts = "/etc/pandora-box/airgap-known-hosts"
timeout = 60.0
```

The copy needs the network: build the image with the profile `online`
(see [Building the image](build.md#profile-online)).

### Credentials

`[airgap]` of `image/credentials.toml` (outside git), installed as systemd
credentials in `/etc/credstore` of the image:

| Key | For | Read by |
|---|---|---|
| `ssh_key` | the private SSH key of the account (rsync, sftp) | the copy service |
| `password` | the password of the account (smb) | the copy service |
| `token_key` | the key that signs the identifiers of the keys | the kiosk |

```sh
ssh-keygen -t ed25519 -N "" -C pandora-box-kiosk -f airgap-ssh-key
openssl genpkey -algorithm ed25519 -out airgap-token.key
```

Paste the private keys into `image/credentials.toml`, and the public SSH key
(`airgap-ssh-key.pub`) into the account on the server. Use the **same
`token_key` on every kiosk** of the organization: a key identified by one
kiosk keeps its folder on the others. Without it, the kiosk shows an error
at start and refuses to copy.

> [!WARNING]
> An image built with credentials must not be published: anyone with the
> image or the disk of the kiosk can read them.

### Host key of the server

rsync and SFTP only connect to a server whose host key is listed in
`airgap.known_hosts`: the kiosk never trusts a server on first use. Put the
key in `image/airgap-known-hosts`, after checking it against the server
itself (not only over the network):

```sh
ssh-keyscan -t ed25519 files.example.org > image/airgap-known-hosts
ssh-keygen -lf image/airgap-known-hosts    # compare with: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub on the server
```

With another port, the host is written `[files.example.org]:2222`.

## The server

Give the kiosks an account that can only write into the folder of the keys.

- **SFTP**: an account restricted to SFTP in a chroot, for instance in
  `sshd_config`:

    ```text
    Match User kiosk
        ChrootDirectory /srv/sftp
        ForceCommand internal-sftp
        AllowTcpForwarding no
        X11Forwarding no
    ```

    with `path = "/keys"` (the folder in the chroot, writable by the account).
- **rsync over SSH**: rsync must be installed on the server. Restrict the key
  of the kiosk to rsync in its folder with `rrsync` (shipped with rsync), in
  `~kiosk/.ssh/authorized_keys`:

    ```text
    command="rrsync /srv/keys",restrict ssh-ed25519 AAAA... pandora-box-kiosk
    ```

    then `path = "."`, paths being relative to the folder of `rrsync`.
- **SMB**: a share and an account with write access; SMB 3 with encryption is
  required by the kiosk (`server min protocol = SMB3`, `server smb encrypt =
  required` on Samba).

The folder of a key is deleted by nobody but the kiosks: archive or purge the
old ones on the server.

## Scan report

`actions.airgap` of the report (see [Scan report](engines.md#scan-report)):

| Field | Content |
|---|---|
| `status` | `copied`, `failed`, or `refused` (key not verified) |
| `key_id` | identifier of the key, the name of its folder |
| `destination` | folder of the key on the server |
| `copied`, `deleted`, `unchanged` | files sent, deleted from the server, already there |
| `not_copied` | files that are not clean |
| `errors` | clean files that could not be copied, and why |
| `error` | why the copy failed |

The kiosk log has the events `key_token_written`, `key_identified`,
`key_token_invalid`, `airgap_copied`, `airgap_failed` and `airgap_refused`;
the service logs `airgap_done` and `airgap_failed` in its journal
(`journalctl -u pandora-box-airgap`).

## Tests

- `tests/airgap/run.sh` copies to real servers in containers: OpenSSH with
  rsync (SFTP, rsync over SSH, and rsync restricted by `rrsync` as above)
  and Samba (SMB 3 with encryption), first a
  copy, then a synchronization (changed, new and removed files), an unknown
  host key and a wrong password refused, and the service over its socket.
- The [virtual machine test](vm.md) enables the copy to an SFTP server of the
  test: a key is copied, then inserted again without one of its files, which
  is deleted from the server; the key keeps its malicious files and gets its
  identifier.
