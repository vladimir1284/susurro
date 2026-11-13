# I wrote the benchmark before I wrote the feature

*Draft for LinkedIn. Not posted — review before publishing.*

---

There's a number my project lives or dies on, and until this week I didn't know it.

The project is a tool that listens to a video call and offers short suggestions — a
reminder of something you're trying to recall, the shape of an answer. The number is how
long it takes between someone finishing a question and the first word of a suggestion
appearing. Under a second, useful. Over about four, actively harmful, because you end up
reading advice about a question you've already started answering.

I've designed a whole system around that number. Memory budgets, model size limits, a
specific trick for avoiding repeated work. All of it rests on assumptions about how fast
a particular small computer can run a particular model.

I have never measured it. Not once. And I've been careful, all the way through, to write
down that I haven't.

## Estimates that look like facts

My documentation has a table of memory figures: how much the speech model takes, how much
the language model takes, what's left over. It has a claim that generation runs at
fifteen to twenty words-per-second-ish.

Every one of those numbers is derived from published specifications and arithmetic. None
came from running anything. They're reasonable, and reasonable is not the same as true.

The failure mode here is specific and common: an estimate gets written down, gets quoted
in a second document, gets built on, and three weeks later nobody remembers it was ever a
guess. It has the same typographic authority as a measurement. It sits in a table. It has
a decimal point.

So every page carrying those numbers opens with a box saying, in plain words, that they
are estimates derived from specifications rather than measurements, that a particular
milestone exists to replace them, and that if a measurement disagrees, the measurement
wins and the page gets corrected.

That box costs nothing and it's the difference between a known assumption and an
accidental fact.

## The harness comes before the thing

What I built this week is not a feature. It's the measuring instrument: a program that
starts a model, pushes work through it, and reports whether the machine meets the
thresholds the design assumes.

Building it first — before the code that depends on the answers — is the whole point. If
the machine turns out to be twice as slow as estimated, the response is to shorten the
prompt or use a smaller model. That's a cheap change now and an expensive one after
another month of code assumes otherwise.

Three things in that harness took more thought than the timing code, and all three are
ways to accidentally measure nothing.

**It refuses to run without real speech audio.** The obvious shortcut for testing a
speech recognizer is to feed it any audio file — a tone, or silence. But a recognizer
given nothing to transcribe does far less work than one given a real conversation. You'd
get a beautiful number that means nothing. So the tool requires a real recording and
refuses to proceed without one, with an error message explaining why rather than just
"missing argument."

**It changes the input between repeats.** I measure "cold" performance — how long the
model takes with nothing cached. But run the same request five times and the server
caches it, so repeats two through five measure the cache instead. The harness varies the
prompt each time. One line of code, and without it the entire cold measurement is
fiction.

**It asks which process to measure.** The memory figure is the point of the whole
exercise — it decides whether a given model size fits at all. But the model runs in a
*different* process from the benchmark. Measuring the benchmark's own memory would report
a few hundred megabytes and look like excellent news. The tool asks for the model
server's process id and refuses to guess.

Each of those is a way to produce a confident, precise, completely wrong number. That's
worse than no number, because a wrong number ends the conversation.

## The threshold is in the code, not in someone's head

The thresholds — under a second for a response, under twelve gigabytes for both models —
aren't in a document for a human to check against. They're in the harness. It prints
PASS or FAIL per threshold, and its exit code reflects the overall result, so an automated
check can't miss it.

This matters more than it sounds, because of what happens when a number comes back
disappointing. If the gate lives in your head, there's a conversation where 1.2 seconds
becomes "basically a second" and the project proceeds. If it's in code, changing it is a
commit, and a commit needs a message explaining why — which is exactly the friction that
decision deserves.

There's one more rule in there, and it's the one I'd defend hardest: **a threshold with
no measurement behind it does not pass.** If the speech benchmark didn't run, that gate is
not met. It isn't neutral, it isn't skipped. Silence is not evidence.

## And it records where it ran

Every result file includes the machine: architecture, core count, memory, whether there's
an accelerator. If there isn't one, the summary prints a warning that these results don't
transfer to the target hardware and must not be quoted as if they do.

I need that warning, because I wrote all of this on a small rented container with two
cores and no accelerator — a machine that cannot run the real models usefully at all. The
temptation to run something, get a number, and quietly let it inform a decision is real.
The warning is aimed squarely at me, in three weeks, when I've forgotten which machine
produced which file.

## The version of this that applies to you

You probably have a number your project depends on. Request latency under load, memory
per tenant, how long a job takes at ten times the current volume.

Two questions:

**Is it measured, or is it reasoned?** Both are legitimate. Confusing them is not. A
reasoned number should be labelled as reasoned, everywhere it appears, until someone
measures it.

**If it comes back twice as bad as you think, what changes?** If the answer is "quite a
lot," measure it before building the lot. The measuring instrument is usually an
afternoon. The rewrite is not.

I'll have real numbers next week, on the machine this is meant for. I genuinely don't know
what they'll say. The useful part is that I've already decided what I'll do if they're
bad — and written it down, where I can't quietly reinterpret it later.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
