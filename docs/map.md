# Map: download, decrypt, render

The map is **not** available over miIO on this model. The robot only tells you
the *name* of a cloud object; the binary comes from Xiaomi's FDS.

```
10.1  map-obj-name        {"index":..., "obj_name":"<uid>/<did>/0"}   live map
10.5  map-management      {"map_array":[{... "obj_name":"<uid>/<did>/3", "is_current":true}, ...]}
```

Two traps, both of which cost an afternoon:

1. Use **`/v2/home/get_interim_file_url_pro`**. The non-`_pro` endpoint answers
   `{"code": -6, "message": "invalid config for fds"}` for this family.
2. The `<uid>` in `obj_name` is **not** your account `userId`. Take the string
   the robot gives you; do not build it yourself.

## Step 1 — signed URL

```python
url = POST https://de.api.io.mi.com/app/v2/home/get_interim_file_url_pro
      {"obj_name": "8300000000/1170000000/3"}
# -> {"result": {"url": "https://awsde0-fusion.fds.api.xiaomi.com/shunzao-ov51gl/..."}}
```

The bucket is `shunzao-ov51gl` (vendor: Shunzao). The signed URL is valid for a
few minutes and needs no auth headers.

## Step 2 — decrypt

The object is:

```json
{"version": 2, "data": "<urlsafe base64>"}
```

The payload is AES-128-CBC, PKCS7, with this key derivation:

```python
import base64, hashlib, zlib
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

IV = b"ABCDEF1234123412"
MODEL_KEY = model[-16:].encode()          # "mi.vacuum.ov51gl"
DID = str(device_id)

wrapped = AES.new(MODEL_KEY, AES.MODE_CBC, IV).encrypt(pad(MODEL_KEY + DID, 16))
KEY = hashlib.md5(wrapped).digest()

blob = payload["data"].replace("_", "/").replace("-", "+")
blob += "=" * (-len(blob) % 4)
plain = unpad(AES.new(KEY, AES.MODE_CBC, IV).decrypt(base64.b64decode(blob)), 16)
obj = json.loads(zlib.decompress(plain))
```

## Step 3 — what's inside

```json
{
  "map_id": 1, "width": 295, "height": 397,
  "origin_x": -6000, "origin_y": -14400, "resolution": 50,
  "pile_x": 110, "pile_y": -444, "pile_yaw": 7853,
  "map_data": "<base64(zlib(width*height bytes))>",
  "room_attrs": [{"id": 3, "room_name": "...", "name_pos_x": 4597, "name_pos_y": -5613,
                  "fan_level": 1, "water_level": 1, "clean_times": 1, "on": true}],
  "map_room_info": [{"grid_id": 3, "room_id": 3, "color": 3}],
  "fb_regions": [{"id": 0, "fb_attr": 0, "fb_point": [x1,y1,x2,y2,x3,y3,x4,y4]}],
  "fb_walls":   [{"id": 0, "wall_points": [x1,y1,x2,y2]}],
  "carpets":    [{"p": [x1,y1,...], "d": "..."}],
  "furniture":  [{"type": 1110, "pos": [x1,y1,...]}]
}
```

- `map_data` is **one byte per pixel**: `0` empty, `255` wall, anything else is a
  room id.
- Pixel ← mm: `col = (x - origin_x) / resolution`, `row = (y - origin_y) / resolution`.
  No axis flip.
- The **live map** (`.../0`) additionally carries `position {"x","y","yaw"}` and
  `paths.points[]` — the full trajectory of the last run.

## Step 4 — parse or draw it

`tools/map.py` does all of the above and renders a PNG:

```bash
.venv/bin/python tools/map.py            # saved map (is_current)
.venv/bin/python tools/map.py --live     # pose + trajectory
.venv/bin/python tools/map.py --all      # every stored map
```

If you want a richer object, feed the decrypted JSON to
[`vacuum-map-parser-xiaomi`](https://github.com/aronkahrs-us/Python-package-vacuum-map-parser-xiaomi):

```python
from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.size import Sizes
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_xiaomi.map_data_parser import XiaomiMapDataParser

parser = XiaomiMapDataParser(ColorsPalette(), Sizes(), [], ImageConfig(), [])
parsed = parser.parse(obj, model="xiaomi.vacuum.ov51gl", device_id=str(did))
```

That works unmodified on this model (15 rooms, no-go areas, walls, carpets and
path all populated) — it just needs the model registered. See
[PR #7](https://github.com/aronkahrs-us/Python-package-vacuum-map-parser-xiaomi/pull/7).

## Two maps, one robot

Multi-floor is supported: `map-management` lists every saved map with
`is_current`. In practice `is_current` may not match the map the robot is
actually using, so compare zone coordinates (props 2.13 / 2.14) against each map's
`fb_regions` / `fb_walls` if you need to be sure.

## History

`10.3 clean-record` gives the sessions, and each run's `paths` gives the route:

```json
{"total_time": 942, "total_area": 901000, "total_count": 13,
 "history_list": [{"label": "07853_132000_1_0_0", "stime": 1792759368}, ...]}
```

The label is a compressed record of the run; `stime` is a plain unix timestamp.
