"""Entity and taxonomy registry.

Loads config/entities.yaml and config/taxonomy.yaml, validates them, and
answers the two questions the pipeline actually asks:

    * is this entity_id / category pair legal?
    * which folder does it live in?

Validation happens at load, not at use. A typo in entities.yaml should stop
the daemon starting, not surface three weeks later as a document filed into
a directory named after a misspelling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

PLACEHOLDER_MARKER = "TODO(C16)"


class RegistryError(ValueError):
    pass


@dataclass
class Entity:
    entity_id: str
    name: str
    tree: str
    kind: str                      # person | business | system
    ein: str | None = None
    short: str | None = None
    domains: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    role: str | None = None

    @property
    def is_placeholder(self) -> bool:
        """True while this entity still carries unanswered C16 values."""
        return PLACEHOLDER_MARKER in (self.name or "") or PLACEHOLDER_MARKER in (self.ein or "")

    def short_or_name(self) -> str:
        """A label safe to put in a prompt.

        Prefers the trading name over `name`, because while C16 is unanswered
        `name` reads "TODO(C16) — legal name on the formation documents" and
        injecting that into the entity table would be both useless and quietly
        confusing to the model.
        """
        if self.short:
            return self.short
        if PLACEHOLDER_MARKER in (self.name or ""):
            return self.entity_id
        return self.name


@dataclass
class Registry:
    entities: dict[str, Entity]
    personal_categories: dict[str, list[str]]
    business_categories: dict[str, list[str]]
    min_confidence: float
    precedence: list[str]

    # -- lookups ------------------------------------------------------------

    def get(self, entity_id: str) -> Entity:
        try:
            return self.entities[entity_id]
        except KeyError:
            raise RegistryError(f"unknown entity_id {entity_id!r}") from None

    def categories_for(self, entity_id: str) -> dict[str, list[str]]:
        ent = self.get(entity_id)
        if ent.kind == "business":
            return self.business_categories
        if ent.kind == "person":
            return self.personal_categories
        # System buckets accept either tree's categories plus UNSORTED.
        return {**self.personal_categories, **self.business_categories}

    def validate_category(self, entity_id: str, category: str,
                          subcategory: str | None = None) -> None:
        cats = self.categories_for(entity_id)
        if category not in cats:
            raise RegistryError(
                f"category {category!r} is not valid for {entity_id!r}; "
                f"expected one of {sorted(cats)}"
            )
        if subcategory and subcategory not in cats[category]:
            raise RegistryError(
                f"subcategory {subcategory!r} is not valid under {category!r}"
            )

    def resolve_by_email(self, address: str) -> Entity | None:
        addr = (address or "").strip().lower()
        if not addr:
            return None
        for ent in self.entities.values():
            if addr in (e.lower() for e in ent.emails):
                return ent
        return None

    def resolve_by_domain(self, address_or_domain: str) -> Entity | None:
        text = (address_or_domain or "").strip().lower()
        domain = text.rpartition("@")[2] if "@" in text else text
        if not domain:
            return None
        for ent in self.entities.values():
            if domain in (d.lower() for d in ent.domains):
                return ent
        return None

    def placeholders(self) -> list[str]:
        """Entity ids still carrying TODO(C16) values.

        Called at startup and printed as a warning, and asserted empty in the
        acceptance test. This is what stops the placeholders quietly becoming
        permanent.
        """
        return sorted(e.entity_id for e in self.entities.values() if e.is_placeholder)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def load_registry(config_dir: Path | str = CONFIG_DIR) -> Registry:
    config_dir = Path(config_dir)
    entities_raw = yaml.safe_load((config_dir / "entities.yaml").read_text(encoding="utf-8"))
    taxonomy_raw = yaml.safe_load((config_dir / "taxonomy.yaml").read_text(encoding="utf-8"))

    entities: dict[str, Entity] = {}

    for eid, body in (entities_raw.get("people") or {}).items():
        entities[eid] = Entity(
            entity_id=eid, name=body.get("name", eid), tree=body["tree"], kind="person",
            short=body.get("short"),
            aliases=_as_list(body.get("aliases")), emails=_as_list(body.get("emails")),
            role=body.get("role"),
        )

    for eid, body in (entities_raw.get("businesses") or {}).items():
        entities[eid] = Entity(
            entity_id=eid, name=body.get("name", eid), tree=body["tree"], kind="business",
            ein=body.get("ein"), short=body.get("short"),
            domains=_as_list(body.get("domains")),
            emails=_as_list(body.get("emails")), aliases=_as_list(body.get("aliases")),
        )

    for eid, body in (entities_raw.get("system") or {}).items():
        entities[eid] = Entity(
            entity_id=eid, name=eid, tree=(body or {}).get("tree", f"_{eid}"), kind="system",
        )

    if not entities:
        raise RegistryError("entities.yaml defined no entities")

    # Trees must be unique, or two entities file into the same folder and the
    # tree stops being a reliable index.
    seen: dict[str, str] = {}
    for ent in entities.values():
        if ent.tree in seen:
            raise RegistryError(
                f"entities {seen[ent.tree]!r} and {ent.entity_id!r} share tree {ent.tree!r}"
            )
        seen[ent.tree] = ent.entity_id

    routing = entities_raw.get("routing") or {}

    personal = {k: _as_list(v) for k, v in (taxonomy_raw.get("personal") or {}).items()}
    business = {k: _as_list(v) for k, v in (taxonomy_raw.get("business") or {}).items()}

    for tree_name, tree in (("personal", personal), ("business", business)):
        if "UNSORTED" not in tree:
            raise RegistryError(f"{tree_name} taxonomy must define UNSORTED")

    return Registry(
        entities=entities,
        personal_categories=personal,
        business_categories=business,
        min_confidence=float(routing.get("min_confidence", 0.60)),
        precedence=_as_list(routing.get("precedence")),
    )
