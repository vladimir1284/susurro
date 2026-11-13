# Two microphones, zero machine learning

*Draft for LinkedIn. Not posted — review before publishing.*

---

I'm building a tool that listens to a video call and transcribes both sides of the
conversation in real time. Early on I hit a problem that looked like it needed a
sophisticated answer, and turned out to need no answer at all.

The problem is called **speaker diarization**: given a recording with several people in
it, work out who said what. It's a genuinely hard research area. The usual approach is to
chop the audio into short pieces, convert each piece into a numerical fingerprint of the
voice, cluster the fingerprints, and hope that each cluster corresponds to one person.

It works reasonably well. It is also slow, memory-hungry, and wrong often enough to
matter. And when it's wrong, it's wrong in a particularly annoying way: it attributes one
person's sentence to another. In my application that means a suggestion generated for a
question *I* asked, as though the other person had asked it. Confidently useless.

I spent a while reading about which model to use. Then I realised I was solving a problem
I had accidentally created.

## Where the information went

When you're on a video call, your computer has two completely separate audio streams
flowing through it.

One is your microphone: the sound of you talking.

The other is what your speakers are playing: the sound of everyone else.

These are not mixed together anywhere in the operating system. They're separate because
they have to be — your microphone feed goes out to the network, the incoming feed comes
in from it. Mixing them would break the call. The separation is structural.

The standard way to record a meeting is to capture "the audio" — one combined stream,
both sides blended into a single waveform. And the moment you do that, you have thrown
away the single most reliable piece of information in the entire system: which direction
the sound was travelling.

Then you reach for a machine learning model to recover it.

## Don't mix them

So: capture both streams, keep them separate, transcribe them separately.

Everything that came out of your microphone is you. Everything the speakers played is
them. Not "probably," not "with 94% confidence." It's not an inference at all — it's
where the bytes came from.

No model. No clustering. No fingerprints. No memory budget spent on a diarizer, which
matters a lot when you're running everything on one small machine and every gigabyte is
contested. And no error mode, because there's nothing being guessed.

On Linux this costs nothing: the audio system exposes a "monitor" of whatever is being
played, right next to the microphone input. On macOS you need a small free utility to
expose the system output as a recordable device, which is mildly irritating and still far
less work than running a neural network.

## The part I didn't expect

Having the two streams separate didn't just solve the attribution problem. It handed me a
feature I hadn't planned.

I want suggestions to start being written *before* the user asks for one — the moment the
other person finishes a question, rather than when the user presses a key. By the time
they decide they want help, the first words should already be on screen.

To do that, I need to detect one specific event: **the other party stopped talking.**

On a mixed stream, that's awkward. Silence in the combined audio means nobody is talking,
which isn't the same thing. If I'm talking over them, there's no silence at all. You'd
end up writing heuristics about who was dominant in which frequency band, and those
heuristics would be wrong during the exact moments that matter most.

On separate streams it's trivial. Point a simple voice-activity detector — a tiny,
decades-old piece of signal processing, not a language model — at the incoming stream
alone. When it goes quiet, they've stopped. What I'm doing is irrelevant to it.

A decision I made to avoid a machine learning model also gave me a clean trigger for the
feature I cared most about. That's usually the sign of a good architectural choice: it
pays twice.

## The general lesson

The pattern here isn't about audio. It's this:

**Before you reach for a model to infer something, check whether you destroyed the
information yourself.**

A surprising amount of applied machine learning is reconstructing facts that were present
earlier in the pipeline and got flattened away — usually because the flattening happened
in a default, in a library's convenience function, or in a format that someone chose for
different reasons. Recovering it statistically is always more expensive and always less
accurate than not discarding it.

It's worth asking the question before you start comparing models. Sometimes the answer is
that the information really is gone and inference is the only option. And sometimes you
just forgot to keep the two cables apart.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
