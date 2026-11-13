# Puesta en marcha

!!! info "Todavía no hay nada que ejecutar"
    Susurro está en pre-M0. Esta página describe la estructura prevista y los comandos
    que van a existir, para acordar la forma antes de que aterrice el código. Tómalo como
    un contrato para [M0](../roadmap.md#m0-benchmark) y
    [M1](../roadmap.md#m1-espina-dorsal), no como instrucciones que funcionen hoy.

## Estructura del repositorio

```
susurro/
├── core/
│   ├── protocol.py    # mensajes, framing, validación. Sin E/S.
│   ├── audio.py       # decodificación Opus, un decoder por carril
│   ├── vad.py         # actividad de voz por carril; fin de turno en el carril far
│   │                  # session/ también tiene history.py: grabación y exportación
│   ├── server.py      # ambos extremos WebSocket + la consola como ficheros estáticos
│   ├── main.py        # punto de entrada; backends elegidos por configuración
│   ├── stt/           # interfaz de reconocedor, fake con guion, faster-whisper
│   ├── llm/           # interfaz de generador, fake enlatado, cliente compatible con OpenAI
│   └── session/       # perfiles, armado del preámbulo, máquina de estados
├── capture/
│   ├── codec.py       # codificación Opus
│   ├── client.py      # un WebSocket por carril
│   ├── main.py
│   └── backends/      # file.py (reproducción), pipewire.py (nunca ejecutado)
├── console/           # interfaz web estática, sin build; state.js es puro
├── bench/             # arnés M0: metrics.py, report.py, run.py
│   └── results/       # mediciones versionadas, un fichero por ejecución
├── tests/
│   └── fixtures/profiles/   # sintéticos; los reales van en local/, ignorado por git
├── docs/              # esta documentación (page.md + page.es.md)
├── core.toml.example
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
.venv/bin/pip install -e ".[core,whisper,openai]"
cp core.toml.example core.toml   # y edítalo
.venv/bin/python -m core.main --config core.toml
```

Imprime la URL de la consola, token incluido, y los perfiles que encontró.

El núcleo se niega a escuchar en `0.0.0.0` sin `--allow-public-bind`. Se autentica con un
token compartido en un parámetro de consulta, que alcanza para una LAN de confianza y no
alcanza para nada más.

### Backends

Ambos se seleccionan por configuración y no por importación, que es lo que permite que el
mismo servidor corra contra fakes en una máquina sin modelos y contra los reales donde los
haya.

| Ajuste | Valores | Notas |
|---|---|---|
| `[stt] backend` | `fake`, `whisper` | `fake` reproduce un guion y no necesita modelo. |
| `[session] record` | `false`, `true` | Apagado por defecto. Encendido, las sesiones se escriben en `[session] directory`. |
| `[vad] backend` | `silero`, `energy`, `off` | `off` deja las sugerencias manuales. |
| `[llm] backend` | `fake`, `openai` | `openai` es cualquier servidor compatible con OpenAI: `llama.cpp`, Ollama, vLLM, LM Studio. |

Instala solo lo que uses: `.[core]` a secas te da los fakes, que bastan para trabajar en la
consola, el protocolo o el cliente de captura.

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

La consola le pregunta al núcleo qué perfiles existen y los ofrece en un selector, así que
el único parámetro que necesitas de verdad es el token. Los demás preseleccionan una
sesión, lo que sirve para un marcador que entre directo a la correcta:

| Parámetro | Significado |
|---|---|
| `token` | Token compartido del núcleo. Obligatorio. |
| `profile` | Identificador de perfil a configurar al pulsar **Start**. |
| `language` | `en` o `es`. Debe coincidir con el perfil. |
| `mode` | `conversational`, `technical` o `business`. |

**Espacio** pide una sugerencia: es la tecla más grande de cualquier teclado y la única que
puedes acertar sin mirar hacia abajo.

**History** lista las sesiones grabadas, muestra una transcripción en Markdown y la descarga
o la borra. Borrar cuesta dos clics: no se puede deshacer, y un diálogo pesaría más de lo
que la decisión merece. Cuando el núcleo está grabando, un indicador rojo se queda en la
cabecera toda la sesión: lo único que un usuario no debe tener que ir a buscar.

## Contribuir

El proyecto es AGPL-3.0 y público desde el primer commit. Las contribuciones más útiles al
principio son resultados de benchmark en hardware distinto del Mac mini de referencia: el
diseño está construido sobre las cifras de una sola máquina, y saber dónde se rompe en
otras vale más que las funcionalidades.
