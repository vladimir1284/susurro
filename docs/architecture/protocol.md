# Protocol

The core exposes two WebSocket endpoints and serves the console as static files. Default
port `8723`.

Both endpoints require a shared token, passed as `?token=…`. This is LAN-level protection
only — see [Overview](overview.md#network-assumptions).

## Capture endpoint

`ws://core:8723/capture?token=…`

Client to core. One connection **per lane**, so a capture client opens two.

### Hello

The first message is JSON and establishes the lane:

```json
{
  "type": "hello",
  "protocol": 1,
  "session_id": "2026-11-13T14:02:11Z-a4f1",
  "lane": "mic",
  "codec": "opus",
  "sample_rate": 16000,
  "frame_ms": 20,
  "client": "susurro-capture-linux/0.1.0"
}
```

`lane` is `mic` or `far`. The core replies `{"type":"ready","protocol":1}` or sends an
error and closes.

### Audio frames

Every subsequent message is binary:

```
┌────────────────────────┬──────────────────────┐
│  capture timestamp     │   Opus packet        │
│  uint64 LE, microsec.  │   (20 ms, variable)  │
└────────────────────────┴──────────────────────┘
       8 bytes                   N bytes
```

The Opus packet is capped at 4000 bytes. Speech at 16 kHz mono is far under that; the cap
exists so a stream that has gone wrong fails loudly instead of being fed to a model.

The timestamp comes from the client's monotonic clock at capture time, not from wall
clock. The core uses it for ordering within a lane and for measuring jitter; it does not
assume the two clients' clocks agree.

!!! note "Open question"
    Cross-machine lane alignment is unresolved. In the reference setup both lanes come
    from the same client and therefore share a clock, so this does not block
    [M1](../roadmap.md#m1-spine). It becomes real if lanes ever originate on different
    machines.

## Console endpoint

`ws://core:8723/console?token=…`

Bidirectional JSON. No binary frames.

### Core to console

```json
{"type":"transcript.partial","lane":"far","text":"so tell me about a time when","t":12.40}
{"type":"transcript.final","lane":"far","text":"So tell me about a time when you disagreed with a teammate.","t":12.40,"dur":3.10,"seq":41}
{"type":"hint.start","hint_id":"h-93","trigger":"speculative","mode":"conversational"}
{"type":"hint.delta","hint_id":"h-93","text":"- Migration rollback, Q2\n"}
{"type":"hint.end","hint_id":"h-93","reason":"complete","tokens":38,"ttft_ms":780}
{"type":"hint.end","hint_id":"h-93","reason":"cancelled"}
{"type":"profiles","profiles":[{"id":"backend-role-acme","label":"Acme — senior backend","language":"en","mode":"conversational"}]}
{"type":"profile","profile":{"id":"backend-role-acme","label":"Acme — senior backend","language":"en","mode":"conversational","context":{"resume":"…"}}}
{"type":"sessions","recording":false,"sessions":[{"session_id":"2026-11-13T14-02-11Z","profile_id":"backend-role-acme","started_at":"2026-11-13T14:02:11+00:00","ended_at":"2026-11-13T14:48:02+00:00","lines":84,"hints":11}]}
{"type":"export","session_id":"2026-11-13T14-02-11Z","markdown":"# Session …"}
{"type":"state","session":"live","stt":"ready","llm":"ready","lanes":{"mic":"up","far":"up"}}
{"type":"error","code":"lane_busy","detail":"lane 'mic' is already connected"}
```

`hint.end` with `reason: "cancelled"` is the normal outcome of a speculative generation
the other party interrupted. The console discards the partial hint rather than showing it.

`ttft_ms` is the measured time from the hint being asked for to its first token — not from
the request reaching the model, since scheduling and prompt assembly are latency the user
waits through too. It is present on a cancelled hint as well, when generation got that far:
an abandoned hint still measured the model. This is the number
[M2's gate](../roadmap.md#m2-manual-hint) is stated in.

A hint has two deadlines. If no first token arrives within eight seconds, or the whole
thing has not finished within twenty, it ends with `reason: "error"`. Both are generous
against the one-second budget — they exist to catch a stuck model, not to enforce quality.
A suggestion arriving after them is not late, it is noise, and a console left saying
"writing…" for the rest of a call is worse than one that says it failed.

### Console to core

```json
{"type":"hint.request","mode":"conversational"}
{"type":"hint.cancel","hint_id":"h-93"}
{"type":"session.config","profile_id":"backend-role-acme","language":"en","mode":"conversational"}
{"type":"session.start"}
{"type":"session.stop"}
{"type":"profiles.request"}
{"type":"sessions.request"}
{"type":"session.export","session_id":"2026-11-13T14-02-11Z"}
{"type":"session.delete","session_id":"2026-11-13T14-02-11Z"}
{"type":"profile.request","profile_id":"backend-role-acme"}
{"type":"profile.save","profile_id":"backend-role-acme","label":"Acme — senior backend","language":"en","mode":"conversational","context":{"resume":"…","role":"…","notes":"…"}}
```

## Recorded sessions

**Off by default.** This project's argument is that a conversation stays on machines you
own; writing every meeting to disk unasked would undercut it. Recording is switched on in
the core's configuration, and `sessions` carries a `recording` flag so the console can say
when it is happening — a tool that quietly records conversations is not one anybody should
trust.

A recorded session is one JSON file holding the profile it used, when it ran, the committed
transcript and the hints with their metrics. `session.export` renders Markdown from it on
demand rather than storing a second copy, and `session.delete` removes it: deleting has to
be as easy as recording. An empty session is not written at all, so an accidental start
does not bury the ones that matter.

A cancelled hint is recorded too. Afterwards it is useful to see what the thing was about
to say and whether abandoning it was right.

## Profiles

`profiles` carries labels only; `profile.request` fetches one in full, context included,
for the editor. `profile.save` writes it, creating the profile if the id is new, and the
core answers with a fresh listing — to the saving console first, then to every other one,
since two screens is a normal setup and a stale picker is a trap.

A save is refused for the profile a **live** session was built from. Its context is
already prefilled into the model's cache, so saving over it would leave the console
showing text the model has never seen, with nothing to indicate it. Other profiles can be
edited mid-session.

!!! warning "This makes the token a write credential"
    `profile.save` writes files on the core machine. Anyone who can reach the port and
    holds the token can create and overwrite profiles. Profile ids are rejected if they
    could address anything outside the profile directory — checked both here and in the
    profile store — but that is containment, not authentication. See
    [Overview](overview.md#network-assumptions).

`state` reports what the two models are actually doing. `stt` is `loading` while weights
are being read — a first run downloads them, which takes minutes — and `ready` afterwards.
`llm` is `unknown` until something has spoken to the model server, then `ready` or
`unavailable` depending on whether it answered. Both said `ready` unconditionally until a
session was started against a server that was not running.

A console is greeted with `profiles` before `state`, so it can offer a choice rather than
requiring the id to be known in advance. `profiles.request` asks for the listing again. A
profile whose file is unreadable is left out and logged: one bad file must not leave the
console with an empty picker.

`session.config` must arrive before `session.start`, because it determines the static
preamble that gets prefilled into the
[prompt cache](inference.md#prompt-cache-is-a-requirement-not-an-optimization). Changing
it mid-session invalidates that cache and is therefore rejected, not silently honoured.

## Error codes

Every error the core sends carries one of these. The list is exhaustive and checked by a
test: a code in the source that is missing here, or a code here that nothing emits, fails
the suite.

| Code | Means |
|---|---|
| `unsupported_version` | The client speaks a protocol version this core does not. Checked before anything else about the message, so a newer client is told the useful thing. |
| `malformed_message` | A JSON message was unparseable, had the wrong shape, or carried an unknown field. |
| `malformed_frame` | A binary frame was too short, had an out-of-range timestamp, or an empty or oversized payload. |
| `decode_failed` | An Opus packet would not decode. One bad packet is reported and skipped; the lane stays up. |
| `lane_busy` | Something is already connected on that lane. |
| `bad_transition` | A command arrived in a session phase that does not allow it — a hint with no live session, `session.config` while live. |
| `bad_request` | A profile is missing, invalid, or disagrees with the `session.config` that named it. |
| `llm_unavailable` | The model server did not answer when the session started. Transcription still works; hints will not until it is back. |
| `stt_failed` | A recognition pass raised, or the speech backend would not warm up at `session.start` (the session then stays `configured`). A failed pass leaves the lane up: a transcription hiccup must not become a dead microphone for the rest of a call. |
| `history_failed` | Recording is disabled, or a recorded session is missing or unreadable. |
| `hint_failed` | Generation raised. The matching `hint.end` carries `reason: "error"`. |
| `protocol_error` | Base code; not emitted directly. |

## Profiles

A profile is the static context for a session, stored on the core:

```json
{
  "id": "backend-role-acme",
  "label": "Acme — senior backend",
  "language": "en",
  "mode": "conversational",
  "context": {
    "resume": "…",
    "role": "…",
    "notes": "…"
  }
}
```

`language` is `en` or `es`, and it is the whole of the language configuration: a session is
in one language from start to finish. The core uses it to select the recognizer at
`session.start` — see [One language per session](audio.md#one-language-per-session) — and
to pick the system prompt the hints are written in. There is no auto-detection and no
mid-session switch; changing the language means stopping and starting a new session.

`mode` is `conversational`, `technical` or `business`, and sets the shape of the hints
rather than their content.

Profiles are plain files on disk. There is no database, because there is no scale problem
to solve: a user has a handful of profiles, not thousands.
