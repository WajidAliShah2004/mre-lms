# Task ledger v2 — apply on the Mac (R13, D-066)

Rebuilds the `tasks` table in `/Volumes/MacStudioHD/LMS/lms.db` to the
addendum's §2 shape. Ids stay the same, so a task id from an old brief still
closes the task it named. About a minute; no new permissions, nothing for
Matthew to do.

**Do it while the system is halted.** It has been since Matthew's `/halt` of
Oct 19 (D-065). If `PHASE7_ACCEPTANCE.md` is not finished, do its steps 1 and 2
first (they read the halt evidence), then this, then its step 3 (resume).

Type commands one line at a time over RustDesk.

```bash
cd ~/lms-repo/lms
./ops/halt.py --status            # must say HALTED
./ops/done.py --list              # note the ids and the count at the bottom
```

If it is not halted, run `./ops/halt.py` first: the mail and watcher jobs must
not be writing tasks while the table is rebuilt.

## 1. Pull, then migrate at once

```bash
git pull
./ops/migrate_ledger.py --check   # "ledger v1 — migration needed", counts per status
./ops/migrate_ledger.py
```

Expect:

```
Task ledger migrated to v2: N tasks, ids unchanged.
  DONE -> CLOSED: …
  OPEN -> TRIAGED: …
  snapshot before migrating: /Volumes/MacStudioHD/LMS/lms.db.pre-ledger-v2
…
PASS: N tasks, integrity ok
```

N is the same number before and after; the script fails if not.

**"Task ledger is already v2. Nothing to do."** followed by PASS is also fine:
something opened the database between `git pull` and the script (any
`./ops/` script does) and migrated it. The possible duplicates are then
printed from the task history instead.

Run the migration straight after `git pull`. Until the haltbot is restarted
(step 2) it runs the old code against the new schema file, and it writes to the
database when it records a `/halt`. That case is handled (D-066), but there is
no reason to leave it that way.

## 2. Restart the haltbot on the new code

```bash
launchctl kickstart -k gui/$(id -u)/com.lms.haltbot
tail -2 ~/LMS/logs/haltbot.log    # "haltbot up; owner configured"
```

The other jobs are halted and load the new code when they are resumed.

## 3. Check

```bash
./ops/done.py --list              # same open ids and count as before step 1
./ops/done.py --dismissed         # the same dismissed tasks as before
./ops/done.py <any id> --show     # status TRIAGED, its source, history "migrate OPEN -> TRIAGED"
```

**Possible duplicates.** The report lists pairs that look like the same
obligation (same entity, counterparty, due date and amount), such as an
email and its attached invoice that each became a task before v2. They are
left open. For each one, look at both:

```bash
./ops/done.py 9 --show
./ops/done.py 3 --show
./ops/done.py 9 --duplicate-of 3  # only if they really are the same bill
```

`--duplicate-of` moves 9's sources onto 3 and takes 9 off the list. It refuses
if the two belong to different entities. `./ops/done.py 9 --reopen` undoes it.

## 4. Resume

`PHASE7_ACCEPTANCE.md` step 3 (`./ops/halt.py --resume`). From then on a
document that matches an existing task is added to that task as evidence, and
does not create a second one.

## If it has to be undone

Only if step 3 shows something wrong. Tasks created after the migration are
lost by this, so do it the same day or not at all.

```bash
./ops/halt.py
git log --oneline -3              # find the commit before "ledger v2"
git checkout <that commit>
cd /Volumes/MacStudioHD/LMS
cp lms.db lms.db.ledger-v2-failed
rm -f lms.db-wal lms.db-shm
cp lms.db.pre-ledger-v2 lms.db
cd ~/lms-repo/lms && ./ops/done.py --list   # the old list, old code
```

Then tell the developer, and do not resume until the branch is fixed: the next
`git pull` would migrate again.

The snapshot `lms.db.pre-ledger-v2` stays on the array next to `lms.db`. The
nightly backup does not include it. Delete it after a week of normal briefs.
