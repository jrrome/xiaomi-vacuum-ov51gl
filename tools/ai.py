#!/usr/bin/env python3
"""Gestion de la capa de vision (IA) de la H40 y recogida de fotos.

  .venv/bin/python tools/ai.py status      # solo lectura
  .venv/bin/python tools/ai.py on          # activa deteccion (silencioso)
  .venv/bin/python tools/ai.py off         # lo devuelve a fabrica
  .venv/bin/python tools/ai.py photos      # lee lo que haya detectado

`photos` solo lee. Con --request pide las imagenes al robot (accion 17.1),
que puede tardar y no deberia hacer ruido, pero conviene hacerlo de dia.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from miio import MiotDevice  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
SECRETS = BASE / ".secrets" / "devices.json"

SWITCHES = [
    (2, 45, "sweep-ai-detection"),
    (2, 46, "dirt-detection"),
    (2, 47, "object-detection"),
    (2, 48, "room-detection"),
    (2, 49, "floor-material-detection"),
    (2, 50, "defecation-detection"),
    (2, 51, "liquid-dirt-detection"),
    (2, 93, "real-scene-capture"),
]
# 2.94 auto-night-fill-light se deja aparte: enciende luz en la oscuridad.

INFO = [(2, 56, "sweep-ai-object"), (17, 1, "ai-object-id"), (17, 2, "ai-picture-info"), (10, 9, "map-3d-info")]


def device() -> MiotDevice:
    data = json.loads(SECRETS.read_text())
    dev = next(
        d
        for devices in data.values()
        for d in devices
        if str(d.get("model", "")).startswith("xiaomi.vacuum")
    )
    return MiotDevice(dev["localip"], dev["token"], lazy_discover=False, timeout=8)


def read(m: MiotDevice, entries: list[tuple[int, int, str]]) -> dict:
    out = {}
    for start in range(0, len(entries), 8):
        batch = entries[start : start + 8]
        params = [{"did": f"{s}.{p}", "siid": s, "piid": p} for s, p, _ in batch]
        for item in m.send("get_properties", params):
            name = next(n for s, p, n in entries if (s, p) == (item["siid"], item["piid"]))
            out[name] = item.get("value")
    return out


def write(m: MiotDevice, entries: list[tuple[int, int, bool]]) -> None:
    params = [
        {"did": f"{s}.{p}", "siid": s, "piid": p, "value": v} for s, p, v in entries
    ]
    result = m.send("set_properties", params)
    for item in result:
        if item.get("code"):
            print(f"  {item['siid']}.{item['piid']} -> error {item['code']}")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    m = device()

    if command == "status":
        for name, value in read(m, SWITCHES + INFO).items():
            print(f"{name:<28} {value!r}")
        return 0

    if command in {"on", "off"}:
        value = command == "on"
        write(m, [(s, p, value) for s, p, _ in SWITCHES])
        time.sleep(1)
        state = read(m, SWITCHES)
        for name, current in state.items():
            print(f"{name:<28} {current!r}")
        return 0

    if command == "photos":
        info = read(m, INFO)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        if "--request" in argv:
            object_id = info.get("ai-object-id") or 0
            print(f"peticion de imagen para id={object_id}...")
            result = m.send(
                "action", {"did": "17.1", "siid": 17, "aiid": 1, "in": [object_id]}
            )
            print(json.dumps(result, ensure_ascii=False))
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
