# Guiones de grabación

Todo lo que hay que leer en voz alta para el set de evaluación del
[protocolo de grabación](recording-protocol.es.md), en el orden de su tabla. Ningún programa ejecuta
esto salvo los JSON que se nombran en cada sección: `bench.replay_set` puntúa la grabación contra
ellos.

Para cada llamada: crea la carpeta, copia el guion si lo hay, escribe `meta.json`, graba las dos
pistas como dice el protocolo y di en voz alta el número de cada intercambio antes de leerlo: luego
es fácil ubicarlo en la grabación. Las frases se puntúan una a una por separado, así que una pausa,
una risa o una palabra de más entre ellas no cuesta nada. **Lee a ritmo normal y no leas en voz alta
las indicaciones de interrupción.**

```bash
mkdir -p recordings/<llamada>
cp bench/fixtures/<guion>.json recordings/<llamada>/script.json   # solo las llamadas con guion
```

## Llamada 1 — español, audífonos, con guion, unos 5 min

Guion: `bench/fixtures/call_script_es.json`. Perfil `es-business-industrial`. Quien está lejos es el
cliente (lee la primera lista), quien está cerca es el consultor (lee la segunda); alternad: lejos 1,
mic 1, lejos 2, mic 2 y así. Las listas no tienen el mismo largo: cuando una se acaba, la otra
termina.

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business", "setup": "headphones", "scripted": true, "minutes": 5}
```

**Lejos (cliente)**

1. Tenemos una línea de envasado con controladores de dos fabricantes y ninguna telemetría centralizada
2. Qué se podría instrumentar sin parar la línea
3. Mi preocupación es que otro integrador nos dejó la línea parada dos días
4. Cómo evitamos eso
5. Qué datos podríamos ver en remoto
6. Temperatura, caudal, algo más
7. Cuánto tardaría una primera fase
8. Y más o menos cuánto costaría
9. A mí lo inalámbrico no me gusta, se cae
10. Qué pasa si se cae el enlace con la nube

**Mic (consultor)**

1. Se puede empezar leyendo los PLC por Modbus TCP sobre cable, en solo lectura, sin tocar la lógica de control
2. Así la línea nunca se detiene
3. Con fases reversibles
4. La primera solo observa; si algo falla, se desconecta el equipo y la línea sigue como estaba
5. Temperatura, caudal, peso con celdas de carga y estado de cada máquina
6. Se guarda en TimescaleDB sobre PostgreSQL y se consulta desde cualquier navegador
7. Sin ver la planta no prometo plazos
8. Propongo una visita técnica para medir, y entonces damos fechas y un presupuesto cerrado
9. Estoy de acuerdo, por eso la propuesta es cableada: Modbus TCP por Ethernet, sin Wi-Fi en la línea

## Llamada 2 — español, altavoces, sin guion, unos 10 min

Sin guion ni `script.json`; `"scripted": false`. Perfil `es-business-industrial`. Dos personas que
conocen un tema y se interrumpen de vez en cuando. Temas sugeridos (elige uno; lo importante es que
no se lea): un cliente que quiere monitorizar máquinas sin parar la producción; elegir entre un
servicio en la nube y almacenamiento local para datos de planta; explicar un presupuesto a alguien
que cree que es caro.

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business", "setup": "loudspeakers", "scripted": false, "minutes": 10}
```

## Llamada 3 — inglés, audífonos, con guion, unos 5 min

Guion: `bench/fixtures/call_script_en.json`. Perfil `en-technical-backend`: una entrevista técnica.
Lejos es el líder del equipo, mic es el candidato. Se lee como conversación, en este orden (lo que
se dice está en inglés a propósito).

```json
{"language": "en", "profile": "en-technical-backend", "mode": "technical", "setup": "headphones", "scripted": true, "minutes": 5}
```

