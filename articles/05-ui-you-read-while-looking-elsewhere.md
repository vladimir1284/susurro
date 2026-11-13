# Designing an interface for someone who isn't looking at it

*Draft for LinkedIn. Not posted — review before publishing.*

---

Most interface design assumes the user is looking at the screen. It's such a basic
assumption that it's rarely stated: you arrange things so the eye can find them, and the
eye is there to do the finding.

I've been building something where that assumption is false, and it's been a useful
exercise in noticing which design habits were actually load-bearing and which were just
habits.

The thing is a copilot for video calls. It listens to the conversation and writes short
suggestions — a reminder of a project you're trying to recall, the shape of an answer.
The suggestions appear on a second screen: a phone propped beside the laptop, or a tablet
off to the side.

The user is looking at a camera. They're mid-sentence. Their attention budget for my
interface is somewhere around half a second, and they'd rather spend none at all.

That constraint knocks out a lot.

## Nothing can require a decision

Any element that makes the user choose between two things costs more attention than it
saves. A dropdown, a tab bar, a settings panel, two buttons that both plausibly do what
they want — in a normal app these are mild friction. Here they're disqualifying, because
the decision happens *during* a sentence the user is also trying to finish.

So the screen has three buttons — start, hint, stop — and at any moment at least one is
disabled. There is never a question of which one applies. The configuration that would
normally live in a settings panel lives in the URL instead: you set it up once before the
call, and during the call there is nothing to configure.

That sounds restrictive. In practice it clarified the product, because every feature that
wanted a setting had to justify being configurable at all, and most couldn't.

## Reading has to be possible from the corner of the eye

Peripheral vision is bad at letter shapes and good at position, contrast, and movement.
Which means text that gets glanced at rather than read needs to be bigger than text that
gets read properly — the opposite of the usual instinct, which puts the "important"
content in a modest size and reserves large type for headings.

The suggestion area uses the biggest type on the screen, and it always lives in the same
place. Not because suggestions are the most important thing semantically, but because
they're the only thing anyone will try to read without turning their head.

The transcript — which is the bulk of the content — is deliberately smaller and quieter.
It isn't for reading. It's for *glancing at to confirm the system is still keeping up*,
which is a different job with different requirements. You need to see that words are
appearing. You don't need to read them.

That distinction turned out to matter more than any font choice. Once I separated
"content the user reads" from "content that proves the system is alive," the layout
designed itself.

## Say what's provisional

Live speech recognition produces rough guesses first and corrects them as more audio
arrives. The words appear, then shift.

The temptation is to hide this — wait until text is settled, then show it cleanly. I
tried that. It felt broken. A system that shows nothing for two seconds and then a
sentence is indistinguishable, in the moment, from a system that has crashed.

So provisional text is shown, dimmed and in italics, and settles into normal text when
it's final. The flicker is honest. It's also the fastest possible signal that recognition
is tracking the conversation — faster than any status indicator, because it *is* the
work, not a report about the work.

## Remove things that have expired

My tool sometimes starts writing a suggestion before the user asks for one, as soon as
the other person stops talking. If they start talking again, that suggestion is abandoned
halfway through.

The obvious treatment is to grey it out: it's old, but maybe still useful.

It isn't. It answers a question the conversation has moved past, and the user has no
attention to spare for working out which of two things on screen is current. A stale
suggestion sitting next to a fresh one isn't extra information — it's a decision, and
we've already established decisions are too expensive.

So an abandoned suggestion is removed entirely. Gone. The screen shows one current thing
or nothing, and nothing is a perfectly good state.

There's a test in my repo asserting exactly this, with a comment explaining why, because
it's the kind of behaviour a future contributor would reasonably "fix."

## Don't move things under the reader

The transcript scrolls to follow the conversation. But if the user has scrolled up to
look at something, it stops following until they scroll back to the bottom.

This is standard chat-app behaviour and takes about four lines of code, and I'm
mentioning it because the cost of getting it wrong is higher here than in a chat app. If
someone has deliberately looked away from the live edge to find something, yanking them
back isn't just annoying — it's spending the attention they borrowed from a conversation
they're trying to have.

## What generalizes

Not the specific decisions, which are mine. But the question that produced them is worth
borrowing:

**How much attention does this interface actually get, and what is the user doing with
the rest of it?**

Most answers aren't "all of it, and nothing else." Dashboards get watched while something
else is being done. Alerting gets read while context-switching. Anything on a second
monitor is, by definition, competing with a first monitor.

If you can estimate the real attention budget — half a second, two seconds, one glance —
a lot of design arguments resolve themselves. Font size stops being taste. Removing a
feature stops being a loss.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
