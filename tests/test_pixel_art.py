"""像素画工具：栅格语义、持久化、导出与自检。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pet.tools.pixel_art import canvas as art_canvas
from pet.tools.pixel_art import core, export, storage
from pet.tools.pixel_art.canvas import PixelArtError


@pytest.fixture
def art(tmp_path, monkeypatch):
    """每个用例独占临时库与导出目录。"""
    db = tmp_path / "pixel_art.db"
    core.reset_store(str(db))
    monkeypatch.setattr(export, "desktop_dir", lambda: str(tmp_path))
    monkeypatch.setattr(export, "check_write_path", lambda path: str(path))
    yield tmp_path
    core.reset_store()


def _blank_rows(size: int = 16) -> list[str]:
    return ["." * size for _ in range(size)]


def test_create_defaults(art):
    result = core.create("苹果", "16")
    assert result["data"]["name"] == "苹果"
    assert result["data"]["revision"] == 0
    assert result["data"]["palette_size"] == 16
    assert "透明" in result["headline"]


def test_ops_field_error_carries_field_list(art):
    core.create("图", "16")
    with pytest.raises(PixelArtError) as exc:
        core.paint(ops=[{"op": "ellipse", "cx": 8, "cy": 8, "rx": 4, "ry": 4, "c": 1}])
    assert exc.value.code == "bad_param"
    assert "x, y, w, h" in exc.value.hint


def test_db32_preset_supports_32_colors(art):
    created = core.create("多彩", "16", preset="db32")
    assert created["data"]["palette_size"] == 32

    rows = ["v" * 16] + ["." * 16 for _ in range(15)]
    data = core.set_pixels(rows=rows)["data"]
    assert data["changed_cells"] == 16
    assert core.preview()["body"].splitlines()[0].startswith("00 vv")

    with pytest.raises(PixelArtError) as exc:
        core.set_pixels(rows=["w" * 16], region=[0, 0, 16, 1])
    assert exc.value.code == "bad_rows"


def test_custom_palette_allows_32_colors(art):
    colors = [f"#{i:02X}{i:02X}{i:02X}" for i in range(32)]
    created = core.create("自定义", "16", palette=colors)
    assert created["data"]["palette_size"] == 32

    with pytest.raises(PixelArtError) as exc:
        core.create("超长", "16", palette=colors + ["#FFFFFF"])
    assert exc.value.code == "palette_full"


def test_name_validation(art):
    with pytest.raises(PixelArtError) as exc:
        core.create("   ", "16")
    assert exc.value.code == "bad_param"

    with pytest.raises(PixelArtError):
        core.create("x" * 25, "16")

    first = core.create("苹果", "16")
    second = core.create("苹果", "16")
    assert first["data"]["canvas_id"] != second["data"]["canvas_id"]
    assert core.open_canvas(second["data"]["canvas_id"])["data"]["name"] == "苹果"


def test_persist_roundtrip(art):
    canvas_id = core.create("云", "16", background="a")["data"]["canvas_id"]
    core.paint(ops=[{"op": "point", "x": 1, "y": 1, "c": 3}])
    core.reset_store(str(art / "pixel_art.db"))

    opened = core.open_canvas(canvas_id)
    assert opened["data"]["name"] == "云"
    assert opened["data"]["revision"] == 1
    assert opened["data"]["filled_cells"] == 256


def test_latest_after_restart(art):
    core.create("第一张", "16")
    core.create("第二张", "32")
    core.reset_store(str(art / "pixel_art.db"))

    page = core.list_canvases(page=1, page_size=5)
    assert "共 2 张" in page["headline"]
    assert "第二张" in page["body"]
    assert page["pagination"]["has_next"] is False


def test_list_pagination(art):
    for i in range(12):
        core.create(f"画布{i}", "16")

    first = core.list_canvases(page=1, page_size=5)
    second = core.list_canvases(page=2, page_size=5)
    third = core.list_canvases(page=3, page_size=5)
    assert first["pagination"]["has_next"] is True
    assert len(second["body"].splitlines()) == 5
    assert third["pagination"]["has_next"] is False
    assert len(third["body"].splitlines()) == 2


def test_rename_allows_duplicates(art):
    canvas_id = core.create("旧名", "16")["data"]["canvas_id"]
    core.paint(ops=[{"op": "point", "x": 0, "y": 0, "c": 1}])
    renamed = core.rename(name="新名")
    assert "新名" in renamed["headline"]

    second_id = core.create("占位", "16")["data"]["canvas_id"]
    core.rename(canvas_id=second_id, name="新名")
    assert core.list_canvases()["body"].count("新名") == 2
    assert core.open_canvas(canvas_id)["data"]["revision"] == 1
    assert core.open_canvas(second_id)["data"]["revision"] == 0


def test_open_requires_canvas_id(art):
    core.create("画", "16")
    with pytest.raises(PixelArtError) as exc:
        core.open_canvas()
    assert exc.value.code == "bad_param"


def test_delete(art):
    canvas_id = core.create("临时", "16")["data"]["canvas_id"]
    result = core.delete()
    assert result["data"]["deleted"] is True
    assert "共 0 张" in core.list_canvases()["headline"]

    with pytest.raises(PixelArtError) as exc:
        core.open_canvas(canvas_id)
    assert exc.value.code == "no_canvas"


def test_set_whole_image(art):
    core.create("图", "16")
    rows = _blank_rows()
    rows[0] = "0123456789abcdef"
    data = core.set_pixels(rows=rows)["data"]
    assert data["changed_cells"] == 16
    assert data["bbox"] == [0, 0, 15, 0]


def test_set_local_keeps_question_mark(art):
    core.create("图", "16", background="7")
    core.set_pixels(rows=["11", "11"], region=[0, 0, 2, 2])
    data = core.set_pixels(rows=["2?", "?2"], region=[0, 0, 2, 2])["data"]
    assert data["changed_cells"] == 2

    matrix = core.preview()["body"].splitlines()
    assert matrix[0].startswith("00 21")
    assert matrix[1].startswith("01 12")


def test_bad_rows_reports_position(art):
    core.create("图", "16")
    rows = _blank_rows()
    rows[4] = "." * 15
    with pytest.raises(PixelArtError) as exc:
        core.set_pixels(rows=rows)
    assert exc.value.code == "bad_rows"
    # 报错带出错行的实际长度与画布宽度，位置由调用方按行号定位
    assert "15" in exc.value.message and "16" in exc.value.message
    assert core.open_canvas(core._store().current_id)["data"]["revision"] == 0


def test_set_region_required_for_32(art):
    core.create("图", "32")
    with pytest.raises(PixelArtError) as exc:
        core.set_pixels(rows=["." * 32 for _ in range(32)])
    assert exc.value.code == "region_too_large"


def test_rle_roundtrip(art):
    core.create("图", "16")
    index = np.zeros((16, 16), dtype=np.uint8)
    index[0, :] = 10
    index[:, 0] = 12
    text = art_canvas.encode_rle(index)
    assert "15:a" in text
    assert "1:c" in text
    decoded = art_canvas.decode_rle(text, 16)
    assert np.array_equal(decoded, index)

    core.set_pixels(rle=text)
    again = core.export(format="rle")["data"]["path"]
    with open(again, encoding="utf-8") as fh:
        assert fh.read() == text


def test_rle_rejects_missing_count(art):
    with pytest.raises(PixelArtError) as exc:
        art_canvas.decode_rle("a12:.", 16)
    assert exc.value.code == "bad_rle"


def test_region_bounds_and_clipping(art):
    core.create("图", "16")
    with pytest.raises(PixelArtError) as exc:
        core.paint(ops=[{"op": "gradient", "region": [8, 8, 16, 16], "from": 1, "to": 2}])
    assert exc.value.code == "bad_region"

    data = core.paint(ops=[{"op": "rect", "x": -4, "y": 0, "w": 8, "h": 2, "c": 3}])["data"]
    assert data["changed_cells"] == 8
    assert data["bbox"] == [0, 0, 3, 1]


def test_idempotent_retry(art):
    core.create("图", "16")
    op = [{"op": "rect", "x": 0, "y": 0, "w": 4, "h": 4, "c": 5}]
    first = core.paint(ops=op)["data"]
    second = core.paint(ops=op)["data"]
    assert first["changed_cells"] == 16
    assert second["changed_cells"] == 0
    assert second["revision"] == first["revision"]
    assert second["bbox"] is None


def test_stale_revision(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "point", "x": 0, "y": 0, "c": 1}])
    with pytest.raises(PixelArtError) as exc:
        core.paint(ops=[{"op": "point", "x": 1, "y": 1, "c": 1}], revision=0)
    assert exc.value.code == "stale_revision"
    assert "1" in exc.value.message


def test_color_outside_palette(art):
    core.create("图", "16")
    with pytest.raises(PixelArtError) as exc:
        core.paint(ops=[{"op": "point", "x": 0, "y": 0, "c": "#FF00FF"}])
    assert exc.value.code == "bad_color"
    assert "14" in exc.value.message


def test_mirror_x(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "rect", "x": 0, "y": 0, "w": 3, "h": 2, "c": 4}],
               mirror_x=True)
    index = core._store().cache["1"].index
    assert np.array_equal(index[0:2, 0:3], index[0:2, 13:16])


def test_procedural_is_deterministic(art):
    core.create("图", "32")
    op = [{"op": "noise", "region": [0, 0, 32, 32], "c": 8, "ratio": 0.4}]
    core.paint(ops=op)
    first = core._store().cache["1"].index.copy()
    core.paint(ops=[{"op": "clear"}])
    core.paint(ops=op)
    second = core._store().cache["1"].index
    assert np.array_equal(first, second)


def test_persist_failure_keeps_memory(art, monkeypatch):
    core.create("图", "16")
    store = core._store()

    def boom(canvas, index, revision):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(store.storage, "save_pixels", boom)
    with pytest.raises(PixelArtError) as exc:
        core.paint(ops=[{"op": "point", "x": 0, "y": 0, "c": 1}])
    assert exc.value.code == "persist_failed"
    assert store.cache["1"].revision == 0
    assert store.cache["1"].filled_cells == 0


def test_export_png_is_nearest(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "point", "x": 0, "y": 0, "c": 8}])
    result = core.export(format="png")["data"]
    assert (result["width"], result["height"]) == (256, 256)

    with Image.open(result["path"]) as image:
        assert image.size == (256, 256)
        assert image.getpixel((8, 8))[:3] == (0xFF, 0x00, 0x4D)
        assert image.getpixel((9, 9))[:3] == (0xFF, 0x00, 0x4D)
        assert image.getpixel((20, 20))[3] == 0


def test_export_json_roundtrip(art):
    core.create("图", "16")
    rows = _blank_rows()
    rows[0] = "0123456789abcdef"
    core.set_pixels(rows=rows)
    path = core.export(format="json")["data"]["path"]
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["size"] == 16
    assert payload["rows"][0] == "0123456789abcdef"
    assert payload["palette"][0] == "#000000"


def test_export_json_single_char_for_32_colors(art):
    core.create("多彩", "16", preset="db32")
    rows = ["v" * 16] + ["." * 16 for _ in range(15)]
    core.set_pixels(rows=rows)
    path = core.export(format="json")["data"]["path"]
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["rows"][0] == "v" * 16
    assert len(payload["palette"]) == 32


def test_export_svg_crisp(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "rect", "x": 0, "y": 0, "w": 4, "h": 4, "c": 2, "fill": True}])
    path = core.export(format="svg")["data"]["path"]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    assert 'shape-rendering="crispEdges"' in text
    assert 'width="4"' in text


def test_export_rejects_outside_roots(art, monkeypatch):
    core.create("图", "16")
    monkeypatch.undo()
    from pet.file_intake import paths
    allowed = art / "allowed"
    allowed.mkdir()
    monkeypatch.setattr(paths, "_ROOTS", [str(allowed)])
    monkeypatch.setattr(export, "check_write_path", paths.check_write_path)
    monkeypatch.setattr(export, "desktop_dir", lambda: str(art))
    with pytest.raises(PixelArtError) as exc:
        core.export(format="png")
    assert exc.value.code == "export_failed"


def test_preview_matrix_shape(art):
    core.create("图", "64")
    matrix = core.preview()["body"].splitlines()
    assert len(matrix) == 16
    assert all(len(line) == 19 for line in matrix)


def test_postprocess_invert_on_gameboy(art):
    core.create("图", "16", preset="gameboy")
    core.paint(ops=[{"op": "point", "x": 0, "y": 0, "c": 0}])
    core.postprocess(op="invert")
    index = core._store().cache["1"].index
    assert index[0, 0] == 3
    visible = index[index != art_canvas.TRANSPARENT]
    assert visible.max() <= 3


def test_postprocess_outline_and_cleanup(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "point", "x": 5, "y": 5, "c": 2}])
    core.postprocess(op="outline", params={"c": 0})
    index = core._store().cache["1"].index
    assert index[5, 4] == 0
    assert index[4, 5] == 0

    core.postprocess(op="cleanup")
    assert core._store().cache["1"].index[5, 5] == 2


def test_palette_set_and_quantize(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "rect", "x": 0, "y": 0, "w": 2, "h": 2, "c": 1}])
    core.palette(action="set", colors=["#000000", "#FF0000"])
    assert core.palette(action="get")["body"].startswith("0:#000000 1:#FF0000")

    data = core.palette(action="quantize", colors=["#000000", "#FFFFFF"])["data"]
    assert data["palette_size"] == 2
    assert data["remapped_cells"] == 4


def test_storage_marks_revision(art):
    core.create("图", "16")
    store = storage.PixelArtStorage(str(art / "pixel_art.db"))
    row = store.get("1")
    assert row is not None
    assert row.revision == 0
    store.close()


def test_index_beyond_palette_rejected(art):
    core.create("小色板", "16", preset="gameboy")
    rows = _blank_rows()
    rows[0] = "f" + "." * 15
    with pytest.raises(PixelArtError) as exc:
        core.set_pixels(rows=rows)
    assert exc.value.code == "bad_rows"
    assert "0-3" in exc.value.message or "0-3" in exc.value.hint

    with pytest.raises(PixelArtError) as rle_exc:
        core.set_pixels(rle="255:f1:.")
    assert rle_exc.value.code == "bad_rle"


def test_32_accepts_whole_image_rle(art):
    core.create("大图", "32")
    assert core.set_pixels(rle="1024:.")["data"]["changed_cells"] == 0
    core.paint(ops=[{"op": "rect", "x": 0, "y": 0, "w": 4, "h": 4, "c": 1}])
    assert core.set_pixels(rle="1024:.")["data"]["revision"] == 2

    with pytest.raises(PixelArtError) as exc:
        core.set_pixels(rows=["." * 32 for _ in range(32)])
    assert exc.value.code == "region_too_large"


def test_scale_bounds(art):
    core.create("图", "16")
    for bad in (0, -3, 65):
        with pytest.raises(PixelArtError) as exc:
            core.export(format="png", scale=bad)
        assert exc.value.code == "bad_param"


def test_mirror_polygon_alignment(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "polygon", "points": [[0, 0], [2, 0], [2, 2]], "c": 3}],
               mirror_x=True)
    index = core._store().cache["1"].index
    assert index[0, 15] == 3
    assert index[0, 13] == 3


def test_procedural_requires_region(art):
    core.create("图", "16")
    with pytest.raises(PixelArtError) as exc:
        core.paint(ops=[{"op": "noise", "c": 5}])
    assert exc.value.code == "bad_param"
    assert "region" in exc.value.message


def test_bad_params_use_stable_codes(art):
    core.create("图", "16")
    with pytest.raises(PixelArtError) as ratio:
        core.paint(ops=[{"op": "noise", "region": [0, 0, 4, 4], "c": 1, "ratio": "多"}])
    assert ratio.value.code == "bad_param"

    with pytest.raises(PixelArtError) as count:
        core.paint(ops=[{"op": "scatter", "region": [0, 0, 4, 4], "c": 1, "count": "多"}])
    assert count.value.code == "bad_param"

    with pytest.raises(PixelArtError) as steps:
        core.postprocess(op="shade", params={"steps": 5})
    assert steps.value.code == "bad_param"

    with pytest.raises(PixelArtError) as page:
        core.list_canvases(page="第一页")
    assert page.value.code == "bad_param"


def test_postprocess_region_scope(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "point", "x": 2, "y": 2, "c": 4},
                    {"op": "point", "x": 12, "y": 12, "c": 4}])
    core.postprocess(op="outline", region=[0, 0, 6, 6])
    index = core._store().cache["1"].index
    assert index[2, 1] == 0
    assert index[12, 12] == 4
    assert index[12, 11] == art_canvas.TRANSPARENT

    with pytest.raises(PixelArtError) as exc:
        core.postprocess(op="rotate90", region=[0, 0, 4, 4])
    assert exc.value.code == "bad_param"


def test_refresh_view_without_canvas_pushes_empty(art, monkeypatch):
    pushed = []
    monkeypatch.setattr(core, "_emit", pushed.append)
    core.refresh_view()
    assert pushed == [{"empty": True, "force_show": True}]


def test_refresh_view_pushes_current_canvas(art, monkeypatch):
    pushed = []
    created = core.create("苹果", "16")
    monkeypatch.setattr(core, "_emit", pushed.append)
    core.refresh_view()
    assert pushed and pushed[0]["name"] == created["data"]["name"]
    assert pushed[0]["revision"] == created["data"]["revision"]
    assert pushed[0]["force_show"] is True


def test_parse_hex_rejects_signed_digits():
    with pytest.raises(PixelArtError) as exc:
        art_canvas.parse_hex("#-10000")
    assert exc.value.code == "bad_color"
    assert art_canvas.parse_hex("#000000") == (0, 0, 0)
    assert art_canvas.parse_hex("#FFFFFF") == (255, 255, 255)


def test_parse_region_rejects_fractional_and_bool():
    for region in ([0.9, 0, 1, 1], [True, 0, 1, 1], [0, 0, 1.5, 1]):
        with pytest.raises(PixelArtError) as exc:
            art_canvas.parse_region(region, 16)
        assert exc.value.code == "bad_param"
    assert art_canvas.parse_region([0, 0, 2, 3], 16) == (0, 0, 2, 3)


def test_preview_lines_follow_aggregate_step(art):
    core.create("图", "32")
    core.paint(ops=[{"op": "rect", "x": 0, "y": 0, "w": 32, "h": 32, "c": 4}])
    lines = core.preview()["body"].splitlines()
    assert [int(line[:2]) for line in lines] == list(range(0, 32, 2))


def test_mirror_interleaves_each_op(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "point", "x": 1, "y": 0, "c": 1},
                    {"op": "point", "x": 14, "y": 0, "c": 2}], mirror_x=True)
    index = core._store().cache["1"].index
    assert index[0, 1] == 2
    assert index[0, 14] == 2


def test_mirror_pattern_is_symmetric(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "pattern", "region": [0, 0, 4, 16],
                     "tile": "checker", "c": 5}], mirror_x=True)
    index = core._store().cache["1"].index
    assert np.array_equal(index[:, :8], np.fliplr(index[:, 8:]))


def test_mirror_mask_keeps_both_sides(art):
    core.create("图", "16")
    core.paint(ops=[{"op": "rect", "x": 0, "y": 0, "w": 16, "h": 16, "c": 6}])
    core.paint(ops=[{"op": "mask", "region": [0, 0, 8, 16], "shape": "rect"}],
               mirror_x=True)
    index = core._store().cache["1"].index
    assert index[0, 0] == 6
    assert index[0, 15] == 6


def test_clip_segment_cuts_to_canvas():
    from pet.tools.pixel_art.ops import _clip_segment

    assert _clip_segment(0, 0, 10 ** 12, 0, 16) == (0, 0, 15, 0)
    assert _clip_segment(-8, -8, -1, -1, 16) is None
    assert _clip_segment(3, 3, 9, 9, 16) == (3, 3, 9, 9)


def test_export_tilde_path_goes_to_home(art, monkeypatch):
    core.create("图", "16")
    monkeypatch.setenv("HOME", str(art))
    monkeypatch.setenv("USERPROFILE", str(art))
    path = core.export(format="png", path="~/画.png")["data"]["path"]
    assert Path(path) == art / "画.png"
