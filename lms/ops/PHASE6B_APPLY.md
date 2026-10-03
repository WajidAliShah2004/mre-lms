# Phase 6b/6c — LM Studio as OpenClaw's model provider

Finishes Phase 6. Phase 6a (D-021) locked tools and commands down but left
`models.providers` empty, so the gateway has never run an inference and
`models.mode: "replace"` was withheld. This registers the provider, proves
one inference works, then removes the built-in cloud catalog (D-052).

Everything here runs **on the Mac**, from `~/lms-repo/lms`, as `mleca`.
Time: ~20 minutes. Nothing here touches mail, launchd jobs, or backups —
`core/` talks to LM Studio directly and does not depend on this.

> **Never use `openclaw agent exec`** for testing. Its documented defaults are
> "sandbox off, coding tool profile enabled" — the opposite of D-021. Use
> `openclaw agent` (through the gateway) only.

---

## 0. Before

```bash
cd ~/lms-repo/lms && git pull
./ops/verify_setup.py                     # everything must pass first
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-phase6b
openclaw --version                        # record it; must be extended-stable
```

## 1. LM Studio: both chat tiers loaded, loopback only

```bash
lsof -nP -iTCP:1234 -sTCP:LISTEN          # must show 127.0.0.1:1234, not *:1234
curl -s http://localhost:1234/api/v0/models | python3 -c '
import json,sys
for m in json.load(sys.stdin)["data"]:
    print("%-45s %-12s loaded_ctx=%7s max_ctx=%s" % (m["id"], m.get("state", "?"),
          m.get("loaded_context_length", "-"), m.get("max_context_length", "-")))'
```

`qwen3.6-35b-a3b-mlx` and `qwen3.5-122b-a10b` must both read `loaded`.

## 2. Check the context numbers against the patch

`phase6b.patch.json5` declares `contextWindow: 32768` for both. If either
model's **`loaded_ctx`** from step 1 is smaller than 32768, lower that
model's `contextWindow` in the patch to the loaded value and `maxTokens` to a
quarter of it, and commit the change. Larger is fine — the patch is
deliberately conservative.

If a model id differs from step 1 (LM Studio sometimes prefixes `author/`),
**stop**: the id must change in three places together — the patch,
`core/adapters/lmstudio.py` `TIER_MODELS`, and `agents.fragment.json` — and
`tests/test_agents.py` will fail until they agree.

## 3. Apply 6b

```bash
openclaw config patch --file ops/phase6b.patch.json5 --dry-run
```

If the validator rejects a key: nothing was written. Run
`openclaw config schema | less`, find the key it names, and report back
before editing — that is what went wrong in D-021.

```bash
openclaw config patch --file ops/phase6b.patch.json5
openclaw gateway restart
openclaw config get models.providers
openclaw models list --provider lmstudio  # both TIER models listed
```

## 4. One inference through the gateway — the thing that has never run

In a **second terminal**, watch for any non-loopback connection while it runs:

```bash
# every second, list the gateway's (node's) internet sockets that are NOT loopback
sudo lsof -nP -r 1 -a -c node -i | grep -v -e '127.0.0.1' -e '\[::1\]' -e '^=' -e '^COMMAND'
```

Then:

```bash
time openclaw agent --agent lms-orchestrator --json --message "Reply with exactly the word READY and nothing else."
```

**Pass:** the reply contains `READY`, the JSON shows the `lmstudio` provider /
`qwen3.6-35b-a3b-mlx` model, and the lsof window printed nothing. Record the wall time — it is the first TTFT data point for
Phase 9 (§10.3 target p95 < 3.5 s).

If the flags differ on this version, check `openclaw agent --help`; do not
substitute `agent exec`.

**Also note** the reply may arrive in a reasoning field rather than `content`
(D-022 saw this under constrained decoding). If the text is empty but the
JSON shows reasoning output containing READY, that is a pass for the
provider — record it; it matters for Phase 7 replies.

**If it fails:** `cp ~/.openclaw/openclaw.json.pre-phase6b ~/.openclaw/openclaw.json && openclaw gateway restart`, and send the error.

## 5. Apply 6c — `models.mode: "replace"`

Only if step 4 passed.

```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-phase6c
openclaw config patch --file ops/phase6c.patch.json5 --dry-run
openclaw config patch --file ops/phase6c.patch.json5
openclaw gateway restart
openclaw models list --all                # ONLY the two lmstudio models
openclaw agent --agent lms-orchestrator --json --message "Reply with exactly the word READY and nothing else."
```

**Pass:** `models list --all` shows exactly two models, both `lmstudio/…`,
and the same READY reply comes back. Anything else — empty catalog, a cloud
model still listed, inference error — roll back to `.pre-phase6c` and send
the output.

## 6. After

```bash
openclaw doctor --allow-exec
openclaw security audit --deep
openclaw secrets audit
./ops/verify_setup.py                      # now includes checks 9 and 10
sudo systemsetup -gettimezone              # must say America/New_York
```

**Expected end state**

| Check | Expected |
|---|---|
| `models list --all` | 2 models, both `lmstudio/` |
| `openclaw agent` READY test | passes, zero non-loopback egress |
| `security audit --deep` | 0 critical, 2 warn (`trusted_proxies_missing`, `probe_failed/operator.read` — the latter clears in Phase 7) |
| `secrets audit` | if it flags `models.providers.lmstudio.apiKey`: justified, placeholder with no authority (D-052) |
| `verify_setup.py` | all pass, including [9] system timezone and [10] live providers loopback |
| plugins / skills | still 3 / 0 (D-015) |

Send back: the step-1 table, `openclaw --version`, the step-4 JSON (trimmed)
and wall time, `models list --all`, and the audit summary line. That output
closes Phase 6 in PHASES.md.
