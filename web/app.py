#!/usr/bin/env python3
"""Panel web local para la Xiaomi Robot Vacuum H40.

Se abre desde el movil en http://<ip-del-mac>:8787 (misma red Wi-Fi).

Todo el control va por LAN (miIO). El mapa es lo unico que necesita la nube.

  .venv/bin/python web/app.py            # 0.0.0.0:8787
  .venv/bin/python web/app.py --port 9000

Sin autenticacion: es para tu red local. No lo expongas a internet.
"""

from __future__ import annotations

import argparse
import io
import json
import socket
import sys
import threading
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from flask import Flask, Response, jsonify, request

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "tools"))

from miio import MiotDevice  # noqa: E402

SECRETS = BASE / ".secrets" / "devices.json"
app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"))
_lock = threading.Lock()
_device: MiotDevice | None = None
_map_cache: dict = {"at": 0.0, "png": None}

PROPS = {
    "battery": (3, 1),
    "charging": (3, 2),
    "status": (2, 2),
    "fault": (2, 3),
    "progress": (2, 89),
    "area": (2, 6),
    "clean_time": (2, 7),
    "mode": (2, 9),
    "water": (2, 10),
    "sweep_mop": (2, 4),
    "times": (2, 8),
    "sewage": (2, 97),
    "water_tank": (2, 98),
    "mop": (2, 11),
}

ACTIONS = {
    "start": (2, 1, []),
    "stop": (2, 2, []),
    "dock": (2, 3, []),
    "pause": (2, 7, []),
    "resume": (2, 8, []),
    "spot": (2, 59, []),
    "enter_remote": (2, 28, []),
    "exit_remote": (2, 29, []),
}

BUTTONS = {"up": 1, "up_lift": 2, "left": 3, "left_lift": 4, "right": 5, "right_lift": 6}

STATUS_TEXT = {
    1: "En espera",
    2: "Cargando",
    4: "Limpiando",
    5: "Pausado",
    6: "Volviendo",
    7: "Lavando mopa",
    8: "Mando",
    9: "Cargado",
    10: "Mapeando",
    15: "Error",
    16: "Aspirando y fregando",
    17: "Fregando",
}


def device() -> MiotDevice:
    global _device
    if _device is None:
        data = json.loads(SECRETS.read_text())
        dev = next(
            d
            for devices in data.values()
            for d in devices
            if str(d.get("model", "")).startswith("xiaomi.vacuum")
        )
        _device = MiotDevice(dev["localip"], dev["token"], lazy_discover=False, timeout=8)
    return _device


def _send(method: str, params):
    """Envia con reintento: el primer paquete tras un rato inactivo se pierde."""
    global _device
    last: Exception | None = None
    for attempt in range(3):
        try:
            return device().send(method, params)
        except Exception as exc:  # noqa: BLE001
            last = exc
            _device = None
            time.sleep(0.3 * (attempt + 1))
    raise last  # type: ignore[misc]


def read(names: list[str]) -> dict:
    pairs = [PROPS[n] for n in names if n in PROPS]
    out: dict[str, object] = {}
    with _lock:
        for start in range(0, len(pairs), 10):
            batch = pairs[start : start + 10]
            params = [{"did": f"{s}.{p}", "siid": s, "piid": p} for s, p in batch]
            for item in _send("get_properties", params):
                name = next(n for n, sp in PROPS.items() if sp == (item["siid"], item["piid"]))
                out[name] = item.get("value")
    return out


def action(name: str, args: list | None = None) -> object:
    siid, aiid, default = ACTIONS[name]
    payload = {"did": f"{siid}.{aiid}", "siid": siid, "aiid": aiid, "in": args or default}
    with _lock:
        return _send("action", payload)


def set_prop(name: str, value) -> object:
    siid, piid = PROPS[name]
    payload = [{"did": f"{siid}.{piid}", "siid": siid, "piid": piid, "value": value}]
    with _lock:
        return _send("set_properties", payload)


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/state")
def api_state():
    try:
        data = read(list(PROPS))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503
    data["status_text"] = STATUS_TEXT.get(data.get("status"), f"Estado {data.get('status')}")
    data["ok"] = True
    return jsonify(data)


@app.post("/api/action")
def api_action():
    name = (request.json or {}).get("name")
    if name not in ACTIONS:
        return jsonify({"ok": False, "error": "accion desconocida"}), 400
    try:
        return jsonify({"ok": True, "result": action(name)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.post("/api/set")
def api_set():
    body = request.json or {}
    name, value = body.get("name"), body.get("value")
    if name not in {"mode", "water", "sweep_mop", "times"}:
        return jsonify({"ok": False, "error": "propiedad no permitida"}), 400
    try:
        return jsonify({"ok": True, "result": set_prop(name, int(value))})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.post("/api/move")
def api_move():
    body = request.json or {}
    key = f"{body.get('dir')}" + ("_lift" if body.get("op") == "lift" else "")
    if key not in BUTTONS:
        return jsonify({"ok": False, "error": "boton desconocido"}), 400
    try:
        with _lock:
            _send("action", {"did": "2.26", "siid": 2, "aiid": 26, "in": [BUTTONS[key]]})
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.get("/api/rooms")
def api_rooms():
    try:
        with _lock:
            raw = _send("get_properties", [{"did": "2.16", "siid": 2, "piid": 16}])[0]["value"]
        rooms = json.loads(raw)["rooms"]
        return jsonify({"ok": True, "rooms": rooms})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.get("/api/map.png")
def api_map():
    if _map_cache["png"] and time.time() - _map_cache["at"] < 45:
        return Response(_map_cache["png"], mimetype="image/png")
    try:
        from map import download, render

        with _lock:
            raw = _send("get_properties", [{"did": "10.1", "siid": 10, "piid": 1}])[0]["value"]
        obj_name = json.loads(raw)["obj_name"]
        from cloud import Cloud

        obj = download(Cloud(), obj_name)
        with NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            render(obj, Path(tmp.name))
            png = Path(tmp.name).read_bytes()
            Path(tmp.name).unlink()
        _map_cache.update({"at": time.time(), "png": png})
        return Response(png, mimetype="image/png")
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.1.1", 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    # Calienta el handshake miIO para que la primera visita no espere.
    threading.Thread(target=lambda: read(list(PROPS)), daemon=True).start()

    url = f"http://{lan_ip()}:{args.port}"
    print(f"\nPanel listo: {url}\n")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception:  # noqa: BLE001
        pass
    app.run(host="0.0.0.0", port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
