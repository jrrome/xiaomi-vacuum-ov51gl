# Custom voice packs

The H40 speaks 93 numbered MP3s per language. You **can** replace them with your
own audio, without root: the robot accepts a pack uploaded through Xiaomi's own
install flow, and only checks the md5 and size you hand it.

Two projects already do this for sibling models, and are worth reading:

- [`Kalvisan/vacuum-voice-studio`](https://github.com/Kalvisan/vacuum-voice-studio) — X20 Max (`d109gl`)
- [`SashaEee/Trouver_audio_install`](https://github.com/SashaEee/Trouver_audio_install) — Trouver / Dreame / Mova, ships character packs

## The official catalog is public

```
https://awsde0-fusion.fds.api.xiaomi.com/shunzao-ov51gl/0Cloud_Management/voice_config/voice.config
```

21 languages, each with its zip URL and md5:

```json
{"voiceList": [
  {"code": "es", "url": "/shunzao-ov51gl/0Cloud_Management/voice_config/2025080810/es.zip",
   "md5": "80711a4f5a5dfc6c731451a20d8d44ec"},
  ...
]}
```

The Spanish pack: 93 MP3s, numeric names from `10.mp3` to `910.mp3`, zip stored
(no compression), with the audio under an `es/` folder. The official zip even
carries macOS `__MACOSX` junk, so extra entries are tolerated.

Useful slots (numbering is shared across the family):

| File | Phrase |
|---|---|
| `130.mp3` | start cleaning |
| `170.mp3` | paused |
| `210.mp3` | cleaning finished |
| `240.mp3` | low battery |
| `520.mp3` | "the robot vacuum is here" (the `identify` shout) |
| `720.mp3` | robot is stuck |

## Install flow

```
1. POST /v2/home/genpresignedurl_v3  {"did": <did>, "suffix": ".zip"}
   -> {"result": {".zip": {"url": <presigned PUT>, "obj_name": ...}}}

2. PUT <zip bytes> to that URL

3. POST /v2/home/getfileurl_v3       {"obj_name": ...}
   -> {"result": {"url": <signed GET>}}          # verify md5 by downloading it

4. POST /miotspec/action
   {"params": {"did": <did>, "siid": 15, "aiid": 1,
               "in": ["es", "<path>[#/.zip]", "{\"md5\":\"...\",\"size\":123}]}}

5. Poll siid 15 aiid 2 until status 4 (applied) and current == "es"
```

`tools/voice_install.py` implements all of it:

```bash
# put your files in voice/custom/, named exactly like the official pack
.venv/bin/python tools/voice_install.py --build-only   # inspect the zip
.venv/bin/python tools/voice_install.py                # upload and apply
.venv/bin/python tools/voice_install.py --restore      # reinstall the official pack
```

It downloads the official pack once to `voice/base/` to use its file names as the
template, so nothing copyrighted is committed.

## Rules

- **Keep the exact file names.** Same numbers, same count.
- A missing file aborts the build; extra files are ignored.
- Audio that is much longer than the slot may get cut off. The official files are
  ~1.5 s; anything under ~10 s behaves.
- Mono 44.1 kHz MP3 is what the stock pack uses and is a safe target.

## Status semantics

`download-status` (`15.3`): `0` not downloaded, `1` downloading, `2` downloaded,
`3` download failed, `4` applied, `5` apply failed.

**It returns to `0` shortly after a successful install.** The `4` is transient, so
do not use a later read of `15.3` to verify — watch the progress during the
install instead, and confirm by ear with `try-listen` (`2.27`).

## Community packs

Character packs exist (GLaDOS, R2-D2, Rick & Morty, Warcraft, ...) but they are
built for the Dreame/Trouver numbering (`003.mp3` ... 116 files), not this one.
They need slot translation. Packs built for Xiaomi models that use the same
numeric scheme (for example the S20/`d106gl`) drop in almost directly: 77 of the
93 slots line up.

If you publish a pack, check the redistribution rights of the audio first.
