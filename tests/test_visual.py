"""Tests for the `visual` subcommand — screenshot baselines and comparison.

Run: pytest -q tests/test_visual.py
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foresight  # noqa: E402


# ---------------------------------------------------------------------------
# a minimal PNG encoder so the tests use real image files
# ---------------------------------------------------------------------------


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _filter_row(raw: bytes, prev: bytes, bpp: int, ftype: int) -> bytes:
    out = bytearray()
    for i, x in enumerate(raw):
        a = raw[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ftype == 0:
            pred = 0
        elif ftype == 1:
            pred = a
        elif ftype == 2:
            pred = b
        elif ftype == 3:
            pred = (a + b) >> 1
        else:
            pred = _paeth(a, b, c)
        out.append((x - pred) & 0xFF)
    return bytes(out)


def make_png(path: Path, width: int, height: int, pixel, channels: int = 3,
             ftype: int = 0) -> None:
    """Write a PNG. `pixel(x, y)` returns a tuple of `channels` bytes."""
    color_type = {1: 0, 3: 2, 4: 6}[channels]
    rows = []
    prev = bytes(width * channels)
    for y in range(height):
        raw = bytes(v for x in range(width) for v in pixel(x, y))
        rows.append(bytes([ftype]) + _filter_row(raw, prev, channels, ftype))
        prev = raw
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(b"".join(rows))) + _chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def solid(r, g, b):
    return lambda x, y: (r, g, b)


def run_dir(project: Path, ts: str, platform: str = "web") -> Path:
    return project / ".tdd" / "foresight" / "exploration" / ts / platform


def read_visual(project: Path) -> dict:
    return json.loads((project / ".tdd" / "foresight" / "visual" / "visual.json").read_text())


# ---------------------------------------------------------------------------
# PNG decoding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ftype", [0, 1, 2, 3, 4])
def test_png_decoder_handles_every_filter_type(tmp_path, ftype):
    def px(x, y):
        return ((x * 7) & 0xFF, (y * 13) & 0xFF, (x * y) & 0xFF)
    p = tmp_path / f"f{ftype}.png"
    make_png(p, 9, 6, px, ftype=ftype)
    decoded = foresight._png_decode(p)
    assert decoded is not None
    w, h, ch, pixels = decoded
    assert (w, h, ch) == (9, 6, 3)
    assert pixels == bytes(v for y in range(6) for x in range(9) for v in px(x, y))


def test_png_decoder_handles_rgba_and_gray(tmp_path):
    make_png(tmp_path / "rgba.png", 4, 3, lambda x, y: (x, y, 1, 200), channels=4)
    make_png(tmp_path / "gray.png", 4, 3, lambda x, y: (x + y,), channels=1)
    assert foresight._png_decode(tmp_path / "rgba.png")[:3] == (4, 3, 4)
    assert foresight._png_decode(tmp_path / "gray.png")[:3] == (4, 3, 1)


def test_png_decoder_returns_none_for_garbage(tmp_path):
    (tmp_path / "bad.png").write_bytes(b"\x89PNG not really")
    assert foresight._png_decode(tmp_path / "bad.png") is None


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


def test_visual_baseline_records_hash_and_dimensions(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-login.png", 10, 8, solid(1, 2, 3))
    make_png(run_dir(tmp_path, "20260101-000000") / "0002-home.png", 20, 5, solid(9, 9, 9))

    assert foresight.main(["visual", "--project", str(tmp_path), "--baseline"]) == 0

    base = json.loads((tmp_path / ".tdd" / "foresight" / "visual" / "baseline.json").read_text())
    assert base["run"] == "20260101-000000"
    assert set(base["images"]) == {"web/login.png", "web/home.png"}
    assert base["images"]["web/login.png"]["width"] == 10
    assert base["images"]["web/login.png"]["height"] == 8
    assert len(base["images"]["web/login.png"]["sha256"]) == 64


def test_visual_baseline_uses_latest_run_by_default(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-a.png", 2, 2, solid(0, 0, 0))
    make_png(run_dir(tmp_path, "20260202-000000") / "0001-a.png", 2, 2, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    base = json.loads((tmp_path / ".tdd" / "foresight" / "visual" / "baseline.json").read_text())
    assert base["run"] == "20260202-000000"


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def test_visual_compare_reports_unchanged_for_identical_images(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-login.png", 10, 8, solid(1, 2, 3))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    make_png(run_dir(tmp_path, "20260102-000000") / "0001-login.png", 10, 8, solid(1, 2, 3))

    rc = foresight.main(["visual", "--project", str(tmp_path), "--fail-on-change"])

    assert rc == 0
    v = read_visual(tmp_path)
    assert v["baseline_run"] == "20260101-000000"
    assert v["run"] == "20260102-000000"
    assert v["summary"]["unchanged"] == 1
    assert v["images"][0]["status"] == "unchanged"


def test_visual_compare_measures_pixel_change(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-login.png", 10, 10, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    # repaint the top row (10 of 100 pixels)
    make_png(run_dir(tmp_path, "20260102-000000") / "0001-login.png", 10, 10,
             lambda x, y: (255, 0, 0) if y == 0 else (0, 0, 0))

    rc = foresight.main(["visual", "--project", str(tmp_path), "--fail-on-change"])

    assert rc == 1
    [img] = read_visual(tmp_path)["images"]
    assert img["status"] == "changed"
    assert img["diff_pct"] == 10.0


def test_visual_threshold_tolerates_small_changes(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-login.png", 10, 10, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    make_png(run_dir(tmp_path, "20260102-000000") / "0001-login.png", 10, 10,
             lambda x, y: (255, 0, 0) if (x, y) == (0, 0) else (0, 0, 0))

    rc = foresight.main(["visual", "--project", str(tmp_path),
                         "--fail-on-change", "--threshold", "5"])

    assert rc == 0
    [img] = read_visual(tmp_path)["images"]
    assert img["status"] == "unchanged"
    assert img["diff_pct"] == 1.0


def test_visual_detects_resized_new_and_missing(tmp_path):
    base = run_dir(tmp_path, "20260101-000000")
    make_png(base / "0001-login.png", 10, 10, solid(0, 0, 0))
    make_png(base / "0002-home.png", 10, 10, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    new = run_dir(tmp_path, "20260102-000000")
    make_png(new / "0001-login.png", 12, 10, solid(0, 0, 0))   # resized
    make_png(new / "0003-settings.png", 10, 10, solid(0, 0, 0))  # new; home is missing

    rc = foresight.main(["visual", "--project", str(tmp_path), "--fail-on-change"])

    assert rc == 1
    by_key = {i["key"]: i["status"] for i in read_visual(tmp_path)["images"]}
    assert by_key == {"web/login.png": "resized", "web/home.png": "missing",
                      "web/settings.png": "new"}
    s = read_visual(tmp_path)["summary"]
    assert (s["resized"], s["missing"], s["new"]) == (1, 1, 1)


def test_visual_keys_ignore_step_number_prefix(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-login.png", 4, 4, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    make_png(run_dir(tmp_path, "20260102-000000") / "0007-login.png", 4, 4, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path)])
    [img] = read_visual(tmp_path)["images"]
    assert img["key"] == "web/login.png"
    assert img["status"] == "unchanged"


def test_visual_compares_platforms_independently(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000", "web") / "0001-login.png", 4, 4, solid(0, 0, 0))
    make_png(run_dir(tmp_path, "20260101-000000", "ios") / "0001-login.png", 4, 4, solid(9, 9, 9))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    make_png(run_dir(tmp_path, "20260102-000000", "web") / "0001-login.png", 4, 4, solid(0, 0, 0))
    make_png(run_dir(tmp_path, "20260102-000000", "ios") / "0001-login.png", 4, 4, solid(9, 9, 9))
    foresight.main(["visual", "--project", str(tmp_path)])
    assert {i["key"] for i in read_visual(tmp_path)["images"]} == {"web/login.png", "ios/login.png"}
    assert read_visual(tmp_path)["summary"]["unchanged"] == 2


def test_visual_undecodable_png_falls_back_to_hash(tmp_path):
    base = run_dir(tmp_path, "20260101-000000")
    base.mkdir(parents=True)
    (base / "0001-odd.png").write_bytes(b"\x89PNG garbage A")
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    new = run_dir(tmp_path, "20260102-000000")
    new.mkdir(parents=True)
    (new / "0001-odd.png").write_bytes(b"\x89PNG garbage B")
    rc = foresight.main(["visual", "--project", str(tmp_path), "--fail-on-change"])
    assert rc == 1
    [img] = read_visual(tmp_path)["images"]
    assert img["status"] == "changed"
    assert img["diff_pct"] is None


def test_visual_explicit_run_selection(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-a.png", 4, 4, solid(0, 0, 0))
    make_png(run_dir(tmp_path, "20260102-000000") / "0001-a.png", 4, 4, solid(1, 1, 1))
    make_png(run_dir(tmp_path, "20260103-000000") / "0001-a.png", 4, 4, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline", "--run", "20260101-000000"])
    foresight.main(["visual", "--project", str(tmp_path), "--run", "20260102-000000"])
    assert read_visual(tmp_path)["images"][0]["status"] == "changed"
    foresight.main(["visual", "--project", str(tmp_path), "--run", "20260103-000000"])
    assert read_visual(tmp_path)["images"][0]["status"] == "unchanged"


# ---------------------------------------------------------------------------
# graceful degradation
# ---------------------------------------------------------------------------


def test_visual_without_baseline_is_graceful(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-a.png", 4, 4, solid(0, 0, 0))
    rc = foresight.main(["visual", "--project", str(tmp_path), "--fail-on-change"])
    assert rc == 0
    v = read_visual(tmp_path)
    assert v["baseline_present"] is False
    assert v["images"] == []


def test_visual_without_exploration_runs_is_graceful(tmp_path):
    rc = foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    assert rc == 0
    rc = foresight.main(["visual", "--project", str(tmp_path), "--fail-on-change"])
    assert rc == 0


def test_visual_markdown_lists_changed_images_first(tmp_path):
    base = run_dir(tmp_path, "20260101-000000")
    make_png(base / "0001-login.png", 10, 10, solid(0, 0, 0))
    make_png(base / "0002-home.png", 10, 10, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    new = run_dir(tmp_path, "20260102-000000")
    make_png(new / "0001-login.png", 10, 10, solid(0, 0, 0))
    make_png(new / "0002-home.png", 10, 10, solid(255, 255, 255))
    foresight.main(["visual", "--project", str(tmp_path)])
    md = (tmp_path / ".tdd" / "foresight" / "visual" / "visual.md").read_text()
    assert md.index("web/home.png") < md.index("web/login.png")
    assert "100.0%" in md


def test_report_includes_visual_section(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-login.png", 10, 10, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    make_png(run_dir(tmp_path, "20260102-000000") / "0001-login.png", 10, 10, solid(1, 1, 1))
    foresight.main(["visual", "--project", str(tmp_path)])
    foresight.main(["report", "--project", str(tmp_path)])
    report = (tmp_path / ".tdd" / "foresight" / "report.md").read_text()
    assert "## Visual" in report
    assert "1 changed" in report


def test_visual_single_pixel_change_on_large_image_is_still_changed(tmp_path):
    make_png(run_dir(tmp_path, "20260101-000000") / "0001-big.png", 200, 100, solid(0, 0, 0))
    foresight.main(["visual", "--project", str(tmp_path), "--baseline"])
    make_png(run_dir(tmp_path, "20260102-000000") / "0001-big.png", 200, 100,
             lambda x, y: (255, 255, 255) if (x, y) == (7, 3) else (0, 0, 0))
    rc = foresight.main(["visual", "--project", str(tmp_path), "--fail-on-change"])
    assert rc == 1
    [img] = read_visual(tmp_path)["images"]
    assert img["status"] == "changed"
    assert 0 < img["diff_pct"] < 0.01
