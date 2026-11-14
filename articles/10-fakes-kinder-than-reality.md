# My test doubles were lying to me

*Draft for LinkedIn. Not posted — note the gap between "all tests pass" and "it works".*

---

I wrote about building a system whose two most important components I couldn't run — a
speech recognizer and a language model, on a machine that can host neither. The approach
was to define a small interface for each and write a simple working stand-in behind it. The
rest of the system runs on the stand-ins, and it works.

It does work. Four hundred tests pass. But this week I found a bug that would have made the
real thing unusable on the first attempt, and the reason no test caught it is worth more
than the bug.

## The bug

Speech recognition takes a few hundred milliseconds per pass. My server is a single-threaded
event loop — it handles the audio arriving, the connection to the display, everything — and
I was calling the recognizer directly inside it.

So every time it transcribed, the entire server stopped. Audio queued up behind it. The
display stopped updating. On a slow machine it would look like a freeze, several times a
second, forever.

Worse: the model loads the first time you transcribe, and on a first run that means
*downloading* it. Minutes. With audio already arriving and the interface already open and
apparently dead. Someone trying this for the first time would conclude their hardware
wasn't up to it and stop.

## Why four hundred tests missed it

**My fake recognizer returns instantly.**

That's it. That's the whole reason. The stand-in does no work, so blocking the event loop
with it blocks nothing for no time, and every test passes comfortably.

The fake wasn't wrong, exactly. It implements the interface correctly, it produces the right
shape of output, it exercises every code path around it. But it has one property the real
thing doesn't have — it's fast — and the entire bug lived in that gap.

Once I saw it, the pattern was everywhere.

## Checking the rest of them

I went looking for every other way my stand-ins were kinder than reality. There were three
more, each hiding a failure path I had no handling for.

**My fake language model never hangs.** A real one does — an overloaded server, a model
mid-swap, a connection that opens and then produces nothing. My code waited forever. The
display would have sat there saying "writing…" for the remainder of the call. There was no
deadline because nothing had ever failed to meet one.

**My fake never drops a connection mid-sentence.** A real server does. I had error
handling, but it had never actually run, which is a different thing from not existing.

**My fake recognizer never raises.** A real one can — malformed audio, a bad moment for
memory. That exception would have propagated up and killed the audio connection, turning a
one-off transcription hiccup into a dead microphone for the rest of the conversation.

Every one of those is a bad day for a user. Every one passed four hundred tests.

## The fix is to make the fakes capable of being nasty

Not to replace them. They're still the only way to develop this system at all. But they
needed the ability to misbehave:

- The language model stand-in can now be told to hang forever, or to fail after N chunks.
- The recognizer stand-in can be told to raise on the Nth call.
- One test uses a deliberately slow recognizer and asserts the display still responds
  *while* it's working — which is exactly the property that was broken, now pinned down.

The fakes got about twenty lines bigger. Those twenty lines made four real failure paths
testable, and all four needed fixing.

The thing I'd underline: **the failure modes had to be added to the fakes before the
handling could be written.** I didn't write the deadline and then test it. There was no
deadline, because nothing had ever been slow. The stand-in's politeness had removed the
problem from view entirely, and I only went looking because I asked a direct question — what
is my fake doing that the real thing won't?

## The question to ask about your own

If you're developing against a stand-in of any kind — a mock service, an in-memory
database, a seeded fixture, a local emulator — it's worth writing down explicitly:

**What does this do reliably that the real thing does not?**

Common answers, all of which hide real bugs:

- **It's instant.** Hides every concurrency problem, every blocked thread, every missing
  timeout.
- **It never fails.** Hides every retry path, every error message, every partial state.
- **It's ordered.** Hides race conditions that real concurrency produces.
- **Its data is small and clean.** Hides pagination, truncation and encoding problems.
- **It's always available.** Hides startup and reconnection entirely.

You don't have to fix all of them. But each one is a category of bug your test suite is
structurally incapable of finding, and knowing which categories those are tells you what to
check by hand — or what to teach the fake to do.

Mine was "it's instant." It cost me one bug that would have ruined the first real session,
and three more I found by asking the question properly.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
