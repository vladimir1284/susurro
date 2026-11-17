# The first real call broke three things my tests said were fine

*Draft for LinkedIn. Not posted — review before publishing. Numbers are from one call and one machine.*

---

I had written about my stand-ins being kinder than reality. Last week I stopped using stand-ins.
I put my father on the other end of a Google Meet call, he played an interviewer reading a script,
I answered, and a copilot running on my own machines listened to both sides and tried to suggest
what I should say.

Everything fell over, in a useful order.

## What broke

**Transcription fell minutes behind.** I was sending *all* audio to the speech recognizer, silence
included, on both lanes. The recognizer serves one request at a time. Two lanes of mostly silence
were enough to saturate it. The fix was to let the voice detector decide what the recognizer hears:
the same call went from 75 requests and 43 seconds of server time to 15 requests and 8 seconds.

**Then partial results piled up.** While someone talks, I re-transcribe a growing window every
second so the screen updates live. When the server slowed down, those requests queued, and
final answers arrived 59 seconds late. Now a lane that falls more than 1.5 seconds behind stops
asking for partials. Finals are never held back. With the server simulated at 1.5 s per request, 59 seconds late became 5.5.

**And the connections died.** My server read audio and recognized it in the same loop, so while it
waited on the recognizer it stopped answering the connection's keepalive pings, and the
connection was cut. The audio reader and the recognizer now talk through a queue. The test that
proves it fails with the old handler and passes with the new one.

A fourth bug was quieter: after one dropped connection the capture client crashed on an error that
simply meant "I still think you're connected", and never came back. It now retries.

## The part about tests

None of this was visible to the suite. Not because the tests were bad, but because I had
written them against fakes that were instant, silent and polite. Real speech has pauses, real servers
slow down, real clients drop. The rule I took from it: **reproduce the failure offline first,
then show that your test fails without the fix.** I recorded the call and built a replay tool that
plays it back at real speed through the real pipeline and scores the transcript. Every fix above
was proved against that recording.

## What I could not trust, and said so

- **One call.** Roughly 100 words per lane, one script, two voices. A difference of one or two words
  means nothing. I report these as measurements, not benchmarks.
- **A rubric that measures the wrong thing.** My check that a suggestion "answers the question"
  counts keywords. It measures on-topic, not good. Before the fix, suggestions about what to ask
  about remote monitoring said "propose a two-day site visit", because the profile notes said to book
  one. After a closing instruction and a rewritten prompt, 16 of 18 started on the topic (from 8),
  and the off-topic site visit went from 11 of 15 to 0. I tested one mode of three.
- **Speakers.** My microphone picked up my father through the speakers and transcribed him as me.
  It is separable by level in this recording, but that depends on the room. The honest
  answer is headphones; I did not ship a rule I couldn't test in the dangerous case, me talking
  over him.

## What the speed numbers said

After the fixes, replayed at real time, finals arrived 1.2 seconds late on average and 2.6 at worst,
with no dropped lanes. With 1.2 s of extra delay added to every request, to imitate the server on
a bad hour, that became 6 and 10 seconds. It degrades; it does not collapse.

*Update, a day later: the "bad hours" I was imitating were real, and not the server's fault. The Mac
mini was going to sleep for about 13 seconds every minute. With that fixed, the same replay with
real hints running gave finals 0.8 seconds late on average and 1.8 at worst, and no stalls.
The next article has the story.*

I also tried the bigger Whisper model, expecting it to win. On this call it did not: better on one lane,
worse on the other by a few words, missing two key terms, and three times slower. That
says "not worth it here", not "never". A faster engine, Parakeet, lost too, for a less obvious reason: the next article.

## The question for you

If your system works in tests, ask when it last met something it did not choose: a real user, a
real network, a real hour of the day. The fakes I wrote were not wrong. They were just never
tired, slow or interrupted.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
