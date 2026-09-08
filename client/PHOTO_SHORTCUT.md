# The photo shortcut

Snap a bill, tap Personal or Business, done. It files itself.

This is the feature you'll use most, and it's the one that needs something
from you — about three minutes, once.

---

## What it does

You photograph a piece of paper. The shortcut asks which side of your life it
belongs to. The Mac picks it up within a few seconds, reads it, works out
which business or person it belongs to, files it under a consistent name, and
if there's a date to act on, adds it to your list.

You never open a folder. You never name a file.

---

## Building it — three minutes on your iPhone

Open **Shortcuts** → **+** → name it **File This**.

Add these actions in order:

**1. Take Photo**
- Turn **Show Camera Preview** on, so you can line the page up.

**2. Choose from Menu**
- Prompt: `Whose is this?`
- Two options: `Business` and `Personal`

**3.** Under **Business**, add **Save File**
- Destination: `iCloud Drive → LMS → inbox → business`
- Turn **Ask Where to Save** **OFF** — that's the whole point.

**4.** Under **Personal**, add **Save File**
- Destination: `iCloud Drive → LMS → inbox → personal`
- **Ask Where to Save** off.

Then, so it's one tap from anywhere:

- Shortcut settings → **Add to Home Screen**
- Or Settings → Action Button → Shortcut → File This *(iPhone 15 Pro and later)*
- Or just say "Hey Siri, file this"

The first time you run it, iOS asks permission for the camera and for iCloud
Drive. Say yes to both — it won't work otherwise, and it won't ask again.

---

## Two things worth adding later

**A voice note.** Add **Dictate Text** after the photo and save it alongside.
"This is the Con Ed bill, it's the one I disputed" gives the system context no
amount of reading the page would provide. Worth it for anything unusual.

**Multiple pages.** For a several-page letter, use **Take Photo** with **Allow
Multiple** on, then **Make PDF** before saving. One document instead of four
loose photos.

Start without these. Add them once the basic version is a habit.

---

## What happens on the Mac

Within about five seconds the file is noticed. Then:

- The text is read on-device — nothing is uploaded anywhere
- It's classified: which business or person, what kind of document, how urgent
- It's filed under a consistent name, in a place you could find by hand
- The original photo is kept, untouched, forever
- A due date becomes a task on your list

**Under 90 seconds from tap to filed**, and that's the number we'll test in
front of you.

If it can't work something out, it goes to a review pile instead of guessing.
A document in review costs you thirty seconds; a document filed confidently
into the wrong business is one you won't find when you need it.

---

## What it will never do

The photo never leaves the Mac. Not to us, not to a cloud AI, not anywhere.
That's not a policy — there's no code in the system that could.

It won't delete the original. It won't send anything to anyone.

---

## If nothing seems to happen

Almost always one of two things:

**The Mac is asleep or locked.** After a power cut it sits at the unlock
screen and nothing runs until someone types the password. Photos queue up in
iCloud and get processed when it's back.

**iCloud hasn't synced.** Open the Files app on your phone, go to
iCloud Drive → LMS → inbox, and check the photo is actually there. If it shows
a cloud icon it hasn't finished uploading — usually a weak signal.

Filed documents appear in `LMS/archive` on the Mac, sorted by business and
category. Everything the system does is written down, so if something lands
somewhere odd we can see exactly why and correct it — and it learns from the
correction.
