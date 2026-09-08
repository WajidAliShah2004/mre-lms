---
name: lms-classifier
version: 1.0.0
description: Classify one ingested artifact into domain, entity, category, and urgency. Returns JSON only. Has no tools and takes no actions.
tools: []
model: TIER-L1
---

# lms-classifier

Hand-authored in-repo per D-002. Never installed from ClawHub, never edited
on the Mac outside the repo. If this file differs from the repo copy, the
machine has drifted and the deployment is not trustworthy.

## Contract

**Input:** one sanitised artifact record — metadata plus body text already
wrapped in `<<EXTERNAL_UNTRUSTED_CONTENT>>` markers by
`core/pipeline/sanitise.py`. This skill never reads a file or a mailbox
itself.

**Output:** a single JSON object matching `prompts/classifier.schema.json`,
enforced by LM Studio's `response_format.json_schema` constrained decoding.
Nothing else — no prose, no explanation, no markdown fence.

**Side effects:** none. There is no code path from this skill to the
filesystem, the network, or a mailbox.

## Why `tools: []` is load-bearing

This skill is the only component that reads hostile text. Every other defence
in the system — the egress deny-list, the approval gate, the no-delete rule —
is a second line. The first line is that the component reading the attack has
nothing to attack *with*.

The empty tool list is asserted at gateway start by
`ops/verify_classifier_sandbox.py`, which fails the startup check if this
agent resolves any tool at all. Treat a failure there as a stop-the-line
event, not a warning: it means either this file was edited or an OpenClaw
update introduced a default-on tool.

That last case is not hypothetical. D-015 records that OpenClaw has no
default-deny for skills or plugins — control is per-name only — so every
update can ship new capability that defaults to available.

## Model tier

TIER-L1 (~35B MLX 4-bit, resident). Local only, via LM Studio on
`http://localhost:1234/v1`, loopback-bound.

There is no cloud tier and no API key on the machine (D-003). The spec's
cloud guards — `block_cloud_if_tax_engagement`, `block_cloud_if_unredacted_pii`,
reason-code gating — are **not built**. Adding a cloud key without building
them first creates an unguarded path from client NPI and tax data to a third
party.

## Confidence and quarantine

Below `routing.min_confidence` (0.60 in `config/entities.yaml`) the artifact
is quarantined and queued for review rather than filed. This is a correct
outcome, not a failure: a document in the review queue costs thirty seconds,
a document filed confidently into the wrong entity costs an audit.

## Prompt

`prompts/classifier.md`. Its hash is recorded in
`classifications.prompt_hash` on every row, so any classification can be
traced to the exact prompt revision that produced it — which is what makes a
bad revision findable and re-runnable instead of permanent.

## Explicitly not this skill's job

Filing, naming, path resolution, deduplication, task creation, notification,
and drafting. All of those are deterministic code in `core/`, decided
without consulting a model, so that nothing arriving in an email can
influence where a document lands or whether it is kept.
