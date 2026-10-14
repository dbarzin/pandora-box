# Contributing to PandoraBox

Thank you for your interest in PandoraBox! Contributions of all kinds are welcome: bug reports, documentation, detection engines, hardening improvements, translations, hardware validation and code.

Please read this guide before opening an issue or a pull request.

## Code of conduct

Be respectful, constructive and patient. Harassment or discriminatory behavior will not be tolerated.

## Reporting security issues

**Do not open a public issue for a security vulnerability.** PandoraBox is a security product: please follow the process described in [SECURITY.md](SECURITY.md).

## Reporting bugs and requesting features

- Search the [existing issues](https://github.com/dbarzin/pandora-box/issues) first to avoid duplicates.
- For a bug, include:
  - the PandoraBox version or commit,
  - the hardware (model, USB ports, display) or the development environment,
  - the steps to reproduce, the expected result and the actual result,
  - the relevant logs or scan report (remove any sensitive data first).
- For a feature request, explain the use case and how it fits the [roadmap](ROADMAP.md) and the guiding principles (minimal image, defense in depth, offline by default).

**Never attach real malware samples to an issue.** Use the EICAR test file or share hashes (SHA-256) instead.

## Development workflow

1. Fork the repository and create a branch from `main`:
   ```sh
   git checkout -b feature/short-description
   ```
2. Make focused changes: one logical change per pull request.
3. Add or update tests and documentation.
4. Make sure linting, type checking and tests pass locally.
5. Open a pull request against `main` describing **what** changes and **why**, and link the related issue.

### Development environment

PandoraBox requires **Python 3.11+** (the system image uses Python 3.13). Upgrade pip first (`pip install -U pip`): old versions, such as the one of Debian 12, take a very long time to report dependency errors.

```sh
git clone https://github.com/<your-account>/pandora-box.git
cd pandora-box
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

Use the `FAKE_SCAN` mode (`fake_scan = true` in the configuration, or `--fake-scan`) to work on the orchestrator and the interface without real detection engines: only the EICAR test file is reported as malicious. Use `--interface console` to run without curses.

To work with the real engines, install `clamav-daemon` and download the other signatures with `pandora-box-signatures publish dev-signatures --sources yara-forge,malwarebazaar,hashlookup`: see [docs/engines.md](docs/engines.md).

### Coding standards

- Format and lint with `ruff`; check types with `mypy`. Public functions must have type hints.
- Follow the existing structure and naming of the `pandora_box` package.
- Treat every USB device and every file it contains as **hostile input**: validate paths, sizes and types, never execute or interpret content, and respect the configured limits.
- Detection engines must implement the common interface (`pandora_box.engines.Engine`: `load()`, `version()`, `signature_info()` and `scan(file_info) -> EngineResult`), read files only through the descriptor given in `FileInfo`, keep no state between files, and must not require network access at scan time.
- Do not add dependencies without discussing them first: each new package increases the attack surface of the image.
- Keep log messages and scan reports structured (JSON) and free of secrets.

### Tests

```sh
ruff check .
ruff format --check .
mypy
pytest
```

- Unit tests are required for new code and bug fixes.
- Detection tests must use harmless samples (EICAR, synthetic files). Do not commit real malware, nor the raw EICAR string: generate it at test time (`tests/samples.py`).
- Add regression samples and known false positives to the detection corpus: see [tests/corpus/README.md](tests/corpus/README.md).
- End-to-end tests run in a privileged container with simulated USB disk images (loop devices, vfat/exfat/ext4). Run them with `tests/e2e/run.sh` (requires Docker and the `loop`, `vfat` and `exfat` kernel modules).

### Commit messages

- Write clear, imperative messages in English: `Add YARA-X engine`, `Fix mount options for exFAT`.
- Keep the first line under 72 characters, add details in the body when useful.
- Reference issues where relevant: `Fixes #42`.

## Translations

The kiosk interface targets FR, EN, DE and NL. Translation improvements and new languages are welcome; keep messages short so they fit on the 7-inch touchscreen.

## Hardware contributions

Reports on validated ThinkCentre models, touchscreens and enclosure improvements are valuable. Please include the exact model, firmware/BIOS version and what was tested.

## License

PandoraBox is licensed under the [GNU General Public License v3.0](LICENSE). By submitting a contribution, you agree that it will be distributed under the same license, and you confirm that you have the right to submit it.