| # | Quién | Dice |
|---|---|---|
| 1 | Lejos (líder) | Thanks for joining. I wrote the original billing service, so I will be asking about it. Tell me about a system you replaced without a big bang rewrite |
| 1 | Mic (candidato) | At Arbor Systems we replaced a fourteen year old pricing engine. We did not rewrite it in one go. We ran both engines in parallel against live traffic for six weeks |
| 2 | Lejos (líder) | How did you decide the new engine was safe to switch to |
| 2 | Mic (candidato) | We compared the outputs every night and defined an error budget up front. We switched when the residual difference held under 0.05 percent |
| 3 | Lejos (líder) | What did you do when the two engines disagreed |
| 3 | Mic (candidato) | Every disagreement became a ticket with the input that caused it. Most were rounding rules nobody had written down, and we added tests for each one before changing anything |
| 4 | Lejos (líder) | Our billing tables are write heavy. What happens to a Postgres query under that kind of load |
| 4 | Mic (candidato) | Writers and readers contend for the same pages, so latency grows with lock waits and with dead tuples that vacuum has not cleaned yet. Indexes make writes slower too, so I would check which ones are really used |
| 5 | Lejos (líder) | How would you find out that autovacuum is falling behind |
| 5 | Mic (candidato) | I would watch dead tuples per table and the age of the oldest transaction, and compare vacuum duration to the rate of updates. A long running transaction blocks cleanup for everyone |
| 6 | Lejos (líder) | Let's say we want to move an endpoint to a new service. How do you do that without downtime |
| 6 | Mic (candidato) | Put the new service behind a feature flag, send a small share of traffic, and keep the old path as the fallback. Roll forward only while the error rate stays inside the budget |
| 7 | Lejos (líder) | What if a client retries a payment request during the cutover |
| 7 | Mic (candidato) | The payment request needs an idempotency key, stored with the result, so a retry returns the first answer instead of charging twice. That has to exist before the cutover starts, not after |
| 8 | Lejos (líder) | We have two junior engineers joining the team. How do you mentor them |
| 8 | Mic (candidato) | I pair with them on the first few changes and explain why, not only what. Then I review their work for the reasoning, and I let them make small mistakes in a safe place |
| 9 | Lejos (líder) | What do you do when a junior engineer's pull request is wrong in a way that matters |
| 9 | Mic (candidato) | I say what the risk is, with a concrete failing case, and ask them how they would fix it. If they find it themselves they keep it |
| 10 | Lejos (líder) | Do you have any questions for me |
| 10 | Mic (candidato) | Yes. What does on-call actually look like for the platform team, and how often does a person get paged |

## Llamada 4 — inglés, altavoces, sin guion, unos 10 min

Sin guion. Perfil `en-technical-backend`. Temas sugeridos: una discusión de diseño sobre mover un
endpoint de facturación a un servicio nuevo; qué mirar cuando una consulta de Postgres solo es lenta
en hora punta; cómo revisar el cambio de un ingeniero junior. Interrumpíos.

```json
{"language": "en", "profile": "en-technical-backend", "mode": "technical", "setup": "loudspeakers", "scripted": false, "minutes": 10}
```

## Llamada 5 — cualquier idioma, cualquier montaje, sin guion, 30 min

Sin guion. La que más importa para los fallos de llamada larga: seguid hablando, con pausas
naturales, la media hora completa. Si puedes, otra voz al otro lado distinta de las llamadas 1 a 4.
Sugerencia: una entrevista de práctica que vaya donde vaya.

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business", "setup": "headphones", "scripted": false, "minutes": 30}
```

Cambia `language`, `profile` y `mode` en `meta.json` si la haces en inglés.

## Llamada 6 opcional — español, prueba de interrupciones, con guion, unos 3 min

Guion: `bench/fixtures/call_script_es_interrupt.json`. Perfil `es-business-industrial`. Está hecha
para probar el detector de la pista lejana y la puerta de eco: en los intercambios 3 y 4 quien está
cerca **interrumpe a propósito, a mitad de la frase del otro**, una vez con voz normal y otra con
voz baja. En 1, 2 y 5 no os solapéis. Al terminar dime si algo salió distinto (os pisasteis más de
lo previsto, cambiaste una frase).

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business", "setup": "headphones", "scripted": true, "minutes": 3}
```

| # | Quién | Dice |
|---|---|---|
| 1 | Lejos (entrevistador) | Buenas. Cuéntame: cómo monitorizarías el consumo eléctrico de una planta de tres naves sin parar la producción |
| 1 | Mic (candidato) | Instalaría medidores con salida Modbus TCP en cada cuadro, leyendo en solo lectura, y guardaría todo en TimescaleDB |
| 2 | Lejos (entrevistador) | Y si el cuadro está lejos del router |
| 2 | Mic (candidato) | Cable Ethernet hasta un switch industrial. El Wi-Fi no lo recomiendo en una planta con tanto ruido |
| 3 | Lejos (entrevistador) | *(despacio, sin parar)* Mira, mi preocupación es que otro proveedor ya nos instaló algo, que se cayó a las dos semanas, y ahora nadie en la empresa confía en que esto vaya a durar de verdad |
| 3 | Mic (candidato) | **INTERRUMPES con voz NORMAL, a mitad de su frase:** «Perdona que te interrumpa: eso se evita con fases reversibles, la primera solo observa» |
| 3 | Lejos (entrevistador) | *(se calla, y luego)* Vale, eso me tranquiliza |
| 4 | Lejos (entrevistador) | *(despacio, sin parar)* Y cuánto tardarías en tener una primera fase funcionando, más o menos, y qué costaría |
| 4 | Mic (candidato) | **INTERRUMPES con voz BAJA, como un aparte, a mitad de su frase:** «Unas cuatro semanas, sin comprometer el presupuesto todavía» |
| 4 | Lejos (entrevistador) | *(sigue hasta terminar su pregunta)* |
| 5 | Lejos (entrevistador) | Qué pasa si se cae el enlace con la nube |
| 5 | Mic (candidato) | Los datos se guardan localmente en el PLC y se sincronizan cuando vuelve |
