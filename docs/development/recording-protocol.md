# Recording protocol

The tuned defaults (`end_windows`, `bleed_ratio`, `bleed_far_floor`, the prompts) were
fitted to **one** scripted Spanish call. This protocol produces the set that tells whether
they hold. Only the operator can record it.

## Consent and privacy

A recording holds another person's voice. Ask them first, or use friends who agree to be
recorded for this. Recordings never go to git (`*.wav` and `sessions/` are ignored), never to
a hosted service, and are only ever sent to the speech server you already run.

## What to record

At least five calls. The mix matters more than the length of any one:

| # | Language | Audio setup | Script | Length |
|---|---|---|---|---|
| 1 | es | headphones | scripted | about 5 min |
| 2 | es | loudspeakers | unscripted | about 10 min |
| 3 | en | headphones | scripted | about 5 min |
| 4 | en | loudspeakers | unscripted | about 10 min |
| 5 | either | either | unscripted | **30 min** |

Vary the voices when you can: a different person on the far side in at least two calls. An
unscripted call is two people talking about something they know, interrupting each other
now and then: that is what the detector has never been tested on.

## How to record

Each lane is recorded on its own, from the same sources the capture client uses
(`susurro-capture --list-devices` names them). Start both at the same moment:

```bash
parec --device=<mic source> --format=s16le --rate=16000 --channels=1 --raw \
  | ffmpeg -loglevel error -y -f s16le -ar 16000 -ac 1 -i - recordings/<call>/mic.wav &
parec --device=<sink monitor> --format=s16le --rate=16000 --channels=1 --raw \
  | ffmpeg -loglevel error -y -f s16le -ar 16000 -ac 1 -i - recordings/<call>/far.wav &
```

Stop both with `Ctrl-C` at the end (`kill -INT` on each pipeline). Check that the two files
are within a second of each other in length.

## What goes in each folder

```
recordings/<call>/
  mic.wav  far.wav      16 kHz mono, as above
  meta.json
  script.json           only for a scripted call
```

`meta.json`:

```json
{"language": "es", "profile": "es-business-industrial", "mode": "business",
 "setup": "headphones", "scripted": true, "minutes": 5}
```

`script.json` has the format of `bench/fixtures/call_script_es.json`: the sentences each lane
read, and the key terms. An unscripted call has none: it is judged by lag, by the hints and
by your ratings, not by word error.

## Running the set

```bash
.venv/bin/python -m bench.replay_set recordings/ --stt-url http://127.0.0.1:8081 \
    --hints-url http://127.0.0.1:8080
```

It replays every call at real-time pace (the whole set takes as long as the calls do) and
prints one table: lag, word error, key terms, hints started and wasted, time to first token.
Re-run it after changing a default and compare tables; a default that cites one call is the
problem this set exists to remove.

## Rating the hints

Keyword overlap says little about whether a hint helps. After a run with `--hints-url`:

```bash
.venv/bin/python -m bench.rating_sheet build sessions/set-*/call*.json \
    --sheet sheet.csv --key key.json
```

Fill the `rating` column of `sheet.csv`: **0** useless or wrong, **1** usable, **2** exactly
what to say. The sheet is shuffled and names no model, so you cannot favour one. Then
`python -m bench.rating_sheet summarize --sheet sheet.csv --key key.json`. Keep `key.json`
away from yourself until you are done.
