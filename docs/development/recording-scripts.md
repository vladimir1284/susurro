# Recording scripts

Everything to read aloud for the evaluation set in [the recording protocol](recording-protocol.md),
in the order of its table. Nothing here is run by a program except the JSON files named in each
section, which `bench.replay_set` scores the recording against.

For each call: make the folder, copy the script if there is one, write `meta.json`, record both
lanes as the protocol says, and say the exchange number out loud before each one: it makes the
recording easy to navigate later. Sentences are scored one by one and independently, so a pause, a
laugh or an extra word between them costs nothing. **Read at a normal speaking pace and do not
read the interruption stage directions aloud.**

```bash
mkdir -p recordings/<call>
cp bench/fixtures/<script>.json recordings/<call>/script.json   # scripted calls only
```

## Call 1 — Spanish, headphones, scripted, about 5 min

Script: `bench/fixtures/call_script_es.json`. Profile `es-business-industrial`. The far person is
the client (reads the first list), the near person is the consultant (reads the second); alternate
them: far 1, mic 1, far 2, mic 2 and so on. The two lists are not the same length: where one runs
out, the other finishes.

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business", "setup": "headphones", "scripted": true, "minutes": 5}
```

**Far (client)**

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

**Mic (consultant)**

1. Se puede empezar leyendo los PLC por Modbus TCP sobre cable, en solo lectura, sin tocar la lógica de control
2. Así la línea nunca se detiene
3. Con fases reversibles
4. La primera solo observa; si algo falla, se desconecta el equipo y la línea sigue como estaba
5. Temperatura, caudal, peso con celdas de carga y estado de cada máquina
6. Se guarda en TimescaleDB sobre PostgreSQL y se consulta desde cualquier navegador
7. Sin ver la planta no prometo plazos
8. Propongo una visita técnica para medir, y entonces damos fechas y un presupuesto cerrado
9. Estoy de acuerdo, por eso la propuesta es cableada: Modbus TCP por Ethernet, sin Wi-Fi en la línea

## Call 2 — Spanish, loudspeakers, unscripted, about 10 min

No script, no `script.json`; `"scripted": false`. Profile `es-business-industrial`. Two people who
know a subject and interrupt each other now and then. Suggested subjects (pick one, it is the
point that they are not read): a client who wants to monitor machines without stopping production;
choosing between a cloud service and local storage for plant data; explaining a quote to someone
who thinks it is too high.

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business", "setup": "loudspeakers", "scripted": false, "minutes": 10}
```

## Call 3 — English, headphones, scripted, about 5 min

Script: `bench/fixtures/call_script_en.json`. Profile `en-technical-backend`: a technical screen.
Far is the team lead, mic is the candidate. Read it as a conversation, in this order.

```json
{"language": "en", "profile": "en-technical-backend", "mode": "technical", "setup": "headphones", "scripted": true, "minutes": 5}
```

| # | Who | Says |
|---|---|---|
| 1 | Far (lead) | Thanks for joining. I wrote the original billing service, so I will be asking about it. Tell me about a system you replaced without a big bang rewrite |
| 1 | Mic (candidate) | At Arbor Systems we replaced a fourteen year old pricing engine. We did not rewrite it in one go. We ran both engines in parallel against live traffic for six weeks |
| 2 | Far (lead) | How did you decide the new engine was safe to switch to |
| 2 | Mic (candidate) | We compared the outputs every night and defined an error budget up front. We switched when the residual difference held under 0.05 percent |
| 3 | Far (lead) | What did you do when the two engines disagreed |
| 3 | Mic (candidate) | Every disagreement became a ticket with the input that caused it. Most were rounding rules nobody had written down, and we added tests for each one before changing anything |
| 4 | Far (lead) | Our billing tables are write heavy. What happens to a Postgres query under that kind of load |
| 4 | Mic (candidate) | Writers and readers contend for the same pages, so latency grows with lock waits and with dead tuples that vacuum has not cleaned yet. Indexes make writes slower too, so I would check which ones are really used |
| 5 | Far (lead) | How would you find out that autovacuum is falling behind |
| 5 | Mic (candidate) | I would watch dead tuples per table and the age of the oldest transaction, and compare vacuum duration to the rate of updates. A long running transaction blocks cleanup for everyone |
| 6 | Far (lead) | Let's say we want to move an endpoint to a new service. How do you do that without downtime |
| 6 | Mic (candidate) | Put the new service behind a feature flag, send a small share of traffic, and keep the old path as the fallback. Roll forward only while the error rate stays inside the budget |
| 7 | Far (lead) | What if a client retries a payment request during the cutover |
| 7 | Mic (candidate) | The payment request needs an idempotency key, stored with the result, so a retry returns the first answer instead of charging twice. That has to exist before the cutover starts, not after |
| 8 | Far (lead) | We have two junior engineers joining the team. How do you mentor them |
| 8 | Mic (candidate) | I pair with them on the first few changes and explain why, not only what. Then I review their work for the reasoning, and I let them make small mistakes in a safe place |
| 9 | Far (lead) | What do you do when a junior engineer's pull request is wrong in a way that matters |
| 9 | Mic (candidate) | I say what the risk is, with a concrete failing case, and ask them how they would fix it. If they find it themselves they keep it |
| 10 | Far (lead) | Do you have any questions for me |
| 10 | Mic (candidate) | Yes. What does on-call actually look like for the platform team, and how often does a person get paged |

