import pytest

from bot.places import (
    FOREST,
    Area,
    Scope,
    ScopeKind,
    area_scope,
    find_areas,
    find_places,
    place_scope,
    scopes_for_place,
)


@pytest.mark.parametrize(
    ("question", "places"),
    [
        ("Скільки внесок під дубом?", ["Під дубом"]),
        ("а в соснах?", ["У соснах"]),
        ("Як дійти від дуба до ліщини?", ["Під дубом", "У ліщині"]),
        ("Хто чергує біля бобрової греблі?", ["Біля бобрової греблі"]),
        ("Що є на узліссі?", ["На узліссі"]),
        ("Що їсти під ялиною?", ["Під ялиною"]),
        # Plural «під ялинами» means coniferous forest, not the place (client's hint).
        ("Що росте під ялинами?", []),
        ("Хто скликає толоку?", []),
    ],
)
def test_find_places(question, places):
    assert find_places(question) == places


def test_find_areas():
    assert find_areas("Що їсти в хвойному лісі?") == [Area.CONIFEROUS]
    assert find_areas("Що росте під ялинами?") == [Area.CONIFEROUS]
    assert find_areas("небезпеки біля річки") == [Area.RIVER]


def test_willow_belongs_to_two_areas():
    assert scopes_for_place("Під вербою") == [
        place_scope("Під вербою"),
        area_scope(Area.DECIDUOUS),
        area_scope(Area.RIVER),
        FOREST,
    ]


def test_forest_edge_gets_no_area_advice():
    assert scopes_for_place("На узліссі") == [place_scope("На узліссі"), FOREST]


def test_unknown_place_is_rejected():
    with pytest.raises(ValueError):
        Scope(ScopeKind.PLACE, "Під кущем")
