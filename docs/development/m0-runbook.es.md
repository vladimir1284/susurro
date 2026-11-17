# Runbook de M0

Cómo ejecutar el benchmark de M0 en la máquina de referencia (Mac mini M4, 16 GB).
Presupuesto: menos de una hora en la máquina, casi toda arrancando servidores. Cada cifra
que sale es una medición de **esa máquina en ese momento**: la velocidad del Mac ha variado
unas 3x en una tarde, así que ejecuta cada runtime al menos dos veces, a horas distintas, y
conserva ambos archivos.

!!! warning "Nada de esto se ha ejecutado todavía"
    Los comandos de abajo son el procedimiento previsto. Quien lo ejecute primero debería
    corregir esta página donde esté mal.

## Qué necesitas

- El repositorio en una máquina que alcance el servidor del modelo (el propio Mac, u otra
  máquina por un túnel `ssh -L`).
- Un modelo GGUF ya en disco para llama.cpp y, para MLX, los pesos del modelo ya
  descargados. No cuentes con descargar durante la ejecución.
- Opcional, para la parte de voz: un WAV mono de 16 kHz con habla real. Ver la nota sobre
  voz más abajo.

## 0. Mantén despierto el Mac

Antes que nada: un Mac mini inactivo se duerme y despierta cada minuto más o menos y se
congela ~13 s cada vez, lo que aparece como paradas de 15 s en todas las cifras. Ejecuta
`caffeinate -s &` en él (o `sudo pmset -a sleep 0`); ver [Hardware](../hardware.md).
Comprueba que `pmset -g log | grep -c DarkWake` no crece mientras esperas.

## 1. llama.cpp

```bash
llama-server -m /ruta/al/modelo.gguf -c 8192 --port 8080
```

Espera a `server is listening` y, desde el repositorio:

```bash
.venv/bin/python -m bench.run --label llama-cpp --skip-stt \
    --llm-url http://127.0.0.1:8080 --server-pid $(pgrep -f '[l]lama-server') \
    --repeats 5
```

Detén el servidor antes de arrancar el siguiente runtime, para que no compitan por memoria.

## 2. MLX

```bash
mlx_lm.server --model <ruta local o repo> --port 8080
.venv/bin/python -m bench.run --label mlx --skip-stt \
    --llm-url http://127.0.0.1:8080 --server-pid $(pgrep -f '[m]lx_lm.server')
```

Si `python3 -c "import mlx_lm"` falla, MLX no está instalado: anótalo en las notas del
resultado en vez de instalarlo en una máquina cuya red no puede descargarlo.

## 3. Voz

`bench.run` mide la voz con `faster-whisper`, cuyos pesos no están en el Mac. Para la cifra
de voz en el Mac, repite una llamada grabada con whisper.cpp:

```bash
.venv/bin/python -m bench.replay_call recordings/call1 \
    --stt-url http://127.0.0.1:8081 --profile <id del perfil>
```

y cita el factor de tiempo real que informa, etiquetado con el backend. Pasa `--skip-stt` a
`bench.run` como arriba, para que la puerta de voz salga como *sin medir* en vez de fallar.

## 4. Dónde cae cada cosa y cuánto tarda

Cada ejecución escribe `bench/results/<fecha>-<etiqueta>.json` e imprime un resumen. Cuenta
con unos 5–10 minutos por runtime con `--repeats 5` (estimación, no medición).
`bench/results/*.json` está ignorado por git a propósito, así que haz commit de un
`bench/results/<fecha>-<etiqueta>.md` breve al lado con: el archivo del modelo, la
cuantización, la versión del runtime, el estado de la máquina (¿Chrome abierto?
¿`mediaanalysisd` ocupado?) y el bloque de resumen.

## 5. Leer el resultado

| Clave | Qué responde |
|---|---|
| `warm_ttft_s` | La puerta de M0: mediana bajo 1.0 s |
| `cold_prefill_*_s` | Lo que ahorra la caché |
| `d1_first_hint_ttft_cold_s` | Primera sugerencia de una sesión es/business, sin prefill |
| `d1_first_hint_ttft_prefilled_s` | Igual, tras el `prefill` actual |
| `d1_first_hint_ttft_legacy_s` | Igual, tras el `prefill` anterior a la corrección (una reconstrucción) |
| `resident_memory_gb` | Puerta: menos de 12 |

**¿`max_tokens=0` llena la caché?** El código lo asume; los servidores difieren. Sí lo hace
si `prefilled` queda cerca de `warm_ttft_s` y muy por debajo de `cold`. Si `prefilled` queda
cerca de `cold`, ese runtime ignora la petición de cero tokens y arrancar la sesión no
compra nada. Anota la respuesta por runtime.

**¿Funciona la corrección de D1?** `prefilled` debería ganar a `legacy`, que debería
parecerse a `cold`. Si no, detente y avisa.

## 6. Después de la ejecución

1. Sustituye las estimaciones de [Inferencia](../architecture/inference.md) y su gemela
   inglesa por los valores medidos, y solo entonces quita el aviso de estimaciones (un test
   lo vigila).
2. Anota si la puerta de M0 pasó o no en la [hoja de ruta](../roadmap.md).
3. Si la puerta falla, detente y avisa. No ajustes para esquivarla: la respuesta es un
   modelo más pequeño o un prompt más corto, y los hitos posteriores cambian de forma.
