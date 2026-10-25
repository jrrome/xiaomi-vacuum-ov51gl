#!/usr/bin/env python3
"""Descarga, descifra y dibuja el mapa de la Xiaomi Robot Vacuum H40.

La familia Shunzao/Dreame (ov51gl) no sirve el mapa por miIO local ni por el
endpoint normal de la nube. El camino que funciona es:

  1. /v2/home/get_interim_file_url_pro  (con sufijo _pro) -> URL firmada de FDS
  2. descargar el objeto -> {"version": 2, "data": "<base64 urlsafe>"}
  3. AES-128-CBC con clave derivada de modelo + did
  4. unpad -> zlib -> JSON con width/height/origin/resolution/map_data

map_data es base64(zlib(width*height bytes)), 1 byte por pixel:
  0 = vacio, 255 = pared, resto = id de habitacion.

  .venv/bin/python tools/map.py            # mapa guardado (is_current)
  .venv/bin/python tools/map.py --live     # mapa en vivo (posicion + recorrido)
  .venv/bin/python tools/map.py --all      # todos los mapas
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402
from cloud import Cloud  # noqa: E402
from Crypto.Cipher import AES  # noqa: E402
from Crypto.Util.Padding import pad, unpad  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "maps"
IV = b"ABCDEF1234123412"

PALETTE = [
    (255, 255, 255),
    (198, 226, 255),
    (255, 224, 178),
    (200, 240, 200),
    (230, 200, 255),
    (255, 200, 200),
    (210, 235, 160),
    (255, 235, 160),
    (180, 220, 235),
    (240, 210, 180),
]
WALL = (70, 70, 80)


def derive_key(model: str, did: str) -> bytes:
    """Clave AES por dispositivo: MD5(AES-CBC(model[-16:] + did))."""
    model_key = model[-16:].encode()
    wrapped = AES.new(model_key, AES.MODE_CBC, IV).encrypt(
        pad(model_key + str(did).encode(), 16)
    )
    return hashlib.md5(wrapped).digest()


def download(cloud: Cloud, obj_name: str) -> dict:
    response = cloud.call("/v2/home/get_interim_file_url_pro", {"obj_name": obj_name})
    url = response["result"]["url"]
    raw = requests.get(url, timeout=30).content
    payload = json.loads(raw)
    if payload.get("version") != 2:
        raise SystemExit(f"version inesperada: {payload.get('version')}")

    blob = payload["data"].replace("_", "/").replace("-", "+")
    blob += "=" * (-len(blob) % 4)
    key = derive_key(cloud.device["model"], cloud.device["did"])
    plain = unpad(AES.new(key, AES.MODE_CBC, IV).decrypt(base64.b64decode(blob)), 16)
    return json.loads(zlib.decompress(plain))


def map_pixels(obj: dict) -> bytes:
    raw = base64.b64decode(obj["map_data"])
    return zlib.decompress(raw)


def to_pixel(obj: dict, x_mm: float, y_mm: float) -> tuple[int, int]:
    res = obj["resolution"]
    return (
        int((x_mm - obj["origin_x"]) / res),
        int((y_mm - obj["origin_y"]) / res),
    )


def _font(size: int):
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def render(obj: dict, path: Path, scale: int = 2) -> None:
    width, height = obj["width"], obj["height"]
    pixels = map_pixels(obj)
    if len(pixels) != width * height:
        raise SystemExit(f"map_data no cuadra: {len(pixels)} != {width*height}")

    img = Image.new("RGB", (width, height), PALETTE[0])
    draw = ImageDraw.Draw(img)

    for row in range(height):
        base = row * width
        for col in range(width):
            value = pixels[base + col]
            if value == 0:
                continue
            draw.point(
                (col, row),
                fill=WALL if value == 255 else PALETTE[value % len(PALETTE)],
            )

    for region in obj.get("fb_regions") or []:
        pts = region.get("fb_point") or []
        coords = [to_pixel(obj, pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
        if len(coords) >= 4:
            draw.polygon(coords, outline=(220, 60, 60))
    for wall in obj.get("fb_walls") or []:
        pts = wall.get("wall_points") or []
        if len(pts) >= 4:
            draw.line(
                [to_pixel(obj, pts[0], pts[1]), to_pixel(obj, pts[2], pts[3])],
                fill=(220, 60, 60),
                width=1,
            )
    for carpet in obj.get("carpets") or []:
        pts = carpet.get("p") or []
        coords = [to_pixel(obj, pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
        if len(coords) >= 4:
            draw.polygon(coords, outline=(150, 110, 60))

    points = (obj.get("paths") or {}).get("points") or []
    if len(points) > 1:
        draw.line(
            [to_pixel(obj, p["x"], p["y"]) for p in points],
            fill=(0, 140, 255),
            width=1,
        )

    if obj.get("have_pile"):
        col, row = to_pixel(obj, obj["pile_x"], obj["pile_y"])
        draw.rectangle((col - 4, row - 4, col + 4, row + 4), outline=(200, 0, 0), width=2)

    img = img.resize((width * scale, height * scale), Image.NEAREST)

    # etiquetas encima, ya en la imagen ampliada
    draw = ImageDraw.Draw(img)
    font = _font(13)
    for room in obj.get("room_attrs") or []:
        name = room.get("room_name")
        if not name:
            continue
        col, row = to_pixel(obj, room["name_pos_x"], room["name_pos_y"])
        col, row = col * scale, row * scale
        draw.text((col, row), name, font=font, fill=(10, 10, 10), stroke_width=2, stroke_fill=(255, 255, 255))

    if isinstance(obj.get("position"), dict):
        col, row = to_pixel(obj, obj["position"]["x"], obj["position"]["y"])
        col, row = col * scale, row * scale
        draw.ellipse((col - 6, row - 6, col + 6, row + 6), fill=(0, 120, 0), outline=(255, 255, 255))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def main(argv: list[str]) -> int:
    cloud = Cloud()

    slugs: list[tuple[str, str]] = []
    if "--live" in argv:
        live = next(i for i in cloud.get([(10, 1)]) if i.get("code") == 0)
        obj_name = json.loads(live["value"])["obj_name"]
        slugs.append(("live", obj_name))
    else:
        management = json.loads(
            next(
                item["value"]
                for item in cloud.get([(10, 5)])
                if item.get("code") == 0
            )
        )
        for entry in management["map_array"]:
            if "--all" in argv or entry.get("is_current"):
                slugs.append((f"map{entry['map_id']}", entry["obj_name"]))

    for label, obj_name in slugs:
        print(f"descargando {obj_name}...")
        obj = download(cloud, obj_name)
        png = OUT / f"{label}.png"
        render(obj, png)
        meta = {k: v for k, v in obj.items() if k not in {"map_data", "paths"}}
        (OUT / f"{label}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        rooms = [r["room_name"] for r in obj.get("room_attrs", []) if r.get("room_name")]
        print(f"  {obj['width']}x{obj['height']} px, res {obj['resolution']} mm -> {png}")
        print(f"  habitaciones: {rooms}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
