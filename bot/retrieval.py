"""Which documents may answer a question.

1. Scope filter: if the question names a place, only that place's documents
   plus advice for its areas and for the whole forest can apply («На узліссі»
   gets no area advice). Plain code, not the LLM: exact, free, testable.
2. Among those candidates, two signals are combined (hybrid search):
   - the LLM router picks by meaning from titles («поїсти» → «Чим поживитись»);
   - lexical BM25 search finds words inside the text («тиша» is a section of
     «Правила співжиття», which the title doesn't reveal).
   The answering model then uses only the documents that really answer.

Groq has no embeddings model, so there is no vector search.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from bot.places import (
    AREA_NAMES,
    FOREST,
    PLACE_AREAS,
    PLACES,
    Area,
    Scope,
    ScopeKind,
    area_scope,
    find_areas,
    find_places,
    place_scope,
    scopes_for_place,
    strip_scope_words,
)


class Doc(Protocol):
    title: str
    text: str

    @property
    def scope(self) -> Scope: ...


@dataclass
class QueryScope:
    """Places/areas a question is about. Both empty = not specified."""

    places: list[str] = field(default_factory=list)
    areas: list[Area] = field(default_factory=list)

    @property
    def is_specific(self) -> bool:
        return bool(self.places or self.areas)

    def allowed(self) -> list[Scope] | None:
        """Scopes whose documents may answer, or None when nothing was named."""
        if not self.is_specific:
            return None
        scopes: list[Scope] = []
        for place in self.places:
            scopes += scopes_for_place(place)
        for area in self.areas:
            scopes.append(area_scope(area))
            # «що їсти в хвойному лісі?» may also be answered by its places' documents.
            scopes += [place_scope(p) for p in PLACES if area in PLACE_AREAS[p]]
        scopes.append(FOREST)
        return list(dict.fromkeys(scopes))  # dedupe, keep order


def detect_scope(question: str, previous_question: str | None = None) -> QueryScope:
    """Places/areas named in the question; if none, inherit from the previous one.

    Inheriting makes follow-ups work: «скільки внесок під дубом?» → «а до якого числа?».
    """
    scope = QueryScope(places=find_places(question), areas=find_areas(question))
    if not scope.is_specific and previous_question:
        scope = QueryScope(
            places=find_places(previous_question), areas=find_areas(previous_question)
        )
    return scope


_SPECIFICITY = {ScopeKind.PLACE: 0, ScopeKind.AREA: 1, ScopeKind.FOREST: 2}


def candidates[D: Doc](docs: list[D], query_scope: QueryScope) -> list[D]:
    """Documents allowed for this question, most specific scope first."""
    allowed = query_scope.allowed()
    if allowed is not None:
        allowed_set = set(allowed)
        docs = [d for d in docs if d.scope in allowed_set]
    return sorted(docs, key=lambda d: _SPECIFICITY[d.scope.kind])


def level_label(scope: Scope) -> str:
    """How the scope is described to the LLM."""
    if scope.kind is ScopeKind.PLACE:
        return f"рукавичка «{scope.value}»"
    if scope.kind is ScopeKind.AREA:
        area = Area(scope.value)
        members = ", ".join(f"«{p}»" for p in PLACES if area in PLACE_AREAS[p])
        return f"{AREA_NAMES[area]} (рукавички {members})"
    return "увесь ліс"


# --- lexical search ---

# Ukrainian words change endings (горіх / горіхи / горіхів), so words are
# compared by their first letters: a crude but predictable stem.
STEM_LENGTH = 5
TITLE_WEIGHT = 3
BM25_K1 = 1.5
BM25_B = 0.75
LEXICAL_TOP = 3
# Ignore hits much weaker than the best one: they are noise for the LLM.
MIN_RELATIVE_SCORE = 0.5

_WORD = re.compile(r"[а-яіїєґa-z0-9']+")
_STOPWORDS = {
    "і", "й", "та", "а", "але", "в", "у", "на", "з", "із", "зі", "до", "від", "по",
    "за", "під", "над", "біля", "для", "про", "чи", "що", "як", "де", "коли", "хто",
    "який", "яка", "яке", "які", "якого", "якій", "це", "той", "ті", "то", "не", "ні",
    "так", "же", "ж", "би", "б", "є", "мені", "мене", "нам", "нас", "ми", "ти", "ви",
    "вони", "він", "вона", "його", "її", "їх", "можна", "треба", "скільки", "чому",
    "куди", "тут", "там", "все", "усе", "всі", "усі", "якщо", "мій", "моя", "мої",
    "рукавичка", "рукавички", "рукавичці", "рукавичку", "рукавичкою", "живу",
}


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("ʼ", "'")
    return [w[:STEM_LENGTH] for w in _WORD.findall(text) if w not in _STOPWORDS]


def lexical_search[D: Doc](question: str, docs: list[D], top: int = LEXICAL_TOP) -> list[D]:
    """Best BM25 matches by title and text. Place names are removed from the query:
    the scope filter already used them, and they'd match every document of a place."""
    query_terms = set(tokenize(strip_scope_words(question)))
    if not docs or not query_terms:
        return []
    scores = _bm25(query_terms, docs)
    best = max(scores)
    if best <= 0:
        return []
    ranked = sorted(zip(scores, docs, strict=True), key=lambda pair: -pair[0])
    return [d for score, d in ranked[:top] if score >= best * MIN_RELATIVE_SCORE]


def _bm25(query_terms: set[str], docs: list[Doc]) -> list[float]:
    doc_terms = [Counter(tokenize(d.text) + tokenize(d.title) * TITLE_WEIGHT) for d in docs]
    avg_len = sum(sum(c.values()) for c in doc_terms) / len(doc_terms)
    doc_freq = Counter(term for c in doc_terms for term in c)
    n = len(docs)
    scores = []
    for counts in doc_terms:
        length = sum(counts.values())
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if tf:
                idf = math.log(1 + (n - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                score += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * length / avg_len))
        scores.append(score)
    return scores
