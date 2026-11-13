# The 4-second answer and the 0.4-second answer are the same answer

*Draft for LinkedIn. Not posted — review before publishing.*

---

I'm building a tool that whispers suggestions to you during a live conversation. The
whole thing lives or dies on one number: how long after someone finishes asking a
question does the first word of a suggestion appear.

Under about two seconds, it's useful. Over about four, it's actively harmful — you end up
reading a suggestion about a question you've already started answering, and now you're
doing two things badly instead of one thing adequately.

So I went looking for where the seconds go. Most of the answer turned out to be a thing
that is easy to miss if you've only ever used language models through a chat window.

## Two very different kinds of slow

When a language model answers you, it does two separate jobs.

First it **reads** everything you gave it: the instructions, the background, the
conversation so far. In the jargon this is "prefill," and it's the model building up an
internal representation of your input before it says anything.

Then it **writes**, one word-fragment at a time, each one depending on everything before
it.

These two jobs have completely different performance characteristics, and people
conflate them constantly. Writing is slow per word but steady — a small model on a modern
desktop machine produces maybe fifteen to twenty fragments a second. Reading is fast per
word but you pay it on *everything you sent*, all at once, before the first word comes
out.

For a chat message that's fine. You type three sentences, the model reads them in
negligible time, and starts answering.

My situation is different. My model's input isn't three sentences. It's a page of
instructions about how to write a useful suggestion, the user's entire professional
background, a description of who they're talking to and why, their own private notes, and
a running transcript of the conversation so far. Call it three thousand words.

Every single time I ask for a suggestion, the model would re-read all of that from
scratch. Before saying a word. That's where the seconds went.

## The part that doesn't change

Here's the thing that makes it fixable: **almost none of that input changes between one
suggestion and the next.**

The instructions are the same. The background is the same. The notes are the same. The
only thing that's different is the handful of new sentences that have been spoken since
last time — maybe thirty words out of three thousand.

So: don't re-read it.

When the model reads your input, it builds up intermediate state — a big pile of numbers
that represents "everything I've read so far." If the beginning of your input is
identical to last time, that intermediate state is identical too. You can keep it and
start from where you left off.

This is usually called prompt caching, or prefix caching, and essentially every serious
way of running a language model locally supports it. Done right, the sequence looks like
this:

- **Once, when the session starts:** read the full three thousand words. Takes a while.
  Nobody is waiting — the conversation hasn't begun.
- **For every suggestion after that:** read only the thirty new words. Essentially
  instant.

Same model. Same hardware. Same quality of answer. The waiting mostly disappears.

## The constraint this puts on everything else

The catch is the word *prefix*. The saving only works if the unchanged part comes
**first** and stays byte-for-byte identical. Change anything early in the input and
everything after it has to be re-read, and you're back to four seconds.

This sounds like a minor implementation detail. It is not. It reaches into design
decisions that otherwise look unrelated:

**The transcript can only ever be appended to, never edited.** Live speech recognition
normally produces rough guesses that it revises as it hears more — the words flicker and
correct themselves as you speak. Those revisions can never go into the model's input.
Only finalized, never-to-be-revised text does. Rewriting an earlier line would invalidate
everything after it.

**The background can't change mid-conversation.** My tool refuses to let you switch
profiles while a session is running. Not because it's hard, but because it would silently
throw away the cache and make everything slow for reasons the user cannot see. A refusal
with an explanation beats a mysterious slowdown.

**Section order has to be fixed.** The background, role and notes get assembled into one
block of text. If that assembly ever produced the sections in a different order — say,
because they came out of a dictionary that got rebuilt — the text would differ, the cache
would miss, and everything would quietly get slower with no error anywhere. There's a
test in my repo that does nothing but assert the same profile produces byte-identical
text twice. It looks like a pointless test. It's guarding the single most important
performance property in the system.

## The one thing worth checking in your own code

If you're building anything that calls a language model repeatedly with mostly-similar
input — an agent loop, a document processor, a classifier running over a stream — this is
worth twenty minutes of your time.

Look at what you send on each call. Ask how much of it is identical to last time, and
whether the identical part comes first. If a stable chunk is sitting at the end, or
interleaved with things that change, moving it to the front may be the entire
optimization.

And the way you'd find out is not by reading about it. It's by measuring the same request
twice and seeing whether the second one is faster. Which brings me to the thing I keep
insisting on in this project, and which I'll write about next: measure the machine before
you write the code that assumes an answer.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
