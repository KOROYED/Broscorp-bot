import json

import pytest

from bot.archive import import_archive
from bot.documents import SOURCE_USER, active_documents, save_document
from bot.places import place_scope

pytestmark = pytest.mark.anyio


@pytest.fixture
def archive_dir(tmp_path):
    directory = tmp_path / "archive"
    directory.mkdir()
    (directory / "fees.txt").write_text("Внесок — 4 жолуді", encoding="utf-8")
    (directory / "fire.txt").write_text("Біжіть до річки", encoding="utf-8")
    manifest = [
        {"file": "fees.txt", "title": "Членські внески", "scope_kind": "place",
         "scope_value": "Під дубом"},
        {"file": "fire.txt", "title": "Що робити при пожежі", "scope_kind": "forest",
         "scope_value": ""},
    ]
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


async def test_import_is_idempotent(session_factory, archive_dir):
    assert await import_archive(session_factory, archive_dir) == 2
    assert await import_archive(session_factory, archive_dir) == 0


async def test_changed_archive_file_replaces_old_version(session_factory, archive_dir):
    await import_archive(session_factory, archive_dir)
    (archive_dir / "fees.txt").write_text("Внесок — 5 жолудів", encoding="utf-8")
    assert await import_archive(session_factory, archive_dir) == 1
    async with session_factory() as session:
        texts = [d.text for d in await active_documents(session)]
    assert "Внесок — 5 жолудів" in texts
    assert "Внесок — 4 жолуді" not in texts


async def test_archive_document_replaced_by_animal_is_not_restored(session_factory, archive_dir):
    await import_archive(session_factory, archive_dir)
    async with session_factory() as session:
        await save_document(
            session, title="Членські внески", scope=place_scope("Під дубом"), text="Нові внески",
            source=SOURCE_USER, content_hash="new",
        )
    await import_archive(session_factory, archive_dir)  # e.g. after a restart
    async with session_factory() as session:
        texts = [d.text for d in await active_documents(session)]
    assert texts.count("Нові внески") == 1
    assert "Внесок — 4 жолуді" not in texts

