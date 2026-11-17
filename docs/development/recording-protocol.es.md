# Protocolo de grabación

Los valores ajustados (`end_windows`, `bleed_ratio`, `bleed_far_floor`, los prompts) se
ajustaron con **una** llamada en español con guion. Este protocolo produce el conjunto que
dice si se sostienen. Solo el operador puede grabarlo.

## Consentimiento y privacidad

Una grabación contiene la voz de otra persona. Pídele permiso antes, o usa amigos que acepten
ser grabados para esto. Las grabaciones nunca van a git (`*.wav` y `sessions/` están
ignorados), nunca a un servicio alojado, y solo se envían al servidor de voz que ya ejecutas.

## Qué grabar

Al menos cinco llamadas. La mezcla importa más que la duración de cada una:

| # | Idioma | Audio | Guion | Duración |
|---|---|---|---|---|
| 1 | es | auriculares | con guion | unos 5 min |
| 2 | es | altavoces | sin guion | unos 10 min |
| 3 | en | auriculares | con guion | unos 5 min |
| 4 | en | altavoces | sin guion | unos 10 min |
| 5 | cualquiera | cualquiera | sin guion | **30 min** |

Varía las voces cuando puedas: una persona distinta al otro lado en al menos dos llamadas.
Una llamada sin guion es dos personas hablando de algo que conocen, interrumpiéndose de vez
en cuando: eso es lo que el detector nunca ha probado.

Lo que se lee en voz alta en cada llamada, listo para leer, está en [Guiones de grabación](recording-scripts.es.md).

## Cómo grabar

Cada carril se graba por separado, desde las mismas fuentes que usa el cliente de captura
(`susurro-capture --list-devices` las nombra). Arranca ambos a la vez:

```bash
parec --device=<fuente del micrófono> --format=s16le --rate=16000 --channels=1 --raw \
  | ffmpeg -loglevel error -y -f s16le -ar 16000 -ac 1 -i - recordings/<llamada>/mic.wav &
parec --device=<monitor del sink> --format=s16le --rate=16000 --channels=1 --raw \
  | ffmpeg -loglevel error -y -f s16le -ar 16000 -ac 1 -i - recordings/<llamada>/far.wav &
```

Páralos con `Ctrl-C` al final (`kill -INT` a cada tubería). Comprueba que los dos ficheros
difieren menos de un segundo de duración.

## Qué va en cada carpeta

```
recordings/<llamada>/
  mic.wav  far.wav      16 kHz mono, como arriba
  meta.json
  script.json           solo en una llamada con guion
```

`meta.json`:

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business",
 "setup": "headphones", "scripted": true, "minutes": 5}
```

`script.json` tiene el formato de `bench/fixtures/call_script_es.json`: las frases que leyó
cada carril y los términos clave. Una llamada sin guion no lo tiene: se juzga por el retraso,
por las sugerencias y por tus valoraciones, no por el error de palabras.

## Ejecutar el conjunto

```bash
.venv/bin/python -m bench.replay_set recordings/ --stt-url http://127.0.0.1:8081 \
    --hints-url http://127.0.0.1:8080
```

Repite cada llamada a ritmo de tiempo real (el conjunto entero tarda lo que duran las
llamadas) e imprime una tabla: retraso, error de palabras, términos clave, sugerencias
iniciadas y desperdiciadas, tiempo hasta el primer token. Vuelve a ejecutarlo tras cambiar un
valor y compara tablas; un valor que cita una sola llamada es el problema que este conjunto
existe para quitar.

## Valorar las sugerencias

El solapamiento de palabras clave dice poco sobre si una sugerencia ayuda. Tras una ejecución
con `--hints-url`:

```bash
.venv/bin/python -m bench.rating_sheet build sessions/set-*/llamada*.json \
    --sheet sheet.csv --key key.json
```

Rellena la columna `rating` de `sheet.csv`: **0** inútil o errónea, **1** aprovechable, **2**
justo lo que había que decir. La hoja va mezclada y no nombra ningún modelo, así que no
puedes favorecer a uno. Después, `python -m bench.rating_sheet summarize --sheet sheet.csv
--key key.json`. No abras `key.json` hasta terminar.
