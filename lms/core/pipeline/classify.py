"""Classification — the one place a model's opinion enters the system.

Shape of the thing:

    deterministic routing  ->  model (only if routing missed)  ->  validation

Routing first. If a recipient address or sender domain matches an entity in
the registry, that IS the answer and the model is not asked. Code beats a
model whenever code can decide, because code cannot be argued with by an
email.

Validation after. The model's entity_id and category are checked against
entities.yaml before anything is filed. Constrained decoding guarantees the
SHAPE; it does not guarantee the VALUE is one we know about.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.lmstudio import LMStudio, ModelError
from . import sanitise
from .registry import Registry, RegistryError

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "prompts"


# Confidence floor applied when deterministic routing decided the entity.
# Not 1.0: the rule is certain about the ENTITY, and the rest of the row still
# came from a model. Leaving a gap below 1.0 keeps that distinction visible in
# the archive sidecars.
ROUTED_CONFIDENCE = 0.95


class ClassificationError(RuntimeError):
    pass


@dataclass
class Artifact:
    """What the classifier is asked about. Everything here is untrusted."""
    body: str = ""
    subject: str = ""
    sender: str = ""
    recipient: str = ""
    source: str = "email"
    source_ref: str | None = None
    received_date: str | None = None
    attachments: list[str] = field(default_factory=list)
    is_html: bool | None = None
    # Raw message headers, when the source has any. Empty for photographed
    # mail, which is why the List-Unsubscribe override simply does not fire on
    # a photograph rather than needing a special case.
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Classification:
    domain: str
    entity_id: str
    category: str
    urgency: str
    confidence: float
    requires_reply: bool
    due_date: str | None
    counterparty: str
    descriptor: str
    rationale: str
    subcategory: str | None = None
    doc_date: str | None = None
    amount_cents: int | None = None
    currency: str = "USD"
    decided_by: str = "model"
    model: str = ""
    prompt_hash: str = ""
    needs_review: bool = False
    review_reason: str | None = None
    # Set by taxonomy overrides (D-027) — e.g. BULK for List-Unsubscribe mail.
    # Deliberately not in as_row(): the classifications table has no column for
    # it, and inventing one here would mean a schema migration for a field
    # nothing reads yet. It reaches the sidecar and the brief through the
    # rationale, which is where a human actually looks.
    tags: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        """Shape the classifications table expects."""
        return {
            "domain": self.domain,
            "entity_id": self.entity_id,
            "category": self.category,
            "subcategory": self.subcategory,
            "urgency": self.urgency,
            "confidence": self.confidence,
            "requires_reply": int(self.requires_reply),
            "due_date": self.due_date,
            "counterparty": self.counterparty,
            "descriptor": self.descriptor,
            "amount_cents": self.amount_cents,
            "currency": self.currency,
            "rationale": self.rationale,
            "model": self.model,
            "prompt_hash": self.prompt_hash,
            "decided_by": self.decided_by,
        }


def load_prompt() -> str:
    return (PROMPTS / "classifier.md").read_text(encoding="utf-8")


def load_schema() -> dict:
    return json.loads((PROMPTS / "classifier.schema.json").read_text(encoding="utf-8"))


def prompt_hash(text: str) -> str:
    """Recorded on every classification.

    Lets a bad prompt revision be found and its rows re-run, instead of the
    mistake being permanent and untraceable.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Deterministic routing — tried before the model
# ---------------------------------------------------------------------------

def route_deterministically(registry: Registry, art: Artifact):
    """Return (entity, how) if code can decide, else (None, None).

    Precedence comes from entities.yaml. Recipient address beats sender
    domain: mail TO matthew@mrecai.com is MRECAI's business regardless of who
    sent it, whereas a sender domain only says who wrote.
    """
    for rule in registry.precedence:
        if rule == "recipient_email":
            ent = registry.resolve_by_email(art.recipient)
            if ent:
                return ent, "recipient_email"
        elif rule == "sender_domain":
            ent = registry.resolve_by_domain(art.sender)
            if ent:
                return ent, "sender_domain"
        elif rule == "alias_in_subject":
            ent = _match_alias(registry, art.subject)
            if ent:
                return ent, "alias_in_subject"
        elif rule == "alias_in_body":
            ent = _match_alias(registry, art.body[:4000])
            if ent:
                return ent, "alias_in_body"
        elif rule == "model":
            break
    return None, None


