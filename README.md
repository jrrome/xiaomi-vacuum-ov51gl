# Xiaomi Robot Vacuum H40 — `xiaomi.vacuum.ov51gl`

Local control, map extraction and custom voice packs for the Xiaomi H40, with
**no root, no firmware modification and no Home Assistant**.

This is a Shunzao-made robot (FDS bucket `shunzao-ov51gl`) using the Dreame-style
map container and the MIoT spec. It belongs to a 2025 family that also includes
`ov43gb` (H50), `ov71gl` (S40 Pro), `ov81gl` (S40), `d106gl` (S20) and `d109gl`
(X20 Max). Most of what is documented here applies to those too.

---

## What works

| Capability | Status | Notes |
|---|---|---|
| Local control (LAN, miIO/UDP 54321) | ✅ | Instant, no cloud round-trip |
| MIoT properties and actions | ✅ | 93 properties, 63 actions in service 2 |
| Status, battery, consumables | ✅ | |
| Zone / room / spot cleaning | ✅ | |
| Per-room suction, water and passes | ✅ | `set-room-clean-configs`, barely exposed in the app |
| Remote control (joystick) | ✅ | Forward / left / right only — no reverse |
| Map + trajectory | ✅ | Cloud FDS download, decrypted locally |
| Custom voice pack | ✅ | Uploaded through Xiaomi's own voice-pack flow |
| AI detection toggles | ✅ | All disabled by default; writes are accepted |
| AI detection photos | ⚠️ | Only produced during a cleaning run with detection on |
| Root / Valetudo / custom firmware | ❌ | No public work for this generation |

## The two things that cost the most time

**1. `user ack timeout` / `-9999` does not mean the device is asleep, and it does
not mean local control is unsupported.**

The firmware stops answering when you request **more than ~12 properties in a
single `get_properties` call**. 12 works, 14 goes silent. The response no longer
fits in one UDP datagram, so it is dropped and you get:

```
{'code': -9999, 'message': 'user ack timeout'}
```

Chunk your reads. That single detail turns "local control doesn't work on this
generation" into "local control works fine, in batches of 10".

**2. The map needs `get_interim_file_url_pro`, not `get_interim_file_url`.**

The regular endpoint answers `-6 invalid config for fds` for this family. The
`_pro` variant returns a signed FDS URL, and the object is AES-128-CBC encrypted
with a key derived from the model name and the device id. Full recipe in
[docs/map.md](docs/map.md).

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install python-miio flask pillow qrcode

# 1. Get the device token (QR login with the Xiaomi Home app, no password)
.venv/bin/python tools/xiaomi_login.py

# 2. Talk to the robot over the LAN
.venv/bin/python tools/vacuum.py info
.venv/bin/python tools/vacuum.py status
.venv/bin/python tools/vacuum.py act start-sweep

# 3. Same API through the cloud (needed for history, useful as fallback)
.venv/bin/python tools/cloud.py status

# 4. Map
.venv/bin/python tools/map.py            # saved map
.venv/bin/python tools/map.py --live     # current map with pose + trajectory

# 5. Web panel for the phone (joystick, modes, live state, map)
.venv/bin/python web/app.py              # http://<your-ip>:8787
```

Credentials and derived artefacts stay out of git:

```
.secrets/devices.json   token + device list
.secrets/session.json   cloud session
maps/                   decrypted floor plans — personal data
voice/                  official audio (Xiaomi's, not redistributable) and yours
```

## Tools

| File | Purpose |
|---|---|
| `tools/xiaomi_login.py` | QR or password login, dumps devices and cloud session |
| `tools/vacuum.py` | LAN CLI: `info`, `status`, `get`, `set`, `act`, `props`, `acts` |
| `tools/cloud.py` | The same over the cloud API: `status`, `get`, `set`, `act`, `raw` |
| `tools/map.py` | Download, decrypt and render the map (`--live`, `--all`) |
| `tools/voice_install.py` | Build, upload and apply a custom voice pack (`--restore`) |
| `tools/miot_spec.py` | Name → `siid`/`piid` resolution from `spec/ov51gl.json` |
| `tools/ai.py` | Inspect / toggle the vision layer and read detected pictures |
| `web/` | Mobile web panel |

`spec/ov51gl.json` is a local copy of the public MIoT spec:
`https://miot-spec.org/miot-spec-v2/instance?type=urn:miot-spec-v2:device:vacuum:0000A006:xiaomi-ov51gl:1`

## Docs

- [docs/miot.md](docs/miot.md) — protocol, auth, the batching limit, service map
- [docs/map.md](docs/map.md) — FDS endpoint, key derivation, map format
- [docs/voice.md](docs/voice.md) — voice pack catalog, layout, install and rollback

## What this is not

No rooting, no firmware flashing, no Valetudo. Everything here goes through the
documented MIoT surface and Xiaomi's own cloud endpoints. The only thing that is
"unofficial" is that the voice-pack installer trusts the md5 you hand it.

## Credits

Standing on shoulders:

- [`PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor`](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor) — issue [#704](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor/issues/704) (Luiznunes12) published the `_pro` endpoint and the key derivation, and [issue #762](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor/issues/762) tracks this exact model.
- [`aronkahrs-us/Python-package-vacuum-map-parser-xiaomi`](https://github.com/aronkahrs-us/Python-package-vacuum-map-parser-xiaomi) — the map JSON parser for this family.
- [`Kalvisan/vacuum-voice-studio`](https://github.com/Kalvisan/vacuum-voice-studio) — the `genpresignedurl_v3` / `getfileurl_v3` upload flow and the file-numbering reference.
- `breakneck-git`'s dossier on the Shunzao/ov81gl family, which mapped the vendor surface first.

## License

MIT — see [LICENSE](LICENSE).
