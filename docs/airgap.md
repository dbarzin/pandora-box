# Copy to a server (airgap)

A kiosk can act as an inbound station: once a key is scanned and verified,
the kiosk copies its clean files to a server of the organization (rsync over
SSH or SFTP), where they are picked up from the protected network. The
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
- **Windows server.** With `windows_names = true`, a file whose name Windows
  refuses (`\ : * ? " < > |`, a trailing space or dot, `CON`, `NUL`,
  `COM1`...), or that differs from another one only by its case, is not
  copied and is listed as such.

The screen shows the copy, then **Files copied to the server** with the
files that were not copied (dangerous, suspicious, or that the server refused),
or **Copy to the server failed**: the key is then neither cleaned nor
copied, and can be inserted again later.

## Configuration

`[airgap]` of `packaging/pandora-box.toml`:

```toml
[airgap]
enabled = true
# "rsync" (rsync over SSH) or "sftp"
protocol = "sftp"
host = "files.example.org"
port = 0                    # 22
user = "kiosk"
path = "/srv/keys"
windows_names = false       # sftp to a Windows server: see below
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
| `ssh_key` | the private SSH key of the account on the server | the copy service |
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

The folder of a key is deleted by nobody but the kiosks: archive or purge the
old ones on the server.

### Windows server

Windows ships an OpenSSH server (Windows 10 1809 and Windows Server 2019 or
later): use `protocol = "sftp"` (Windows has no rsync) and
`windows_names = true`.

1. Install and start it, in PowerShell as administrator:

    ```powershell
    Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
    Set-Service -Name sshd -StartupType Automatic
    Start-Service sshd
    ```

2. Create a local account for the kiosks, **not an administrator** (the keys
   of administrators are read from
   `C:\ProgramData\ssh\administrators_authorized_keys` instead), and put the
   public key of the kiosks in `C:\Users\kiosk\.ssh\authorized_keys`.
3. Restrict it to SFTP in a folder, at the end of
   `C:\ProgramData\ssh\sshd_config`, then restart the service
   (`Restart-Service sshd`):

    ```text
    Match User kiosk
        ChrootDirectory C:\PandoraBox
        ForceCommand internal-sftp
        AllowTcpForwarding no
        PermitTTY no
    ```

    with `path = "/keys"`, the folder `C:\PandoraBox\keys` writable by the
    account. Without a chroot, the paths are written `/C:/PandoraBox/keys`.
4. The host key of the server, for `image/airgap-known-hosts`:
   `ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub` on the server
   gives the fingerprint to compare with that of `ssh-keyscan`.

The copy sends each file to a temporary name, then renames it over the old
one (SFTP extension `posix-rename`); a server without it gets the old file
deleted first, then the new one renamed.

A Windows server can be tested in a virtual machine: see
[Tests](#tests).

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

- `tests/airgap/run.sh` copies to a real OpenSSH server with rsync, in a
  container (SFTP, rsync over SSH, and rsync restricted by `rrsync` as
  above): first a copy, then a synchronization (changed, new and removed
  files), an unknown host key refused, and the service over its socket.
- `tests/windows/windows.sh` copies to a Windows Server in a QEMU virtual
  machine, set up as above (see below).
- The [virtual machine test](vm.md) enables the copy to an SFTP server of the
  test: a key is copied, then inserted again without one of its files, which
  is deleted from the server; the key keeps its malicious files and gets its
  identifier.

### Windows server in a virtual machine

The copy to a Windows server is checked against a real Windows Server 2025,
in a QEMU virtual machine of the build container (with KVM):

1. Download the ISO of **Windows Server 2025 evaluation** (English, 180 days,
   no product key) from the
   [Microsoft Evaluation Center](https://www.microsoft.com/en-us/evalcenter/download-windows-server-2025).
   It stays out of the repository.
2. Install it once, unattended (20 to 40 minutes, to watch over VNC on
   `localhost:5900`):

    ```sh
    tests/windows/windows.sh prepare ~/Downloads/windows-server-2025-eval.iso
    ```

    `tests/windows/autounattend.xml` installs image 1 of the ISO (Standard,
    Server Core; `--index 2` for the desktop), then `tests/windows/setup.ps1`
    sets up OpenSSH as described above (the account `kiosk`, its key, SFTP
    only in `C:\PandoraBox`) and shuts down. The disk is
    `tests/windows/work/windows.qcow2` (outside git).
3. Copy keys to it, as often as needed (each run starts from the installed
   disk):

    ```sh
    tests/windows/windows.sh test
    ```

    A first copy (the names Windows refuses or confuses reported), a
    replacement, an addition and a deletion, dates kept, and a server with
    another host key refused.

The evaluation expires after 180 days: prepare a new disk then.
