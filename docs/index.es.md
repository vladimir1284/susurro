# Susurro

Un copiloto local y en tiempo real para reuniones y entrevistas. Tanto el reconocimiento
de voz como la inferencia del modelo de lenguaje corren en hardware tuyo. El audio nunca
sale de tu red.

## Por qué existe

Los copilotos de entrevista alojados en la nube suben tu micrófono y el audio de la
reunión a un servidor remoto, ejecutan los modelos allí y devuelven las sugerencias. Ese
diseño funciona bien con una conexión rápida y simétrica, y resulta inútil con una lenta:
el enlace de subida se convierte en el cuello de botella, y una sugerencia que llega ocho
segundos después de la pregunta es peor que ninguna, porque la lees cuando la
conversación ya avanzó.

Susurro elimina la subida por completo. Un cliente ligero de captura envía audio
codificado en Opus por tu LAN a una máquina de la misma red; esa máquina transcribe,
razona y transmite las sugerencias a una consola que lees en una segunda pantalla o en el
teléfono.

El beneficio secundario es la privacidad. Las reuniones de negocio y las entrevistas son
exactamente el tipo de conversación que no debería quedar en los registros de un tercero.

## Qué hace

- Transcribe ambos lados de una conversación en vivo, en dos carriles separados.
- Genera sugerencias cortas y legibles, bajo demanda o cuando la otra parte deja de
  hablar.
- Mantiene tu contexto —currículum, descripción del puesto, agenda de la reunión, notas—
  residente en la caché del modelo durante toda la sesión.
- Muestra todo en una consola que corre en un dispositivo distinto al de la reunión.

## Qué no hace

Susurro es un copiloto, no una herramienta para hacer trampa. El proyecto no va a
implementar:

- Evasión de software de supervisión o proctoring.
- Ocultar ventanas de la pantalla compartida o de la grabación de pantalla.
- Ninguna funcionalidad cuyo propósito sea burlar la detección en lugar de ayudarte a
  pensar.

Ejecutar el copiloto en un segundo dispositivo es una consecuencia de diseño de no
quererlo en tu pantalla compartida. No es una función anti-detección y no se va a
desarrollar como tal.

## Estado

La fontanería está construida y probada; las mediciones no están tomadas.

El protocolo, el núcleo, el cliente de captura, la consola, ambos backends reales y el
arnés de benchmark existen y funcionan. Lo que no existe todavía es una sola medición del
hardware objetivo: cada cifra de rendimiento de estas páginas sigue siendo una estimación
derivada de especificaciones publicadas, etiquetada como tal, y
[M0](roadmap.md#m0-benchmark) existe para reemplazarlas.

Dos cosas están explícitamente sin probar, no simplemente sin terminar.
`capture/backends/pipewire.py` nunca se ha ejecutado, porque la máquina donde se desarrolló
no tiene dispositivos de audio: eso es [M3](roadmap.md#m3-cliente-linux). Y no se ha
ejecutado ningún modelo en el Mac mini, que es de lo que dependen todas las puertas de la
hoja de ruta.

## Licencia

AGPL-3.0. Elegida a propósito: el equivalente alojado de esta herramienta se vende por
suscripción, y la licencia busca que cualquier derivado ofrecido como servicio siga
siendo abierto.
