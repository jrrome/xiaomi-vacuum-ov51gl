#!/usr/bin/env python3
"""CLI de control LOCAL para la Xiaomi Robot Vacuum H40 (miIO / MIoT).

Lee el token de .secrets/devices.json (lo genera tools/xiaomi_login.py).

  .venv/bin/python tools/vacuum.py info
  .venv/bin/python tools/vacuum.py status
  .venv/bin/python tools/vacuum.py props            # catalogo de propiedades
  .venv/bin/python tools/vacuum.py acts             # catalogo de acciones
  .venv/bin/python tools/vacuum.py get status mode battery-level
  .venv/bin/python tools/vacuum.py set mode 3
  .venv/bin/python tools/vacuum.py act start-sweep

Nota: en el H40 la capa MIoT local no contesta si el robot esta dormido en la base
(`user ack timeout`). Para eso esta tools/cloud.py, que habla con la nube.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from miot_spec import human, load_spec  # noqa: E402
from miio import DeviceException, MiotDevice  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
SECRETS = BASE / ".secrets" / "devices.json"

STATUS_PROPS = [
    "battery-level",
    "charging-state",
    "voltage",
    "status",
    "fault",
    "sweep-mop-type",
    "sweep-type",
    "mode",
    "mop-water-output-level",
    "cleaning-area",
    "cleaning-time",
    "cleaning-progress",
    "last-clean-time",
    "mop-status",
    "drying-progress",
    "dry-left-time",
    "sewage-tank-status",
    "water-tank-status",
    "statistical-clean-area",
    "statistical-cleaning-times",
    "statistical-cleaning-duration",
]


def find_device() -> dict:
    if not SECRETS.exists():
        sys.exit("Falta .secrets/devices.json — ejecuta tools/xiaomi_login.py primero.")
    data = json.loads(SECRETS.read_text())
    for devices in data.values():
        for dev in devices:
            if str(dev.get("model", "")).startswith("xiaomi.vacuum"):
                return dev
    sys.exit("No hay ninguna aspiradora en .secrets/devices.json.")


def make_device(props: dict, acts: dict) -> MiotDevice:
    dev = find_device()
    mapping = {name: {"siid": siid, "piid": piid} for name, (siid, piid, _) in props.items()}
    mapping |= {f"action:{n}": {"siid": s, "aiid": a} for n, (s, a, _) in acts.items()}
    return MiotDevice(dev["localip"], dev["token"], mapping=mapping, lazy_discover=False)


def fetch(dev: MiotDevice, pairs: list[tuple[int, int]], chunk: int = 10) -> dict:
    """Lee propiedades en lotes.

    El H40 no contesta si se le piden mas de ~12 propiedades de golpe: la respuesta
    no cabe en un datagrama UDP y se pierde (`user ack timeout` / sin respuesta).
    """
    out: dict[tuple[int, int], dict] = {}
    for start in range(0, len(pairs), chunk):
        batch = pairs[start : start + chunk]
        params = [{"did": f"{s}.{p}", "siid": s, "piid": p} for s, p in batch]
        for item in dev.send("get_properties", params):
            out[(item["siid"], item["piid"])] = item
    return out


def cmd_status(dev: MiotDevice, props: dict) -> None:
    pairs = [(props[n][0], props[n][1]) for n in STATUS_PROPS if n in props]
    data = fetch(dev, pairs)
    for name in STATUS_PROPS:
        if name not in props:
            continue
        siid, piid, _ = props[name]
        item = data.get((siid, piid), {})
        if item.get("code", 0) != 0:
            print(f"{name:<32} ERROR {item.get('code')}")
            continue
        print(f"{name:<32} {human(name, item.get('value'))}")


def cmd_props(props: dict, query: str | None) -> None:
    for name, (siid, piid, prop) in sorted(props.items()):
        if query and query not in name:
            continue
        access = ",".join(prop.get("access", []))
        vals = ""
        if prop.get("value-list"):
            vals = "  " + " ".join(
                f"{v['value']}={v['description']}" for v in prop["value-list"]
            )
        print(f"{siid}.{piid:<3} {name:<36} {prop['format']:<8} {access}{vals}")


def cmd_acts(acts: dict, query: str | None) -> None:
    for name, (siid, aiid, act) in sorted(acts.items()):
        if query and query not in name:
            continue
        print(f"{siid}.{aiid:<3} {name:<36} in={act.get('in', [])} out={act.get('out', [])}")


def main(argv: list[str]) -> int:
    props, acts = load_spec()
    if len(argv) < 2:
        print(__doc__)
        return 1

    command, args = argv[1], argv[2:]

    if command == "props":
        cmd_props(props, args[0] if args else None)
        return 0
    if command == "acts":
        cmd_acts(acts, args[0] if args else None)
        return 0

    dev = make_device(props, acts)
    try:
        if command == "info":
            for key, value in vars(dev.info()).items():
                print(f"{key:<16} {value}")
        elif command == "status":
            cmd_status(dev, props)
        elif command == "get":
            pairs = []
            for name in args:
                if name in props:
                    pairs.append((props[name][0], props[name][1]))
                elif "." in name:
                    siid, piid = name.split(".")
                    pairs.append((int(siid), int(piid)))
            data = fetch(dev, pairs)
            for (siid, piid), item in data.items():
                label = next(
                    (n for n, (s, p, _) in props.items() if (s, p) == (siid, piid)),
                    f"{siid}.{piid}",
                )
                print(f"{label:<32} {human(label, item.get('value'))}")
        elif command == "set":
            name, raw = args[0], args[1]
            value = json.loads(raw) if raw[:1] in "[{" else int(raw)
            print(f"{name} <- {value}: {dev.set_property_by(name, value)}")
        elif command == "act":
            name = args[0]
            params = json.loads(args[1]) if len(args) > 1 else []
            print(f"{name}({params}) -> {dev.call_action_by(name, params)}")
        elif command == "raw":
            print(dev.send(args[0]))
        else:
            print(f"comando desconocido: {command}")
            return 1
    except DeviceException as exc:
        print(f"error del dispositivo: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