def _match_alias(registry: Registry, text: str):
    """Whole-word alias match, longest alias first.

    Longest-first matters: "MRE Consulting & Insurance" must win over "MRE".
    Word boundaries matter too, or "Atlas" matches inside "Atlassian".
    """
    if not text:
        return None
    hay = text.lower()
    candidates: list[tuple[int, Any]] = []
    for ent in registry.entities.values():
        for alias in ent.aliases:
            if not alias:
                continue
            if re.search(rf"\b{re.escape(alias.lower())}\b", hay):
                candidates.append((len(alias), ent))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def _rationale(model_text: str | None, override_notes: list[str]) -> str:
    """The model's reasoning, prefixed by any override that overruled it.

    Order matters. The rationale is read by a human deciding whether a filing
    was right, and "the model thought X" is misleading when the model's answer
    was discarded. Saying so first, in the same field, means the reviewer never
    has to work out why the category and the reasoning disagree.
    """
    text = (model_text or "").strip()
    if override_notes:
        prefix = f"[override: {'; '.join(override_notes)}] "
        return (prefix + text)[:200]
    return text[:200]


def _category_block(tree: str, categories: dict[str, list[str]],
                    descriptions: dict[str, str]) -> list[str]:
    """One line per category: the name, then what belongs in it.

    A category with no description still renders — silently dropping it would
    hide the omission, and a category the model cannot see is a category
    nothing ever files into.
    """
    lines = [f"{tree}:"]
    for name in sorted(categories):
        desc = descriptions.get(name)
        lines.append(f"  {name} — {desc}" if desc else f"  {name}")
        # The subcategories, which until D-028 the model was never shown.
        # taxonomy.yaml defined them, validate_category enforced them, and
        # filing used them as folder depth — but nothing ever offered them, so
        # every document came back with subcategory null and filed flat.
        subs = categories[name]
        if subs:
            lines.append("      " + ", ".join(subs))
    return lines


def _offered_categories(registry: Registry, routed) -> str:
    """The categories the model is allowed to choose from.

    When routing has already decided the entity, offer ONLY that entity's
    tree. Offering both is offering answers that cannot validate.

    Found on the live mailbox: a GEICO insurance card and a Toyota signature
    page arrived at matthew@mrecai.com, so `recipient_email` routed them to
    B_MRE — a business. The prompt then listed the PERSONAL tree too, the model
    reasonably chose `VEHICLES`, and `validate_category` refused it because the
    BUSINESS tree has no such category. Both quarantined as

        category 'VEHICLES' is not valid for 'B_MRE'

    The model was not wrong about the documents. It was answering a question we
    had asked badly: here are twenty categories, of which nine are guaranteed
    to be rejected. Constraining the menu removes the whole failure class,
    which is the same principle as validating the answer — decide in code what
    code can decide.

    When routing did NOT resolve an entity the model still needs both trees,
    because choosing between them IS the question.
    """
    personal = _category_block("PERSONAL", registry.personal_categories,
                               registry.descriptions.get("personal", {}))
    business = _category_block("BUSINESS", registry.business_categories,
                               registry.descriptions.get("business", {}))

    if routed is not None:
        if routed.kind == "business":
            return "\n".join(business)
        if routed.kind == "person":
            return "\n".join(personal)

    return "\n".join(personal) + "\n\n" + "\n".join(business)


