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
| Cerrarla | 16 ventanas (~512 ms) | Las pausas dentro de una frase son más largas de lo que la gente cree. |

El número de cierre es el que importa. Si se pone demasiado bajo, el fin de turno salta
durante una respiración, a mitad de frase: el fallo más molesto que puede tener este
componente, porque produce una sugerencia confiada sobre media pregunta.

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

Cada carril produce dos tipos de salida:

- `transcript.partial` — la mejor conjetura actual, reemplazada en cada pasada, dibujada
  en gris en la consola.
- `transcript.final` — texto confirmado de un segmento cerrado, nunca revisado.

Solo los finales entran en el contexto del modelo de lenguaje. Meter los parciales
supondría reescribir el prompt en cada pasada, lo que destruye la caché de prefijo de la
que depende la
[Inferencia](inference.md#la-cache-de-prompt-es-un-requisito-no-una-optimizacion).

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
