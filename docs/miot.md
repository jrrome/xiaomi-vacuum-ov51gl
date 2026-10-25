# MIoT over LAN (and when to use the cloud)

## Transport

- **UDP 54321**, miIO framing:

```
magic(2)=0x2131 | length(2) | unknown(4) | device_id(4) | ts(4) | md5(16)
```

- Payload: JSON, AES-128-CBC encrypted with the device's 16-byte token.
- Semantics: **MIoT spec**, not the legacy Roborock command set. You call
  `get_properties` / `set_properties` / `action` with `siid` / `piid` / `aiid`.

There is no discovery: this firmware does **not** answer the unauthenticated
`hello` packet, so `miiocli discover` finds nothing even though the robot is on
the network. Find it by hostname (`xiaomi_vacuum_ov51gl`) or in the router, then
talk to it by IP.

`miIO.info` always answers and needs only the token:

```json
{"model": "xiaomi.vacuum.ov51gl", "fw_ver": "4.5.6_0105", "miio_client_ver": "4.5.6",
 "mac": "...", "netif": {"localIp": "...", "gw": "..."}, "ap": {"rssi": "-61", "freq": 2462}}
```

## The batching limit (the important one)

`get_properties` takes a list. **Keep it at 10 or fewer.**

| Properties in one call | Result |
|---|---|
| 1, 2, 5, 10, 12 | answers |
| 14, 16, 18, 21 | silence, then `-9999 user ack timeout` |

The response grows with each entry and stops fitting in a single UDP datagram.
The device-side client never sees a complete request, the application layer never
answers, and you get an error that looks like a permissions or sleep problem.

This is why so many reports conclude that local control "doesn't work" on this
generation while `miIO.info` clearly does.

## Reading and writing

```python
from miio import MiotDevice

dev = MiotDevice(ip, token, lazy_discover=False, timeout=8)

# read (batched)
dev.send("get_properties", [
    {"did": "2.2", "siid": 2, "piid": 2},
    {"did": "3.1", "siid": 3, "piid": 1},
])
# -> [{'did': '...', 'siid': 2, 'piid': 2, 'code': 0, 'value': 9}, ...]

# write
dev.send("set_properties", [{"did": "2.9", "siid": 2, "piid": 9, "value": 3}])

# act
dev.send("action", {"did": "2.1", "siid": 2, "aiid": 1, "in": []})
```

The first packet after a few minutes of inactivity is sometimes lost; retry once.

## Service map

| `siid` | Service | What's in it |
|---|---|---|
| 1 | device-information | manufacturer, model, serial, firmware |
| 2 | vacuum | 93 properties, 63 actions — the core |
| 3 | battery | `battery-level`, `charging-state`, `voltage` |
| 4 | alarm | `alarm`, `volume` |
| 5 | physical-controls-locked | child lock |
| 6 | identify | makes the robot announce itself (**loud**) |
| 9 | mop | mop life |
| 10 | vacuum-map | 15 properties, 8 actions |
| 11 | no-disturb | do-not-disturb window |
| 12/13/14 | brushes / filter | consumables |
| 15 | voice-management | voice pack install |
| 17 | ai-small-pictures | AI detection pictures |
| 18/19 | detergent / dust-bag | consumables |
| 20 | custom | history objects, schedules |

## Properties worth knowing

| `siid.piid` | Name | Notes |
|---|---|---|
| 2.1 | `vacuum-frameware-version` | string — note it occupies piid 1, status is piid 2 |
| 2.2 | `status` | 1 idle, 2 charging, 4 sweeping, 5 paused, 6 returning, 9 charged, 15 error, 16/17 sweep+mop |
| 2.3 | `fault` | uint32; `2.66 fault-ids` carries the actual list |
| 2.6 / 2.7 | `cleaning-area` / `cleaning-time` | area in 0.01 m², time in seconds |
| 2.13 / 2.14 | `restricted-sweep-areas` / `restricted-walls` | JSON, coordinates in mm |
| 2.16 | `room-information` | JSON: room ids and names |
| 2.57 / 2.58 | `sweep-furniture` / `carpet-object` | JSON bounding boxes |
| 10.1 / 10.5 | `map-obj-name` / `map-management` | cloud object names for the live and saved maps |
| 10.3 | `clean-record` | history list with timestamps |
| 17.2 | `ai-picture-info` | detected objects, once detection has run |

## Actions used most

| `siid.aiid` | Action |
|---|---|
| 2.1 | `start-sweep` |
| 2.2 | `stop-sweeping` (stop in place) |
| 2.3 | `stop-and-gocharge` (stop and dock) |
| 2.4 | `start-only-sweep` |
| 2.7 / 2.8 | `pause-sweeping` / `continue-sweep` |
| 2.16 | `start-vacuum-room-sweep` |
| 2.26 | `remote-control` (1/2 up press/lift, 3/4 left, 5/6 right) |
| 2.28 / 2.29 | `enter-remote` / `exit-remote` |
| 2.59 | `spot-cleaning` |
| 15.1 / 15.2 | `download-voice` / `get-download-status` |

## Cloud vs LAN

Local is faster and keeps working without internet. The cloud is still needed for:

- **map objects** (the binary is only served from FDS)
- **historical data**: `statistical-*` properties are 0 locally and populated in the cloud
- fallback when the robot is unreachable from your network

The cloud API is the same JSON signed with RC4 (`/miotspec/prop/get`,
`/miotspec/prop/set`, `/miotspec/action`, `/v2/home/*`). See `tools/cloud.py`.

## Gotchas

- **Batching**: never more than ~12 properties (see above).
- **`sleep-status`** reads `True` while docked and means nothing for reachability.
- **The voice is loud.** `identify` (6.1) and `try-listen` (2.27) make noise; the
  base also runs a dust-emptying cycle after every clean.
- **`fault` = 100008** appears on a healthy, idle robot with `fault-ids: [0]`.
  Unexplained; treat `fault-ids` as authoritative.
