# The AI assistant that couldn't keep up with my internet

*Draft for LinkedIn. Not posted — review before publishing.*

---

A few weeks ago I tried one of those AI "interview copilots." The pitch is simple enough:
it listens to your video call, and when the other person asks you something, a short
suggestion appears on your screen. A reminder of the project you're trying to recall, the
shape of an answer, a term you've blanked on.

It didn't work for me. Not because the suggestions were bad — they were fine. It didn't
work because they arrived about eight seconds late.

By then the conversation had moved on. I'd already started answering. Reading a hint
about a question I was halfway through answering was worse than having no hint at all,
because it split my attention at exactly the wrong moment.

The problem wasn't the AI. It was my upload speed.

## Where the time actually goes

Here's what one of these tools does between you hearing a question and seeing a
suggestion.

Your computer captures the audio from the call. It sends that audio over the internet to
a server somewhere. The server converts speech into text. It feeds that text, along with
your résumé and whatever else it knows about you, into a large language model. The model
writes a suggestion. The suggestion travels back over the internet to your screen.

Four of those six steps are fast. Speech-to-text is quick on a decent machine. Language
models generate their first words in a fraction of a second when they're warm. The
round-trip over a good connection is tens of milliseconds.

The one that isn't fast, on my connection, is sending the audio **up**.

Most home internet is asymmetric. You get a big download pipe and a small upload one,
because the people who designed consumer broadband assumed you'd mostly be consuming. A
connection advertised as "100 down" might give you 5 or 10 up, and that's before you
account for the video call already running, which is itself uploading your camera feed
the entire time.

So the audio trickles up. The server does its clever work in half a second. The answer
comes back fast, because downloads are the direction that works. And the total is still
eight seconds, because one leg of the journey was the bottleneck the whole time.

## The fix is embarrassingly obvious

Don't send the audio anywhere.

Run the speech recognition and the language model on a computer you already own, on your
own network. The audio travels from one machine in your house to another machine in your
house. That trip takes about a millisecond, and your internet connection isn't involved
at all.

This used to be unreasonable advice. Running a useful language model meant a server rack
or a cloud bill. That changed quietly over the last two years. The models got smaller
without getting much worse, the tools for running them got dramatically better, and the
hardware caught up from the other direction — the Mac mini on my desk has enough memory
to hold a capable language model and a speech recognizer at the same time, and it cost
less than a year of the subscription it replaces.

So I'm building it. It's called Susurro, it's open source, and the whole design follows
from one constraint: **the audio never leaves the network.**

## What that constraint does to the design

It's a good constraint, because it forces decisions instead of deferring them.

You can't hide behind a datacenter. If a model is too slow, you can't scale up a bigger
instance — you have to pick a smaller model or write a shorter prompt. Every latency
problem becomes visible immediately instead of being absorbed by someone else's hardware.

It changes what you're allowed to build. A suggestion that takes twenty seconds to write
is useless in a live conversation, whatever its quality. So suggestions have to be short.
Not "we prefer brevity" short — short because the machine physically cannot produce more
words than that in the time available. The hardware is making a product decision, and
it's making the right one.

And it makes a question disappear that I didn't enjoy thinking about: where does this
recording end up? Interviews and business calls are exactly the conversations that
shouldn't be sitting in a third party's logs indefinitely. When the audio never leaves
your house, that isn't a privacy policy you have to trust. It's just a fact about how the
system is wired.

## Honest status

Nothing is finished. There's a documented architecture, a public repository, and a plan
whose very first step is a benchmark — measuring how fast the models actually run on that
Mac mini before writing the code that depends on the answer.

That ordering matters more than it sounds, and it's what I want to write about next.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
