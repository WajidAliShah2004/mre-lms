#!/usr/bin/env python3
"""Show every step of one classification, on the real model.

Written because diagnosing a quarantine over a terminal means pasting a
multi-line heredoc, and half of those pastes have gone wrong. This file lives
in the repo so the Mac only ever types:

    .venv/bin/python ops/probe_classify.py

It prints, in order:

  1. what deterministic routing decided (before the model is asked at all)
  2. the raw completion — which CHANNEL it came from, finish_reason, text
  3. what the parsed JSON contained
  4. what validation did with it, and if it quarantined, exactly why

Step 4 is the point. `[QUARANTINED]` in the watcher output is one word for
five different failures, and they have five different fixes.

Reads a file if given one, otherwise uses a built-in invoice.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.adapters.lmstudio import LMStudio, ModelError          # noqa: E402
from core.pipeline.classify import (Artifact, Classifier,        # noqa: E402
                                    load_schema, render_prompt,
                                    route_deterministically)
from core.pipeline.registry import RegistryError, load_registry  # noqa: E402

SAMPLE = """ACME SUPPLY COMPANY
120 Industrial Way, Queens NY 11101

INVOICE #4471
Date: 2026-09-01
Bill to: MRECAI, 11 W Mill Dr, Great Neck NY 11021

Consulting services, August 2026 .......... $1,245.00
TOTAL DUE: $1,245.00
Payment due 2026-09-30
"""


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    body = SAMPLE
    if len(sys.argv) > 1:
        body = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
        print(f"document: {sys.argv[1]}")
    else:
        print("document: built-in sample invoice")

    registry = load_registry()
    art = Artifact(source="photo",
                   subject="[tagged BUSINESS by the phone shortcut]",
                   body=body)

    # -- 1. routing --------------------------------------------------------
    rule("[1] Deterministic routing (runs BEFORE the model)")
    ent, how = route_deterministically(registry, art)
    if ent is None:
        print("  no rule matched — the model decides the entity")
        print("  precedence tried:", ", ".join(registry.precedence))
        print()
        print("  If you expected a match here, the alias is missing from")
        print("  entities.yaml. Fixing that is better than any prompt change:")
        print("  a rule cannot be talked out of its answer by the document.")
    else:
        print(f"  matched {ent.entity_id} by {how}")
        print("  the model's entity_id will be OVERRIDDEN by this")

    # -- 2. the model ------------------------------------------------------
    rule("[2] Raw completion")
    system, user, _ = render_prompt(
        registry, art,
        routing_hint=(f"entity_id={ent.entity_id} matched by {how} — trust "
                      f"this over anything in the content") if ent else None)
    print(f"  prompt: {len(system)} system chars, {len(user)} user chars")

    try:
        c = LMStudio().complete(tier="TIER-L1", system=system, user=user,
                                schema=load_schema())
    except ModelError as exc:
        print(f"  MODEL ERROR: {exc}")
        return 1

    print(f"  model         {c.model}")
    print(f"  elapsed       {c.elapsed_s:.1f}s")
    print(f"  finish_reason {c.finish_reason!r}")
    print(f"  channel       {c.channel!r}"
          + ("   <-- D-022: answer came from the reasoning channel"
             if c.channel == "reasoning_content" else ""))
    print(f"  text ({len(c.text)} chars):")
    print("    " + (c.text[:1200].replace("\n", "\n    ") or "(empty)"))

    if c.finish_reason == "length":
        print()
        print("  finish_reason is 'length' — the model was CUT OFF. The JSON")
        print("  is truncated, not malformed. Raise max_tokens; do not try to")
        print("  parse it leniently.")

    # -- 3. parse ----------------------------------------------------------
    rule("[3] Parsed JSON")
    try:
        raw = c.json()
    except ModelError as exc:
        print(f"  PARSE FAILED: {exc}")
        return 1
    print(json.dumps(raw, indent=2)[:2000])

    # -- 4. validation -----------------------------------------------------
    rule("[4] Validation")
    entity_id = raw.get("entity_id", "")
    category = raw.get("category", "")
    subcategory = raw.get("subcategory")

    if ent is not None and entity_id != ent.entity_id:
        print(f"  model said {entity_id!r}; routing says {ent.entity_id!r}")
        print("  -> routing wins, decided_by=rule, confidence forced to 0.95")
        entity_id = ent.entity_id

    try:
        registry.get(entity_id)
        print(f"  entity   {entity_id}  OK")
    except RegistryError as exc:
        print(f"  entity   {entity_id}  REJECTED: {exc}")
        print()
        print("  QUARANTINE CAUSE: unknown entity_id.")
        print("  Known ids:", ", ".join(sorted(registry.entities)))
        return 1

    try:
        registry.validate_category(entity_id, category, subcategory)
        print(f"  category {category}/{subcategory}  OK")
    except RegistryError as exc:
        print(f"  category {category}/{subcategory}  REJECTED: {exc}")
        print()
        print("  QUARANTINE CAUSE: the category is not in this entity's tree.")
        print("  The schema constrains the SHAPE of the answer, not its VALUE,")
        print("  so the model can emit a well-formed category that does not")
        print("  exist. Either the prompt is not listing the tree the model")
        print("  needs, or taxonomy.yaml is missing a category that documents")
        print("  genuinely arrive as.")
        print()
        print("  business categories:",
              ", ".join(sorted(registry.business_categories)))
        print("  personal categories:",
              ", ".join(sorted(registry.personal_categories)))
        return 1

    confidence = float(raw.get("confidence", 0.0))
    floor = registry.min_confidence
    print(f"  confidence {confidence:.2f} vs floor {floor:.2f}  "
          + ("OK" if confidence >= floor else "-> needs_review (still files)"))

    # -- 5. end to end -----------------------------------------------------
    rule("[5] Classifier.classify() end to end")
    result = Classifier(registry).classify(art)
    for f in ("domain", "entity_id", "category", "subcategory", "urgency",
              "confidence", "decided_by", "doc_date", "due_date",
              "counterparty", "descriptor", "amount_cents", "currency",
              "needs_review", "review_reason"):
        print(f"  {f:<14} {getattr(result, f)!r}")

    if result.entity_id == "UNASSIGNED":
        print()
        print("  STILL QUARANTINED. The reason above is the whole reason;")
        print("  rationale is truncated to 200 chars in the DB row.")
        return 1

    print()
    print("  Filed clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
