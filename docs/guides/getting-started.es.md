# Primeros pasos

Montar Susurro de extremo a extremo: una máquina ejecutando los modelos, otra capturando
audio, y un teléfono o segunda pantalla mostrando las sugerencias.

Lee [Hardware](../hardware.md) primero si no tienes claro que tu máquina pueda con esto.
En corto: Apple Silicon con 16 GB, o un equipo x86 con 8 GB de VRAM. Por debajo de eso, el
modelo de voz y el de lenguaje no se mantienen residentes a la vez.

!!! warning "Nada de esto está medido todavía"
    Estas instrucciones funcionan. Si funcionan *lo bastante rápido* en tu hardware es lo
    que [M0](../roadmap.md#m0-benchmark) existe para responder, y no se ha ejecutado. Si
    las sugerencias llegan tarde para servir, eso es lo que hay que medir antes de cambiar
    cualquier otra cosa.

## 1. Pruébalo sin ningún modelo

Haz esto primero, incluso si tienes el hardware. Cuesta dos minutos, demuestra que la
fontanería funciona, y significa que un problema posterior es un problema de modelo y no
un misterio.

```bash
git clone https://github.com/vladimir1284/susurro
cd susurro
python3 -m venv .venv
.venv/bin/pip install -e ".[core]"
.venv/bin/python -m core.main
```

Imprime una URL de consola con un token dentro. Ábrela en la misma máquina.

Todavía no hay perfiles, así que **Start** está desactivado. Pulsa **New**, dale una
etiqueta y algo de texto de fondo, y pulsa **Save**. Start ya está disponible.

Pulsa **Start** y luego **Hint**. Aparece texto enlatado. No se transcribió ni se generó
nada: el núcleo trae un reconocedor con guion y un generador enlatado para que la consola,
el protocolo y el cliente de captura se puedan trabajar sin modelos. Eso es el sistema
entero salvo las dos piezas caras.

## 2. Instala los modelos

```bash
.venv/bin/pip install -e ".[core,whisper,openai,vad]"
.venv/bin/pip install --no-deps silero-vad
```

La última línea va aparte a propósito. `silero-vad` declara dependencia de torch, y solo se
usa el fichero ONNX de 2 MB que trae, así que instalar torch por él sería absurdo.

Después arranca un servidor de modelo de lenguaje. Cualquiera de estos habla la API que
Susurro espera:

```bash
llama-server -m <modelo>.gguf -c 8192 --port 8080    # llama.cpp
mlx_lm.server --model <repo> --port 8080             # MLX, Apple Silicon
```

Un modelo de 8B cuantizado a 4 bits es el techo en una máquina de 16 GB; el porqué está en
[Inferencia](../architecture/inference.md#la-restriccion-que-manda-es-la-memoria).

## 3. Configura el núcleo

```bash
cp core.toml.example core.toml
python3 -c "import secrets; print(secrets.token_urlsafe(16))"   # un token
```

Edita `core.toml`: pon el token, pon `[stt] backend = "whisper"` y
`[llm] backend = "openai"`, y comprueba que `[llm] base_url` coincide con el servidor que
arrancaste.

Pon `[stt] language` al idioma que vas a hablar de verdad. Una sesión está en un idioma de
principio a fin — ver [Pipeline de audio](../architecture/audio.md#un-solo-idioma-por-sesion).

Deja `[session] record = false` salvo que quieras transcripciones escritas a disco. Está
apagado por defecto a propósito.

```bash
.venv/bin/python -m core.main --config core.toml
```

La primera ejecución descarga el modelo de voz, lo que tarda unos minutos. Ocurre al pulsar
**Start**, y la consola muestra la sesión como cargando hasta que termina: es normal, no un
cuelgue.

## 4. Captura el audio

En la máquina que aloja la reunión, **no** en la que ejecuta los modelos, si puedes
evitarlo. 16 GB alcanzan para los modelos o para una videollamada, no cómodamente para
ambos.

=== "Linux"

    ```bash
    .venv/bin/python -m capture.main --list-devices
    .venv/bin/python -m capture.main \
      --core ws://<host-del-nucleo>:8723 --token "$SUSURRO_TOKEN" \
      --mic <tu nodo de micrófono> \
      --far <el monitor de tu sink de salida>
    ```

    El carril `far` es el **monitor** del sink por el que reproduce tu aplicación de
    reunión. Ese es el flujo que lleva la voz de los demás.

=== "macOS"

    macOS no tiene captura del audio del sistema. Instala
    [BlackHole](https://github.com/ExistentialAudio/BlackHole), ponlo como salida que usa
    tu aplicación de reunión, y pásalo como `--far`.

=== "Sin dispositivos de audio"

    ```bash
    .venv/bin/python -m capture.main \
      --core ws://localhost:8723 --token "$SUSURRO_TOKEN" \
      --replay-mic yo.wav --replay-far ellos.wav
    ```

    WAV mono de 16 kHz y 16 bits. Útil para pruebas, para máquinas sin hardware de sonido,
    y para reproducir un problema de transcripción contra el mismo audio dos veces.

Los indicadores `mic` y `far` de la consola se ponen verdes según conecta cada carril.

## 5. Abre la consola en otro sitio

`http://<host-del-nucleo>:8723/?token=…` en un teléfono o un segundo monitor.

Que sea otro sitio es justamente el punto. Las sugerencias en la pantalla que compartes las
ve todo el mundo en la llamada.

Elige un perfil, pulsa **Start** y habla. La transcripción aparece en dos carriles. Una
sugerencia empieza a escribirse sola cuando la otra parte deja de hablar, o de inmediato si
pulsas **Espacio**.

## Cuando no funciona

| Síntoma | Causa probable |
|---|---|
| La consola no conecta | Token que no coincide, o el núcleo escuchando en loopback y tú en otra máquina. Se niega a `0.0.0.0` sin `--allow-public-bind`. |
| Un carril nunca se pone verde | El cliente de captura murió. Ejecútalo en primer plano y lee su error. |
| `lane_busy` | Ya hay algo conectado en ese carril, normalmente un cliente de captura que olvidaste parar. |
| La transcripción está vacía con los carriles arriba | Dispositivo `far` equivocado: estás capturando silencio. `--list-devices` y prueba el otro monitor. |
| La transcripción se atrasa cada vez más | El modelo de voz va más lento que el tiempo real en ese hardware. Prueba uno más pequeño y ejecuta el benchmark. |
| Las sugerencias tardan segundos | Casi seguro el prefill. Ver [Inferencia](../architecture/inference.md#la-cache-de-prompt-es-un-requisito-no-una-optimizacion). |
| Las sugerencias salen en el idioma equivocado | El idioma del perfil, no el del núcleo. Una sesión lo toma del perfil. |
| No hay sugerencia cuando la otra parte calla | `[vad] backend = "off"`, o `silero-vad` sin instalar y el respaldo por energía no los oye. |

## Qué no hace esto

Susurro no se oculta de la pantalla compartida, no evade software de supervisión y no
esquiva la detección. La consola corre en un segundo dispositivo porque las sugerencias no
deben estar en tu pantalla compartida: una consecuencia del diseño, no una función de
sigilo.
