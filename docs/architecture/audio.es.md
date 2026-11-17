# Pipeline de audio

## Dos carriles, nunca mezclados

El cliente de captura abre dos flujos independientes y los mantiene separados durante todo
el recorrido hasta la transcripción:

| Carril | Fuente | Quién habla |
|---|---|---|
| `mic` | Entrada de micrófono | Tú |
| `far` | Loopback de la salida del sistema (lo que reproduce la app de reunión) | Los demás |

Es la decisión más valiosa del pipeline, porque vuelve la diarización gratuita y exacta.
No hay diarizador neuronal, ni clustering, ni modelo de embeddings, ni una clase de error
donde el sistema te atribuya a ti la pregunta del entrevistador. El sistema operativo ya
sabe quién habla; capturar los carriles por separado simplemente evita tirar esa
información.

También aporta el disparador de las sugerencias automáticas. «La otra parte dejó de
hablar» es un evento de actividad de voz del carril `far` en solitario: trivial de
detectar, e imposible de expresar limpiamente sobre un flujo mezclado.

### Backends por plataforma

| Plataforma | `mic` | `far` |
|---|---|---|
| Linux | Fuente de PulseAudio (`parec`), o PipeWire | Monitor del sink por defecto (`@DEFAULT_MONITOR@`) |

**macOS no está construido.** `capture/backends/` tiene `file`, `pulse` y `pipewire`; no hay
backend de CoreAudio. Construirlo es una decisión abierta. Si se hace, macOS no tiene
loopback de sistema propio, así que un dispositivo de audio virtual (BlackHole o
equivalente) sería un requisito de instalación. En Linux el monitor existe por defecto y no
necesita software extra.

## Formato y transporte

Cada carril se remuestrea a **16 kHz mono**, se codifica con **Opus** a unos 24 kbit/s y
se envía como tramas binarias de WebSocket. 16 kHz es lo que consumen los modelos de voz;
mandar más desperdicia ancho de banda y acaba remuestreado igual.

