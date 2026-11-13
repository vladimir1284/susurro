# I'm building a system I can't run

*Draft for LinkedIn. Not posted — review before publishing.*

---

The tool I'm working on needs two things to function: a speech recognizer and a language
model, both running locally. The machine I'm writing the code on can host neither. It's a
small container on a rented server — two CPU cores, no graphics card, no sound hardware
at all. It can't even open a microphone.

By the obvious logic, there's nothing to do until I'm sitting in front of the right
machine.

I've got a working system anyway. Not a finished one, but one where audio goes in one end
and transcripts and suggestions come out the other, end to end, under test. Here's how,
because the technique generalizes to a lot of situations that look like being blocked and
aren't.

## Separate what you can't run from what you can

The first move is to look honestly at which parts of the system actually need the thing
you're missing.

In my case: the recognizer needs a model. The suggestion generator needs a model. And
that's it. Everything else — the network protocol, the audio plumbing, the session
lifecycle, deciding which of two speakers said what, the rule that a suggestion already
being written gets abandoned when a new question arrives — none of that touches a model.
It just *talks* to one.

That's typically the shape of it. The component you can't run is usually smaller than the
system built around it, and the system around it is usually where the bugs live.

So I defined the smallest possible description of what the rest of the system needs from
a model. For the recognizer: feed it audio, get back transcript fragments, some marked
provisional and some marked final. For the generator: hand it the background once, then
ask it for suggestions and receive text as it's produced.

Two small interfaces. Everything else depends on those, not on any particular model.

## Then write stand-ins that are honest about being stand-ins

Behind each interface, a fake.

My fake recognizer reads from a script. You hand it a list of sentences, tell it which
speaker says each one, and it emits them as audio arrives — pacing itself by frame count,
emitting provisional versions first and a final version at the end, exactly like the real
thing. It completely ignores the actual sound.

My fake suggestion generator returns canned text, a chunk at a time, with a configurable
delay so I can simulate a slow model and test what happens when a user cancels halfway
through.

These are not mocks in the testing-library sense — not objects that record calls so you
can assert they were made. They're small working implementations of the same contract,
and the system genuinely runs on them.

There's a trap here worth naming: a fake that's too convenient. If my fake generator
responded instantly and never failed, I'd build a system that only works when the model
is fast and reliable, which is the opposite of true. So the fakes are deliberately
awkward where reality is awkward — the delay knob exists precisely so cancellation gets
tested.

## The fake can test things the real thing can't

This is the part I didn't anticipate, and it's the strongest argument for the whole
approach.

The most important performance property in my system is that the background material —
which is long — gets processed by the model **once per conversation**, not once per
suggestion. Get that wrong and every suggestion takes four seconds instead of under one.

With a real model, verifying that means measuring timings, which is slow, noisy, and
dependent on the hardware you happen to be sitting at. A test like that fails
intermittently and eventually gets deleted.

With my fake, the generator simply counts how many times it was asked to process the
background. The test asks for three suggestions and asserts the count is one. It runs in
milliseconds, it's completely deterministic, and it will fail the instant someone
refactors in a way that reintroduces the cost.

That test is possible *because* there's no model. The property being checked isn't a
property of the model at all — it's a property of my code's calling pattern. Putting a
real model behind it would only add noise.

A lot of "we can't test this without the real system" turns out to be this: the thing
worth asserting is about your own code, and the real dependency was obscuring it.

## Say clearly what hasn't been run

The other half of working this way is discipline about the gaps.

My repository has a Linux audio capture module. It's written, reviewed, and plausible.
**It has never been executed.** There's no sound hardware here to execute it against.

So the first line of that file says so, in those words, and explains that it should be
treated as a first draft until it's run on real hardware. The project plan has a
milestone whose entire purpose is to run it and find out what's wrong.

That's not modesty. It's the difference between a known gap and a surprise. Unrun code
that's labelled unrun is a task. Unrun code that looks finished is a trap for whoever
touches it next — including me, in three weeks, having forgotten.

The same applies to numbers. Anything I measured on this container is labelled as having
come from a two-core machine with no accelerator, and is useless for deciding anything
about the target hardware. The temptation to quote a number because you have one is
strong, and a number from the wrong machine is worse than no number — it feels like
evidence.

## The shape of it

Find the thing you can't run. Draw the smallest line around it. Write a usable stand-in on
the other side of that line. Build and test everything else for real. Label what hasn't
been run, in the code, where the next person will see it.

What you end up with when the real hardware shows up isn't a prototype you have to throw
away. It's a system with two components left to fill in, and a test suite waiting to tell
you whether they work.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