## Call 4 — English, loudspeakers, unscripted, about 10 min

No script. Profile `en-technical-backend`. Suggested subjects: a design discussion about moving a
billing endpoint to a new service; what to look at when a Postgres query is slow only at peak;
how to review a junior engineer's change. Interrupt each other.

```json
{"language": "en", "profile": "en-technical-backend", "mode": "technical", "setup": "loudspeakers", "scripted": false, "minutes": 10}
```

## Call 5 — either language, either setup, unscripted, 30 min

No script. The one that matters most for the call-length failures: keep talking, with natural
pauses, for the full half hour. Use a different far voice from calls 1 to 4 if you can. Suggested:
a mock interview that goes where it goes.

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business", "setup": "headphones", "scripted": false, "minutes": 30}
```

Change `language`, `profile` and `mode` in `meta.json` if you do it in English.

## Optional call 6 — Spanish, interruption test, scripted, about 3 min

Script: `bench/fixtures/call_script_es_interrupt.json`. Profile `es-business-industrial`. This is
the one built to test the far-lane detector and the bleed gate: in exchanges 3 and 4 the near
person **interrupts on purpose, in the middle of the far person's sentence**, once in a normal
voice and once in a low voice. In exchanges 1, 2 and 5 do not overlap. Afterwards tell me if
anything went differently (you talked over each other more than planned, you changed a sentence).

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business", "setup": "headphones", "scripted": true, "minutes": 3}
```

| # | Who | Says |
|---|---|---|
| 1 | Far (interviewer) | Buenas. Cuéntame: cómo monitorizarías el consumo eléctrico de una planta de tres naves sin parar la producción |
| 1 | Mic (candidate) | Instalaría medidores con salida Modbus TCP en cada cuadro, leyendo en solo lectura, y guardaría todo en TimescaleDB |
| 2 | Far (interviewer) | Y si el cuadro está lejos del router |
| 2 | Mic (candidate) | Cable Ethernet hasta un switch industrial. El Wi-Fi no lo recomiendo en una planta con tanto ruido |
| 3 | Far (interviewer) | *(slowly, without stopping)* Mira, mi preocupación es que otro proveedor ya nos instaló algo, que se cayó a las dos semanas, y ahora nadie en la empresa confía en que esto vaya a durar de verdad |
| 3 | Mic (candidate) | **INTERRUPT in a NORMAL voice, mid-sentence:** «Perdona que te interrumpa: eso se evita con fases reversibles, la primera solo observa» |
| 3 | Far (interviewer) | *(falls silent, then)* Vale, eso me tranquiliza |
| 4 | Far (interviewer) | *(slowly, without stopping)* Y cuánto tardarías en tener una primera fase funcionando, más o menos, y qué costaría |
| 4 | Mic (candidate) | **INTERRUPT in a LOW voice, like an aside, mid-sentence:** «Unas cuatro semanas, sin comprometer el presupuesto todavía» |
| 4 | Far (interviewer) | *(keeps going until the question is finished)* |
| 5 | Far (interviewer) | Qué pasa si se cae el enlace con la nube |
| 5 | Mic (candidate) | Los datos se guardan localmente en el PLC y se sincronizan cuando vuelve |
