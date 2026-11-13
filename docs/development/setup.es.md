# Puesta en marcha

!!! info "Todavía no hay nada que ejecutar"
    Susurro está en pre-M0. Esta página describe la estructura prevista y los comandos
    que van a existir, para acordar la forma antes de que aterrice el código. Tómalo como
    un contrato para [M0](../roadmap.md#m0-benchmark) y
    [M1](../roadmap.md#m1-espina-dorsal), no como instrucciones que funcionen hoy.

## Estructura del repositorio

```
susurro/
├── core/              # servidor de inferencia: VAD, STT, LLM, estado de sesión
│   ├── audio/         # manejo de carriles, VAD, remuestreo
│   ├── stt/           # backends de reconocimiento
│   ├── llm/           # backends de runtime, caché de prompt, presets
│   ├── session/       # perfiles, armado de contexto, máquina de estados
│   └── server.py      # extremos WebSocket + consola estática
├── capture/           # cliente ligero
│   ├── backends/      # file.py (reproducción), pipewire.py, coreaudio.py
│   └── main.py
├── console/           # interfaz web estática (sin paso de build)
├── bench/             # arnés del benchmark M0
│   └── results/       # mediciones versionadas
├── docs/              # esta documentación (en + .es.md)
└── mkdocs.yml
```

## Herramientas

Python para núcleo y captura. El ecosistema de reconocimiento de voz vive en Python, y
pelearse con eso para ganar unos milisegundos de arranque es un mal canje.

El cliente de captura también se queda en Python en v1. Codificar en Opus dos flujos mono
de 16 kHz es lo bastante barato como para que un N95 ni se entere. Si la medición dice
otra cosa, el cliente son unos cientos de líneas y reescribirlo en Rust es un trabajo
acotado — lo que en sí mismo es una razón para mantener limpia la frontera del protocolo.

La consola es HTML, CSS y JavaScript planos, sin paso de build. Dibuja una lista con
scroll y habla con un WebSocket; un framework añadiría una cadena de herramientas y no
resolvería nada.

## Ejecutar el núcleo

```bash
cd susurro
python3 -m venv .venv
.venv/bin/pip install -e ".[core]"
.venv/bin/python -m core.server --config core.toml
```

La configuración cubre dirección de escucha, token compartido, rutas de modelos y
selección de runtime. El núcleo se niega a escuchar en `0.0.0.0` sin una anulación
explícita, porque un servidor de inferencia con un secreto compartido en un parámetro de
consulta no es algo que se exponga por accidente.

## Ejecutar el cliente de captura

```bash
.venv/bin/python -m capture.main \
  --core ws://mac-mini.local:8723 \
  --token "$SUSURRO_TOKEN" \
  --mic default \
  --far @DEFAULT_MONITOR@
```

El backend se selecciona según la plataforma. `--far` acepta un nodo de PipeWire en Linux
o un nombre de dispositivo CoreAudio en macOS; ambos admiten `--list-devices` para
encontrar el correcto.

Sin dispositivos de audio a mano —un contenedor, una máquina remota, CI— reproduce un par
de ficheros WAV en su lugar. Tienen que ser 16 kHz mono de 16 bits:

```bash
.venv/bin/python -m capture.main \
  --core ws://localhost:8723 --token "$SUSURRO_TOKEN" \
  --replay-mic yo.wav --replay-far ellos.wav
```

La reproducción va a tiempo real por defecto, así que el núcleo ve el mismo patrón de
llegada que vería de un dispositivo. `--speed 0` reproduce tan rápido como el núcleo
acepte, que es lo que usan los tests.

## Abrir la consola

`http://mac-mini.local:8723/?token=…` desde el teléfono o la segunda pantalla. El núcleo
sirve la consola desde el mismo puerto que los extremos WebSocket, así que hay una sola
dirección que recordar.

Los parámetros de consulta seleccionan la sesión:

| Parámetro | Significado |
|---|---|
| `token` | Token compartido del núcleo. Obligatorio. |
| `profile` | Identificador de perfil a configurar al pulsar **Start**. |
| `language` | `en` o `es`. Debe coincidir con el perfil. |
| `mode` | `conversational`, `technical` o `business`. |

**Espacio** pide una sugerencia: es la tecla más grande de cualquier teclado y la única que
puedes acertar sin mirar hacia abajo.

## Contribuir

El proyecto es AGPL-3.0 y público desde el primer commit. Las contribuciones más útiles al
principio son resultados de benchmark en hardware distinto del Mac mini de referencia: el
diseño está construido sobre las cifras de una sola máquina, y saber dónde se rompe en
otras vale más que las funcionalidades.
