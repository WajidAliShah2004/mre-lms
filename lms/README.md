# LMS — core

Ingest → classify → file → surface. Everything runs locally on the Mac
Studio; no client or tax data leaves the machine.

## Layout

```
config/     entities.yaml, taxonomy.yaml, rules.yaml — the routing brain
core/db/    schema.sql (WAL) + a thin sqlite3 wrapper. No ORM.
core/pipeline/
            naming.py    filename construction (D-007). Pure, no I/O.
            registry.py  entity + taxonomy loading and validation
            filing.py    deterministic filing, dedupe, sidecars, quarantine
core/adapters/  per-source ingestion (email, imessage, photo, transcript)
openclaw/skills/  hand-authored SKILL.md files (D-002). Never from ClawHub.
prompts/    classifier.md + its json_schema
tests/      run with `python3 -m pytest` from this directory
```

## Running the tests

```bash
cd lms
python3 -m pytest
```

41 pass, 1 xfail. The xfail is `test_no_placeholders_remain` — it stays red
until C16 (legal names, EINs, exact spellings) is supplied, at which point it
turns green and becomes the acceptance gate. That is deliberate: an
unanswered question should be visible in the test output, not buried in a
document.

## The division of labour

The model decides **what** a document is. Code decides **where it goes**,
**what it is called**, and **whether anything happens as a result**.

That split is the whole security design. The classifier reads hostile text
and has zero tools; everything with an effect is deterministic and cannot be
influenced by the contents of an email. See `config/rules.yaml` for the hard
stops and `tests/test_hard_stops.py` for their enforcement.

## Before this runs on real mail

- **C16** — fill in every `TODO(C16)` in `config/entities.yaml`
- **D-017** — rotate the exposed credentials; do not connect a mailbox with a
  password that travelled through a PDF
- **D-009** — LMS runs under Matthew's macOS user, so Phase 8 default-deny
  egress is the containment boundary. It is mandatory, not optional
