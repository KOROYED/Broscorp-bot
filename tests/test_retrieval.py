from dataclasses import dataclass

from bot.places import FOREST, Area, Scope, area_scope, place_scope
from bot.retrieval import QueryScope, candidates, detect_scope, lexical_search


@dataclass
class FakeDoc:
    title: str
    scope: Scope
    text: str = ""


FEES_OAK = FakeDoc("Членські внески", place_scope("Під дубом"), "Внесок під дубом — 4 жолуді на місяць")
FEES_FIR = FakeDoc("Членські внески", place_scope("Під ялиною"), "3 шишки на місяць")
RULES_OAK = FakeDoc("Правила співжиття", place_scope("Під дубом"), "Тиша в рукавичці «Під дубом» триває з полудня")
CONIFEROUS = FakeDoc("Небезпеки хвойного лісу", area_scope(Area.CONIFEROUS), "Живиця липне")
DECIDUOUS = FakeDoc("Що їсти в листяному лісі", area_scope(Area.DECIDUOUS), "Жолуді")
FOREST_DOC = FakeDoc("Правила толоки", FOREST, "Толоку скликає Мишка")
ALL = [FEES_OAK, FEES_FIR, RULES_OAK, CONIFEROUS, DECIDUOUS, FOREST_DOC]


def test_named_place_limits_candidates_and_puts_specific_first():
    result = candidates(ALL, detect_scope("Скільки внесок під дубом?"))
    assert result == [FEES_OAK, RULES_OAK, DECIDUOUS, FOREST_DOC]


def test_forest_edge_does_not_get_coniferous_advice():
    result = candidates(ALL, detect_scope("Що робити на узліссі, якщо прилипла живиця?"))
    assert CONIFEROUS not in result
    assert FOREST_DOC in result


def test_no_place_means_all_documents():
    assert len(candidates(ALL, detect_scope("Де найменший внесок?"))) == len(ALL)


def test_follow_up_inherits_place_from_previous_question():
    scope = detect_scope("А до якого числа?", previous_question="Скільки внесок під дубом?")
    assert scope == QueryScope(places=["Під дубом"])


def test_new_place_overrides_previous_question():
    scope = detect_scope("А під ялиною?", previous_question="Скільки внесок під дубом?")
    assert scope.places == ["Під ялиною"]


def test_lexical_search_finds_word_inside_text():
    assert lexical_search("Коли тиша під дубом?", [FEES_OAK, RULES_OAK]) == [RULES_OAK]


def test_lexical_search_ignores_place_words():
    # "дубом" is in every oak document's text; it must not make them all match.
    assert lexical_search("під дубом", [FEES_OAK, RULES_OAK]) == []
