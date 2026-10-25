# Panel web de la H40

Servidor local (Flask) que controla la aspiradora por LAN. Pensado para el móvil.

```bash
.venv/bin/python web/app.py            # http://<ip-del-mac>:8787
.venv/bin/python web/app.py --port 9000
```

Al arrancar imprime la URL y un QR para abrirlo desde el teléfono.

## Qué incluye

- Estado en vivo cada 2,5 s: batería, estado, progreso, superficie, tiempo,
  depósito sucio y limpio, mopa.
- **Mando** con joystick (arriba / izquierda / derecha; este modelo no tiene
  marcha atrás). Botón `PARAR` que además sale del modo mando.
- **Modos**: aspiración (4), agua (4), tipo de limpieza (4), pasadas (1-3).
- **Acciones**: empezar, pausar, reanudar, volver a la base, limpiar un punto.
- **Mapa** en vivo (posición + recorrido), cacheado 45 s.

## API

| Método | Ruta | Cuerpo |
|---|---|---|
| GET | `/api/state` | — |
| POST | `/api/action` | `{"name": "start\|stop\|dock\|pause\|resume\|spot\|enter_remote\|exit_remote"}` |
| POST | `/api/set` | `{"name": "mode\|water\|sweep_mop\|times", "value": 3}` |
| POST | `/api/move` | `{"dir": "up\|left\|right", "op": "press\|lift"}` |
| GET | `/api/rooms` | — |
| GET | `/api/map.png` | — |

## Notas

- **Sin autenticación**: cualquiera en tu Wi-Fi puede usarlo. No lo expongas a internet.
- Todo va por LAN salvo `/api/map.png`, que necesita la sesión de nube.
- Las acciones físicas (empezar, mando) están sin probar a propósito: mueven y
  hacen ruido. El resto (lecturas y ajustes) está verificado.
- El firmware corta si se piden más de ~12 propiedades por paquete; el panel
  trocea en lotes de 10.
