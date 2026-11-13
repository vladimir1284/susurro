# The two lines of code that decide whether your AI feature is fast

*Draft for LinkedIn. Not posted — review before publishing.*

---

I added the real model backends to my project this week — the actual speech recognizer,
the actual connection to a language model, replacing the stand-ins I'd been building
against. It took less time than I expected, and the reason is a decision I made weeks
earlier that didn't look important at the time.

It's worth explaining, because the same decision shows up in nearly every project that
touches a language model, and the common way of making it quietly costs you the thing you
most need.

## The usual way

Most code that calls a language model has one function. You pass it everything — the
instructions, the background, the conversation, the question — and it returns an answer.
One call in, one answer out.

That's the shape of the APIs, it's the shape of every tutorial, and it works fine until
performance matters.

Here's why it stops working. When a model answers, it first has to **read** everything you
sent, and only then starts **writing**. The reading is proportional to how much you sent.
In my case that's a few thousand words of background that is *identical on every single
call* — the user's professional history doesn't change between one question and the next.

Every serious way of running a language model can skip re-reading an unchanged beginning.
It keeps the intermediate state from last time and resumes. The saving is the difference
between a four-second wait and a sub-second one.

But you cannot use that saving through a one-function interface. "Here is everything,
give me an answer" has no way to say "the first ninety percent of this is what you saw
last time." The optimization is invisible to the shape of the call.

## Two functions instead of one

So my interface has two:

```
prefill(background)        # once, when the session starts
stream(conversation)       # every time a hint is wanted
```

The first processes the unchanging part and says nothing. The second appends what's new
and generates.

That's the whole idea. It's not clever. But splitting it changed three things that have
nothing to do with the model.

**It made the requirement visible.** A new contributor implementing a backend sees two
methods and immediately asks why. The answer is in the docstring. In the one-function
version, the requirement would live in a comment somewhere, and it would be violated
within a month by someone making a reasonable-looking change.

**It made the requirement testable without a model.** My fake backend counts how many
times `prefill` was called. A test asks for three hints and asserts the count is one. That
test runs in milliseconds, is completely deterministic, and fails instantly if someone
refactors in a way that reintroduces the cost. Timing-based tests for the same property
would be slow, flaky, and eventually deleted.

**It pushed a constraint outward, where it belonged.** For the saving to work, the start
of the input must stay byte-identical. Once that's expressed in the interface, it stops
being a model detail and becomes a rule the rest of the system has to follow — and the
rest of the system is where it actually gets violated:

- Live speech recognition revises its guesses as it hears more. Those revisions can never
  enter the model's input; only finalized text does.
- The background can't be swapped mid-conversation. My code refuses it, with an error
  message explaining why, rather than silently making everything slow.
- The background sections are assembled in a fixed order, with a test asserting the same
  input produces identical text twice. That test looks pointless. It's guarding the most
  important performance property in the system.

None of those three are things I'd have thought about if the model call were one function.
They surfaced because the interface asked a question the rest of the design had to answer.

## The same move, on the other side

I did the equivalent thing with audio and got the same kind of payoff.

Audio arrives compressed and has to be decompressed before anything can transcribe it. The
obvious place to decompress is inside the recognizer, which is what needs the audio.

I moved it out, into the layer above. Every recognizer wants plain audio; none wants the
compressed format. Putting the decompression in the shared layer means a new recognizer —
a different model, a different vendor, a faster English-only one — never has to think
about the wire format at all. It receives audio and returns text.

The test for this one is almost embarrassing: each audio stream gets its own decoder,
because the decompressor carries state between packets and sharing one between two
speakers corrupts both. One line of code, one test, and a class of bug that would have
been maddening to diagnose — two voices intermittently garbled, with nothing in the logs.

## What to actually take from this

When you're wrapping something expensive — a model, a database, a remote service — the
interface you draw determines which optimizations stay available to you.

A single "do the thing" function is the easiest interface to write and the easiest one to
test against, and it hides exactly the structure that performance work needs: what's
repeated, what's cacheable, what changes between calls.

The question worth asking before you write that wrapper is simple:

**What does the expensive thing do more than once that it didn't need to?**

If there's an answer, your interface should have a seam where that repetition can be
removed. Not because you'll optimize it today — you probably shouldn't — but because an
interface without the seam means the optimization isn't a change to one file later. It's
a rewrite.

I haven't measured any of this yet, incidentally. The hardware it's meant to run on is a
small desktop machine I'll be in front of next week, and the machine I'm writing on can't
host the models at all. Every number in my documentation is currently labelled as an
estimate, and the first milestone is replacing them with measurements. If the measurements
say something different from what I've argued here, the measurements win.

But the seam is in place, which means acting on them is a configuration change rather than
a rebuild.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
