# The faster speech model lost, and the reason wasn't speed

*Draft for LinkedIn. Not posted — review before publishing. Numbers are from one recorded call and one Mac mini.*

---

My copilot transcribes with Whisper's small model, and on a bad hour the server behind it slows
down enough that answers arrive late. The tempting move was obvious: swap in a faster, newer
model. Nvidia's Parakeet covers Spanish, runs natively on Apple silicon, and even ships a streaming
mode that would remove the thing I think is the root cause, re-reading a growing window of audio
every second.

So I did it properly: downloaded 2.5 GB over a slow connection, installed it in an isolated
environment, put it behind the same interface, and replayed the same recorded call at real speed.

## What I measured

| | Whisper small + vocabulary | Parakeet, batch |
|---|---|---|
| Error rate, interviewer lane | 11.5 % | **10.3 %** |
| Error rate, my lane | **5.5 %** | 24.5 % |
| Key terms heard (of 9) | **9** | 6 |
| Typical request | 0.3–0.5 s | **0.12–0.17 s** |

It is faster per request, as promised. It is also much worse on my lane, and the reason is not
the model's ears. Whisper accepts a short prompt before it decodes, and I feed it the names and
acronyms from the user's profile: *Modbus TCP*, *TimescaleDB*. That one trick had taken my lane's
error from 13.6 % to 5.5 %. Parakeet has no such input, so it wrote "Mobbus" again.

The capability I rely on most was the one I hadn't thought to put in the comparison table.

## The streaming mode

Streaming was the real reason for the experiment. On the same audio it produced broken sentences
with English words appearing in the middle of Spanish ones, and it cost 0.6 to 0.8 seconds of
compute per second of audio, per lane. Two lanes would not keep up. The setting that makes it
equivalent to a normal pass was so slow it never finished 150 seconds of audio in eight minutes.

I also saw a few requests take 14 to 26 seconds with no pattern I could find. I ruled out one
explanation (recompiling for a new window length) and did not find the cause. A faster median
with unexplained stalls is not a win in a live conversation.

I closed the experiment and deleted what I'd installed. The cost was an evening and a download.
Without the replay, the cost would have been finding this out mid-call.

## A smaller lesson: tuning a number on one recording

While I was at it, I tuned the silence that ends a turn. At half a second, the copilot cut my
father's first question at its comma and started answering half of it. At 0.77 s it didn't. At one
second nothing more improved, and at one and a half it merged a genuine hesitation at the cost of a
full second of delay on every hint. I took 0.77.

That is one recording and about a hundred words per speaker. I would not defend 0.77 as the
right number for anyone else, only as a better one than 0.5 for this voice, in this room, on this call.
The sweep itself ran while the server happened to be slow, so I ignored its latency columns
entirely and kept only what the voice detector decides, which does not depend on server speed.

## The question for you

When you evaluate a faster replacement, list what the current one does that you stopped
noticing. Mine had a one-line prompt that did more for accuracy than the model size difference.
You only find that out by running both on the same input, under the same conditions, and
reading what each one actually wrote.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
