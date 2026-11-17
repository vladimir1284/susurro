# Inference

!!! warning "Mostly estimates"
    The numbers on this page are derived from published hardware specifications and model
    sizes. [M0](../roadmap.md#m0-benchmark) has since measured part of them on the reference
    machine (time to first token, prefill, decode speed, speech speed, the effect of the
    prefix prefill; see `bench/results/2026-11-17-awake.md`). Memory with both models loaded,
    MLX and long calls are still estimates. If a measured number contradicts something here,
    the measurement wins and this page gets corrected.

## The binding constraint is memory

The reference core is a Mac mini M4 with **16 GB of unified memory** and roughly
**120 GB/s** of memory bandwidth. Both models have to be resident simultaneously —
unloading and reloading between a transcript segment and a hint would cost seconds.

| Component | Quantization | Memory |
|---|---|---|
| macOS and background services | — | ~3.5 GB |
| Speech model (`whisper-large-v3-turbo`) | q8 | ~1.6 GB |
| Language model, 8B class | q4 | ~5 GB |
| KV cache, 8k context | — | ~1 GB |
| **Total** | | **~11 GB** |

That leaves about 5 GB of headroom, which is the right amount: macOS gets unhappy well
before memory is literally exhausted, and the console browser plus the core process
itself need room.

A 14B model at q4 is roughly 9 GB, bringing the total to about 15 GB. That is not a
tight fit, it is a broken one. **The practical ceiling is the 8B class** — Qwen3-8B or
Llama-3.1-8B.

## Decode speed

Generation of each token requires reading the full weight set from memory. At 120 GB/s
with 5 GB of weights, the theoretical ceiling is about 24 tokens/s; real throughput on
Apple Silicon typically lands meaningfully below the bandwidth ceiling, so plan for
15–20 tokens/s.

That rate shapes the product. A 40-token hint in bullet form appears in roughly two to
three seconds of streaming, which is usable because you start reading the first bullet
while the rest arrives. A 400-token essay takes twenty seconds and is useless in a live
conversation. **Hints are short by architecture, not by preference.**

## Prompt cache is a requirement, not an optimization

Decode speed is the comfortable half of the problem. Prefill is the dangerous half.

A hint prompt contains the system instructions, your résumé, the job description or
meeting agenda, and the recent transcript — call it 2–3k tokens. Processing that from
scratch on every hint request puts seconds of latency in front of the first token, and
blows the entire budget from [Audio pipeline](audio.md#latency-budget) before the model
has said anything.

The fix is to never process it from scratch. The session preamble — system prompt plus
all static context — is prefilled **once** when the session starts, and its KV cache is
retained. Each subsequent hint only prefills the delta: the new transcript lines, on the
order of 100 tokens.

This requires the prompt to be strictly **append-only**. Anything that rewrites earlier
tokens invalidates the cache from that point forward and reintroduces the full prefill
cost. Concretely:

- Static context goes first and never changes mid-session.
- What `prefill` sends is exactly the start of what every hint sends: the system message
  depends on the session's language and its preamble, and on nothing else. The mode's
  instruction goes **after** the transcript, so a manual hint in a different mode does not
  change the cached prefix. A test checks this for every language and mode.
- Transcript lines are appended, never edited — which is why only
  `transcript.final` enters the context, never `transcript.partial`.
- Context overflow is handled by starting a new cache branch at a segment boundary, not
  by rewriting history in place. Concretely: a hint carries a window of whole transcript
  lines, at most `[llm] transcript_budget_chars` characters (default 12000, about 3k
  tokens; a conservative figure not yet tuned against M0). The window holds still while
  the transcript fits. When it does not, it jumps forward to keep about half the budget:
  one cache miss, then a long quiet stretch, rather than a shifting prompt start that
  would discard the cache on every hint. The preamble is never in the window, so there is
  nothing to re-prefill.

Both candidate runtimes support this:

| Runtime | Mechanism |
|---|---|
| `llama.cpp` server | Slots with prefix-cache reuse; `--slot-save-path` for persistence |
| `mlx-lm` | Explicit prompt cache objects, saved and restored between calls |

Both expose an OpenAI-compatible HTTP API, so Susurro reaches them through one client
(`core/llm/openai_compat.py`) rather than two code paths, and switching between them is a
configuration change. The warm-up is a completion requested with `max_tokens=0`: it
processes the preamble and populates the server's cache without generating anything. A
server that ignores a zero-token request simply moves that cost to the first hint, which
is harmless.

MLX generally prefills faster on Apple Silicon. [M0](../roadmap.md#m0-benchmark) measures
both and picks the winner on evidence, not on reputation.

## No retrieval in v1

The obvious instinct is to put the résumé and notes behind a retriever. Resist it.

The entire static corpus — résumé, job description, meeting agenda, personal notes — is
2–3k tokens. It fits in the context window whole, and once it is in the prefix cache it
costs nothing per hint. A retriever would add an embedding model to a memory budget that
has no room for one, add tens of milliseconds to a latency budget that has none to
spare, and introduce a failure mode where the relevant paragraph is the one that does not
get retrieved.

Retrieval earns its place when the corpus stops fitting — notes accumulated across fifty
past meetings, for instance. That is a real future feature and explicitly not a v1 one.

## Two presets, one model

The same weights serve two very different interaction styles, separated by system prompt
and sampling parameters:

| Preset | Output shape | Latency tolerance |
|---|---|---|
| **Conversational** | 3–5 fragments, keywords not sentences | Hard: under 1.5 s or it is noise |
| **Technical** | Approach, then code sketch | Softer: you are at a whiteboard or editor |

A conversational hint that reads like a paragraph fails even when it is correct, because
you cannot read a paragraph and maintain eye contact at the same time. The prompt has to
enforce brevity explicitly; models do not default to it.

## Speculative generation

The naive design waits for you to press a key. By then the other party has been silent
for a second, and the full latency budget starts from zero.

Instead, the core starts generating when the VAD reports end-of-turn on the `far` lane.
By the time you decide you want a hint, the first tokens already exist. If the other party
resumes speaking, the in-flight generation is cancelled and discarded.

The cost is wasted compute on discarded generations. On a dedicated machine that is
capacity nobody else was using, which makes it close to free.
