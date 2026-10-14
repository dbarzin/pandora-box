from __future__ import annotations

import functools
import shutil
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pandora_box.config import Config, parse_config
from pandora_box.kiosk import Kiosk, build_pool
from pandora_box.monitor import NO_DEVICE, Action, DeviceEvent
from pandora_box.online import UpdateError, download
from pandora_box.sigsets import MANIFEST, install, installed_manifest, main, trusted_keys

from .conftest import DirectoryMounter, ListSource, RecordingDisplay
from .test_sigsets import make_key, make_set

pytestmark = pytest.mark.skipif(not Path("/usr/bin/openssl").exists(), reason="no openssl")

FILES = {"clamav/test.hdb": b"clamav db\n", "yara/rules/test.yar": b"rule x { condition: true }"}


class Server:
    """A local HTTP server publishing a folder; it records the requested paths."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.requests: list[str] = []
        server = self

        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self) -> None:
                server.requests.append(self.path)
                if self.path.startswith("/redirect/"):
                    self.send_response(302)
                    self.send_header("Location", "file:///etc/passwd")
                    self.end_headers()
                    return
                super().do_GET()

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(Handler, directory=str(root))
        )
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path: str = "set") -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/{path}/"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    root = tmp_path / "www"
    root.mkdir()
    server = Server(root)
    yield server
    server.httpd.shutdown()


@pytest.fixture
def key(tmp_path: Path) -> Path:
    return make_key(tmp_path / "keys", "update")


def updates_config(tmp_path: Path, url: str, enabled: bool = True) -> Config:
    return parse_config(
        {
            "kiosk": {"name": "test", "fake_scan": True},
            "scan": {"sandbox": False},
            "signatures": {
                "folder": str(tmp_path / "signatures"),
                "keys": str(tmp_path / "keys"),
            },
            "updates": {"enabled": enabled, "url": url},
        }
    )


def publish(server: Server, files: dict[str, bytes], serial: int, key: Path) -> None:
    folder = server.root / "set"
    shutil.rmtree(folder, ignore_errors=True)
    make_set(folder, files, serial, key)


def install_staged(staging: Path, config: Config) -> int:
    return main(
        [
            "install",
            str(staging),
            "--staged",
            "--keys",
            str(config.signatures.keys),
            "--target",
            str(config.signatures.folder),
        ]
    )


def test_download_then_only_the_changed_files(tmp_path: Path, server: Server, key: Path) -> None:
    config = updates_config(tmp_path, server.url())
    staging = tmp_path / "staging"
    publish(server, FILES, 1, key)
    manifest = download(config, staging, log=lambda _: None)
    assert manifest is not None
    assert manifest.serial == 1
    assert sorted(server.requests) == sorted(
        ["/set/manifest.json", "/set/manifest.json.sig", "/set/clamav/test.hdb",
         "/set/yara/rules/test.yar"]
    )  # fmt: skip
    assert install_staged(staging, config) == 0
    assert installed_manifest(config.signatures.folder).serial == 1  # type: ignore[union-attr]
    assert list(staging.iterdir()) == []

    server.requests.clear()
    publish(server, FILES | {"yara/rules/test.yar": b"rule y { condition: true }"}, 2, key)
    download(config, staging, log=lambda _: None)
    assert "/set/clamav/test.hdb" not in server.requests
    assert "/set/yara/rules/test.yar" in server.requests
    assert install_staged(staging, config) == 0
    current = config.signatures.folder / "current"
    assert (current / "yara/rules/test.yar").read_bytes() == b"rule y { condition: true }"
    assert (current / "clamav/test.hdb").read_bytes() == b"clamav db\n"


def test_up_to_date(tmp_path: Path, server: Server, key: Path) -> None:
    config = updates_config(tmp_path, server.url())
    publish(server, FILES, 1, key)
    install(server.root / "set", config.signatures.folder, trusted_keys(config.signatures.keys))
    staging = tmp_path / "staging"
    assert download(config, staging, log=lambda _: None) is None
    assert server.requests == ["/set/manifest.json", "/set/manifest.json.sig"]
    assert install_staged(staging, config) == 0  # nothing staged


def test_disabled(tmp_path: Path, server: Server) -> None:
    config = updates_config(tmp_path, server.url(), enabled=False)
    assert download(config, tmp_path / "staging", log=lambda _: None) is None
    assert server.requests == []


def test_set_signed_by_another_key_is_refused(tmp_path: Path, server: Server, key: Path) -> None:
    publish(server, FILES, 1, make_key(tmp_path / "foreign", "foreign"))
    config = updates_config(tmp_path, server.url())
    with pytest.raises(UpdateError, match="published set refused: invalid signature"):
        download(config, tmp_path / "staging", log=lambda _: None)
    assert server.requests == ["/set/manifest.json", "/set/manifest.json.sig"]


def test_modified_file_is_refused(tmp_path: Path, server: Server, key: Path) -> None:
    publish(server, FILES, 1, key)
    (server.root / "set/clamav/test.hdb").write_bytes(b"clamav dB\n")
    config = updates_config(tmp_path, server.url())
    staging = tmp_path / "staging"
    with pytest.raises(UpdateError, match="does not match the manifest"):
        download(config, staging, log=lambda _: None)
    # The manifest is written last: nothing is installed
    assert not (staging / MANIFEST).exists()
    assert install_staged(staging, config) == 0
    assert installed_manifest(config.signatures.folder) is None


def test_redirect_to_a_file_is_refused(tmp_path: Path, server: Server, key: Path) -> None:
    config = updates_config(tmp_path, server.url("redirect"))
    with pytest.raises(UpdateError, match="file:///etc/passwd"):
        download(config, tmp_path / "staging", log=lambda _: None)


def test_idle_kiosk_loads_a_new_set(tmp_path: Path, server: Server, key: Path) -> None:
    config = updates_config(tmp_path, server.url())
    keys = trusted_keys(config.signatures.keys)
    install(make_set(tmp_path / "s1", FILES, 1, key), config.signatures.folder, keys)
    display = RecordingDisplay()
    pool = build_pool(config)
    pool.start()
    try:
        kiosk = Kiosk(
            config,
            display,
            ListSource([DeviceEvent(Action.IDLE, NO_DEVICE), DeviceEvent(Action.IDLE, NO_DEVICE)]),
            pool,
            DirectoryMounter(tmp_path),
        )
        # Installed by the update service while the kiosk runs
        install(make_set(tmp_path / "s2", FILES, 2, key), config.signatures.folder, keys)
        kiosk.run()
    finally:
        pool.stop()
    assert display.messages.count("New signatures installed: set 2") == 1
    assert display.messages.count("Ready. Insert a USB device.") == 2
