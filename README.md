# USB-Pasteur README

**USB-Pasteur** is an open source USB decontamination kiosk ("sheep-dip station") to detect and remove malware from USB storage devices.
Successor to [pandora-box](https://github.com/dbarzin/pandora-box), with no dependency on Pandora (CIRCL).

> [!WARNING]
> **USB-Pasteur is under active development and is not production-ready.**
> The scanning engine (phase 1) works, but signature updates are not verified and the system is not hardened yet (phase 2).
> **Do not rely on it to check USB devices.** Until the first release, use a supported commercial or open source solution.

## Vision

Provide a **free, auditable and hardened** USB decontamination kiosk, a credible alternative to commercial solutions, suited to sensitive environments (healthcare, industry, public administration).

## Guiding principles

- **Minimal, hardened image**: the system contains only what is needed to scan.
- **Defense in depth**: the USB device is hostile, and analyzers are assumed to be compromisable.
- **Offline by default**: no network connection is required to scan; updates go through a signed channel.
- **Plugin engines**: every detection engine implements a common interface.
- **Traceability**: every scan produces a structured, timestamped report.
- **Language**: Python 3.11+ for the orchestrator (reuse of pandora-box code, analysis ecosystem).

---

## Phase 0 — Foundations

Goal: restart from a clean base while reusing what works in pandora-box.

### Code reused from pandora-box

| Component | Decision |
|---|---|
| State machine (START, WAIT, SCAN, CLEAN, STOP) | Kept, refactored into a dedicated module |
| USB detection with `pyudev` | Kept |
| Device mounting / unmounting | Kept, hardened (mount options, read-only) |
| Quarantine of infected files | Kept |
| Curses interface (administrator mode) | Kept |
| Logging, logrotate, systemd service | Kept, moved to structured JSON |
| USB auto-mount mode | Kept for development only (`device.auto_mount`): never on the kiosk, which mounts devices read-only itself |
| Image slideshow display (`fim`) | Removed, replaced by the graphical kiosk interface in phase 3, which reuses its images |
| Single-instance lock | Kept |
| `pypandora` calls | Removed, replaced by the engine pipeline |
| Comodo engine | Removed (no longer maintained on Linux) |

### Tasks

- [x] Project name: **USB-Pasteur** (repository `usb-pasteur`, Python module `usb_pasteur`)
- [x] New repository, GPL-3.0 license, `CONTRIBUTING.md`, `SECURITY.md`
- [x] Python package structure (`pyproject.toml`), type hints, `ruff`, `mypy`
- [x] TOML configuration (replaces the `.ini`), validated at startup
- [x] Keep `FAKE_SCAN` mode for development
- [x] Continuous integration: lint, unit tests, package build
- [x] End-to-end tests in a container with a simulated USB disk image (loop device)

### Current status

The pandora-box code has been ported to the `usb_pasteur` package (`src/usb_pasteur/`):

| Module | Role |
|---|---|
| `cli.py` | Command line entry point (`usb-pasteur`) |
| `config.py` | TOML configuration, validated at startup |
| `statemachine.py`, `kiosk.py` | State machine and kiosk workflow |
| `monitor.py` | USB detection with udev |
| `device.py` | Hardened mounting (`ro,noexec,nosuid,nodev`, read-write only to clean) |
| `inventory.py` | Device inventory: never follows links, never leaves the device, limits |
| `scanner.py`, `workers.py`, `worker.py` | Scan in supervised worker processes, timeouts and watchdog |
| `sigsets.py`, `publish.py` | Signed signature sets: verification, installation, publication from the sources, `usb-pasteur-signatures` command |
| `sandbox.py`, `seccomp.py`, `protocol.py` | Worker sandbox (bubblewrap, dedicated user, system call filter), validated JSON messages from the workers |
| `pipeline.py`, `hashing.py`, `filetype.py` | Per file: hashes (SHA-256, SHA-1, MD5), libmagic type, engines |
| `engines/` | Engine interface, MalwareBazaar, Hashlookup, ClamAV, YARA-X and fake engine |
| `hashdb.py`, `bloom.py`, `clamd.py` | MalwareBazaar database, DCSO Bloom filter reader, clamd client |
| `policy.py` | Verdict aggregation per file and per device |
| `report.py`, `schemas/` | JSON scan report and its JSON Schema |
| `signatures.py` | Signature provenance and freshness, verification hook (phase 2) |
| `quarantine.py` | Copy of infected files with a manifest referencing the report |
| `ui/` | Graphical kiosk interface (framebuffer, themes), curses (administrator) and console interfaces |
| `logs.py`, `lock.py` | JSON logs, single-instance lock |

Deployment files are in `packaging/`: example configuration, systemd service, tmpfiles and logrotate.

The detection engines, their data files and the verdict policy are described in [docs/engines.md](docs/engines.md). Every engine is enabled by default: the kiosk refuses to start when an enabled engine cannot load its signatures, or when no content engine (ClamAV or YARA-X) is enabled.

### Quick start (development)

#### Prerequisites

- Linux with udev (USB detection and mounting require root privileges)
- **Python 3.11 or later**
- libmagic (`sudo apt install libmagic1`)
- for real scans: `clamav-daemon` (see [docs/engines.md](docs/engines.md#clamav))

Recent distributions (Debian 12 and later, Ubuntu 23.04 and later) forbid `pip install` into the system Python ([PEP 668](https://peps.python.org/pep-0668/), `externally-managed-environment` error). Always work inside a virtual environment, and never use `--break-system-packages`.

#### Create the virtual environment

If your system Python is 3.11 or later (Debian 12 and 13, Ubuntu 24.04):

```sh
sudo apt install python3-venv python3-full
git clone https://github.com/dbarzin/usb-pasteur.git
cd usb-pasteur
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

If your system Python is older (Ubuntu 22.04 ships Python 3.10), use [uv](https://docs.astral.sh/uv/), which downloads a suitable Python without touching the system:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/dbarzin/usb-pasteur.git
cd usb-pasteur
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
```

#### Configure and run

```sh
cp packaging/usb-pasteur.toml usb-pasteur.toml
# YARA rules, MalwareBazaar (needs ABUSECH_AUTH_KEY), Hashlookup: an unsigned set
usb-pasteur-signatures publish dev-signatures --sources yara-forge,malwarebazaar,hashlookup
# copy the printed [engines.*] and [signatures] settings into usb-pasteur.toml,
# set the quarantine, report and log folders, then check the configuration
usb-pasteur --config usb-pasteur.toml --check-config
sudo .venv/bin/usb-pasteur --config usb-pasteur.toml --interface console
```

`sudo` resets `PATH`, so the kiosk is started with the full path of the executable installed in the virtual environment (`.venv/bin/usb-pasteur`).

Mounting a device requires root: adding your user to a group (such as `disk`) is not enough. On a desktop that mounts USB devices itself (udisks automount), set `auto_mount = true` in `[device]` instead: the kiosk then uses the system mount, and unmounts and mounts the device again with `udisksctl`, so it runs without `sudo`. This is for development only: the system mount is usually read-write and not hardened.

The device is unmounted while the kiosk asks the user to confirm the cleaning, so that it can be removed safely. To clean, it is mounted again read-write; a file is only removed if its SHA-256 is still the one that was scanned (another device may have been inserted in the meantime). When the scan and the cleaning are over, the device is ejected (`device.eject`, with `eject`, or `udisksctl power-off` in auto-mount mode).

The scan workers run in a sandbox by default (`scan.sandbox`): it needs root, `bwrap` (Debian package `bubblewrap`), `setpriv` and a `usb-pasteur-scan` user (`sudo systemd-sysusers packaging/sysusers/usb-pasteur.conf`). For development without them, set `sandbox = false` in `[scan]`.

The kiosk only uses a signed signature set (`signatures.verify`, see [docs/signatures.md](docs/signatures.md)): development signatures are not signed, set `verify = false` in `[signatures]` to use them.

To work on the workflow without signatures, set `fake_scan = true` in `[kiosk]` (or use `--fake-scan`): only the [EICAR test file](https://www.eicar.org/download-anti-malware-testfile/) is reported as malicious, so the whole workflow (scan, quarantine, cleaning) can be tested without real engines.

#### Lint and tests

With the virtual environment activated:

```sh
ruff check .
mypy src
pytest
```

Integration tests run against real engines when they are available: a clamd socket (`USB_PASTEUR_CLAMD_SOCKET`, default `/run/clamav/clamd.ctl`), real rules (`USB_PASTEUR_YARA_RULES`, paths separated by `:`) or a full configuration for the detection corpus (`USB_PASTEUR_CORPUS_CONFIG`). End-to-end tests with loop devices and a real clamd run in a container: `tests/e2e/run.sh`.

## Phase 1 — Scanning engine (MVP)

Goal: replace Pandora with an equivalent or better local pipeline.

- [x] Common plugin interface: `scan(file) -> Verdict` (clean, suspicious, malicious, error, skipped)
- [x] Device inventory and hash computation (SHA-256, SHA-1, MD5)
- [x] **Hashlookup (CIRCL)** as an offline Bloom filter: skip the content engines for known files (known does not mean benign)
- [x] **MalwareBazaar**: offline list of malicious hashes
- [x] **ClamAV** through `clamd` (Unix socket) + third-party signatures
- [x] **YARA-X** + YARA Forge and signature-base rules
- [x] Real file type identification (`libmagic`)
- [x] Parallel scan in supervised worker processes, timeout per file and per engine
- [x] Verdict aggregation with a configurable policy (one positive engine is enough, etc.); `scan.suspicious` and `scan.on_error` policies (`block` / `warn`)
- [x] Limits: maximum file size, number of files, directory depth
- [x] JSON scan report (device, files, verdicts, engines, signature versions) with a JSON Schema
- [x] Test set: EICAR, harmless samples, known false positives (`tests/corpus/`)

See [docs/engines.md](docs/engines.md) for the engines and the verdict policy.

## Phase 2 — Minimal hardened image

Goal: ship a ready-to-flash system image with the smallest possible attack surface.

### Current status

A first image is built with mkosi from Debian 13 packages and boots in a QEMU/KVM virtual machine, where an automated test plays the whole workflow with an emulated USB key and the real engines. The root filesystem is read-only (EROFS) and protected by dm-verity, the data (`/var`) is on its own partition, grown to fill the disk at boot, and the bootloader and unified kernel image are signed for Secure Boot. Only USB storage devices are allowed (USBGuard), and the image has no driver for USB network, wireless or Bluetooth devices. The system is hardened (kernel settings and lockdown, firewall, no login console, clamd sandbox) and the scan workers run in a sandbox (bubblewrap, dedicated user, system call filter). The kiosk only uses a signed signature set, verified at each start; without one, it scans nothing and waits for a signature update device. With the image profile `online`, the kiosk downloads the signatures from their sources itself (ClamAV, YARA Forge, MalwareBazaar, Hashlookup), with the credentials of the image, and signs the set with its own key. See [docs/image.md](docs/image.md):

```sh
image/build-test.sh   # build the test images (Docker only)
image/vm.sh test      # end-to-end test in a virtual machine
```

### Build

- [x] Image based on Debian 13 (trixie), built with `mkosi`
- [ ] Reproducible builds and an SBOM generated for every release
- [ ] Single target: x86_64 (see reference hardware); ARM64 is not targeted, as the Raspberry Pi proved too slow
- [ ] Signed image and published checksums

### Reference hardware

- **Computer**: refurbished Lenovo ThinkCentre (x86_64)
- **Memory**: 2 GB minimum, the size of the test virtual machine (clamd alone uses about 1 GB with the full ClamAV databases)
- **Display**: Waveshare 7-inch HDMI LCD (C), capacitive touchscreen, 1024x600 (USB touch controller `0eef:0005`)
- **Enclosure**: 3D-printed

Validated hardware, its settings and the BIOS configuration: [docs/hardware.md](docs/hardware.md).

Tasks:

- [x] List of validated ThinkCentre models (CPU, RAM, USB ports, Secure Boot support): ThinkCentre M720q Tiny (10T7, Core i5-9400T, Intel UHD 630)
- [x] Touchscreen model selection and driver support: Waveshare 7-inch HDMI LCD (C), mode of the screen on the kernel command line, touch controller allowed by USBGuard
- [ ] Enclosure design published in the repository (source files + STL), under an open hardware license: the STL is in [3D/](3D/), under the CERN-OHL-S v2; the source file of the model remains to be added
- [ ] Assembly guide (bill of materials, printing settings, wiring, USB port layout for the user)
- [ ] Physical hardening: only the scanning USB ports reachable from the outside, other ports and BIOS access protected
- [x] BIOS configuration guide (password, boot order locked, Secure Boot with the image key enrolled from setup mode, VT-d, restart after a power failure)

### System integrity

- [x] Read-only root filesystem, variable data on a separate partition
- [x] `dm-verity` on the system partition
- [x] Unified Kernel Image (UKI) and Secure Boot (development keys; release keys and their storage still to define)
- [x] Atomic A/B partition updates (`systemd-sysupdate`), installed from a signed image update device, with boot assessment and automatic rollback
- [x] Online image updates (`updates.image_url`): downloaded by the update service, installed without network, the idle kiosk restarts on the new version
- [x] Smaller UKI: 40 MB instead of 130 MB, the initrd only holds the kernel modules needed to mount the root filesystem

### Hardening

- [x] No interactive account by default, no SSH: no `login` program, no root password, no login console, Ctrl-Alt-Del masked
- [x] Maintenance device: a USB key with a request signed with the update key (valid 7 days) brings back the logs of the kiosk, without login or network access
- [ ] Maintenance console or SSH, if the logs are not enough
- [x] Hardened kernel settings (`sysctl`, `lockdown=confidentiality`, memory initialization, IOMMU, unused modules removed)
- [x] `nftables` firewall: deny all by default
- [x] **USBGuard**: only mass-storage devices (and hubs) are allowed (BadUSB protection), no device is authorized before USBGuard starts (`usbcore.authorized_default=0`)
- [x] No USB network, wireless, Bluetooth, USB serial and modem drivers in the image; USB HID is kept for the touchscreen, keyboards and mice are blocked by USBGuard
- [x] USBGuard rule allowing the touchscreen of the reference hardware (Waveshare `0eef:0005`, its serial, HID interface only, internal port), touch tested in the virtual machine
- [x] No boot loader menu: systemd-boot does not read the keyboard (a keyboard works in the firmware, before USBGuard starts)
- [x] Devices mounted with `ro,noexec,nosuid,nodev` by the kiosk, no automount (no udisks in the image)
- [x] Limited set of supported filesystems (vfat, exfat, ntfs3, ext4): the kiosk refuses to mount any other
- [x] Hardened systemd services (`ProtectSystem`, `NoNewPrivileges`, seccomp filters; `PrivateNetwork` and `MemoryDenyWriteExecute` for clamd), exposure measured by `systemd-analyze security` in the virtual machine test
- [x] Each scan worker runs in a `bubblewrap` sandbox, without network, under a dedicated user without capabilities, with a system call filter; the kiosk opens the files and passes their descriptors, and only accepts validated JSON from the workers
- [x] One worker per engine, so that a compromised engine cannot forge the results of another one: the kiosk hashes the files itself, hash engines never get the file, the kiosk combines the results
- [x] Audit log (`auditd`) for sensitive operations
- [x] Assessment with `lynis` and the ANSSI configuration recommendations for GNU/Linux systems

### Signature updates

See [docs/signatures.md](docs/signatures.md).

- [x] Signed signature sets (Ed25519, verified with `openssl`): no rollback, atomic installation, previous set kept
- [x] Signature verification before loading databases: the installed set is verified at each start, the engines (and clamd) only read it
- [x] Offline updates from a signed USB device, for air-gapped kiosks
- [x] Publication: `usb-pasteur-signatures publish` downloads the sources (ClamAV, YARA Forge, MalwareBazaar, Hashlookup) with a cache, checks the set with the kiosk engines, then signs it; container `publish/`
- [x] Online updates through a dedicated channel: image profile `online`, update service as the only user allowed out by the firewall, HTTP(S) and proxy, only the changed files downloaded, new set loaded when the kiosk is idle
- [x] Online updates from the sources themselves (freshclam, YARA Forge, MalwareBazaar, Hashlookup): the abuse.ch Auth-Key is a systemd credential of the image (`image/credentials.toml`, outside git), the set is signed with a key generated on the kiosk
- [ ] Release signing key of the project, its storage (hardware token) and the publication schedule

### Testing in a virtual machine

The container end-to-end tests (`tests/e2e/run.sh`) cover the scan workflow, but not the image itself: boot, Secure Boot, dm-verity, kernel settings, udev, USBGuard, systemd services. The whole chain is tested locally in a QEMU/KVM virtual machine, without the reference hardware: the image boots with UEFI firmware (OVMF) and an empty USB 3 controller, and emulated USB keys (disk images) are inserted and removed while the kiosk runs, which triggers the same udev events as a real device. Hostile samples are only ever copied into key images, never onto the development desktop. See [docs/image.md](docs/image.md) for the automated test and the interactive virtual machine.

Tasks:

- [x] Test image profile: test-only signatures that detect the test corpus, root shell on the virtio console
- [x] Automated end-to-end test in the virtual machine: insertion through QMP, verdicts of every engine, cleaning confirmed on the kiosk screen, quarantine, report, eject, removal, clean key inserted again
- [x] Read-only root filesystem, data partition grown to fill the disk, data kept after a reboot
- [x] OVMF with the image signing keys enrolled: Secure Boot enabled, kernel lockdown, root filesystem on dm-verity; a modified root block cannot be read; firmware trusting another key refuses the image
- [x] USB key images for every supported filesystem (vfat, exfat, ntfs3, ext4), plus partitioned, unsupported and corrupted filesystems
- [x] An emulated keyboard (`usb-kbd`) and network adapter (`usb-net`) are blocked by USBGuard: no input device, no network interface
- [x] No outgoing network outside the update channel
- [x] A/B update and rollback tested in the virtual machine
- [x] Run in continuous integration (KVM when nested virtualization is available, TCG emulation otherwise)
- [ ] Software TPM (`swtpm`), once measured boot is used

## Phase 3 — Kiosk interface

### Current status

The kiosk draws a graphical interface on its touch screen: waiting for a key, scan progress (files scanned, threats found, latest files and their verdict), the dangerous files listed before they are removed, a touch anywhere on the screen to clean, then the result. It is drawn with Pillow straight into the framebuffer, without a display server or a browser. An administrator customizes it with a theme built into the image: logo, colors, texts and images. See [docs/interface.md](docs/interface.md).

- [x] Graphical interface drawn into the framebuffer (Pillow), console in graphics mode: no display server, browser or local web server (the earlier plan, FastAPI + htmx in `cage` + Chromium, would have added a browser and a socket to the image)
- [x] User flow: insertion, progress, dangerous files listed, cleaning on a touch of the screen (no buttons), verdict
- [x] Touch-first interface designed for the 7-inch screen (1024x600), drawn for the size of any screen
- [x] Customization by the administrator: theme folder built into the image (logo, colors, texts of every screen, images, fonts), checked at build time, `usb-pasteur-theme preview` to see the screens without a kiosk
- [ ] Security awareness messages during the scan
- [ ] Internationalization: FR, EN (built-in), DE, NL (a theme can add a language)
- [ ] Protected administrator mode (curses or web): scan reports, signature status
- [ ] Accessibility (contrast, font size)

## Phase 4 — Advanced analysis

- [ ] Office documents: `oletools` (olevba, mraptor); documents with macros are already suspicious (ClamAV `AlertOLE2Macros`)
- [x] PDF: JavaScript and Launch actions are suspicious, automatic actions, embedded files and forms reported (like `pdfid`, `engines.heuristics`)
- [x] Archives: unpacked and scanned by ClamAV within its limits; beyond them (decompression bombs) the file is not fully scanned
- [x] Encrypted archives and documents flagged as unscannable (ClamAV `AlertEncrypted`)
- [ ] Executables: `capa` (optional, can be disabled for performance)
- [x] Detection of programs disguised as documents: extension / real type mismatch, double extension (`engines.heuristics`)
- [ ] **Content disarm and reconstruction (CDR)** to a trusted device using Dangerzone
- [ ] Selective copy of clean files only to a second device

## Phase 5 — Fleet management and traceability

- [ ] Log export as syslog / JSON to a SIEM (Wazuh, etc.)
- [ ] Printed or exported scan receipt for the user
- [ ] Optional central server: kiosk inventory, reports, signature status
- [ ] Centralized distribution of signed updates
- [ ] Statistics dashboard (devices scanned, detections)

## Phase 6 — Assurance and compliance

- [ ] Documented threat model
- [ ] Fuzzing of entry points (mounting, metadata parsing)
- [ ] Detection regression test corpus
- [ ] External security audit of the image
- [ ] Deployment guide: kiosk placement, user procedure, associated USB policy
- [ ] Mapping to NIS2 and ISO 27001 requirements (control 7.10 "Storage media")

---

## Out of scope (for now)

- Workstation agent blocking devices that have not been scanned
- Dynamic analysis (execution sandbox)
- Encrypted devices (BitLocker, VeraCrypt)
- Smartphones and MTP devices
- `.deb` package installable on an existing system: USB-Pasteur is only distributed as a complete hardened image

## Decisions

- Policy for a "suspicious" verdict: configurable with `scan.suspicious`, `block` (default) or `warn`.
- Policy for files that could not be fully scanned (engine error or timeout, limits): configurable with `scan.on_error`. `block` (default) reports the device as not verified; unscanned files are listed but never removed, since they are not known to be malicious.
- A Hashlookup hit means "known file", not "benign file": content engines also scan known files by default (the CIRCL filter lists the EICAR test file, tools and malware; `engines.hashlookup.skip_content_engines` skips them, opt-in), a malicious hash always wins, and the decision is recorded in the scan report.
- YARA Forge already includes signature-base: only YARA Forge `core` is configured by default, signature-base is opt-in.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability, follow [SECURITY.md](SECURITY.md).

## License

USB-Pasteur is free software, licensed under the [GNU General Public License v3.0](LICENSE).
