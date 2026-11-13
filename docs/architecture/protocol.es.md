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

`lane` es `mic` o `far`. El núcleo responde `{"type":"ready","protocol":1}` o envía un
error y cierra.

### Tramas de audio

Todo mensaje posterior es binario:

```
┌────────────────────────┬──────────────────────┐
│  marca de tiempo       │   paquete Opus       │
│  uint64 LE, microseg.  │   (20 ms, variable)  │
└────────────────────────┴──────────────────────┘
       8 bytes                   N bytes
```

El paquete Opus está limitado a 4000 bytes. El habla a 16 kHz mono queda muy por debajo;
el límite existe para que un flujo que se ha estropeado falle de forma ruidosa en lugar de
acabar en un modelo.

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
{"type":"profiles","profiles":[{"id":"backend-role-acme","label":"Acme — backend senior","language":"es","mode":"conversational"}]}
{"type":"profile","profile":{"id":"backend-role-acme","label":"Acme — backend senior","language":"es","mode":"conversational","context":{"resume":"…"}}}
{"type":"state","session":"live","stt":"ready","llm":"ready","lanes":{"mic":"up","far":"up"}}
{"type":"error","code":"lane_busy","detail":"lane 'mic' is already connected"}
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
{"type":"profiles.request"}
{"type":"profile.request","profile_id":"backend-role-acme"}
{"type":"profile.save","profile_id":"backend-role-acme","label":"Acme — backend senior","language":"es","mode":"conversational","context":{"resume":"…","role":"…","notes":"…"}}
```

`profiles` lleva solo etiquetas; `profile.request` trae uno completo, contexto incluido,
para el editor. `profile.save` lo escribe, creando el perfil si el identificador es nuevo,
y el núcleo responde con un listado fresco: primero a la consola que guardó y luego a todas
las demás, porque dos pantallas es una configuración normal y un selector obsoleto es una
trampa.

Un guardado se rechaza para el perfil con el que se construyó una sesión **en vivo**. Su
contexto ya está prefilleado en la caché del modelo, así que sobrescribirlo dejaría a la
consola mostrando texto que el modelo nunca ha visto, sin nada que lo indique. Los demás
perfiles se pueden editar a mitad de sesión.

!!! warning "Esto convierte el token en credencial de escritura"
    `profile.save` escribe ficheros en la máquina del núcleo. Quien alcance el puerto y
    tenga el token puede crear y sobrescribir perfiles. Los identificadores de perfil se
    rechazan si pudieran direccionar algo fuera del directorio de perfiles —comprobado aquí
    y en el almacén de perfiles— pero eso es contención, no autenticación. Ver
    [Visión general](overview.md#supuestos-de-red).

A una consola se la saluda con `profiles` antes que con `state`, para que pueda ofrecer una
elección en lugar de exigir que el identificador se sepa de antemano. `profiles.request`
vuelve a pedir el listado. Un perfil cuyo fichero no se puede leer se deja fuera y se
registra: un fichero malo no debe dejar la consola con un selector vacío.

`session.config` debe llegar antes de `session.start`, porque determina el preámbulo
estático que se prefillea en la
[caché de prompt](inference.md#la-cache-de-prompt-es-un-requisito-no-una-optimizacion).
Cambiarlo a mitad de sesión invalida esa caché y por tanto se rechaza, no se acepta en
silencio.

## Códigos de error

Todo error que envía el núcleo lleva uno de estos. La lista es exhaustiva y la comprueba un
test: un código en el código fuente que falte aquí, o uno aquí que nadie emita, rompe la
suite.

| Código | Significa |
|---|---|
| `unsupported_version` | El cliente habla una versión del protocolo que este núcleo no. Se comprueba antes que nada del mensaje, para que a un cliente más nuevo se le diga lo útil. |
| `malformed_message` | Un mensaje JSON no se pudo interpretar, tenía forma incorrecta o traía un campo desconocido. |
| `malformed_frame` | Una trama binaria era demasiado corta, tenía marca de tiempo fuera de rango, o carga vacía o excesiva. |
| `decode_failed` | Un paquete Opus no se pudo decodificar. Un paquete malo se reporta y se omite; el carril sigue en pie. |
| `lane_busy` | Ya hay algo conectado en ese carril. |
| `bad_transition` | Llegó un comando en una fase de sesión que no lo permite: una sugerencia sin sesión en vivo, `session.config` estando en vivo. |
| `bad_request` | Un perfil falta, es inválido, o no concuerda con el `session.config` que lo nombró. |
| `hint_failed` | La generación lanzó una excepción. El `hint.end` correspondiente lleva `reason: "error"`. |
| `protocol_error` | Código base; no se emite directamente. |

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
