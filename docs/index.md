# Susurro

A local-first, real-time copilot for meetings and interviews. Speech recognition and
language model inference both run on hardware you own. Audio never leaves your network.

## Why this exists

Hosted interview copilots stream your microphone and your meeting audio to a remote
server, run the models there, and stream hints back. That design is fine on a fast
symmetric connection and useless on a slow one: the uplink becomes the bottleneck, and a
hint that arrives eight seconds after the question was asked is worse than no hint at
all, because you read it while the conversation has already moved on.

Susurro removes the upload entirely. A thin capture client sends Opus-encoded audio over
your LAN to a machine on the same network; that machine transcribes, reasons, and streams
hints to a console you read on a second screen or a phone.

The secondary benefit is privacy. Business meetings and interviews are exactly the kind
of conversation that should not be sitting in a third party's logs.

## What it does

- Transcribes both sides of a live conversation, in two separate lanes.
- Generates short, readable hints on demand or when the other party stops speaking.
- Keeps your context — résumé, job description, meeting agenda, notes — resident in the
  model's cache for the whole session.
- Displays everything on a console that runs on a different device from the meeting.

## What it does not do

Susurro is a copilot, not a cheating tool. The project will not implement:

- Evasion of proctoring or monitoring software.
- Hiding windows from screen sharing or screen recording.
- Any feature whose purpose is to defeat detection rather than to help you think.

Running a copilot on a second device is a design consequence of not wanting it in your
screen share. It is not an anti-detection feature, and it will not be developed into one.

## Status

The plumbing is built and tested; the measurements are not taken.

The protocol, the core server, the capture client, the console, both real backends and the
benchmark harness all exist and run. What does not exist yet is a single measurement from
the target hardware: every performance number on these pages is still an estimate derived
from published specifications, labelled as one, and [M0](roadmap.md#m0-benchmark) exists
to replace them.

Two things are explicitly unproven rather than merely unfinished. `capture/backends/pipewire.py`
has never been executed, because the machine this was developed on has no audio devices —
that is [M3](roadmap.md#m3-linux-client). And no model has been run on the Mac mini, which
is everything the roadmap's gates depend on.

## License

AGPL-3.0. Chosen deliberately: the hosted equivalent of this tool is sold as a
subscription, and the license is meant to keep any derivative that is offered as a
service open as well.
