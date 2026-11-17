# Inferencia

!!! warning "En su mayoría estimaciones"
    Las cifras de esta página se derivan de especificaciones publicadas de hardware y de
    tamaños de modelo. [M0](../roadmap.md#m0-benchmark) ya midió una parte en la máquina de
    referencia (tiempo hasta el primer token, prefill, velocidad de decodificación, velocidad
    del reconocimiento de voz, efecto del prefill del prefijo; ver
    `bench/results/2026-11-17-awake.md`). La memoria con ambos modelos cargados está medida
    (unos 4.2 GB, muy por debajo de la estimación); MLX y las llamadas largas siguen siendo estimaciones. Si una medición contradice algo de aquí, gana
    la medición y esta página se corrige.

## La restricción que manda es la memoria

El núcleo de referencia es un Mac mini M4 con **16 GB de memoria unificada** y unos
**120 GB/s** de ancho de banda de memoria. Ambos modelos tienen que estar residentes a la
vez: descargar y recargar entre un segmento de transcripción y una sugerencia costaría
segundos.

| Componente | Cuantización | Memoria |
|---|---|---|
| macOS y servicios en segundo plano | — | ~3,5 GB |
| Modelo de voz (`whisper-large-v3-turbo`) | q8 | ~1,6 GB |
| Modelo de lenguaje, clase 8B | q4 | ~5 GB |
| Caché KV, contexto de 8k | — | ~1 GB |
| **Total** | | **~11 GB** |

Quedan unos 5 GB de margen, que es la cantidad correcta: macOS se pone incómodo bastante
antes de agotar la memoria literalmente, y el navegador de la consola más el propio
proceso del núcleo necesitan sitio.

Un modelo de 14B a q4 son unos 9 GB, lo que lleva el total a unos 15 GB. Eso no es un
ajuste apretado, es uno roto. **El techo práctico es la clase 8B** — Qwen3-8B o
Llama-3.1-8B.

## Velocidad de decodificación

Generar cada token exige leer el conjunto completo de pesos desde memoria. Con 120 GB/s y
5 GB de pesos, el techo teórico ronda los 24 tokens/s; el rendimiento real en Apple
Silicon suele quedar apreciablemente por debajo del techo de ancho de banda, así que
planifica con 15–20 tokens/s.

Ese ritmo da forma al producto. Una sugerencia de 40 tokens en viñetas aparece en unos dos
o tres segundos de streaming, y resulta usable porque empiezas a leer la primera viñeta
mientras llega el resto. Un ensayo de 400 tokens tarda veinte segundos y es inútil en una
conversación en vivo. **Las sugerencias son cortas por arquitectura, no por preferencia.**

## La caché de prompt es un requisito, no una optimización

La velocidad de decodificación es la mitad cómoda del problema. El prefill es la mitad
peligrosa.

Un prompt de sugerencia contiene las instrucciones de sistema, tu currículum, la
descripción del puesto o la agenda de la reunión, y la transcripción reciente: digamos
2–3k tokens. Procesarlo desde cero en cada petición mete segundos de latencia delante del
primer token, y revienta el presupuesto entero de
[Pipeline de audio](audio.md#presupuesto-de-latencia) antes de que el modelo haya dicho
nada.

La solución es no procesarlo nunca desde cero. El preámbulo de la sesión —prompt de
sistema más todo el contexto estático— se prefillea **una vez** al arrancar la sesión, y
su caché KV se conserva. Cada sugerencia posterior solo prefillea el delta: las líneas
nuevas de transcripción, del orden de 100 tokens.

Esto exige que el prompt sea estrictamente **de solo anexado**. Cualquier cosa que
reescriba tokens anteriores invalida la caché desde ese punto y reintroduce el coste
completo de prefill. En concreto:

- El contexto estático va primero y no cambia a mitad de sesión.
- Lo que envía `prefill` es exactamente el comienzo de lo que envía cada sugerencia: el
  mensaje de sistema depende del idioma de la sesión y de su preámbulo, y de nada más. La
  instrucción del modo va **después** de la transcripción, así que una sugerencia manual en
  otro modo no cambia el prefijo en caché. Un test lo comprueba para cada idioma y modo.
- Las líneas de transcripción se anexan, nunca se editan — por eso solo entra
  `transcript.final` en el contexto, nunca `transcript.partial`.
- El desbordamiento de contexto se maneja abriendo una rama nueva de caché en una frontera
  de segmento, no reescribiendo el historial en su sitio. En concreto: una sugerencia lleva
  una ventana de líneas enteras de la transcripción, de como mucho
  `[llm] transcript_budget_chars` caracteres (12000 por defecto, unos 3k tokens; una cifra
  conservadora aún sin ajustar con M0). La ventana se queda quieta mientras la transcripción
  cabe. Cuando no cabe, salta hacia delante para conservar como la mitad del presupuesto: un
  fallo de caché y luego un largo tramo tranquilo, en lugar de un inicio de prompt que se
  mueve y descarta la caché en cada sugerencia. El preámbulo nunca está en la ventana, así
  que no hay nada que volver a prefillar.

Ambos runtimes candidatos lo soportan:

| Runtime | Mecanismo |
|---|---|
| Servidor `llama.cpp` | Slots con reutilización de caché de prefijo; `--slot-save-path` para persistencia |
| `mlx-lm` | Objetos de caché de prompt explícitos, guardados y restaurados entre llamadas |

Ambos exponen una API HTTP compatible con OpenAI, así que Susurro los alcanza con un solo
cliente (`core/llm/openai_compat.py`) en lugar de dos rutas de código, y cambiar de uno a
otro es un cambio de configuración. El calentamiento es una completion pedida con
`max_tokens=0`: procesa el preámbulo y llena la caché del servidor sin generar nada. Un
servidor que ignore una petición de cero tokens simplemente mueve ese coste a la primera
sugerencia, lo cual es inocuo.

MLX suele prefillear más rápido en Apple Silicon. [M0](../roadmap.md#m0-benchmark) mide
ambos y elige al ganador por evidencia, no por reputación.

## Sin recuperación en v1

El instinto obvio es poner el currículum y las notas detrás de un recuperador. Resístelo.

Todo el corpus estático —currículum, descripción del puesto, agenda de la reunión, notas
personales— son 2–3k tokens. Cabe entero en la ventana de contexto, y una vez está en la
caché de prefijo no cuesta nada por sugerencia. Un recuperador añadiría un modelo de
embeddings a un presupuesto de memoria que no tiene sitio para él, sumaría decenas de
milisegundos a un presupuesto de latencia que no tiene margen, e introduciría un modo de
fallo donde el párrafo relevante es justo el que no se recupera.

La recuperación se gana su sitio cuando el corpus deja de caber: notas acumuladas de
cincuenta reuniones pasadas, por ejemplo. Eso es una funcionalidad futura real y
explícitamente no de v1.

## Dos presets, un modelo

Los mismos pesos sirven a dos estilos de interacción muy distintos, separados por prompt
de sistema y parámetros de muestreo:

| Preset | Forma de salida | Tolerancia a latencia |
|---|---|---|
| **Conversacional** | 3–5 fragmentos, palabras clave, no frases | Dura: menos de 1,5 s o es ruido |
| **Técnico** | Enfoque, luego esbozo de código | Más laxa: estás en una pizarra o un editor |

Una sugerencia conversacional que se lee como un párrafo falla incluso cuando es correcta,
porque no puedes leer un párrafo y mantener el contacto visual a la vez. El prompt tiene
que imponer la brevedad explícitamente; los modelos no la traen por defecto.

## Generación especulativa

El diseño ingenuo espera a que pulses una tecla. Para entonces la otra parte lleva un
segundo callada, y el presupuesto completo de latencia empieza desde cero.

En su lugar, el núcleo arranca la generación cuando el VAD informa de fin de turno en el
carril `far`. Para cuando decides que quieres una sugerencia, los primeros tokens ya
existen. Si la otra parte retoma la palabra, la generación en vuelo se cancela y se
descarta.

El coste es cómputo desperdiciado en generaciones descartadas. En una máquina dedicada eso
es capacidad que nadie más estaba usando, lo que lo deja casi gratis.
