"""Utilidades compartidas para leer la spec MIoT de la H40."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SPEC_FILE = BASE / "spec" / "ov51gl.json"


def slug(type_urn: str) -> str:
    """urn:miot-spec-v2:property:status:... -> status"""
    return type_urn.split(":")[3]


@lru_cache(maxsize=1)
def load_spec() -> tuple[dict, dict]:
    """Devuelve (propiedades, acciones) indexadas por nombre."""
    spec = json.loads(SPEC_FILE.read_text())
    props: dict[str, tuple[int, int, dict]] = {}
    acts: dict[str, tuple[int, int, dict]] = {}
    for service in spec["services"]:
        for prop in service.get("properties", []):
            props[slug(prop["type"])] = (service["iid"], prop["iid"], prop)
        for action in service.get("actions", []):
            acts[slug(action["type"])] = (service["iid"], action["iid"], action)
    return props, acts


def value_names(name: str) -> dict[int, str] | None:
    """Etiquetas legibles de una propiedad enumerada."""
    _, _, prop = load_spec()[0].get(name, (0, 0, {}))
    if not prop.get("value-list"):
        return None
    return {v["value"]: v["description"] for v in prop["value-list"]}


def human(name: str, value):
    names = value_names(name)
    if names and isinstance(value, int) and value in names:
        return f"{value} ({names[value]})"
    if name.endswith("area") and isinstance(value, int):
        return f"{value} ({value / 100:.2f} m2)"
    if name in {"last-clean-time", "cleaning-time", "statistical-cleaning-duration"}:
        if isinstance(value, int) and value > 1_600_000_000:
            import datetime

            return f"{value} ({datetime.datetime.fromtimestamp(value):%Y-%m-%d %H:%M})"
    return value
