# Hoja de ruta

Cada hito termina en una **puerta**: una condición medible que decide si empieza el
siguiente. Una puerta que falla no es motivo para seguir adelante y confiar en la suerte;
es motivo para cambiar el diseño mientras cambiarlo todavía es barato.

M0 a M3 juntos son el producto mínimo real. Todo lo que viene después es incremento.

---

## M0 — Benchmark

**Medio día. No se construye nada más hasta terminarlo.**

Toda la arquitectura descansa sobre cifras de inferencia que hoy son estimaciones.
Escribir un cliente de captura antes de saber si el modelo puede responder en menos de un
segundo es construir sobre una conjetura.

Medir en el Mac mini de referencia:

- Factor de tiempo real del reconocimiento de voz para `whisper-large-v3-turbo` a q8, y
  para Parakeet TDT como alternativa solo-inglés.
- Rendimiento de prefill del modelo de lenguaje con contexto de 1k y 4k, en frío.
- Prefill con caché de prefijo caliente, midiendo solo el delta.
- Rendimiento de decodificación, sostenido.
- Memoria residente pico con ambos modelos cargados.
- Todo lo anterior para `llama.cpp` y para `mlx-lm`.

Entregable: un script en `bench/` que produce un resultado JSON, y una página de
resultados en esta documentación.

!!! success "Puerta"
    Tiempo hasta el primer token con caché caliente, 2k de contexto estático más 100
    tokens de delta: **menos de 1 segundo**. Ambos modelos residentes en **menos de
    12 GB**.

    Si falla el TTFT, la respuesta es acortar el prompt o bajar a un modelo más pequeño,
    no seguir y descubrirlo más tarde. Si falla la memoria, el supuesto de 8B era
    equivocado y el escalón de modelo baja antes de que ningún código dependa de él.

---

## M1 — Espina dorsal

**Uno o dos días.**

Ruta de audio de extremo a extremo sin modelo de lenguaje: captura en macOS de ambos
carriles, Opus por WebSocket, VAD, reconocimiento en streaming, consola dibujando dos
carriles de transcripción.

Esto demuestra la parte difícil de la fontanería —dos flujos independientes, semántica de
transcripción parcial y final, una consola que se actualiza sin parpadeo— mientras los
modos de fallo siguen siendo de audio y no de prompts.

!!! success "Puerta"
    Retraso de transcripción por debajo de **1 segundo** respecto al habla en vivo, en
    ambos carriles, sostenido durante una llamada de diez minutos sin que el reconocedor
    se quede atrás.

---

## M2 — Sugerencia manual

**Uno o dos días.**

Un atajo de teclado o un botón de la consola dispara una sugerencia. El preámbulo de
sesión se prefillea en `session.start` y se mantiene en la caché de prompt. Los tokens
llegan a la consola en streaming según se producen.

!!! success "Puerta"
    TTFT por debajo de **1 segundo** en el sistema real, coincidiendo con el benchmark de
    M0. La sugerencia es lo bastante corta como para leerla de un vistazo y relevante con
    la frecuencia suficiente como para que valga la pena mirarla.

    La segunda mitad de esa puerta es subjetiva y debe juzgarse en llamadas reales, no en
    demos.

---

## M3 — Cliente Linux

**Un día.**

Captura con PipeWire en el N95, mismo protocolo, sin cambios en el núcleo. Si hay que
cambiar el núcleo para aceptar una segunda plataforma de cliente, el protocolo estaba mal
y se arregla aquí.

!!! success "Puerta"
    Una sesión completa conducida desde la máquina Linux con el Mac mini como núcleo, con
    latencia dentro del ruido respecto al caso local de macOS.

**Esta es la primera versión genuinamente útil.**

---

## M4 — Sugerencia especulativa

**Construido.** El fin de turno en el carril `far` arranca la generación antes de que la
pidas, y que la otra parte retome la palabra la cancela. Una sugerencia que pediste tú
nunca se cancela así: la pediste.

El reconocedor se vacía al terminar el turno, antes de generar. Sin eso, la sugerencia se
escribiría contra una transcripción a la que le falta la frase que debe responder, porque
su final todavía no se ha emitido.

!!! success "Puerta"
    La latencia percibida baja de forma medible respecto a M2 en las mismas
    conversaciones, y los arranques en falso no distraen en la práctica.

    **Sin cumplir.** Las dos mitades necesitan llamadas reales para juzgarse, y la
    dirección positiva del detector —que dispare con habla real— nunca se ha ejercitado
    aquí, solo la negativa con silencio, ruido y tonos.

---

## M5 — Contexto de sesión

Perfiles: currículum, descripción del puesto o agenda de la reunión, notas. Modos
predefinidos: conversacional, técnico, reunión de negocio.

**Construido.** Los perfiles se cargan de disco, y la consola los lista, elige uno y lo
edita. El perfil elegido decide el idioma y el estilo de sugerencia por defecto. El
selector se bloquea mientras la sesión está en vivo, porque cambiarlo invalidaría la caché
prefilleada.

Guardar se rechaza para el perfil con el que se construyó una sesión en vivo, por la misma
razón, y el editor lo dice en vez de fallar en silencio. Las escrituras son atómicas: un
fallo a mitad de guardado deja intacto el perfil anterior en lugar de uno a medias.

Los perfiles siguen siendo ficheros JSON planos. El editor es una comodidad, no un
formato.

!!! success "Puerta"
    Las sugerencias hacen referencia a tu trayectoria real. Cambiar de perfil surte efecto
    con un solo prefill de preámbulo y sin coste medible por sugerencia.

    **Sin medir.** La segunda mitad necesita las cifras de M0.

---

## M6 — Pulido

**Historial de sesión y exportación: construido en el núcleo.** La grabación está apagada
por defecto y a propósito: el argumento de este proyecto es que una conversación se queda en
máquinas que son tuyas, así que escribir cada reunión a disco sin pedirlo lo socavaría.
Cuando está encendida se le dice a la consola, cada sesión es un fichero JSON, el Markdown
se renderiza bajo demanda en lugar de guardarse dos veces, y borrar es tan fácil como
grabar.

Queda por hacer: la interfaz de consola para ello, la traducción en vivo, los instaladores
empaquetados y una documentación de instalación lo bastante buena para alguien que no
construyó esto.

---

## Explícitamente fuera de alcance

- Evasión de proctoring o supervisión, ocultación en pantalla compartida, anti-detección
  de cualquier tipo.
- Recuperación sobre el contexto estático — ver
  [Inferencia](architecture/inference.md#sin-recuperacion-en-v1).
- Una versión alojada. El sentido del proyecto es que no hay servidor.
- Clientes de captura móviles. El teléfono es la consola, no el micrófono.
