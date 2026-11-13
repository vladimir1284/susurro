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
| Linux | Fuente de PipeWire | Monitor PipeWire del sink por defecto |
| macOS | Entrada de CoreAudio | Dispositivo de loopback ([BlackHole](https://github.com/ExistentialAudio/BlackHole) o equivalente) |

macOS no tiene loopback de sistema propio, así que un dispositivo de audio virtual es un
requisito de instalación, no algo que Susurro pueda evitar. En Linux el monitor existe por
defecto y no necesita software extra.

## Formato y transporte

Cada carril se remuestrea a **16 kHz mono**, se codifica con **Opus** a unos 24 kbit/s y
se envía como tramas binarias de WebSocket. 16 kHz es lo que consumen los modelos de voz;
mandar más desperdicia ancho de banda y acaba remuestreado igual.

Cada trama lleva un paquete de 20 ms precedido por la marca de tiempo monótona de captura
del cliente, para que el núcleo pueda ordenar y alinear carriles sin fiarse del momento de
llegada. Ver [Protocolo](protocol.md#extremo-de-captura).

## Detección de actividad de voz

[Silero VAD](https://github.com/snakers4/silero-vad) corre por carril en el núcleo, no en
el cliente. Mantenerlo del lado del servidor deja el cliente de captura trivial de portar,
y el modelo de VAD no cuesta casi nada al lado de los modelos ya residentes.

El VAD produce dos cosas que el resto del pipeline necesita:

1. **Fronteras de segmento** para el reconocedor, de modo que transcriba voz y no
   silencio.
2. **Eventos de fin de turno** en el carril `far`, que disparan la generación especulativa
   de sugerencias.

## Reconocimiento de voz

El reconocedor consume una **ventana deslizante con solapamiento**, no bloques fijos e
independientes. Los bloques fijos cortan palabras en la frontera, y los modelos de la
familia Whisper alucinan con seguridad sobre bloques que contienen solo silencio o un
fragmento. La ventana avanza aproximadamente cada segundo y vuelve a decodificar una
región solapada, de modo que una palabra partida en la frontera se ve entera en la pasada
siguiente.

Cada carril produce dos tipos de salida:

- `transcript.partial` — la mejor conjetura actual, reemplazada en cada pasada, dibujada
  en gris en la consola.
- `transcript.final` — texto confirmado de un segmento cerrado, nunca revisado.

Solo los finales entran en el contexto del modelo de lenguaje. Meter los parciales
supondría reescribir el prompt en cada pasada, lo que destruye la caché de prefijo de la
que depende la
[Inferencia](inference.md#la-cache-de-prompt-es-un-requisito-no-una-optimizacion).

### Elección de modelo

| Modelo | Idiomas | Notas |
|---|---|---|
| `whisper-large-v3-turbo` | Multilingüe | Por defecto. Necesario para español y para reuniones con mezcla de idiomas. |
| Parakeet TDT 0.6B | Solo inglés | Bastante más rápido cuando solo hay inglés. Vale la pena medirlo en [M0](../roadmap.md#m0-benchmark) como opción solo-inglés. |

El valor por defecto es multilingüe, porque un copiloto que transcribe mal la única
reunión que se pasó al español no es un copiloto en el que puedas confiar.

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