Cada trama lleva un paquete de 20 ms precedido por la marca de tiempo monótona de captura
del cliente, para que el núcleo pueda ordenar y alinear carriles sin fiarse del momento de
llegada. Ver [Protocolo](protocol.md#extremo-de-captura).

## Detección de actividad de voz

[Silero VAD](https://github.com/snakers4/silero-vad) corre por carril en el núcleo, en
`core/vad.py`, por delante del reconocedor. Va en el núcleo y no en el cliente de captura
por la misma razón que todo lo demás: el cliente se mantiene trivial de portar, y un
detector de 2 MB no cuesta nada al lado de los modelos ya residentes.

Produce dos cosas:

1. **Fronteras de segmento**, para que una pasada de transcripción reciba voz y no
   silencio.
2. **Eventos de fin de turno en el carril `far`**, que arrancan una sugerencia antes de que
   nadie la pida.

Lo segundo es la razón de que sea un componente aparte y no algo delegado al reconocedor.
faster-whisper tiene su propio filtro de VAD y responde bastante bien a la primera
pregunta, pero solo como efecto secundario de una pasada de transcripción que corre
aproximadamente una vez por segundo. Una sugerencia especulativa que espere a eso ya se
gastó casi todo su presupuesto de latencia.

### Histéresis en ambos sentidos

Una probabilidad cruda por ventana no sirve como evento. Ambos flancos se suavizan, por
motivos distintos:

| Flanco | Por defecto | Por qué |
|---|---|---|
| Abrir una locución | 2 ventanas (~64 ms) | Una puerta que se cierra no es voz. |
| Cerrarla | 24 ventanas (~768 ms) | Las pausas dentro de una frase son más largas de lo que la gente cree. Con 16 (~512 ms) una pregunta grabada se cortó en su coma. |

El número de cierre es el que importa. Si se pone demasiado bajo, el fin de turno salta
durante una respiración, a mitad de frase: el fallo más molesto que puede tener este
componente, porque produce una sugerencia confiada sobre media pregunta.

### Altavoces

Con altavoces en lugar de auriculares, el micrófono oye a la otra persona un instante después
de que lo haga su propio carril, y el reconocedor apunta sus palabras como si fueran del usuario.
El carril `far` es una copia limpia de lo que suenan los altavoces, así que mientras está alto,
un fotograma del micrófono que no sea claramente más fuerte que él se sustituye por silencio
antes de que lo vea el detector o el reconocedor (`core/bleed.py`). La prueba es relativa, no un
nivel fijo, porque la fuga y el carril `far` suben y bajan juntos con el volumen.

| Ajuste | Por defecto | Significado |
|---|---|---|
| `[vad] bleed_ratio` | 2.0 | Los fotogramas del micrófono por debajo de este múltiplo del nivel del carril `far` (retenido 0.6 s) se silencian. `0` lo desactiva. |
| `[vad] bleed_far_floor` | 250 | RMS bruto por debajo del cual el carril `far` cuenta como silencioso y la regla se aparta. Depende de la ganancia de captura; ajustado con dos llamadas. |

En la llamada grabada desaparecieron tres de las once líneas del carril `mic` que el usuario nunca dijo (una
fue "nos vemos en el próximo vídeo", inventada a partir de la voz lejana) y se conservaron todas
las que dijo el usuario, con la misma tasa de error. En una mezcla sintética de la voz del usuario
sobre la voz lejana, el habla a la mitad del nivel habitual se conservó en el 95 % de los
fotogramas con 2.0, y a una quinta parte, en el 57 %: un usuario que hable bajo encima del otro
puede perder palabras. La regla también depende de dónde esté el micrófono: pegado a los
altavoces, la fuga es más fuerte de lo que el ratio espera y pasa. Los auriculares siguen siendo
la mejor respuesta. Una grabación; son mediciones, no garantías.

### Ejecutarlo sin torch

El paquete `silero-vad` declara una dependencia dura de torch, que es una cosa enorme para
instalar por un modelo de 2 MB, en una máquina ya justa de memoria. Su wheel trae además el
fichero ONNX, así que Susurro carga ese directamente con `onnxruntime`:

```bash
pip install --no-deps silero-vad onnxruntime
```

Hay un umbral de energía disponible como `backend = "energy"` para instalaciones sin el
modelo. Es honesto sobre lo que es: una prueba de volumen que no distingue una voz de un
portazo. Existe porque es determinista, que es lo que lo hace útil para los tests, y es un
respaldo razonable en una línea silenciosa. `backend = "off"` desactiva la detección de fin
de turno por completo, lo que deja las sugerencias manuales y todo lo demás funcionando.

## Reconocimiento de voz

El reconocedor consume una **ventana deslizante con solapamiento**, no bloques fijos e
independientes. Los bloques fijos cortan palabras en la frontera, y los modelos de la
familia Whisper alucinan con seguridad sobre bloques que contienen solo silencio o un
fragmento. La ventana avanza aproximadamente cada segundo y vuelve a decodificar una
región solapada, de modo que una palabra partida en la frontera se ve entera en la pasada
siguiente.

### El reconocimiento corre fuera del bucle de eventos

Una pasada de transcripción tarda cientos de milisegundos. Ejecutada en línea sobre el
bucle de eventos del servidor, bloquea la ingesta de audio y con ella la consola: las
tramas se acumulan detrás del reconocedor y todo parece congelado mientras trabaja.

Por eso `Recognizer.feed` y `flush` se llaman en un hilo de trabajo, serializados por un
cerrojo: un modelo no es seguro de llamar desde dos hilos a la vez, y serializar también
mantiene en orden los segmentos de los dos carriles.

`warmup()` carga los pesos una vez en `session.start`, antes de que nada informe de que la
sesión está en vivo. La primera ejecución descarga el modelo, lo que tarda minutos; hacerlo
con el audio ya llegando es indistinguible de un cuelgue. Mientras ocurre, el núcleo
informa `stt: "loading"` en su estado para que la consola pueda decirlo.

Esto estuvo mal un tiempo y ningún test lo detectó, porque el reconocedor con guion que usan
los tests devuelve al instante. Un fake más rápido que lo real esconde justo esta clase de
error.

Cada carril produce dos tipos de salida:

- `transcript.partial` — la mejor conjetura actual, reemplazada en cada pasada, dibujada
  en gris en la consola.
- `transcript.final` — texto confirmado de un segmento cerrado, nunca revisado.

Solo los finales entran en el contexto del modelo de lenguaje. Meter los parciales
supondría reescribir el prompt en cada pasada, lo que destruye la caché de prefijo de la
que depende la
[Inferencia](inference.md#la-cache-de-prompt-es-un-requisito-no-una-optimizacion).

### El detector decide qué oye el reconocedor

No todas las tramas llegan al reconocedor. Mientras nadie habla se retienen; cuando el detector
abre un turno se liberan primero los últimos 300 ms (el detector necesita unas ventanas para estar
seguro y el primer sonido de la frase ya pasó), y el turno se cierra en el momento en que el detector
lo oye terminar, no cuando vence el temporizador de silencio del reconocedor. Ese cierre ocurre antes
de que nada reaccione al fin del turno, así que un hint especulativo se escribe sobre una transcripción
que ya contiene la frase a la que responde.

No es solo orden. Si el reconocedor decodifica todos los carriles de forma continua, silencio incluido,
re-decodifica una ventana de hasta 12 s cada segundo en ambos carriles. Un servidor whisper.cpp atiende
una petición a la vez, así que en una llamada grabada la cola creció hasta que una petición agotó su
tiempo, y el silencio es también de donde estos modelos inventan palabras. Con esa grabación, el mismo
audio pasó de 75 peticiones que ocupaban al servidor 43 s para 37 s de audio a 15 peticiones y 8 s.

Con `backend = "off"` no hay detector y el reconocedor lo oye todo, como antes. Los tiempos de los
segmentos siguen una única línea de tiempo de la sesión, compartida por los dos carriles: cada trama
se sella al llegar, en segundos desde `session.start`, y nunca antes de una trama después de la
anterior del mismo carril. Siguen contando durante el silencio que nunca se decodificó y durante los
huecos (una reconexión, tramas perdidas), y un carril que se conecta tarde empieza donde va la
llamada, no en cero.

### Leer no es reconocer

El socket de un carril lo lee una tarea y su audio lo reconoce otra, unidas por una cola. Cuando el
propio bucle de lectura esperaba al reconocedor, dejaba de leer siempre que Whisper iba lento, la
biblioteca de websockets dejaba de responder a los pings con él, y el cliente de captura daba el carril
por muerto tras 20 s de keepalive. Un test lo fija: con el reconocedor atascado, un ping se responde. Lo
que ya se había recibido se transcribe igualmente al cerrarse un carril. Por su parte, el cliente
reintenta un carril que el core aún cree ocupado por su propia conexión muerta, en vez de salir.

### Quedarse atrás del tiempo real

El intervalo de parciales cuenta audio, y el audio sigue llegando mientras una decodificación lenta
corre. Con un servidor lento, los parciales se acumulan unos tras otros y el reconocimiento se atrasa
cada segundo más. Por eso cada carril compara el audio procesado con el tiempo real transcurrido; si
va más de 1.5 s por detrás, deja de pedir parciales hasta estar a menos de 0.5 s. Los finales nunca se
retienen: un parcial es una cortesía para la pantalla, y el final es lo que alimenta al hint.

Medido reproduciendo una llamada grabada a tiempo real con el servidor tardando ~1.5 s por petición,
como en vivo: los finales llegaban 59 s tarde de media sin esto y 6 s con ello. Es una cota, no una
cura: lo que queda son las propias decodificaciones finales, cada una con un coste fijo de codificador
de aproximadamente medio segundo, sea cual sea la ventana. `python -m bench.replay_call` lo reproduce
con cualquier grabación de dos carriles.

### Vocabulario del perfil

Un reconocedor decodifica hacia lo común, así que las palabras que más importan en una sesión son las
que oye como otra cosa: "Modbus TCP" salió como "Mobbus", "TimescaleDB" como "Tyscale de B". El perfil
ya contiene el texto que las nombra. `core/session/vocabulary.py` extrae términos candidatos por su forma
(mayúsculas dentro de una palabra, dígitos, siglas, palabras con mayúscula que no abren frase) y el core
se los entrega al reconocedor al iniciar la sesión como prompt inicial. En la llamada grabada en español,
el error de palabras del carril del micrófono pasó de 13.6 % a 5.5 % y los términos clave de 7/9 a 9/9,
sin que el prompt se filtrara a la transcripción. Una llamada, unas 100 palabras por carril: una medición,
no un benchmark.

### Un solo idioma por sesión

Una sesión está en exactamente un idioma, declarado en el perfil antes de que la sesión
arranque. Las sesiones son unas veces en español y otras en inglés; una misma llamada
nunca cambia de una a otra.

Vale la pena enunciar ese supuesto de forma explícita, porque compra algo real. Un sistema
que tiene que sobrevivir a un cambio de idioma a mitad de frase se ve obligado a ejecutar
un reconocedor multilingüe siempre y a pagar su coste en todas las sesiones. Un sistema
que conoce el idioma de antemano puede elegir el reconocedor más barato que lo cubra.

### Elección de modelo

| Modelo | Idiomas | Papel |
|---|---|---|
| Parakeet TDT 0.6B | Solo inglés | Ruta rápida para `language: "en"`. Bastante más rápido que Whisper cuando solo hay inglés. |
| `whisper-large-v3-turbo` | Multilingüe | Se usa para `language: "es"` y como alternativa para todo lo que Parakeet no cubre. |

El reconocedor se selecciona a partir del campo `language` del perfil en `session.start` y
no cambia mientras la sesión corre. Cambiarlo supondría intercambiar un modelo cargado a
mitad de conversación, lo que cuesta segundos en una máquina sin memoria de sobra para
mantener los dos.

[M0](../roadmap.md#m0-benchmark) mide ambos reconocedores como opciones de primera clase,
en lugar de tratar el de inglés como una nota al pie. Si Parakeet resulta
apreciablemente más rápido, las sesiones en inglés recuperan ese margen y pueden gastarlo
en un modelo de lenguaje mayor o en un contexto más largo.

!!! note "Qué pasa si el supuesto se rompe"
    A quien cambie de idioma a mitad de llamada se le transcribirá mal, en lugar de
    detectarlo y gestionarlo. Es un canje deliberado, no un descuido. La recuperación es
    manual: parar la sesión, cambiar el perfil y volver a empezar.

## Presupuesto de latencia

De extremo a extremo, desde que la otra parte termina una frase hasta el primer token útil
en tu pantalla:

| Etapa | Objetivo |
|---|---|
| Captura + Opus + LAN | < 50 ms |
| Detección de fin de turno por VAD | ~100 ms |
| Final del reconocedor para el último segmento | 300–500 ms |
| Tiempo hasta el primer token del modelo (caché caliente) | < 1000 ms |
| **Total** | **< 2 s** |

Dos segundos es el techo de una sugerencia que todavía vale la pena leer. El reconocedor y
el modelo se reparten aproximadamente la mitad del presupuesto cada uno, y la mitad del
modelo es la que está en riesgo — ver [Inferencia](inference.md).
