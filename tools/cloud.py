#!/usr/bin/env python3
"""Cliente MIoT a traves de la nube de Xiaomi para la Robot Vacuum H40.

Motivo: en este firmware la capa MIoT local no contesta si el robot esta dormido
en la base (`user ack timeout`), y `miIO.info` si. La nube siempre responde.

Necesita .secrets/session.json + .secrets/devices.json (tools/xiaomi_login.py).

  .venv/bin/python tools/cloud.py status
  .venv/bin/python tools/cloud.py get status battery-level
  .venv/bin/python tools/cloud.py get 2.2 3.1                 # siid.piid crudo
  .venv/bin/python tools/cloud.py set mode 3
  .venv/bin/python tools/cloud.py act start-sweep
  .venv/bin/python tools/cloud.py act start-vacuum-room-sweep '[[1,2]]'
  .venv/bin/python tools/cloud.py raw miotspec/prop/get '{"params":[...]}'
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from miot_spec import human, load_spec  # noqa: E402
from xiaomi_login import XiaomiLogin  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
SECRETS = BASE / ".secrets"

STATUS_PROPS = [
    "battery-level",
    "charging-state",
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
    "sleep-status",
    "location-status",
    "statistical-clean-area",
    "statistical-cleaning-times",
    "statistical-cleaning-duration",
]


class Cloud:
    def __init__(self) -> None:
        session_file = SECRETS / "session.json"
        devices_file = SECRETS / "devices.json"
        if not session_file.exists() or not devices_file.exists():
            sys.exit("Faltan credenciales. Ejecuta tools/xiaomi_login.py.")

        session = json.loads(session_file.read_text())
        self.server = next(iter(json.loads(devices_file.read_text())))
        devices = json.loads(devices_file.read_text())[self.server]
        self.device = next(
            d for d in devices if str(d.get("model", "")).startswith("xiaomi.vacuum")
        )

        self.api = XiaomiLogin()
        self.api.user_id = session["user_id"]
        self.api.cuser_id = session["cuser_id"]
        self.api.ssecurity = session["ssecurity"]
        self.api.service_token = session["service_token"]

    # ------------------------------------------------------------------ calls
    def call(self, endpoint: str, payload: dict) -> dict | None:
        url = self.api.api_url(self.server) + endpoint
        age = int(time.time()) - json.loads((SECRETS / "session.json").read_text())["saved_at"]
        if age > 7 * 86400:
            print(f"  aviso: la sesion tiene {age // 86400} dias; si falla, repite el login")
        result = self.api.execute(url, {"data": json.dumps(payload)})
        if isinstance(result, dict) and result.get("code") not in (0, None):
            print(f"  error de la nube: {result.get('code')} {result.get('message')}")
        return result

    @staticmethod
    def _list(result: dict | None) -> list[dict]:
        items = (result or {}).get("result")
        if isinstance(items, dict):
            return [items]
        return items or []

    def get(self, props: list[tuple[int, int]]) -> list[dict]:
        did = self.device["did"]
        payload = {"params": [{"did": did, "siid": s, "piid": p} for s, p in props]}
        return self._list(self.call("/miotspec/prop/get", payload))

    def set(self, entries: list[tuple[int, int, object]]) -> list[dict]:
        did = self.device["did"]
        payload = {
            "params": [
                {"did": did, "siid": s, "piid": p, "value": v} for s, p, v in entries
            ]
        }
        return self._list(self.call("/miotspec/prop/set", payload))

    def action(self, siid: int, aiid: int, args: list) -> dict | None:
        payload = {"params": {"did": self.device["did"], "siid": siid, "aiid": aiid, "in": args}}
        return self.call("/miotspec/action", payload)


def main(argv: list[str]) -> int:
    props, acts = load_spec()
    if len(argv) < 2:
        print(__doc__)
        return 1

    command, args = argv[1], argv[2:]
    cloud = Cloud()

    def resolve(token: str) -> tuple[int, int] | None:
        if "." in token:
            siid, piid = token.split(".")
            return int(siid), int(piid)
        if token in props:
            siid, piid, _ = props[token]
            return siid, piid
        print(f"  propiedad desconocida: {token}")
        return None

    if command == "status":
        resolved = [resolve(n) for n in STATUS_PROPS]
        resolved = [r for r in resolved if r]
        results = cloud.get(resolved)
        for item in results:
            name = next(
                (n for n, (s, p, _) in props.items() if (s, p) == (item["siid"], item["piid"])),
                f"{item['siid']}.{item['piid']}",
            )
            if item.get("code"):
                print(f"{name:<32} ERROR {item['code']}")
                continue
            print(f"{name:<32} {human(name, item.get('value'))}")
        return 0

    if command == "get":
        resolved = [(t, resolve(t)) for t in args]
        results = cloud.get([r for _, r in resolved if r])
        by_key = {(r["siid"], r["piid"]): r for r in results}
        for token, key in resolved:
            if not key:
                continue
            item = by_key.get(key, {})
            print(f"{token:<32} {human(token, item.get('value'))}")
        return 0

    if command == "set":
        key = resolve(args[0])
        if not key:
            return 1
        raw = args[1]
        value = json.loads(raw) if raw[:1] in "[{" else int(raw)
        result = cloud.set([(key[0], key[1], value)])
        print(f"{args[0]} <- {value}: {result}")
        return 0

    if command == "act":
        name = args[0]
        if name not in acts:
            print(f"  accion desconocida: {name}")
            return 1
        siid, aiid, _ = acts[name]
        params = json.loads(args[1]) if len(args) > 1 else []
        print(f"{name}({params}) -> {json.dumps(cloud.action(siid, aiid, params), ensure_ascii=False)}")
        return 0

    if command == "raw":
        endpoint = args[0]
        payload = json.loads(args[1]) if len(args) > 1 else {}
        print(json.dumps(cloud.call("/" + endpoint, payload), indent=2, ensure_ascii=False))
        return 0

    print(f"comando desconocido: {command}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
