# Protocolo

El núcleo expone dos extremos WebSocket y sirve la consola como ficheros estáticos. Puerto
por defecto `8723`.

Ambos extremos exigen un token compartido, pasado como `?token=…`. Es protección a nivel
de LAN solamente — ver [Visión general](overview.md#supuestos-de-red).

## Extremo de captura

`ws://core:8723/capture?token=…`

Del cliente al núcleo. Una conexión **por carril**, así que un cliente de captura abre
dos.

### Saludo

El primer mensaje es JSON y establece el carril:

```json
{
  "type": "hello",
  "protocol": 1,
  "session_id": "2026-11-13T14:02:11Z-a4f1",
  "lane": "mic",
  "codec": "opus",
  "sample_rate": 16000,
  "frame_ms": 20,
  "client": "susurro-capture-linux/0.1.0"
}
```

`lane` es `mic` o `far`. El núcleo responde `{"type":"ready"}` o cierra con un código de
error.

### Tramas de audio

Todo mensaje posterior es binario:

```
┌────────────────────────┬──────────────────────┐
│  marca de tiempo       │   paquete Opus       │
│  uint64 LE, microseg.  │   (20 ms, variable)  │
└────────────────────────┴──────────────────────┘
       8 bytes                   N bytes
```

La marca de tiempo sale del reloj monótono del cliente en el momento de captura, no del
reloj de pared. El núcleo la usa para ordenar dentro de un carril y para medir jitter; no
asume que los relojes de dos clientes coincidan.

!!! note "Pregunta abierta"
    La alineación de carriles entre máquinas distintas está sin resolver. En la
    configuración de referencia ambos carriles vienen del mismo cliente y por tanto
    comparten reloj, así que esto no bloquea [M1](../roadmap.md#m1-espina-dorsal). Se vuelve real
    si algún día los carriles se originan en máquinas distintas.

## Extremo de consola

`ws://core:8723/console?token=…`

JSON bidireccional. Sin tramas binarias.

### Del núcleo a la consola

```json
{"type":"transcript.partial","lane":"far","text":"cuéntame de una vez en que","t":12.40}
{"type":"transcript.final","lane":"far","text":"Cuéntame de una vez en que discrepaste con un compañero.","t":12.40,"dur":3.10,"seq":41}
{"type":"hint.start","hint_id":"h-93","trigger":"speculative","mode":"conversational"}
{"type":"hint.delta","hint_id":"h-93","text":"- Rollback de migración, Q2\n"}
{"type":"hint.end","hint_id":"h-93","reason":"complete","tokens":38,"ttft_ms":780}
{"type":"hint.end","hint_id":"h-93","reason":"cancelled"}
{"type":"state","session":"live","stt":"ready","llm":"ready","lanes":{"mic":"up","far":"up"}}
{"type":"error","code":"stt_overrun","detail":"recognizer fell behind by 2.3s"}
```

Un `hint.end` con `reason: "cancelled"` es el desenlace normal de una generación
especulativa que la otra parte interrumpió. La consola descarta la sugerencia parcial en
lugar de mostrarla.

### De la consola al núcleo

```json
{"type":"hint.request","mode":"conversational"}
{"type":"hint.cancel","hint_id":"h-93"}
{"type":"session.config","profile_id":"backend-role-acme","language":"es","mode":"conversational"}
{"type":"session.start"}
{"type":"session.stop"}
```

`session.config` debe llegar antes de `session.start`, porque determina el preámbulo
estático que se prefillea en la
[caché de prompt](inference.md#la-cache-de-prompt-es-un-requisito-no-una-optimizacion).
Cambiarlo a mitad de sesión invalida esa caché y por tanto se rechaza, no se acepta en
silencio.

## Perfiles

Un perfil es el contexto estático de una sesión, almacenado en el núcleo:

```json
{
  "id": "backend-role-acme",
  "label": "Acme — backend senior",
  "language": "es",
  "mode": "conversational",
  "context": {
    "resume": "…",
    "role": "…",
    "notes": "…"
  }
}
```

`language` es `en` o `es`, y es toda la configuración de idioma que hay: una sesión está en
un idioma de principio a fin. El núcleo lo usa para seleccionar el reconocedor en
`session.start` —ver [Un solo idioma por sesión](audio.md#un-solo-idioma-por-sesion)— y
para elegir el prompt de sistema en el que se escriben las sugerencias. No hay detección
automática ni cambio a mitad de sesión; cambiar de idioma significa parar y arrancar una
sesión nueva.

`mode` es `conversational`, `technical` o `business`, y fija la forma de las sugerencias,
no su contenido.

Los perfiles son ficheros planos en disco. No hay base de datos, porque no hay problema de
escala que resolver: un usuario tiene un puñado de perfiles, no miles.
