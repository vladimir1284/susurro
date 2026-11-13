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

```bash
# en la máquina del núcleo
git clone https://github.com/vladimir1284/susurro
cd susurro
python3 -m venv .venv && .venv/bin/pip install -e ".[bench]"
.venv/bin/python bench/run.py --out bench/results/$(date +%F)-m4-16gb.json
```

Qué reporta:

| Métrica | Por qué importa |
|---|---|
| Factor de tiempo real del STT | Debe quedar bien por debajo de 1,0 o la transcripción se atrasa para siempre |
| Prefill en frío, 1k / 4k tokens | El coste que la caché de prompt existe para evitar |
| Prefill caliente, delta de 100 tokens | El coste que realmente se paga por sugerencia |
| Tokens/s de decodificación | Fija cuánto tarda una sugerencia en terminar de llegar |
| Memoria residente pico | Decide si el supuesto de 8B se sostiene |

Cada resultado se versiona en `bench/results/`. Las cifras de esta documentación se
actualizan a partir de ellos, y los avisos de estimación se retiran según se van
reemplazando por mediciones.
