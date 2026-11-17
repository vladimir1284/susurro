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

What to read aloud for each call, ready to read, is in [Recording scripts](recording-scripts.md).

## Recording with the tool

`bench/record_call.py` does everything below in one command: both lanes, `meta.json`, the
script copy, and a verdict on the take. Call numbers are the rows of the table above (6 is the
optional interruption call).

```bash
.venv/bin/python -m bench.record_call --list-devices   # once: check the sources
.venv/bin/python -m bench.record_call 1                # records recordings/call1/
```

**Before each call**

1. Far person agrees to be recorded. Open the call app (or anything that plays their voice
   through this machine). The far lane is whatever comes out of the default output.
2. Headphones calls (1, 3, 5, 6): headphones on for the whole call. Loudspeaker calls (2, 4):
   speakers at the volume you would really use, and **do not mute or lower the far voice**: the
   mic hearing it is what the call tests.
3. Scripted call: have `recording-scripts.md` open on the call you are doing. Far reads the far
   list, you read the mic list, alternating, saying the exchange number first, at a normal pace.
   Do not read stage directions. Do not talk over each other unless the script says so (call 6).
4. Unscripted call: pick a subject you both know and talk as in a real call. Interrupt each
   other now and then. Call 5: the full 30 minutes, ideally with a far voice not used before.
   To make it English, add `--language en` (call 5 only); `--setup loudspeakers` changes its setup.

**During**: the tool runs a 4 s level check (talk, and have the far side talk), shows both
sources and waits for Enter. Then: 3 s of silence, record, 3 s of silence, Enter to stop. Aim
for speech peaking around -12 dBFS; below -40 means a wrong source.

**After**: it prints measured lengths, levels, how much of the time each side talks, how much
they overlap, and whether the mic follows the far lane. The verdict is one of:

- `KEEP`: use it.
- `KEEP IF THE WARNINGS ARE EXPECTED`: read them; e.g. no overlap is fine in call 1, not in 6.
- `RE-RECORD`: lanes differ by over 1 s, a lane is silent or clips, too short, or the far voice
  leaks into the mic on a headphones call. Move or delete the folder and record again; the tool
  never overwrites. Nothing from a rejected take should reach the set.

Rules that the checker cannot see: one call per take (do not stitch), do not edit audio, do not
re-read a sentence you stumbled on (a stumble is data), and tell me afterwards what went
differently from the script. If the far lane starts late by a second it is the sink waking up;
play any sound before starting.

## Recording by hand

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
