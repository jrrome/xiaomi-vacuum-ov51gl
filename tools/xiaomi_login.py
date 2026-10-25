#!/usr/bin/env python3
"""Login en la nube de Xiaomi y volcado del token del robot aspirador.

Dos metodos, sin guardar credenciales en disco:

  .venv/bin/python tools/xiaomi_login.py            # QR (recomendado)
  .venv/bin/python tools/xiaomi_login.py --password # usuario + contrasena

El metodo QR abre una imagen de codigo QR: se escanea con la app Xiaomi Home
(la sesion del movil ya esta autenticada, asi que no hay contrasena, ni captcha,
ni verificacion en dos pasos).

El metodo por contrasena replica el flujo real de Xiaomi:
  step1 (devuelve 70016 SIEMPRE: es normal, no es un fallo de contrasena)
  step2 (hash MD5 de la contrasena) -> captcha o 2FA si la cuenta los exige
  step3 (intercambio por serviceToken)

Solo LEE: lista dispositivos y guarda el token en .secrets/devices.json (600).
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import random
import re
import stat
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from Crypto.Cipher import ARC4

BASE = Path(__file__).resolve().parent.parent
SECRETS = BASE / ".secrets"
SERVERS = ["de", "cn", "us", "ru", "tw", "sg", "in", "i2"]
LOCALE = "en_US"


def to_json(text: str) -> dict:
    return json.loads(text.replace("&&&START&&&", ""))


def gen_agent() -> str:
    agent_id = "".join(chr(random.randint(65, 69)) for _ in range(13))
    text = "".join(chr(random.randint(97, 122)) for _ in range(18))
    return f"{text}-{agent_id} APP/com.xiaomi.mihome APPV/10.5.201"


class XiaomiLogin:
    def __init__(self) -> None:
        self.agent = gen_agent()
        self.device_id = "".join(chr(random.randint(97, 122)) for _ in range(6))
        self.session = requests.Session()
        for domain in ("mi.com", "xiaomi.com"):
            self.session.cookies.set("sdkVersion", "accountsdk-18.8.15", domain=domain)
            self.session.cookies.set("deviceId", self.device_id, domain=domain)
        self.user_id: str | None = None
        self.ssecurity: str | None = None
        self.cuser_id: str | None = None
        self.service_token: str | None = None
        self._sign: str | None = None
        self._location: str | None = None

    # ------------------------------------------------------------------ utils
    @property
    def _headers(self) -> dict:
        return {
            "User-Agent": self.agent,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _finish(self) -> bool:
        """Canjea la URL de sesion por el serviceToken."""
        if not self._location:
            print("  sin URL de sesion")
            return False
        r = self.session.get(self._location, headers=self._headers, allow_redirects=True)
        token = r.cookies.get("serviceToken") or self.session.cookies.get("serviceToken")
        if not token:
            print(f"  no se obtuvo serviceToken (HTTP {r.status_code})")
            return False
        self.service_token = token
        print("  serviceToken obtenido")
        return True

    # --------------------------------------------------------------- QR login
    def login_qr(self) -> bool:
        r = self.session.get(
            "https://account.xiaomi.com/longPolling/loginUrl",
            params={
                "_qrsize": "480",
                "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
                "callback": "https://sts.api.io.mi.com/sts",
                "_hasLogo": "false",
                "sid": "xiaomiio",
                "serviceParam": "",
                "_locale": LOCALE,
                "_dc": str(int(time.time() * 1000)),
            },
            headers=self._headers,
        )
        data = to_json(r.text)
        if "qr" not in data:
            print(f"  no se pudo pedir el QR: {data}")
            return False

        self._show_qr(data["qr"], data.get("loginUrl", ""))

        timeout = int(data.get("timeout", 120))
        deadline = time.time() + timeout
        print("  esperando a que escanees el codigo...")
        response = None
        while time.time() < deadline:
            try:
                response = self.session.get(data["lp"], timeout=15)
            except requests.exceptions.Timeout:
                continue
            except requests.exceptions.RequestException as exc:
                print(f"  error de red: {exc}")
                return False
            if response.status_code == 200:
                break

        if response is None or response.status_code != 200:
            print("  el QR caduco sin escanearse")
            return False

        result = to_json(response.text)
        self.user_id = result.get("userId")
        self.ssecurity = result.get("ssecurity")
        self.cuser_id = result.get("cUserId")
        self._location = result.get("location")
        return self._finish()

    def _show_qr(self, image_url: str, login_url: str) -> None:
        # 1) la imagen oficial de Xiaomi, abierta en el visor del sistema
        try:
            img = self.session.get(image_url, headers=self._headers, timeout=15).content
            path = Path(tempfile.gettempdir()) / "xiaomi_login_qr.png"
            path.write_bytes(img)
            os.system(f"open '{path}'")
            print(f"  QR abierto en el visor: {path}")
        except Exception as exc:  # noqa: BLE001
            print(f"  no se pudo abrir la imagen ({exc})")
        # 2) el mismo QR en ASCII, por si el visor falla
        try:
            import qrcode

            qr = qrcode.QRCode(border=1)
            qr.add_data(login_url or image_url)
            qr.make()
            qr.print_ascii(invert=True)
        except Exception:  # noqa: BLE001
            print(f"  abre esta URL y escanea el QR: {login_url}")

    # --------------------------------------------------------- password login
    def login_password(self, username: str, password: str) -> bool:
        r = self.session.get(
            "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true",
            headers=self._headers,
            cookies={"userId": username},
        )
        step1 = to_json(r.text)
        if "_sign" not in step1:
            print(f"  respuesta inesperada de Xiaomi: {step1}")
            return False
        self._sign = step1["_sign"]

        fields = {
            "sid": "xiaomiio",
            "hash": hashlib.md5(password.encode()).hexdigest().upper(),
            "callback": "https://sts.api.io.mi.com/sts",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "user": username,
            "_sign": self._sign,
            "_json": "true",
        }

        result = self._step2(fields)

        if result.get("captchaUrl"):
            code = self._solve_captcha(result["captchaUrl"])
            if not code:
                return False
            fields["captCode"] = code
            result = self._step2(fields)
            if result.get("code") == 87001:
                print("  captcha incorrecto")
                return False

        if result.get("notificationUrl"):
            if not self._two_factor(result["notificationUrl"]):
                return False
            return True

        if result.get("result") != "ok":
            print(f"  login rechazado: code={result.get('code')} {result.get('desc')}")
            return False

        self.user_id = result.get("userId")
        self.ssecurity = result.get("ssecurity")
        self.cuser_id = result.get("cUserId")
        self._location = result.get("location")
        return self._finish()

    def _step2(self, fields: dict) -> dict:
        r = self.session.post(
            "https://account.xiaomi.com/pass/serviceLoginAuth2",
            params=fields,
            headers=self._headers,
            allow_redirects=False,
        )
        return to_json(r.text)

    def _solve_captcha(self, captcha_url: str) -> str | None:
        if captcha_url.startswith("/"):
            captcha_url = "https://account.xiaomi.com" + captcha_url
        img = self.session.get(captcha_url, headers=self._headers).content
        path = Path(tempfile.gettempdir()) / "xiaomi_captcha.png"
        path.write_bytes(img)
        os.system(f"open '{path}'")
        print(f"  captcha requerido -> {path}")
        code = input("  escribe el codigo del captcha: ").strip()
        return code or None

    def _two_factor(self, notification_url: str) -> bool:
        """Verificacion en dos pasos por email (flujo identity/*)."""
        print("  se requiere verificacion en dos pasos")
        r = self.session.get(notification_url, headers=self._headers)
        context = parse_qs(urlparse(notification_url).query).get("context", [""])[0]

        self.session.get(
            "https://account.xiaomi.com/identity/list",
            params={"sid": "xiaomiio", "context": context, "_locale": LOCALE},
            headers=self._headers,
        )
        self.session.post(
            "https://account.xiaomi.com/identity/auth/sendEmailTicket",
            params={
                "_dc": str(int(time.time() * 1000)),
                "sid": "xiaomiio",
                "context": context,
                "mask": "0",
                "_locale": LOCALE,
            },
            data={
                "retry": "0",
                "icode": "",
                "_json": "true",
                "ick": self.session.cookies.get("ick", ""),
            },
            headers=self._headers,
        )

        code = input("  codigo recibido por email: ").strip()
        r = self.session.post(
            "https://account.xiaomi.com/identity/auth/verifyEmail",
            params={
                "_flag": "8",
                "_json": "true",
                "sid": "xiaomiio",
                "context": context,
                "mask": "0",
                "_locale": LOCALE,
            },
            data={
                "_flag": "8",
                "ticket": code,
                "trust": "false",
                "_json": "true",
                "ick": self.session.cookies.get("ick", ""),
            },
            headers=self._headers,
        )

        finish = r.headers.get("Location")
        if not finish:
            match = re.search(r"https://account\.xiaomi\.com/identity/result/check\?[^\"']+", r.text or "")
            finish = match.group(0) if match else None
        if not finish:
            print("  no se pudo continuar tras el 2FA")
            return False

        if "identity/result/check" in finish:
            hop = self.session.get(finish, headers=self._headers, allow_redirects=False)
            finish = hop.headers.get("Location")
        if not finish:
            print("  cadena de redireccion incompleta")
            return False

        end = self.session.get(finish, headers=self._headers, allow_redirects=False)
        if end.status_code == 200 and "Xiaomi Account - Tips" in end.text:
            end = self.session.get(finish, headers=self._headers, allow_redirects=False)

        pragma = end.headers.get("extension-pragma")
        if pragma:
            try:
                self.ssecurity = json.loads(pragma).get("ssecurity") or self.ssecurity
            except json.JSONDecodeError:
                pass

        sts = end.headers.get("Location")
        if not sts and end.text:
            idx = end.text.find("https://sts.api.io.mi.com/sts")
            if idx != -1:
                endpos = end.text.find('"', idx)
                sts = end.text[idx : endpos if endpos != -1 else idx + 300]
        if not sts:
            print("  no se obtuvo la URL de STS")
            return False

        r = self.session.get(sts, headers=self._headers, allow_redirects=True)
        token = r.cookies.get("serviceToken") or self.session.cookies.get("serviceToken")
        if not token:
            print("  no se obtuvo serviceToken tras el 2FA")
            return False
        self.service_token = token
        self.user_id = self.user_id or self.session.cookies.get("userId")
        self.cuser_id = self.cuser_id or self.session.cookies.get("cUserId")
        print("  serviceToken obtenido (2FA)")
        return True

    # ------------------------------------------------------------ cloud calls
    @staticmethod
    def api_url(server: str) -> str:
        prefix = "" if server == "cn" else f"{server}."
        return f"https://{prefix}api.io.mi.com/app"

    def execute(self, url: str, params: dict) -> dict | None:
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": self.agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }
        cookies = {
            "userId": str(self.user_id),
            "yetAnotherServiceToken": str(self.service_token),
            "serviceToken": str(self.service_token),
            "locale": "en_GB",
            "timezone": "GMT+02:00",
            "is_daylight": "1",
            "dst_offset": "3600000",
            "channel": "MI_APP_STORE",
        }
        nonce = self._nonce()
        signed = self._signed_nonce(nonce)
        fields = self._enc_params(url, "POST", signed, nonce, params)
        r = self.session.post(url, headers=headers, cookies=cookies, params=fields)
        if r.status_code != 200:
            return None
        return json.loads(self._decrypt(self._signed_nonce(fields["_nonce"]), r.text))

    @staticmethod
    def _nonce() -> str:
        millis = round(time.time() * 1000)
        b = (random.getrandbits(64) - 2**63).to_bytes(8, "big", signed=True)
        part2 = int(millis / 60000)
        b += part2.to_bytes((part2.bit_length() + 7) // 8, "big")
        return base64.b64encode(b).decode()

    def _signed_nonce(self, nonce: str) -> str:
        digest = hashlib.sha256(
            base64.b64decode(self.ssecurity) + base64.b64decode(nonce)
        ).digest()
        return base64.b64encode(digest).decode()

    @staticmethod
    def _enc_signature(url: str, method: str, signed: str, params: dict) -> str:
        parts = [method.upper(), url.split("com")[1].replace("/app/", "/")]
        parts += [f"{k}={v}" for k, v in params.items()]
        parts.append(signed)
        return base64.b64encode(hashlib.sha1("&".join(parts).encode()).digest()).decode()

    def _enc_params(self, url, method, signed, nonce, params) -> dict:
        params["rc4_hash__"] = self._enc_signature(url, method, signed, params)
        for key, value in list(params.items()):
            params[key] = self._encrypt(signed, value)
        params.update(
            {
                "signature": self._enc_signature(url, method, signed, params),
                "ssecurity": self.ssecurity,
                "_nonce": nonce,
            }
        )
        return params

    @staticmethod
    def _rc4(key: str):
        cipher = ARC4.new(base64.b64decode(key))
        cipher.encrypt(bytes(1024))
        return cipher

    @classmethod
    def _encrypt(cls, key: str, payload: str) -> str:
        return base64.b64encode(cls._rc4(key).encrypt(payload.encode())).decode()

    @classmethod
    def _decrypt(cls, key: str, payload: str) -> str:
        return cls._rc4(key).decrypt(base64.b64decode(payload)).decode()

    def get_homes(self, server: str) -> dict | None:
        return self.execute(
            self.api_url(server) + "/v2/homeroom/gethome",
            {
                "data": '{"fg": true, "fetch_share": true, "fetch_share_dev": true, '
                '"limit": 300, "app_ver": 7}'
            },
        )

    def get_shared(self, server: str) -> dict | None:
        return self.execute(
            self.api_url(server) + "/v2/user/get_device_cnt",
            {"data": '{"fetch_own": true, "fetch_share": true}'},
        )

    def get_devices(self, server: str, home_id: int, owner_id: int) -> dict | None:
        return self.execute(
            self.api_url(server) + "/v2/home/home_device_list",
            {
                "data": f'{{"home_owner": {owner_id}, "home_id": {home_id}, '
                '"limit": 200, "get_split_device": true, "support_smart_home": true}'
            },
        )


def collect(login: XiaomiLogin) -> dict:
    found: dict[str, list] = {}
    for server in SERVERS:
        homes = login.get_homes(server)
        targets = []
        if homes and homes.get("result"):
            for home in homes["result"].get("homelist", []):
                targets.append((home["id"], login.user_id))
        shared = login.get_shared(server)
        if shared and shared.get("result"):
            for home in (shared["result"].get("share") or {}).get("share_family", []):
                targets.append((home["home_id"], home["home_owner"]))

        devices = []
        for home_id, owner in targets:
            data = login.get_devices(server, home_id, owner)
            if not data or not data.get("result"):
                continue
            devices += data["result"].get("device_info") or []

        if devices:
            print(f"  {server}: {len(devices)} dispositivo(s)")
            found[server] = devices
            for d in devices:
                tok = d.get("token") or ""
                print(
                    f"    - {d.get('name')!r}  model={d.get('model')}  "
                    f"did={d.get('did')}  ip={d.get('localip')}  "
                    f"token={tok[:4]}...{tok[-4:] if tok else ''}"
                )
        else:
            print(f"  {server}: sin dispositivos")
    return found


def main() -> int:
    mode = "qr" if "--password" not in sys.argv else "password"
    print(f"Login en la nube de Xiaomi (metodo: {mode})\n")

    login = XiaomiLogin()
    if mode == "qr":
        ok = login.login_qr()
    else:
        username = input("usuario (email, telefono o Mi ID): ").strip()
        password = getpass.getpass("contrasena (no se muestra): ")
        ok = login.login_password(username, password)

    if not ok:
        print("\nLogin no completado.")
        return 1

    print()
    found = collect(login)
    if not found:
        print("\nNo se encontro ningun dispositivo.")
        return 1

    SECRETS.mkdir(exist_ok=True)
    out = SECRETS / "devices.json"
    out.write_text(json.dumps(found, indent=2))
    os.chmod(out, stat.S_IRUSR | stat.S_IWUSR)

    session_out = SECRETS / "session.json"
    session_out.write_text(
        json.dumps(
            {
                "user_id": login.user_id,
                "cuser_id": login.cuser_id,
                "ssecurity": login.ssecurity,
                "service_token": login.service_token,
                "saved_at": int(time.time()),
            },
            indent=2,
        )
    )
    os.chmod(session_out, stat.S_IRUSR | stat.S_IWUSR)
    print(f"Guardado en {out} y {session_out} (chmod 600)")

    for devices in found.values():
        for d in devices:
            if str(d.get("model", "")).startswith("xiaomi.vacuum"):
                print(
                    f"\nAspiradora: {d.get('name')} / {d.get('model')}\n"
                    f"  ip    = {d.get('localip')}\n"
                    f"  did   = {d.get('did')}\n"
                    f"  token = {d.get('token')}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
