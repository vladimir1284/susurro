# Visión general

Susurro son tres procesos, separados a propósito. Cada uno puede vivir en una máquina
distinta, y en la configuración de referencia dos de ellos lo hacen.

```
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ cliente captura  │      │      núcleo      │      │     consola      │
│                  │      │                  │      │                  │
│ carril mic  ─────┼─ WS ─┼─▶ VAD ─▶ STT     │      │  transcripción   │
│ carril lejano ───┼─────▶│      │           ├─ WS ─┤  sugerencias     │
│                  │ Opus │      ▼           │ JSON │  controles       │
│ (equipo de la    │      │  contexto + LLM  │      │  (teléfono / 2ª  │
│  reunión)        │      │                  │      │   pantalla)      │
└──────────────────┘      └──────────────────┘      └──────────────────┘
  Linux N95 o Mac           Mac mini M4 16 GB         cualquier navegador
```

## Por qué tres procesos y no uno

**La reunión y la inferencia compiten por los mismos recursos.** Con 16 GB de memoria
unificada, un navegador con una videollamada junto a un modelo de 5 GB y uno de voz de
1,6 GB va a tirar de swap, y el swap en la ruta de inferencia mata la latencia. Mantener
el núcleo en una máquina dedicada elimina la contención por construcción.

**La consola tiene que estar en otro sitio.** Si las sugerencias se dibujan en la pantalla
que estás compartiendo, las ve todo el mundo en la llamada. Un dispositivo aparte es la
única disposición que funciona, y sale sola en cuanto el núcleo ya es un servicio de red.

**El cliente de captura es portable, el núcleo no.** La captura son unos cientos de líneas
por plataforma —PipeWire en Linux, CoreAudio en macOS—. El núcleo es un montón de pesos
de modelos y de runtimes atados al acelerador. Separarlos significa que una plataforma
nueva de cliente es trabajo pequeño y no toca la inferencia.

## Componentes

### Cliente de captura

Abre dos flujos de audio, codifica cada uno en Opus y los envía al núcleo por WebSocket.
Corre en la máquina que aloja la reunión. Deliberadamente tonto: sin VAD, sin política de
buffering, sin modelo. Ver [Pipeline de audio](audio.md).

### Núcleo

Es dueño de todo lo caro. Recibe ambos carriles, los decodifica, ejecuta detección de
actividad de voz,
pasa cada carril por reconocimiento de voz, mantiene el contexto de la sesión y maneja el
modelo de lenguaje. También sirve la consola como ficheros estáticos, así hay una sola
dirección que recordar. Ver [Inferencia](inference.md).

### Consola

Una página de navegador. Dos carriles de transcripción, un área de sugerencias, unos
pocos controles. Sin framework: es una lista de texto que hace scroll y un WebSocket.
Corre en un teléfono, una tableta o un segundo monitor.

## Supuestos de red

Se espera que los tres procesos estén en la misma LAN, o en la misma tailnet de
[Tailscale](https://tailscale.com/). El núcleo escucha en una interfaz de LAN, no en
`0.0.0.0` por defecto, y exige un token compartido en ambos extremos WebSocket. Esto
alcanza para una red doméstica de confianza; no sustituye a una autenticación real, y el
núcleo no debe exponerse a internet.

El viaje de ida y vuelta en LAN es del orden del milisegundo y Opus a 16 kHz mono cuesta
unos 24 kbit/s por carril, así que el transporte no aporta nada relevante al presupuesto
de latencia. Ese es justamente el objetivo del diseño: el enlace caro de un copiloto en la
nube es el que Susurro no tiene.
