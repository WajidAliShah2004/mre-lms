# Phase 6 — applying the agent config on the Mac

**Do not copy `agents.fragment.json` over `~/.openclaw/openclaw.json`.**

The live config already carries two things that exist nowhere else:

- the **D-015 hardening** — 3 plugins enabled of 67, 0 skills ready of 51
- the **D-016 exec SecretRef** — `gateway.auth.token` resolving from the login
  Keychain via `/usr/bin/security`, with `allowInsecure` deliberately true

Overwriting would undo both, and neither would report an error. The gateway
would come back with a plaintext token and 64 re-enabled plugins, looking
entirely healthy.

So this is a key-by-key merge.

---

## Before you start

```bash
cd ~/lms-repo/lms
git pull
./ops/verify_setup.py
```

Every check must pass before you change anything. Checks 5 and 6 only work on
the Mac — they read the live OpenClaw state and the LM Studio socket.

Snapshot the live config:

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-phase6
```

`openclaw.json.pre-rotate` and `.pre-update` already exist from earlier work.
Adding `.pre-phase6` keeps the trail readable.

---

## 1. Model provider and tiers

```bash
openclaw config set models.providers.lmstudio.type openai-compatible
openclaw config set models.providers.lmstudio.baseUrl http://localhost:1234/v1
```

Then confirm what the gateway believes:

```bash
openclaw config get models.providers
curl -s http://localhost:1234/v1/models | python3 -m json.tool | head -20
```

The tier names in the fragment (`TIER-L1`, `TIER-L2`, …) are **normative; the
model names are not**. Substitute whatever MLX build is current — the spec is
explicit that the tier is the contract and re-benchmarking is a quarterly ops
item, not a build blocker.

## 2. Agents

Three agents, all with `tools.allow: []`.

```bash
openclaw config set agents.lms-orchestrator.model TIER-L1
openclaw config set agents.lms-orchestrator.tools.allow '[]'

openclaw config set agents.lms-classifier.model TIER-L1
openclaw config set agents.lms-classifier.tools.allow '[]'

openclaw config set agents.lms-drafter.model TIER-L2
openclaw config set agents.lms-drafter.tools.allow '[]'
```

**Then verify it took**, rather than assuming:

```bash
openclaw config get agents
```

Every `tools.allow` must read `[]`. An agent with a missing `tools` key is not
the same as one with an empty list — missing means "whatever the default is",
and D-015 established that OpenClaw's defaults are permissive and move between
releases.

## 3. Skills

The three `SKILL.md` files are hand-authored in the repo (D-002) and must be
referenced from there, never installed:

```bash
ls ~/lms-repo/lms/openclaw/skills/
# lms-classifier  lms-drafter  lms-orchestrator
```

Never run `openclaw skills install` or `openclaw skills search`. Disabling the
`clawhub` skill closed the *agent-reachable* path to third-party skills; the
*operator-reachable* CLI path is still open by design, which is why D-002 needs
operator discipline and not just a config flag.

## 4. Scheduling

Every job in `America/New_York`, never a UTC offset:

```bash
openclaw config set scheduling.timezone America/New_York
```

`verify_setup.py` check 4 proves the local hour holds across the November
boundary. Re-run it after any scheduling change.

## 5. What you are NOT doing in Phase 6

- **No cloud provider.** D-003. The guards that would make one safe
  (`block_cloud_if_tax_engagement`, `block_cloud_if_unredacted_pii`,
  reason-code gating) are not built. The absence of an API key is not the
  control — the absence of the guards is the reason there is no key.
- **No `commands.ownerAllowFrom`.** That is Phase 7, filled from the numeric
  id on Matthew's first message to the bot. `openclaw doctor` will keep
  reporting "No command owner is configured" until then, and it is right to.
- **No mailbox.** Day 3, and not before the D-017 rotation.

---

## After

```bash
openclaw gateway restart
openclaw doctor --allow-exec
openclaw security audit --deep
./ops/verify_setup.py
```

`doctor --allow-exec` is the one that matters. It is the only way to confirm
the D-016 Keychain SecretRef still resolves — without the flag, doctor skips
the gateway probe entirely and reports nothing wrong. D-016 says to verify
this after every reboot; it has still never been confirmed.

**Expected end state**

| Check | Expected |
|---|---|
| `security audit --deep` | 0 critical, 2 warn — both known and justified |
| `plugins list` | 3 of 67 enabled |
| `skills list` | 0 of 51 ready |
| `config get agents` | three agents, every `tools.allow` `[]` |
| `verify_setup.py` | all checks pass |

The two justified warnings are `gateway.trusted_proxies_missing` (loopback
only, no reverse proxy — accepted, not fixed) and `gateway.probe_failed /
missing scope: operator.read`, which clears in Phase 7.

## If something breaks

```bash
cp ~/.openclaw/openclaw.json.pre-phase6 ~/.openclaw/openclaw.json
openclaw gateway restart
openclaw doctor --allow-exec
```

Then say what happened before trying again. A gateway that starts is not the
same as a gateway that is configured correctly, and the difference is exactly
what `verify_setup.py` exists to catch.
