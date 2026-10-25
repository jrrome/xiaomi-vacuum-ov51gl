#!/usr/bin/env python3
"""Build and install a custom voice pack on the H40, through Xiaomi's own flow.

  .venv/bin/python tools/voice_install.py --build-only   # just build the zip
  .venv/bin/python tools/voice_install.py               # build, upload, apply
  .venv/bin/python tools/voice_install.py --restore     # reinstall official es

How it works (no root, no local server):

  1. The official pack for the model is public:
     https://awsde0-fusion.fds.api.xiaomi.com/shunzao-ov51gl/0Cloud_Management/voice_config/voice.config
     It lists every language with its zip URL and md5.
  2. We reuse its file names, replace the MP3s, and build a zip with the same
     layout (`es/10.mp3`, `es/130.mp3`, ...).
  3. `/v2/home/genpresignedurl_v3` returns a presigned upload URL inside the
     account's own FDS namespace; we PUT the zip there.
  4. `/v2/home/getfileurl_v3` returns a signed download URL, which we verify by
     downloading and comparing the md5.
  5. `miotspec/action` siid=15 aiid=1 with `[language, path, {"md5","size"}]`
     makes the robot fetch and apply the pack. Only md5 and size are checked.

Put your replacement MP3s in `voice/custom/`, named exactly like the official
pack (numeric, e.g. `130.mp3`). Missing files are an error; extra files are
ignored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cloud import Cloud  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
CUSTOM = BASE / "voice" / "custom"
BASELINE = BASE / "voice" / "base"
BUILD = BASE / "voice" / "build"

LANG = "es"
SUFFIX = ".zip"
VOICE_SIID = 15

# One catalog per model. Sibling models use their own bucket name.
CATALOG_URL = (
    "https://awsde0-fusion.fds.api.xiaomi.com"
    "/shunzao-ov51gl/0Cloud_Management/voice_config/voice.config"
)


def catalog() -> list[dict]:
    response = requests.get(CATALOG_URL, timeout=20)
    response.raise_for_status()
    return response.json()["voiceList"]


def official_entry(language: str) -> dict:
    return next(v for v in catalog() if v["code"] == language)


def official_pack(language: str = LANG) -> Path:
    """Download the official pack once, to use its file names as the template."""
    BASELINE.mkdir(parents=True, exist_ok=True)
    target = BASELINE / f"{language}.zip"
    entry = official_entry(language)
    if target.exists() and hashlib.md5(target.read_bytes()).hexdigest() == entry["md5"]:
        return target
    url = "https://awsde0-fusion.fds.api.xiaomi.com" + entry["url"]
    data = requests.get(url, timeout=60).content
    if hashlib.md5(data).hexdigest() != entry["md5"]:
        raise SystemExit("official pack checksum mismatch")
    target.write_bytes(data)
    return target


def official_names(language: str = LANG) -> set[str]:
    names = set()
    for name in zipfile.ZipFile(official_pack(language)).namelist():
        base = name.split("/")[-1]
        if base.startswith("._") or not base.endswith(".mp3"):
            continue
        names.add(base)
    return names


def build_zip(source: Path, out: Path, prefix: str, expected: set[str]) -> Path:
    files = sorted(source.glob("*.mp3"), key=lambda p: int(p.stem))
    present = {p.name for p in files}
    missing = expected - present
    if missing:
        raise SystemExit(f"{len(missing)} missing files: {sorted(missing)[:10]}")
    extra = present - expected
    if extra:
        print(f"ignoring {len(extra)} unknown files: {sorted(extra)[:10]}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for path in files:
            if path.name in expected:
                archive.write(path, arcname=f"{prefix}/{path.name}")
    return out


def status(cloud: Cloud) -> dict | None:
    body = cloud.call(
        "/miotspec/action",
        {"params": {"did": cloud.device["did"], "siid": VOICE_SIID, "aiid": 2, "in": []}},
    )
    out = ((body or {}).get("result") or {}).get("out")
    if isinstance(out, list) and len(out) >= 4:
        return {"target": out[0], "current": out[1], "status": out[2], "progress": out[3]}
    return None


def install(cloud: Cloud, archive: Path, language: str, dry: bool = False) -> bool:
    data = archive.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    size = len(data)
    print(f"zip: {archive.name}  {size / 1024:.0f} KB  md5 {md5}")
    print("preflight:", status(cloud))

    upload = cloud.call(
        "/v2/home/genpresignedurl_v3", {"did": cloud.device["did"], "suffix": SUFFIX}
    )
    entry = ((upload or {}).get("result") or {}).get(SUFFIX)
    if not entry or not entry.get("url"):
        print("unexpected genpresignedurl_v3 response:", json.dumps(upload)[:400])
        return False

    put = requests.put(
        entry["url"],
        data=data,
        headers={"Content-Type": "application/octet-stream"},
        timeout=180,
    )
    print(f"upload: HTTP {put.status_code}")
    put.raise_for_status()

    got = cloud.call("/v2/home/getfileurl_v3", {"obj_name": entry["obj_name"]})
    url = ((got or {}).get("result") or {}).get("url")
    if not url:
        print("unexpected getfileurl_v3 response:", json.dumps(got)[:400])
        return False

    check = requests.get(url, timeout=60)
    if hashlib.md5(check.content).hexdigest() != md5:
        print("signed download does not match the local zip")
        return False
    print(f"verified in FDS: {len(check.content) / 1024:.0f} KB")
    if dry:
        return True

    split = urlsplit(url)
    base = split.path + (f"?{split.query}" if split.query else "")
    variants = [f"{base}#/{SUFFIX}", base, url]
    meta = json.dumps({"md5": md5, "size": size}, separators=(",", ":"))

    for index, variant in enumerate(variants, 1):
        action = cloud.call(
            "/miotspec/action",
            {
                "params": {
                    "did": cloud.device["did"],
                    "siid": VOICE_SIID,
                    "aiid": 1,
                    "in": [language, variant, meta],
                }
            },
        )
        if ((action or {}).get("result") or {}).get("code", 0) != 0:
            print(f"  variant {index} rejected: {json.dumps(action)[:300]}")
            continue
        print(f"  variant {index} accepted, waiting for the robot...")
        for _ in range(60):
            time.sleep(2)
            state = status(cloud)
            if state:
                print(f"    status={state['status']} progress={state['progress']}", end="\r")
                if state["status"] in (0, 4) and state["current"] == language:
                    print(f"\n  applied: {state}")
                    return True
        break
    return False


def restore(cloud: Cloud, language: str = LANG) -> bool:
    entry = official_entry(language)
    print(f"restoring official pack: {entry['url']}")
    meta = json.dumps({"md5": entry["md5"], "size": 0}, separators=(",", ":"))
    action = cloud.call(
        "/miotspec/action",
        {
            "params": {
                "did": cloud.device["did"],
                "siid": VOICE_SIID,
                "aiid": 1,
                "in": [language, entry["url"], meta],
            }
        },
    )
    print(json.dumps(action, ensure_ascii=False))
    return ((action or {}).get("result") or {}).get("code", 0) == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--language", default=LANG)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--no-install", action="store_true", help="upload and verify, do not apply")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()

    if args.restore:
        return 0 if restore(Cloud(), args.language) else 1

    expected = official_names(args.language)
    archive = build_zip(CUSTOM, BUILD / f"{args.language}_custom.zip", args.language, expected)
    print(f"built {archive} with {len(expected)} mp3 under '{args.language}/'")
    if args.build_only:
        return 0

    ok = install(Cloud(), archive, args.language, dry=args.no_install)
    print("\nRESULT:", "applied" if ok else "failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
