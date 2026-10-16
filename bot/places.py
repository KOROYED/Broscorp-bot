"""The forest's fixed geography: 8 places (рукавички), 3 areas, and how they relate.

A document always belongs to exactly one *scope* (confirmed by the client):
the whole forest, one area (e.g. coniferous forest), or one place.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class ScopeKind(StrEnum):
    FOREST = "forest"
    AREA = "area"
    PLACE = "place"


class Area(StrEnum):
    CONIFEROUS = "coniferous"
    DECIDUOUS = "deciduous"
    RIVER = "river"


AREA_NAMES = {
    Area.CONIFEROUS: "хвойний ліс",
    Area.DECIDUOUS: "листяний ліс",
    Area.RIVER: "біля річки",
}

# Fixed list from the client; new places never appear.
PLACE_AREAS: dict[str, frozenset[Area]] = {
    "Під ялиною": frozenset({Area.CONIFEROUS}),
    "У соснах": frozenset({Area.CONIFEROUS}),
    "Біля мурашника": frozenset({Area.CONIFEROUS}),
    "Під дубом": frozenset({Area.DECIDUOUS}),
    "У ліщині": frozenset({Area.DECIDUOUS}),
    "Під вербою": frozenset({Area.DECIDUOUS, Area.RIVER}),
    "Біля бобрової греблі": frozenset({Area.RIVER}),
    "На узліссі": frozenset(),
}
PLACES = list(PLACE_AREAS)


@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    # Place name for PLACE, Area value for AREA, "" for FOREST.
    value: str = ""

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.PLACE and self.value not in PLACE_AREAS:
            raise ValueError(f"Unknown place: {self.value!r}")
        if self.kind is ScopeKind.AREA and self.value not in {a.value for a in Area}:
            raise ValueError(f"Unknown area: {self.value!r}")
        if self.kind is ScopeKind.FOREST and self.value:
            raise ValueError("Forest scope has no value")

    @property
    def label(self) -> str:
        """Human-readable Ukrainian label, e.g. for buttons."""
        if self.kind is ScopeKind.PLACE:
            return f"рукавичка «{self.value}»"
        if self.kind is ScopeKind.AREA:
            return AREA_NAMES[Area(self.value)]
        return "увесь ліс"


FOREST = Scope(ScopeKind.FOREST)


def place_scope(place: str) -> Scope:
    return Scope(ScopeKind.PLACE, place)


def area_scope(area: Area) -> Scope:
    return Scope(ScopeKind.AREA, area.value)


ALL_SCOPES: list[Scope] = (
    [place_scope(p) for p in PLACES] + [area_scope(a) for a in Area] + [FOREST]
)


def scopes_for_place(place: str) -> list[Scope]:
    """Scopes whose advice applies to a place, most specific first.

    E.g. «Під вербою» -> [the place, deciduous, river, whole forest].
    «На узліссі» belongs to no area, so area advice never applies to it.
    """
    areas = sorted(PLACE_AREAS[place])
    return [place_scope(place)] + [area_scope(a) for a in areas] + [FOREST]


# Word forms an animal may use in a question. Plural forms such as «під ялинами»
# are deliberately not matched as a place: the client said they mean the area.
_PLACE_PATTERNS = {
    "Під ялиною": r"ялин(?:а|и|і|у|ою|ці|ка|ки|кою)\b",
    "У соснах": r"соснах\b",
    "Біля мурашника": r"мурашник(?:а|у|ом|ові)?\b",
    "Під дубом": r"дуб(?:а|і|у|ом|ові)?\b",
    "У ліщині": r"ліщин(?:а|и|і|у|ою)\b",
    "Під вербою": r"верб(?:а|и|і|у|ою)\b",
    "Біля бобрової греблі": r"(?:бобров\w*\s+)?гребл(?:я|і|ю|ею)\b",
    "На узліссі": r"узлісс(?:я|і|ям)\b",
}
_AREA_PATTERNS = {
    Area.CONIFEROUS: r"хвойн\w*|ялинами\b|ялинах\b",
    Area.DECIDUOUS: r"листян\w*",
    Area.RIVER: r"річ(?:ка|ки|ці|ку|кою)\b|річков\w*",
}


def _normalize(text: str) -> str:
    return text.lower().replace("’", "'").replace("ʼ", "'")


def find_places(text: str) -> list[str]:
    """Places explicitly named in a question, in the fixed list order."""
    text = _normalize(text)
    return [p for p, pat in _PLACE_PATTERNS.items() if re.search(r"\b" + pat, text)]


def strip_scope_words(text: str) -> str:
    """Remove place/area mentions (after the scope has been taken from them)."""
    text = _normalize(text)
    for pat in [*_PLACE_PATTERNS.values(), *_AREA_PATTERNS.values()]:
        text = re.sub(r"\b" + pat, " ", text)
    return text


def find_areas(text: str) -> list[Area]:
    """Areas explicitly named in a question (e.g. «у хвойному лісі»)."""
    text = _normalize(text)
    return [a for a, pat in _AREA_PATTERNS.items() if re.search(r"\b" + pat, text)]
