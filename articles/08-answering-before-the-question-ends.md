# Starting the answer before the question is finished

*Draft for LinkedIn. Not posted — review before publishing.*

---

The feature I added this week does something that sounds slightly rude when you say it out
loud: it begins composing a response the instant the other person stops talking, without
waiting to be asked.

The context is a tool that listens to a video call and offers short suggestions on a
second screen. The number it lives on is how long after a question ends a suggestion
appears. Under a second is useful. Over about four is worse than nothing.

The naive design waits for you to press a key. That turns out to be the expensive part,
and not for the reason you'd guess.

## Where the second goes

When you decide you want help, that decision itself takes a moment. You hear the question,
you process it, you realise you're stuck, you reach for the key. Call it a second, and
it's a second of pure overhead — nothing is being computed during it.

Only then does the work start: the model reads the new part of the conversation, and
begins writing. Even with everything optimised, that's another half second before the
first word appears.

So the real latency isn't the model's. It's your deliberation plus the model's work, in
series, when they could have been in parallel.

The fix is to stop waiting for the key. Start writing when **the other person stops
talking** — because that's the moment the question exists, and it's observable without
anyone deciding anything. By the time the user reaches for the key, the first line is
already on screen.

When it's wrong, nothing is lost but some electricity on a machine that was otherwise
idle. When it's right, the wait disappears entirely.

## Which means you need to detect silence, which is harder than it sounds

"They stopped talking" seems like it should be trivial. Audio is loud, then it isn't.

Two things make it hard.

The first is that loudness isn't speech. A door closing is loud. Typing is loud. Someone
else's dog is loud. I wrote a simple loudness threshold first, and it is completely honest
about what it is — it cannot tell a voice from a slammed door. What actually works is a
small neural model trained specifically to recognise human speech. The one I'm using is
two megabytes and runs in about a millisecond. Worth every one of those bytes: broadband
noise, which fools a loudness threshold completely, it correctly ignores.

The second problem is the one that nearly sank the feature.

## The pauses inside a sentence are longer than you think

Think about how people actually talk. "So tell me about a time when you... *(pause)* ...had
to disagree with someone senior."

That pause is not the end of the question. But it is, unambiguously, silence — often half
a second of it, sometimes more. It happens mid-sentence, while someone gathers a thought,
takes a breath, or reaches for a word.

If your detector declares "they've finished" at the first quiet moment, you don't get a
slightly early answer. You get a confident, well-written suggestion responding to *half a
question* — which is worse than no suggestion at all, because it looks correct. The user
reads it, it's about the wrong thing, and they've now spent their attention being actively
misled while someone is still mid-sentence.

The fix is a delay before declaring the end: silence has to persist for roughly half a
second before it counts. There's a corresponding, much shorter delay in the other
direction, so a single loud moment doesn't open an utterance.

Those two numbers are the whole feature. I've written a comment in the code saying the
closing one is the one that matters, because the next person to tune it will be tempted to
lower it to make things feel faster, and that trade is much worse than it looks.

## The bug I nearly shipped

Here's a failure that only showed up when I wrote the test, and I think it's the most
interesting thing in this whole piece.

My transcription runs on a cycle: it re-reads the recent audio about once a second and
updates what it thinks was said. The finalised text — the version it won't revise — is
what gets handed to the suggestion model.

So: the other person finishes a question. My silence detector fires immediately. I start
writing a suggestion.

And the transcript doesn't contain their question yet. The transcription cycle hasn't
reached the point of finalising it.

The suggestion would have been generated against the conversation *up to just before the
question was asked* — confidently answering the previous topic. It would have looked like
a quality problem. It would have been diagnosed as a prompt problem. People would have
tried different models.

The fix is one line: when the turn ends, tell the recognizer to finalise what it has right
now, before generating. The silence detector doesn't just trigger the suggestion — it
also tells the transcription where the utterance ended. Which, in hindsight, is obviously
what it should have been doing all along. The two components were each doing half of a job
that belonged to one of them.

## Two smaller decisions, both about trust

**Only their silence counts, not yours.** The user talking is not a reason to write them a
suggestion. They're already saying the thing. My microphone and the incoming call audio are
captured as separate streams, so this distinction is free — there's no guessing about who
spoke.

**A suggestion you asked for is never thrown away.** If someone starts talking again, an
automatic suggestion gets abandoned — it's answering a question that's been superseded.
But one the user explicitly requested stays, even if the room gets noisy. A button that
sometimes silently discards its own work is a button nobody trusts, and the trust is worth
more than the tidiness.

## What I haven't verified

The detector is tested against silence, against broadband noise, and against a pure tone.
It correctly identifies all three as "not speech."

It has never been tested on actual speech.

There's no speech audio in the repository, and synthesising something a speech model
accepts isn't a test of anything. So the positive direction — that it fires when a real
person really talks — is unverified, and the project's milestone for this feature says so
in those words rather than being ticked off.

I could have left that out of the writeup. It's the kind of gap nobody would notice. But a
"done" that quietly means "the half I could check" is how projects acquire the bugs that
surface in front of users, and writing it down is most of what stops me from forgetting.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
