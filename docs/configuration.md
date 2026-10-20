# Configuration

The kiosk reads its configuration from `/etc/pandora-box/pandora-box.toml`
(another file with `--config`, or the `PANDORA_BOX_CONFIG` environment
variable). Every key is optional: a missing key takes its default value.
The configuration is checked at startup, and an unknown key or an invalid
value is refused: the kiosk does not start with a configuration it does not
understand.

In the image, the root filesystem is read-only: the configuration is part of
the image, taken from `packaging/pandora-box.toml` when the image is built.
To change it, edit that file, build a new image and install it as an update
(see [Building the image](build.md) and [System updates](update.md)).

Check a configuration, its signatures, its engines and its theme without
starting the kiosk:

```sh
pandora-box --config pandora-box.toml --check-config
```

## Sections

| Section | Settings |
|---|---|
| `[kiosk]` | Name of the kiosk in the logs, interface (`framebuffer`, `curses`, `console`), FAKE_SCAN mode |
| `[display]` | Theme, language, framebuffer, return to the waiting screen: see [Interface](interface.md) |
| `[device]` | Mount point, allowed filesystems, FUSE drivers, eject |
| `[scan]` | Parallel workers, timeout per file, `suspicious` and `on_error` policies, sandbox |
| `[limits]` | Maximum file size, number of files, folder depth |
| `[policy]` | Number of engines needed for a malicious verdict |
| `[engines.*]` | Each detection engine: see [Detection engines](engines.md) |
| `[signatures]` | Signature sets and their verification: see [Signatures](signatures.md) |
| `[updates]` | Online signature and image updates: see [Signatures](signatures.md#online-updates) and [System updates](update.md#online) |
| `[siem]` | Log export to a SIEM: see [Log export to a SIEM](siem.md) |
| `[airgap]` | Copy of the clean files of the verified keys to a server: see [Copy to a server](airgap.md) |
| `[report]`, `[quarantine]`, `[logging]` | Folders of the scan reports, of the quarantine, log file and level: see [Logs](logs.md) |

## Reference

The configuration of the image,
[`packaging/pandora-box.toml`](https://github.com/sourcentis/pandora-box/blob/main/packaging/pandora-box.toml),
with every key, its default value and its meaning:

```toml
--8<-- "packaging/pandora-box.toml"
```
