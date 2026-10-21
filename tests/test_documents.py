from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from bot.documents import (
    SOURCE_ARCHIVE,
    SOURCE_USER,
    active_documents,
    format_citation,
    normalize_title,
    save_document,
    set_user_name,
    undo_replacement,
)
from bot.places import FOREST, Area, area_scope, place_scope

pytestmark = pytest.mark.anyio
KYIV = ZoneInfo("Europe/Kyiv")
OAK = place_scope("Під дубом")


async def save(
    session, title, scope=OAK, source=SOURCE_USER, author_id=None, text_suffix="", **kw
):
    return await save_document(
        session,
        title=title,
        scope=scope,
        text=f"text of {title}",
        source=source,
        content_hash=f"hash-{title}-{scope}{text_suffix}",
        author_id=author_id,
        **kw,
    )


def test_normalize_title_ignores_place_and_punctuation():
    assert normalize_title("Членські внески рукавички «Під дубом»") == "членські внески"
    assert normalize_title("ЧЛЕНСЬКІ ВНЕСКИ!") == "членські внески"


async def test_newer_version_for_same_place_replaces_old(session):
    await set_user_name(session, 1, "Їжак")
    await set_user_name(session, 2, "Лисичка")
    first = await save(session, "Членські внески", author_id=1)
    second = await save(session, "Членські внески рукавички «Під дубом»", author_id=2)

    assert second.replaced.id == first.document.id
    active = await active_documents(session)
    assert [d.id for d in active] == [second.document.id]


async def test_same_title_for_other_place_is_a_separate_document(session):
    await save(session, "Членські внески", scope=OAK)
    result = await save(session, "Членські внески", scope=place_scope("Під ялиною"))
    assert result.replaced is None
    assert len(await active_documents(session)) == 2


async def test_animal_can_replace_archive_document(session):
    await set_user_name(session, 1, "Їжак")
    archive = await save(session, "Членські внески", source=SOURCE_ARCHIVE)
    result = await save(session, "Членські внески", author_id=1)
    assert result.replaced.id == archive.document.id


async def test_matched_new_version_replaces_that_exact_document(session):
    fees = await save(session, "Членські внески")
    rules = await save(session, "Правила співжиття")
    # Different title, so only the version check links it to «Членські внески».
    result = await save(session, "Внески на зиму", replace_id=fees.document.id)

    assert result.replaced.id == fees.document.id
    assert result.replaced.replaced_by_id == result.document.id
    active = await active_documents(session)
    assert [d.id for d in active] == [rules.document.id, result.document.id]


async def test_replace_id_of_already_replaced_document_replaces_its_newest_version(session):
    old = await save(session, "Членські внески")
    newer = await save(session, "Членські внески", text_suffix="v2")
    # The check matched `old`, but someone replaced it in the meantime.
    result = await save(session, "Внески на зиму", replace_id=old.document.id)

    # The version that is active now is replaced instead; the history chain stays intact.
    assert result.replaced.id == newer.document.id
    assert old.document.replaced_by_id == newer.document.id
    assert newer.document.replaced_by_id == result.document.id
    active = await active_documents(session)
    assert [d.id for d in active] == [result.document.id]


async def test_without_replace_id_a_different_title_is_kept_separate(session):
    await save(session, "Членські внески")
    result = await save(session, "Внески на зиму")
    assert result.replaced is None
    assert len(await active_documents(session)) == 2


async def test_undo_brings_back_the_replaced_document(session):
    fees = await save(session, "Членські внески")
    new = await save(session, "Внески на зиму", replace_id=fees.document.id)
    # «Ні, це інша порада — поверни стару»: both live on as separate advice.
    result = await undo_replacement(session, new.document.id)

    assert result.restored.id == fees.document.id
    assert result.restored.is_active
    assert result.restored.replaced_by_id is None
    active = await active_documents(session)
    assert [d.id for d in active] == [fees.document.id, new.document.id]


async def test_undo_is_refused_when_the_new_document_was_replaced_again(session):
    fees = await save(session, "Членські внески")
    new = await save(session, "Внески на зиму", replace_id=fees.document.id)
    newest = await save(session, "Внески на зиму", text_suffix="v2")
    result = await undo_replacement(session, new.document.id)

    assert result.restored is None
    assert result.newer_exists
    # Nothing changed: only the newest version is active, the history chain is intact.
    assert fees.document.replaced_by_id == new.document.id
    active = await active_documents(session)
    assert [d.id for d in active] == [newest.document.id]


async def test_undo_twice_restores_only_once(session):
    fees = await save(session, "Членські внески")
    new = await save(session, "Внески на зиму", replace_id=fees.document.id)
    await undo_replacement(session, new.document.id)
    again = await undo_replacement(session, new.document.id)
    assert again.restored is None
    assert not again.newer_exists


async def test_undo_without_a_replacement_does_nothing(session):
    new = await save(session, "Внески на зиму")
    result = await undo_replacement(session, new.document.id)
    assert result.restored is None
    assert len(await active_documents(session)) == 1


async def test_citation_for_animal_document_has_author_and_kyiv_time(session):
    await set_user_name(session, 1, "Їжак")
    sent = datetime(2026, 10, 12, 11, 30, tzinfo=UTC)  # 14:30 in Kyiv (UTC+3 in October)
    doc = (await save(session, "Членські внески", author_id=1, sent_at=sent)).document
    assert format_citation(doc, KYIV) == (
        "— з документа «Членські внески» рукавички «Під дубом», надіслав Їжак, 12.10 о 14:30"
    )


async def test_citation_for_archive_document_has_title_and_place_only(session):
    doc = (await save(session, "Членські внески", source=SOURCE_ARCHIVE)).document
    assert format_citation(doc, KYIV) == "— з документа «Членські внески» рукавички «Під дубом»"


async def test_citation_for_area_and_forest_documents(session):
    area = (await save(session, "Небезпеки хвойного лісу", scope=area_scope(Area.CONIFEROUS),
                       source=SOURCE_ARCHIVE)).document
    generic = (await save(session, "Як зимувати", scope=area_scope(Area.RIVER),
                          source=SOURCE_ARCHIVE)).document
    forest = (await save(session, "Правила толоки", scope=FOREST, source=SOURCE_ARCHIVE)).document
    assert format_citation(area, KYIV) == "— з документа «Небезпеки хвойного лісу»"
    assert format_citation(generic, KYIV) == "— з документа «Як зимувати» (біля річки)"
    assert format_citation(forest, KYIV) == "— з документа «Правила толоки»"
