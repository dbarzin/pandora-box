"""Graphical interface: theme, screens and framebuffer display."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from usb_pasteur.cli import main
from usb_pasteur.config import ConfigError, DisplayConfig, parse_config
from usb_pasteur.engines import EngineResult, Verdict
from usb_pasteur.results import FileResult
from usb_pasteur.ui.base import Screen, video_mode
from usb_pasteur.ui.framebuffer import (
    Framebuffer,
    FramebufferDisplay,
    FramebufferError,
    Geometry,
    raw_mode,
)
from usb_pasteur.ui.preview import main as theme_main
from usb_pasteur.ui.preview import sample_states
from usb_pasteur.ui.screens import Renderer, ViewState
from usb_pasteur.ui.theme import ThemeError, load_theme, theme_languages

# -- configuration -----------------------------------------------------------


def test_display_config() -> None:
    config = parse_config(
        {
            "kiosk": {"interface": "framebuffer"},
            "display": {
                "theme": "/usr/share/usb-pasteur/theme",
                "language": "fr",
                "result_seconds": 10,
            },
        }
    )
    assert config.display.theme == Path("/usr/share/usb-pasteur/theme")
    assert config.display.language == "fr"
    assert config.display.result_seconds == 10.0
    # An empty theme is the built-in theme
    assert parse_config({"display": {"theme": ""}}).display.theme is None


@pytest.mark.parametrize(
    ("display", "error"),
    [
        ({"theme": "relative/theme"}, "absolute path"),
        ({"language": "french"}, "language code"),
        ({"result_seconds": 0}, "positive"),
        ({"colors": {}}, "unknown key"),
    ],
)
def test_invalid_display_config(display: dict[str, object], error: str) -> None:
    with pytest.raises(ConfigError, match=error):
        parse_config({"display": display})


def test_video_mode() -> None:
    assert video_mode("ro video=1024x600M@60 quiet") == (1024, 600)
    assert video_mode("video=HDMI-A-1:800x480@60") == (800, 480)
    assert video_mode("ro quiet") is None


# -- theme -------------------------------------------------------------------


def test_builtin_theme() -> None:
    assert theme_languages(None) == ["en", "fr"]
    theme = load_theme(None, "fr")
    assert theme.text("insert") == "Veuillez insérer une clé USB"
    assert theme.text("scan_progress", done=3, total=10) == "3 / 10 fichiers analysés"
    assert theme.logo is None
    assert len(theme.insert) == 5 and len(theme.busy) == 9
    assert theme.colors["header"] == (0x8B, 0x00, 0x19)


def make_theme(folder: Path, toml: str) -> Path:
    folder.mkdir(exist_ok=True)
    (folder / "theme.toml").write_text(toml)
    return folder


def test_custom_theme(tmp_path: Path) -> None:
    Image.new("RGB", (400, 100), "navy").save(tmp_path / "logo.png")
    folder = make_theme(
        tmp_path,
        'logo = "logo.png"\n'
        '[colors]\nheader = "#003366"\n'
        '[texts.fr]\ntitle = "Hôpital de Mons"\nfooter = "Service informatique - 1234"\n'
        '[texts.de]\ninsert = "Bitte einen USB-Stick einstecken"\n',
    )
    theme = load_theme(folder, "fr")
    assert theme.logo is not None and theme.logo.size == (400, 100)
    assert theme.colors["header"] == (0x00, 0x33, 0x66)
    # Keys not set are kept
    assert theme.colors["threat"] == load_theme(None, "fr").colors["threat"]
    assert theme.text("title") == "Hôpital de Mons"
    assert theme.text("insert") == "Veuillez insérer une clé USB"
    # A new language, with the English texts it does not translate
    german = load_theme(folder, "de")
    assert german.text("insert") == "Bitte einen USB-Stick einstecken"
    assert german.text("clean") == "No threat found"
    assert theme_languages(folder) == ["de", "en", "fr"]


def test_theme_folder_without_theme_file(tmp_path: Path) -> None:
    assert load_theme(tmp_path, "en").text("title") == load_theme(None, "en").text("title")


@pytest.mark.parametrize(
    ("toml", "error"),
    [
        ("colour = 1\n", "unknown key"),
        ('[colors]\nheader = "red"\n', "colors.header must be a color"),
        ('[colors]\nbackdrop = "#ffffff"\n', "unknown key"),
        ('[texts.fr]\ntitle = "Borne {name}"\n', "unknown placeholder"),
        ('[texts.fr]\ncleaned_help = "{count"\n', "texts.fr.cleaned_help"),
        ('[texts.fr]\nwelcome = "Bonjour"\n', "unknown key"),
        ('logo = "../logo.png"\n', "must be a file of the theme folder"),
        ('logo = "/etc/passwd"\n', "must be a file of the theme folder"),
        ('logo = "missing.png"\n', "not found"),
        ('logo = "theme.toml"\n', "cannot read the image"),
        ("[images]\ninsert = []\n", "list of file names"),
        ("[images\n", "invalid TOML"),
    ],
)
def test_invalid_theme(tmp_path: Path, toml: str, error: str) -> None:
    with pytest.raises(ThemeError, match=error):
        load_theme(make_theme(tmp_path / "theme", toml), "fr")


def test_theme_language_missing(tmp_path: Path) -> None:
    with pytest.raises(ThemeError, match="no texts for the language 'nl'"):
        load_theme(None, "nl")
    with pytest.raises(ThemeError, match="theme folder not found"):
        load_theme(tmp_path / "missing", "en")


# -- screens -----------------------------------------------------------------


@pytest.mark.parametrize("size", [(1024, 600), (800, 480), (1280, 800)])
@pytest.mark.parametrize("language", ["en", "fr"])
def test_every_screen(size: tuple[int, int], language: str) -> None:
    renderer = Renderer(load_theme(None, language), size)
    for state in sample_states().values():
        frame = renderer.render(state)
        assert frame.base.size == size
        assert frame.image(1.5).size == size
        assert frame.lines


def scanned(path: str, verdict: Verdict = Verdict.MALICIOUS) -> FileResult:
    engine = EngineResult("clamav", verdict, ("Eicar-Signature",))
    return FileResult(Path("/media", path), 68, verdict, (engine,), rel_path=path)


def test_threats_screen() -> None:
    renderer = Renderer(load_theme(None, "en"), (1024, 600))
    files = tuple(scanned(f"folder/virus{i}.exe") for i in range(20))
    lines = renderer.render(ViewState(Screen.THREATS, files=files)).lines
    assert "Threat detected on the USB key" in lines
    assert "Touch the screen to clean the key" in lines
    assert "folder/virus0.exe" in lines and "Eicar-Signature" in lines
    # The files that do not fit are counted
    listed = [line for line in lines if line.startswith("folder/virus")]
    assert f"... and {20 - len(listed)} more files" in lines


def test_untrusted_file_names() -> None:
    renderer = Renderer(load_theme(None, "en"), (1024, 600))
    long_name = "dossier/" * 40 + "facture.pdf.exe"
    files = (scanned("evil\x1b[2J‮name.exe"), scanned(long_name))
    lines = renderer.render(ViewState(Screen.THREATS, files=files)).lines
    # Control characters are never drawn
    assert "evil?[2J?name.exe" in lines
    assert not any("\x1b" in line or "‮" in line for line in lines)
    # A long path is cut in its middle: the file name stays visible
    cut = next(line for line in lines if line.startswith("dossier/"))
    assert "…" in cut and cut.endswith("facture.pdf.exe")


def test_scanning_screen() -> None:
    renderer = Renderer(load_theme(None, "en"), (1024, 600))
    state = sample_states()["scanning"]
    frame = renderer.render(state)
    assert "152 / 411 files scanned" in frame.lines
    assert "Threats found: 1" in frame.lines
    assert "37%" in frame.lines
    # The spinner turns
    assert frame.animation is not None
    assert frame.image(0.0).tobytes() != frame.image(frame.animation.interval).tobytes()


def test_preview(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert theme_main(["check"]) == 0
    assert "OK (en, fr)" in capsys.readouterr().out
    assert theme_main(["preview", "--language", "fr", "--size", "800x480", str(tmp_path)]) == 0
    with Image.open(tmp_path / "fr-threats.png") as image:
        assert image.size == (800, 480)
    assert len(list(tmp_path.glob("*.png"))) == len(sample_states())
    bad = make_theme(tmp_path / "bad", '[colors]\nheader = "red"\n')
    assert theme_main(["check", str(bad)]) == 2


def test_check_config_checks_the_theme(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    theme = make_theme(tmp_path / "theme", '[colors]\nheader = "red"\n')
    path = tmp_path / "usb-pasteur.toml"
    path.write_text(f'[kiosk]\nfake_scan = true\n[display]\ntheme = "{theme}"\n')
    assert main(["--config", str(path), "--check-config"]) == 2
    assert "theme: colors.header" in capsys.readouterr().err


# -- framebuffer -------------------------------------------------------------


def test_raw_mode() -> None:
    assert raw_mode(32, 16, 0) == "BGRX"
    assert raw_mode(32, 0, 16) == "RGBX"
    assert raw_mode(24, 16, 0) == "BGR"
    with pytest.raises(FramebufferError, match="16 bits per pixel"):
        raw_mode(16, 11, 0)


# 1024x768 framebuffer of the firmware, 1024x600 visible, padded lines
GEOMETRY = Geometry(width=64, height=30, line_length=80 * 4, bytes_per_pixel=4, raw_mode="BGRX")
FB_LINES = 40


@pytest.fixture
def fb_file(tmp_path: Path) -> Path:
    path = tmp_path / "fb0"
    path.write_bytes(bytes(GEOMETRY.line_length * FB_LINES))
    return path


def pixel(data: bytes, x: int, y: int) -> bytes:
    offset = y * GEOMETRY.line_length + x * GEOMETRY.bytes_per_pixel
    return data[offset : offset + 4]


def test_framebuffer_write(fb_file: Path) -> None:
    fb = Framebuffer(fb_file, GEOMETRY)
    image = Image.new("RGB", (64, 30), (255, 0, 0))
    image.putpixel((63, 29), (0, 0, 255))
    fb.write(image)
    data = fb_file.read_bytes()
    assert pixel(data, 0, 0) == b"\x00\x00\xff\x00"  # BGRX
    assert pixel(data, 63, 29) == b"\xff\x00\x00\x00"
    # The padding of the lines and the lines out of the screen are not drawn
    assert pixel(data, 64, 0) == bytes(4)
    assert pixel(data, 0, 30) == bytes(4)
    # Only a part
    fb.write(Image.new("RGB", (64, 30), (0, 255, 0)), (10, 5, 20, 6))
    data = fb_file.read_bytes()
    assert pixel(data, 10, 5) == b"\x00\xff\x00\x00"
    assert pixel(data, 9, 5) == b"\x00\x00\xff\x00"
    assert pixel(data, 10, 6) == b"\x00\x00\xff\x00"
    fb.close()


def test_not_a_framebuffer(tmp_path: Path) -> None:
    with pytest.raises(FramebufferError, match="cannot open"):
        Framebuffer(tmp_path / "missing")
    (tmp_path / "file").write_bytes(b"")
    with pytest.raises(FramebufferError, match="not a framebuffer"):
        Framebuffer(tmp_path / "file")


@pytest.fixture
def pty() -> Iterator[tuple[int, int]]:
    master, slave = os.openpty()
    yield master, slave
    os.close(master)
    os.close(slave)


def wait_for(condition: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():  # type: ignore[operator]
        assert time.monotonic() < deadline, "timeout"
        time.sleep(0.02)


def make_display(fb_file: Path, slave: int, result_seconds: float = 30.0) -> FramebufferDisplay:
    config = DisplayConfig(framebuffer=fb_file, result_seconds=result_seconds)
    return FramebufferDisplay(
        config, load_theme(None, "en"), Framebuffer(fb_file, GEOMETRY), console_fd=slave
    )


def current(display: FramebufferDisplay) -> Screen:
    """The screen shown (a function: mypy would narrow the attribute)."""
    return display._state.screen


def test_display_draws(fb_file: Path, pty: tuple[int, int]) -> None:
    display = make_display(fb_file, pty[1])
    display.start()
    try:
        display.show(Screen.CLEAN)
        header = load_theme(None, "en").colors["header"]
        bgrx = bytes((header[2], header[1], header[0], 0))
        wait_for(lambda: pixel(fb_file.read_bytes(), 0, 0) == bgrx)
    finally:
        display.stop()


def test_display_drawn_again_after_a_clear(
    fb_file: Path, pty: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The graphics driver clears the framebuffer behind the kiosk (mode set
    # at boot): the screen does not change, it must still be drawn again
    monkeypatch.setattr("usb_pasteur.ui.framebuffer._FULL_REDRAW", 0.2)
    display = make_display(fb_file, pty[1])
    display.start()
    try:
        display.show(Screen.CLEAN)
        wait_for(lambda: pixel(fb_file.read_bytes(), 0, 0) != bytes(4))
        # In place: a truncated file would make the mapping fail
        with fb_file.open("r+b") as fb:
            fb.write(bytes(GEOMETRY.line_length * FB_LINES))
        wait_for(lambda: pixel(fb_file.read_bytes(), 0, 0) != bytes(4))
    finally:
        display.stop()


def test_result_stays_on_the_screen(fb_file: Path, pty: tuple[int, int]) -> None:
    display = make_display(fb_file, pty[1], result_seconds=0.3)
    display.start()
    try:
        display.show(Screen.CLEANED, [scanned("virus.exe")])
        # The device is ejected, then removed right away: the result stays
        display.show(Screen.READY)
        assert current(display) is Screen.CLEANED
        wait_for(lambda: current(display) is Screen.READY)
        # A new device does not wait
        display.show(Screen.CLEAN)
        display.show(Screen.READY)
        display.show(Screen.SCANNING)
        assert current(display) is Screen.SCANNING
        time.sleep(0.5)
        assert current(display) is Screen.SCANNING
    finally:
        display.stop()


def test_display_state(fb_file: Path, pty: tuple[int, int]) -> None:
    display = make_display(fb_file, pty[1])
    display.show(Screen.SCANNING)
    display.message("Device inserted")
    display.file_scanned(scanned("a.txt", Verdict.CLEAN), 1, 3)
    display.file_scanned(scanned("b.exe"), 2, 3)
    state = display._state
    assert (state.done, state.total, state.threats) == (2, 3, 1)
    assert [f.rel_path for f in state.recent] == ["b.exe", "a.txt"]
    # The messages of a step are dropped by the next one
    display.show(Screen.CLEANING)
    assert list(display._state.messages) == []
    display.message("b.exe removed")
    display.show(Screen.ERROR)
    assert list(display._state.messages) == ["b.exe removed"]


def test_confirm_with_a_key(fb_file: Path, pty: tuple[int, int]) -> None:
    master, slave = pty
    display = make_display(fb_file, slave)
    display.start()
    try:
        os.write(master, b"x")
        # A key pressed before the question is not an answer: the input is flushed
        time.sleep(0.1)
        started = time.monotonic()
        timer = threading.Timer(0.3, lambda: os.write(master, b"\n"))
        timer.start()
        display.confirm("PRESS A KEY OR TOUCH THE SCREEN TO CLEAN")
        assert time.monotonic() - started >= 0.25
    finally:
        display.stop()
