# Model health ping — apply on the Mac (R34, spec §4.6.5)

Installs `com.lms.modelhealth`: every 5 minutes it checks that TIER-L1
(`qwen3.6-35b-a3b-mlx`) and TIER-L2 (`qwen3.5-122b-a10b`) are loaded in LM
Studio and answering, reloads either one with
`lms load <id> --context-length 32768` if it is not, and records the result.
A failure it cannot fix shows in the brief under **THE SYSTEM NEEDS
ATTENTION**. There is no push to Matthew's phone yet (that is R10). Two
minutes; no new permissions.

**If `./ops/halt.py --status` says HALTED**, it can be installed anyway: every
run sees `~/LMS/HALTED.json`, prints `HALTED since …` and exits 0 without
touching LM Studio, so step 2's expected output appears only after
`./ops/halt.py --resume`. Do step 1 regardless; it only reads.

Type commands one line at a time over RustDesk.

## 1. Two facts the tests could not check

```bash
cd ~/lms-repo/lms && git pull
ls -l ~/.lmstudio/bin/lms              # the CLI the job runs; must exist
curl -s http://localhost:1234/api/v0/models | python3 -c '
import json,sys
for m in json.load(sys.stdin)["data"]:
    print("%-45s %s" % (m["id"], m.get("state", "?")))'
```

Both resident ids must be listed, with `loaded` or `not-loaded` beside them.
If the CLI is somewhere else, `which lms` finds it: the job falls back to
PATH, but launchd's PATH is only the one in the plist, so tell the developer.
If the state column says anything other than those two words, **stop**: the
job would reload a model that is already loaded.

## 2. Install

```bash
./ops/model_health.py                  # by hand first: two OK lines, exit 0
echo $?
cp ops/com.lms.modelhealth.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lms.modelhealth.plist
launchctl kickstart -p gui/$(id -u)/com.lms.modelhealth
sleep 20; tail -3 ~/LMS/logs/modelhealth.log
```

Expect:

```
TIER-L1 qwen3.6-35b-a3b-mlx: OK (0.4s)
TIER-L2 qwen3.5-122b-a10b: OK (1.9s)
```

`~/LMS/logs/modelhealth.err` should be empty. `launchctl list | grep
modelhealth` shows `0` in the middle column after a good run, `1` after a
failed one.

## 3. Watch it reload (optional, two minutes of drafter downtime)

Only when nobody is drafting:

```bash
~/.lmstudio/bin/lms unload qwen3.5-122b-a10b
launchctl kickstart -p gui/$(id -u)/com.lms.modelhealth
sleep 180; tail -3 ~/LMS/logs/modelhealth.log
```

Expect `TIER-L2 qwen3.5-122b-a10b: OK after reload (…)`. The brief does not
show reloads, only failures; the record is in the database:

```bash
sqlite3 /Volumes/MacStudioHD/LMS/lms.db "SELECT ts, action, detail FROM actions_log WHERE action LIKE 'MODEL_%' ORDER BY id DESC LIMIT 3"
```

`MODEL_RELOADED … was not-loaded; reloaded with context 32768: …` then
`MODEL_HEALTH_OK`.

## Rollback

`launchctl bootout gui/$(id -u)/com.lms.modelhealth` and remove the plist
from `~/Library/LaunchAgents`. Nothing else depends on it.
