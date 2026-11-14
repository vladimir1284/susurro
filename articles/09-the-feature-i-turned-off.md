# I built a recording feature and shipped it switched off

*Draft for LinkedIn. Not posted — review before publishing.*

---

This week I added session recording to the tool I'm building — it saves a transcript of
your meeting, along with the suggestions it offered, so you can read them back
afterwards. Genuinely useful. It's the kind of thing people ask for first.

It ships disabled. You have to edit a configuration file to switch it on.

That wasn't a hedge or an incomplete implementation. It's the decision the feature exists
inside of, and I want to explain the reasoning, because the same argument comes up in
almost every product that touches data people care about.

## The one claim the project makes

The tool listens to video calls and offers suggestions. The thing that distinguishes it
from the hosted alternatives is simple: **your audio never leaves your network.** Speech
recognition and the language model run on a machine you own. Nothing is uploaded.

That's the entire pitch. It's why someone would use this instead of something more
polished.

Now consider what "record sessions, on by default" would mean. Every meeting you have,
written to disk, automatically, because a developer thought it would be handy. Still local
— the claim is technically intact. But the *reason* anyone believed the claim was that
this tool doesn't do things with your conversations that you didn't ask for. Writing them
all down is doing something with your conversations that you didn't ask for.

The feature wouldn't break the privacy claim. It would break the thing the privacy claim
was standing in for.

## Defaults are the product

There's a line of argument that goes: it's local, it's your own disk, it's one setting, a
user who cares can change it.

I don't find it convincing, for a reason that has nothing to do with privacy
specifically. **Almost nobody changes defaults.** This is one of the most robust findings
in software: whatever you ship as the default is what the overwhelming majority of people
will run, forever. Treating a default as "just a starting point the user will adjust" is
treating a decision you made as a decision they made.

So the real question isn't "should this be possible?" It's "what should happen to someone
who never reads the configuration file?"

For recording meetings, the answer I'm comfortable with is: nothing.

## Making it honest when it is on

Off by default isn't sufficient on its own, because someone will turn it on and then
forget. So three more things.

**The console shows a red indicator whenever recording is active.** Not inside the history
panel — in the header, visible the whole session. Putting the indicator inside the screen
you'd have to open to check would be a joke at the user's expense. The whole value of an
indicator is that you see it without looking for it.

**Deleting is as easy as recording.** The list of sessions has a delete button on every
row. It takes two clicks, because a transcript can't be recovered, but there's no export
step, no "are you sure" dialog with a disabled button, no burying it in a settings page.
If a system makes recording one setting and deletion a research project, it isn't really
offering you a choice.

**Everything is one JSON file per session, in one directory.** No database, no index, no
hidden application-support folder with an opaque name. If you want to delete everything
without opening the app, you delete a directory. If you want to read one without the app,
it's a text file.

That last one has an engineering cost — a database would be faster at scale and tidier to
query. But "you can delete this with the file manager you already know" is worth more than
query performance on a data set that will never exceed a few hundred items.

## The feature I removed to make this work

There was an obvious addition I decided against: storing both the JSON and a rendered
Markdown transcript, so the readable version is just there on disk.

It would have been three lines. I removed it because it means two copies of your
conversation instead of one, and now deleting one doesn't delete the other. Markdown is
generated on demand instead, from the JSON, when you ask for it.

A format you can regenerate isn't worth storing. That's true as a general engineering
principle, and here it also halves the number of places a conversation lives.

## Where this generalises

The question worth asking about any feature that collects, retains, or transmits something
on a user's behalf:

**If this is on by default, am I relying on the user not to mind, or on the user not to
notice?**

Those feel similar from inside a product team and they are not the same thing at all. "Not
minding" is consent. "Not noticing" is the absence of it, and it's the condition most
default-on data collection actually depends on.

The test I used: if a user discovered this was happening six months in, would they feel
served or would they feel managed? For automatically recording every meeting, I know the
answer, and no amount of "but it's local" changes it.

Off by default. One switch. A red light when it's on. One directory you can delete.

---

*Susurro is open source under AGPL-3.0: github.com/vladimir1284/susurro*