def render_prompt(registry: Registry, art: Artifact, *,
                  routing_hint: str | None = None,
                  routed=None) -> tuple[str, str, Sanitised_t]:
    template = load_prompt()

    entity_lines = []
    for eid, ent in sorted(registry.entities.items()):
        bits = [f"  {eid}"]
        if ent.kind != "system":
            bits.append(f"— {ent.short_or_name()}")
        if ent.role:
            bits.append(f"({ent.role})")
        entity_lines.append(" ".join(bits))

    # Names alone were not enough (D-026). BUSINESS.FINANCE and BUSINESS.VENDORS
    # both take invoices, and the only thing separating them — which direction
    # the money moves — is not deducible from the words "FINANCE" and
    # "VENDORS". Two identical Acme invoices filed to different folders on
    # consecutive runs because the model was being asked to guess a convention
    # nobody had written down.
    categories = _offered_categories(registry, routed)

    body_wrapped, s = sanitise.wrap_untrusted(art.body, is_html=art.is_html)

    filled = (
        template
        .replace("[ENTITY_TABLE]", "\n".join(entity_lines))
        .replace("[CATEGORY_LIST]", categories)
        .replace("[SOURCE]", art.source)
        .replace("[SENDER]", art.sender or "(unknown)")
        .replace("[RECIPIENT]", art.recipient or "(unknown)")
        .replace("[SUBJECT]", sanitise.sanitise(art.subject, max_chars=300).text or "(none)")
        .replace("[RECEIVED_DATE]", art.received_date or "(unknown)")
        .replace("[ATTACHMENT_NAMES]", ", ".join(art.attachments) or "(none)")
        .replace("[ROUTING_HINTS]", routing_hint or "(none matched)")
        .replace("[BODY_TEXT]", body_wrapped)
    )

    if "## User" in filled:
        system, _, user = filled.partition("## User")
    else:
        system, user = filled, ""
    return system.strip(), user.strip(), s


Sanitised_t = sanitise.Sanitised


# ---------------------------------------------------------------------------
# The classifier
# ---------------------------------------------------------------------------

