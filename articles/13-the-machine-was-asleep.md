# Every "slow server" I chased was a Mac taking a nap

*Draft for LinkedIn. Not posted — review before publishing. Numbers are from one Mac mini, one recorded call and seven rounds.*

---

For two days I had a performance problem with a clean-looking shape. Transcripts from my copilot
arrived 15 seconds late, now and then. A benchmark step would take 15 seconds when it should
take one. A speech model I was testing had "unexplained stalls of 14 to 26 seconds". Twice the
machine simply didn't answer on the network at all.

I did what the situation invites. I assumed a slow server and built around it: I made the
transcriber stop asking for live partial results when it fell behind, I simulated a server that
needs 1.5 seconds per request, I added a delay to imitate "a bad hour". Each of those is a fine
defence. None of them was the cause.

## How I found it

I left a benchmark running overnight, 14 rounds, plus a small logger on the Mac that wrote one
line every 10 seconds. In the morning the log had 388 holes of 14 to 40 seconds. About 17 % of the
night, the machine had not run my code at all. A `sleep(1)` probe overshot by 14.0 s and, a minute
later, by 12.0 s.

The Mac's own power log explained it: a "Maintenance Sleep" followed by a "DarkWake" about every
70 seconds, 60 times an hour, whenever nobody was touching it. The setting that allows it was one
minute of idle before sleep, on a machine I use as a server. A network packet wakes it, which is
why it looked like random slowness and not like an outage.

I had even tried the obvious fix and it had failed: `caffeinate -i`, the command everyone reaches
for, does not stop this kind of sleep. `caffeinate -s` did, and so did setting the idle sleep to
zero. That detail cost me half a night.

## What the clean run said

With sleep off I repeated the whole thing: seven rounds of the same recorded call, same models,
same code.

| | Mac sleeping | Mac awake |
|---|---|---|
| Gaps over 14 s in the sampler | 388 | **0** |
| Failed or stalled steps | dozens | **0 of 43** |
| Finals late, typical worst case | 15.8 s | **1.8 s** (mean 0.8 s) |
| Speech speed, vs real time | 20x | 19x |
| First token, small model, warm | 0.13 s | 0.13 s |

The last two rows are the interesting ones. The steady speed of the models did not change. The
medians were the same with and without the sleeping. Only the stalls went away, and the stalls were
what I had been designing around.

## What I won't claim

- **Not everything is explained.** One of my models hung while loading, several times that night, and
  not once in the clean run. That is evidence of something, not a proof it was the same cause.
- **The Parakeet stalls are a suspicion.** They look the same, but I deleted that experiment and
  have not rerun it.
- **It is one machine and seven rounds.** The Mac still varies about 2x between rounds (the speech
  speed ranged from 0.047 to 0.105 of real time) with no sleeping at all. I still report medians.
- **The defences stay.** A real server will be slow some day. I am glad the transcriber degrades
  gracefully. I am just not going to describe a sleeping machine as a slow one again.

## The question for you

When a system is slow in bursts, how long does it take you to ask whether the machine was
running at all? I measured the wrong thing for two days, because I measured the work. The thing
that finally helped was boring: a logger that wrote a timestamp every 10 seconds and
did nothing else, so that its silence was the evidence.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
