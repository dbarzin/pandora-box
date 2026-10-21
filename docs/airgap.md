# Copy to a server (airgap)

A kiosk can act as a file transfer station, between the keys of the users
and a server of the organization (rsync over SSH or SFTP), at the same
insertion:

- **inbound**: once a key is scanned and verified, the kiosk copies its
  clean files to the server, where they are picked up from the protected
  network;
- **outbound**: the files that the protected network put on the server for
  that key are scanned, then the clean ones are written on the key.

The key itself is not cleaned, unless `airgap.clean` is set (see
[Cleaning the key](#cleaning-the-key)).

## In short

1. The user inserts a key; the kiosk scans it.
2. A key that was **not verified** (a file could not be fully scanned) is
   refused: nothing is copied, nothing is written on it.
3. Otherwise the kiosk reads the token of the key (the hidden file
   `.pandora-box` at its root), or writes a new one on it.
4. The **clean files** are copied to `<path>/<folder>/` on the server, the
   folder being named after a secret of the token, with the scan report in
   `<path>/<folder>/.pandora-box/`. Malicious and suspicious files are never
   copied.
5. When the key comes back, its folder on the server is **synchronized**:
   new and changed files are copied, files that are no longer on the key, or
   no longer clean, are deleted from the server.
6. Then the files of `<path>/<folder>/.pandora-box/outbound/` that the key
   does not have, or has in another version, are downloaded, **scanned**,
   and the clean ones are written at the root of the key (see
   [Outbound](#outbound)).

```text
/srv/keys/                                   path on the server, not listable
└── 3f9c…e1a7/                               folder of the key (64 hexadecimal digits)
    ├── docs/report.pdf                      clean files of the key
    ├── readme.txt
    └── .pandora-box/                        scan reports, one per insertion
        ├── 7c9e6679-7425-40de-944b-e07fc1f90ae7.json
        ├── 3f2504e0-4f89-41d3-9a0c-0305e82c3301.json
        └── outbound/                        files waiting for the key
            └── answer.pdf
```

## How it works

- **Token of the key.** The serial number of a USB key is missing or shared
  on some keys: at its first insertion, the kiosk writes in `.pandora-box`,
  at the root of its first filesystem, a random identifier (UUID, written in
  the scan report) and a random secret of 256 bits, signed with a key of the
  kiosks of the organization (Ed25519). The file comes from the key: it is
  read as hostile data (size limit, strict format, signature checked). A
  missing, malformed or foreign token is replaced with a new one, so the key
  gets a new folder on the server. The signature prevents forging a token,
  not copying the file to another key. This file is the only change made to
  the key.
- **Folder of the key.** Its name is the SHA-256 of the secret of the token:
  the kiosk computes it when the key is inserted and never logs, shows or
  keeps it. Without the key, the folder of its files cannot be found: not
  from a stolen kiosk, nor from the account of the kiosks on the server,
  which cannot list the folders (see [The server](#the-server)).
- **What is copied.** The files whose verdict is clean. Each one is first
  copied by the kiosk into a staging folder (`airgap.staging`) and checked
  against the SHA-256 that was scanned; a file changed since the scan is not
  copied. The staging folder is in memory (a tmpfs of at most half the
  memory: a bigger copy is refused) and is deleted once the copy is over,
  whatever its result.
- **Nothing of the keys on the station.** A kiosk that copies to a server
  keeps no scan report (they go to the server, with the files), has no
  quarantine unless it cleans the keys with `quarantine.enabled`, and its
  log leaves out the names and
  hashes of the files, the label, vendor, model and serial number of the
  key and the lines of the screen: only events, verdicts and counts. See
  [A stolen kiosk](#a-stolen-kiosk) for what is still there.
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

The screen shows the copy, then **Files copied** with the
files that were not copied, in either direction (dangerous, suspicious, or
refused by the server or the key), or **Copy failed**: the key can be
inserted again later.

## Outbound

The protected network puts files for a key in the folder `.pandora-box/outbound/`
of its folder on the server: an operator finds that folder with the
identifier of the key, in its scan reports (`key_id`). At each insertion:

- **Only a clean key.** A key that holds a file the cleaning would remove (a
  malicious file, or a suspicious one with `scan.suspicious = "block"`) gets
  nothing: clean it at a kiosk that cleans, then insert it again. A key not
  verified gets nothing either.
- **New or changed files.** The kiosk tells the service the files of the
  first filesystem of the key (path, size, modification time); the service
  downloads into the staging folder (in memory) the files of the outbound
  folder that the key does not have, or has in another version. The files
  stay on the server: a file changed there is copied again; delete it there
  when it is delivered.
- **Scanned.** The downloaded files go through the same engines as the files
  of a key: a malicious, suspicious or not fully scanned file is not
  written, and is reported.
- **Written at the root of the key**, with their folders and modification
  times, replacing a file of the same name. Each file is written to a
  temporary name, checked against the SHA-256 that was scanned, then
  renamed. The kiosk checks the free space of the key and the limits of its
  filesystem: files of 4 GiB at most on vfat; names that FAT, exFAT and NTFS
  refuse (`\ : * ? " < > |`, a trailing space or dot, `CON`, `NUL`...);
  names of 255 characters at most; names equal but for the case on FAT,
  exFAT and NTFS. A file that cannot be written is reported, never renamed.
  `.pandora-box` at the root is reserved for the token of the key.

`airgap.mode` chooses the directions: `both` (default), `inbound` or
`outbound`.

## Cleaning the key

By default a station does not clean the keys: the dangerous files are never
copied to the server, but stay on the key, which then gets nothing from the
server. With `airgap.clean = true`, the station cleans the key too, as any
kiosk, at the same insertion:

1. the clean files go to the server;
2. the files to remove are listed, and a touch on the screen removes them;
   removing the key instead leaves it as it is (nothing from the server);
3. a key cleaned entirely then gets the files waiting for it on the server.

What is removed follows the settings of every kiosk: `scan.suspicious =
"block"` (default) removes the suspicious files with the malicious ones,
`"warn"` keeps them (and the key then gets nothing from the server only if
a malicious file remains). `quarantine.enabled` keeps a copy of the removed
files on the station (`quarantine.folder`): the only data of the keys it
keeps; set it to `false` for a station that keeps nothing of the keys.

## Configuration

`[airgap]` of `packaging/pandora-box.toml`:

```toml
[airgap]
enabled = true
# "both" (default), "inbound" (key -> server) or "outbound" (server -> key)
mode = "both"
# Clean the key too, after a touch on the screen (see below)
clean = false
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

Give the kiosks an account that can only write into the folder of the keys,
and **that cannot list it**: the account creates the folder of a key and
opens it by its name, which comes from the key. With the credentials of a
stolen kiosk, the folders of the keys can then neither be listed nor found.

- **SFTP**: an account restricted to SFTP in a chroot, for instance in
  `sshd_config`:

    ```text
    Match User kiosk
        ChrootDirectory /srv/sftp
        ForceCommand internal-sftp
        AllowTcpForwarding no
        X11Forwarding no
    ```

    with `path = "/keys"`, the folder in the chroot owned by the account,
    which may write into it and go through it, but not read it:

    ```sh
    install -d -m 0300 -o kiosk -g kiosk /srv/sftp/keys
    ```
- **rsync over SSH**: rsync must be installed on the server. Restrict the key
  of the kiosk to rsync in its folder with `rrsync` (shipped with rsync), in
  `~kiosk/.ssh/authorized_keys`:

    ```text
    command="rrsync /srv/rsync",restrict ssh-ed25519 AAAA... pandora-box-kiosk
    ```

    `rrsync` must read its folder (it locks it): the keys go into its
    subfolder `keys`, which the account cannot read, and `path = "keys"`,
    paths being relative to the folder of `rrsync`:

    ```sh
    install -d -m 0700 -o kiosk -g kiosk /srv/rsync
    install -d -m 0300 -o kiosk -g kiosk /srv/rsync/keys
    ```

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
   public key of the kiosks in `C:\Users\kiosk\.ssh\authorized_keys`. On
   Windows Server 2025, add it to the local group **OpenSSH Users**: sshd only
   lets this group and the administrators in (`AllowGroups`), any other
   account is refused before its key is read
   (`Add-LocalGroupMember -Group "OpenSSH Users" -Member kiosk`).
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

    with `path = "/keys"`. The folder `C:\PandoraBox\keys` lets the account
    create the folder of a key (`AD`), go through it (`X`) and read its
    attributes (`RA`), but not list it, without the rights inherited from
    `C:\` (the group Users may list it); in the folders of the keys, the
    account may modify:

    ```powershell
    icacls C:\PandoraBox\keys /inheritance:r /grant "SYSTEM:(OI)(CI)F" `
        "Administrators:(OI)(CI)F" "kiosk:(AD,X,RA)" "kiosk:(OI)(CI)(IO)M"
    ```

    Without a chroot, the paths are written `/C:/PandoraBox/keys`.
4. Open port 22 to the network of the kiosks: the firewall rule that comes
   with OpenSSH only allows the **Private** network profile, and an
   unidentified network is Public. For instance:
   `Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Profile Domain,Private,Public -RemoteAddress <addresses of the kiosks>`.
5. The host key of the server, for `image/airgap-known-hosts`:
   `ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub` on the server
   gives the fingerprint to compare with that of `ssh-keyscan`.

The copy sends each file to a temporary name, then renames it over the old
one (SFTP extension `posix-rename`); a server without it gets the old file
deleted first, then the new one renamed.

A Windows server can be tested in a virtual machine: see
[Tests](#tests).

## A stolen kiosk

What a stolen station holds, and what to do:

- **Not on it**: the files of the keys, their names and hashes, the scan
  reports, the folders of the keys on the server.
- **Still on it**: the journal of the system and the audit log of USBGuard
  keep the vendor, product and serial number of the USB devices plugged in,
  as the kernel and USBGuard report them; the disk is not encrypted (no TPM
  yet).
- **The credentials of the image**: the SSH key of the account of the
  kiosks, and the key that signs the tokens. With them, someone can connect
  to the server as a kiosk, but neither list the folders of the keys nor
  find one without its key.

After a theft, remove the public key of the kiosks from the server
(`authorized_keys`), then build the images with a new SSH key and install
them as an update on the other kiosks. To revoke one kiosk without the
others, build each kiosk with its own `image/credentials.toml`. A new
`token_key` makes every token invalid: each key then gets a new folder, and
its old folder stays on the server, to archive or delete.

## Scan report

`actions.airgap` of the report (see [Scan report](engines.md#scan-report)):

| Field | Content |
|---|---|
| `status` | `copied`, `failed`, `refused` (key not verified), or `skipped` (`mode = "outbound"`) |
| `key_id` | identifier of the key (not the name of its folder) |
| `destination` | folder of the key on the server |
| `copied`, `deleted`, `unchanged` | files sent, deleted from the server, already there |
| `not_copied` | files that are not clean |
| `errors` | clean files that could not be copied, and why |
| `error` | why the copy failed |
| `outbound` | the files of the server: `status` (`copied`, `nothing`, `refused` when the key holds dangerous files, `failed`), `copied`, `unchanged` (already on the key), `rejected` (with why), `error` |

The kiosk log has the events `key_token_written`, `key_identified`,
`key_token_invalid`, `airgap_copied`, `airgap_received`, `airgap_failed`
and `airgap_refused`;
the service logs `airgap_done` and `airgap_failed` in its journal
(`journalctl -u pandora-box-airgap`).

## Tests

- `tests/airgap/run.sh` copies to a real OpenSSH server with rsync, in a
  container (SFTP, rsync over SSH, and rsync restricted by `rrsync` as
  above): first a copy, then a synchronization (changed, new and removed
  files), an unknown host key refused, the folders of the keys not listable,
  the download of the outbound files the key lacks, and the service over
  its socket.
- `tests/windows/windows.sh` copies to a Windows Server in a QEMU virtual
  machine, set up as above (see below).
- The [virtual machine test](vm.md) enables the copy to an SFTP server of the
  test: a key is copied, then inserted again without one of its files, which
  is deleted from the server; the key keeps its malicious files and gets its
  identifier. That infected key gets nothing from the server; a clean key
  gets the clean file waiting for it, not the EICAR file next to it.

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