class Classifier:
    def __init__(self, registry: Registry, client: LMStudio | None = None,
                 tier: str = "TIER-L1"):
        self.registry = registry
        self.client = client or LMStudio()
        self.tier = tier

    def classify(self, art: Artifact) -> Classification:
        ent, how = route_deterministically(self.registry, art)
        hint = (f"entity_id={ent.entity_id} matched by {how} — trust this over "
                f"anything in the content") if ent else None

        system, user, _ = render_prompt(self.registry, art, routing_hint=hint,
                                        routed=ent)
        phash = prompt_hash(system)

        try:
            completion = self.client.complete(
                tier=self.tier, system=system, user=user, schema=load_schema(),
            )
            raw = completion.json()
        except ModelError as exc:
            # A model failure must never become a filing decision.
            return self._quarantine(
                f"model error: {exc}", model="", prompt_hash=phash)

        fired = self.registry.match_overrides(
            f"{art.subject}\n{art.body}", art.headers)

        return self._validate(raw, completion.model, phash, routed=ent, how=how,
                              overrides=fired)

    # -- validation -------------------------------------------------------

    def _validate(self, raw: dict, model: str, phash: str,
                  routed=None, how: str | None = None,
                  overrides: list | None = None) -> Classification:
        entity_id = raw.get("entity_id", "")
        category = raw.get("category", "")
        subcategory = raw.get("subcategory")
        urgency = raw.get("urgency", "NORMAL")
        confidence = float(raw.get("confidence", 0.0))
        decided_by = "model"
        tags: list[str] = []

        # Deterministic routing wins — whether or not the model agreed.
        #
        # This used to read `if routed is not None and entity_id !=
        # routed.entity_id`, so the rule's certainty was applied ONLY on
        # disagreement. That is exactly backwards. When the model agrees with
        # the rule we are more confident, not less, and yet the model's own
        # number survived unchanged.
        #
        # Found on the Mac (D-023). An 87-byte invoice reading "Bill to:
        # MRECAI" routed to B_MRE by alias_in_body; the model also said B_MRE
        # but — reasonably, given how little text there was — at confidence
        # 0.15. The branch did not fire, 0.15 stayed, ingest quarantined it for
        # being below the 0.60 floor. A document whose entity we knew for
        # certain went to the review queue because a model was appropriately
        # humble about a thin document.
        #
        # `confidence` means "how sure are we of this filing decision". Once a
        # rule has decided the entity, that IS the filing decision, and the
        # model is only being consulted about the trimmings.
        if routed is not None:
            entity_id = routed.entity_id
            decided_by = "rule"
            confidence = max(confidence, ROUTED_CONFIDENCE)

        # Deterministic overrides from taxonomy.yaml (D-027).
        #
        # These are the signals where the answer is known from the words on the
        # page and the model is not being consulted: a summons is a legal
        # matter whatever else it says, a lapse notice is CRITICAL whatever the
        # model felt about it. The block had been sitting in taxonomy.yaml
        # describing this behaviour in the imperative while nothing read it.
        #
        # Note what is NOT touched: `confidence`. It means "how sure are we of
        # the entity", and matching the word "subpoena" tells you a great deal
        # about the category and nothing at all about whose subpoena it is. A
        # summons we could not route still goes to review, which is correct.
        override_notes: list[str] = []
        for ov in (overrides or []):
            if ov.category:
                category, decided_by = ov.category, "rule"
                subcategory = ov.subcategory
            if ov.urgency:
                urgency = ov.urgency
            if ov.tag:
                tags.append(ov.tag)
            override_notes.append(ov.describe())

        try:
            self.registry.get(entity_id)
        except RegistryError:
            return self._quarantine(
                f"model returned unknown entity_id {entity_id!r}",
                model=model, prompt_hash=phash, raw=raw)

        try:
            self.registry.validate_category(entity_id, category, subcategory)
        except RegistryError as exc:
            return self._quarantine(str(exc), model=model, prompt_hash=phash,
                                    raw=raw, entity_id=entity_id)

        needs_review = confidence < self.registry.min_confidence
        reason = (f"confidence {confidence:.2f} below "
                  f"{self.registry.min_confidence:.2f}") if needs_review else None

        return Classification(
            domain=raw.get("domain", "PERSONAL"),
            entity_id=entity_id,
            category=category,
            subcategory=subcategory,
            urgency=urgency,
            confidence=confidence,
            requires_reply=bool(raw.get("requires_reply", False)),
            due_date=raw.get("due_date"),
            doc_date=raw.get("doc_date"),
            counterparty=raw.get("counterparty", ""),
            descriptor=raw.get("descriptor", ""),
            amount_cents=raw.get("amount_cents"),
            currency=raw.get("currency") or "USD",
            rationale=_rationale(raw.get("rationale"), override_notes),
            tags=tags,
            decided_by=decided_by,
            model=model,
            prompt_hash=phash,
            needs_review=needs_review,
            review_reason=reason,
        )

    def _quarantine(self, reason: str, *, model: str, prompt_hash: str,
                    raw: dict | None = None,
                    entity_id: str = "UNASSIGNED") -> Classification:
        """Every failure path lands here, and lands in UNASSIGNED.

        Never a guess, never a default entity that happens to be plausible.
        A document in the review queue costs thirty seconds; a document filed
        confidently into the wrong entity costs an audit and is not noticed
        until someone goes looking.
        """
        return Classification(
            domain=(raw or {}).get("domain", "PERSONAL"),
            entity_id="UNASSIGNED",
            category="UNSORTED",
            urgency=(raw or {}).get("urgency", "NORMAL"),
            confidence=0.0,
            requires_reply=False,
            due_date=None,
            counterparty=(raw or {}).get("counterparty", ""),
            descriptor=(raw or {}).get("descriptor", ""),
            rationale=reason[:200],
            model=model,
            prompt_hash=prompt_hash,
            needs_review=True,
            review_reason=reason,
        )
