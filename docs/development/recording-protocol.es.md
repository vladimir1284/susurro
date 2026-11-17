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

## Grabar con la herramienta

`bench/record_call.py` hace todo lo de abajo en un comando: los dos carriles, `meta.json`, la
copia del guion y un veredicto sobre la toma. Los números de llamada son las filas de la tabla
de arriba (la 6 es la llamada opcional de interrupción).

```bash
.venv/bin/python -m bench.record_call --list-devices   # una vez: revisa las fuentes
.venv/bin/python -m bench.record_call 1                # graba recordings/call1/
```

**Antes de cada llamada**

1. La otra persona acepta que la graben. Abre la app de llamada (o lo que reproduzca su voz por
   esta máquina). El carril far es lo que sale por la salida de audio por defecto.
2. Llamadas con auriculares (1, 3, 5, 6): auriculares puestos toda la llamada. Llamadas con
   altavoces (2, 4): altavoces al volumen que usarías de verdad, y **no silencies ni bajes la voz
   lejana**: que el micrófono la oiga es lo que prueba esa llamada.
3. Llamada con guion: ten abierto `recording-scripts.es.md` en la llamada que haces. Far lee la
   lista far, tú la lista mic, alternando, diciendo antes el número del intercambio, a ritmo
   normal. No leas las acotaciones. No habléis a la vez salvo que el guion lo diga (llamada 6).
4. Llamada sin guion: elige un tema que ambos conozcan y habla como en una llamada real.
   Interrumpíos de vez en cuando. Llamada 5: la media hora entera, mejor con una voz lejana que
   no hayas usado. Para hacerla en inglés añade `--language en` (solo llamada 5);
   `--setup loudspeakers` cambia su montaje.

**Durante**: la herramienta hace una prueba de nivel de 4 s (habla y que hable el otro lado),
muestra las dos fuentes y espera Enter. Luego: 3 s de silencio, grabas, 3 s de silencio, Enter
para parar. Apunta a picos de voz en torno a -12 dBFS; por debajo de -40 es una fuente
equivocada.

**Después**: imprime duraciones, niveles, cuánto habla cada lado, cuánto se solapan y si el
micrófono sigue al carril far. El veredicto es uno de:

- `KEEP`: sirve.
- `KEEP IF THE WARNINGS ARE EXPECTED`: léelos; p. ej. no haber solapamiento vale en la 1, no en
  la 6.
- `RE-RECORD`: carriles que difieren más de 1 s, un carril mudo o saturado, demasiado corta, o
  la voz lejana se cuela en el micrófono en una llamada con auriculares. Mueve o borra la
  carpeta y graba otra vez; la herramienta nunca sobrescribe. Ninguna toma rechazada debe entrar
  en el conjunto.

Reglas que el verificador no ve: una llamada por toma (no pegues trozos), no edites el audio, no
repitas una frase en la que tropezaste (el tropiezo es dato) y cuéntame después qué fue distinto
del guion. Si el carril far empieza un segundo tarde es el sink despertando; reproduce cualquier
sonido antes de empezar.

## Grabar a mano

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
