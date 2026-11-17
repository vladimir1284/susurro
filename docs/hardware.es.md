# Hardware

## Configuración de referencia

| Rol | Máquina | Notas |
|---|---|---|
| Núcleo | Mac mini M4, 16 GB de memoria unificada | Ejecuta ambos modelos. Dedicado: no aloja la reunión. |
| Captura | Mini PC Intel N95, Linux | Aloja la reunión. Solo captura y codificación Opus. |
| Consola | Teléfono o segundo monitor | Cualquier navegador. |

La separación existe por una razón. 16 GB alcanzan para los modelos *o* para una
videollamada con navegador, no cómodamente para ambos a la vez — ver
[Inferencia](architecture/inference.md#la-restriccion-que-manda-es-la-memoria). El N95
sería inservible como núcleo y es del todo suficiente como cliente de captura, porque
codificar en Opus dos flujos mono de 16 kHz es un error de redondeo de CPU.

## Requisitos del núcleo

**Mínimo viable:** Apple Silicon con 16 GB de memoria unificada, o una máquina x86 con
8 GB de VRAM. Por debajo de eso, un modelo de 8B a q4 más un modelo de voz no se mantienen
residentes a la vez, y el coste de recarga por sugerencia rompe el presupuesto de
latencia.

**Cómodo:** 24 GB o más, lo que abre la clase 14B y deja margen real.

### Mantén despierto el Mac mini

**Un Mac mini inactivo se duerme y se despierta cada minuto más o menos**, y cada vez todo
lo que corre en él se detiene unos 13 segundos: una petición en curso lo espera, así que
las sugerencias y las líneas de transcripción llegan 15 segundos tarde. Ocurre siempre que
nadie usa la máquina, que es el estado normal de un núcleo sin pantalla. En el Mac de
referencia se midieron unos 60 ciclos de sueño y despertar por hora, a todas horas, y
pararon por completo con `caffeinate -s` (también con `-dimsu`; `-i` solo no bastó). Para
arreglarlo de forma permanente, una vez:

```bash
sudo pmset -a sleep 0
```

o activa «Evitar que el equipo entre en reposo automáticamente cuando la pantalla esté
apagada» en Ajustes del Sistema. Como remedio sin `sudo`, ejecuta `caffeinate -s &` antes
de una sesión. Comprueba con `pmset -g log | grep -c DarkWake`: no debería seguir creciendo
mientras la máquina está inactiva.

Susurro no es específico de CUDA. El núcleo habla con `llama.cpp` o con un extremo
compatible con OpenAI, así que una máquina NVIDIA funciona; simplemente no es contra lo
que se está diseñando y midiendo el proyecto.

## Requisitos del cliente de captura

Cualquier cosa capaz de ejecutar Python y abrir dos flujos de audio. Un N95, una Raspberry
Pi 5, un portátil viejo. El cliente no hace inferencia por diseño.

En macOS hace falta un dispositivo de audio de loopback —[BlackHole](https://github.com/ExistentialAudio/BlackHole)
o equivalente— porque macOS no ofrece captura del audio del sistema. En Linux la fuente
monitor de PipeWire existe por defecto.

## Red

LAN, o una tailnet de [Tailscale](https://tailscale.com/) si el núcleo vive en otro sitio.
El ancho de banda es despreciable: dos carriles Opus a unos 24 kbit/s cada uno. La
latencia importa más que el caudal, y por eso conviene revisar un Wi-Fi hacia un punto de
acceso congestionado antes de culpar al modelo.

## Procedimiento del benchmark M0

Ejecutar antes de escribir cualquier otro código. Ver [Hoja de ruta](roadmap.md#m0-benchmark)
para las condiciones de la puerta.

Los dos runtimes que vale la pena comparar hablan la API de OpenAI, así que hay un solo
arnés y no dos rutas de código. Arranca uno, mide, arranca el otro y vuelve a medir:

```bash
# llama.cpp
llama-server -m model.gguf -c 8192 --port 8080

# o MLX
mlx_lm.server --model <repo> --port 8080
```

```bash
# en la máquina del núcleo
git clone https://github.com/vladimir1284/susurro
cd susurro
python3 -m venv .venv && .venv/bin/pip install -e ".[bench]"
.venv/bin/susurro-bench \
  --label llama-cpp \
  --audio samples/entrevista-30s.wav \
  --server-pid "$(pgrep -f llama-server)"
```

Qué reporta:

| Métrica | Por qué importa |
|---|---|
| Factor de tiempo real del STT | Debe quedar bien por debajo de 1,0 o la transcripción se atrasa para siempre |
| Prefill en frío, 1k / 4k tokens | El coste que la caché de prompt existe para evitar |
| Tiempo hasta el primer token en caliente | El coste que realmente se paga por sugerencia. **La cifra de la que vive el producto.** |
| Tokens/s de decodificación | Fija cuánto tarda una sugerencia en terminar de llegar |
| Memoria residente pico | Decide si el supuesto de 8B se sostiene |

Tres cosas que el arnés exige, porque cada una es una forma fácil de no medir nada:

- **Audio de voz real.** `--audio` es obligatorio. El silencio o un tono le dan al
  reconocedor menos trabajo que el habla real y producen un factor de tiempo real
  halagador.
- **Un prompt distinto por repetición en frío.** Un prefill «en frío» medido contra un
  servidor con la caché ya caliente no es una medición.
- **El pid del servidor.** El modelo corre en otro proceso; sin `--server-pid` la cifra de
  memoria describiría al arnés.

El código de salida es la puerta: `0` si se cumple cada umbral de la
[Hoja de ruta](roadmap.md#m0-benchmark), `1` si alguno falla o no se midió. Una puerta sin
medición detrás no pasa: el silencio no es evidencia.

`--dry-run` ejercita el arnés con cifras inventadas, para comprobar el arnés y no la
máquina. Su salida lo dice en todas las líneas que importan.

Cada resultado se versiona en `bench/results/`. Cada fichero registra la máquina de la que
salió —arquitectura, núcleos, memoria, acelerador— para que una cifra de un portátil no
pueda citarse como cifra del objetivo. Las cifras de esta documentación se actualizan a
partir de ellos, y los avisos de estimación se retiran según se van reemplazando por
mediciones.
