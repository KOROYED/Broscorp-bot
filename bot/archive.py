"""Import «Лісовий архів» (the client's Google Drive folder) at startup.

The files are a snapshot in data/archive/. manifest.json records each file's
title and scope: I read every document and wrote down who it is for. Titles
alone mislead: «Коли падають жолуді й горіхи» is for the deciduous forest,
and «Як не заблукати серед однакових сосен» is for all coniferous forest, not
the place «У соснах».

Import is idempotent: a file whose bytes were already imported is skipped, so
restarts are fast (no OCR) and an archive document that an animal replaced is
not brought back.
"""

import asyncio
import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.documents import SOURCE_ARCHIVE, archive_hashes, file_hash, save_document
from bot.parsing import ParsingError, extract_text
from bot.places import Scope, ScopeKind

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"


async def import_archive(
    session_factory: async_sessionmaker[AsyncSession], archive_dir: Path
) -> int:
    """Import new or changed archive files. Returns how many were imported."""
    manifest_path = archive_dir / MANIFEST
    if not manifest_path.exists():
        logger.warning("No archive manifest at %s, skipping archive import", manifest_path)
        return 0
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))

    listed = {e["file"] for e in entries}
    for path in archive_dir.iterdir():
        if path.name != MANIFEST and path.name not in listed:
            logger.warning("Archive file %s is not in the manifest, skipped", path.name)

    imported = 0
    async with session_factory() as session:
        known = await archive_hashes(session)
        for entry in entries:
            path = archive_dir / entry["file"]
            data = path.read_bytes()
            digest = file_hash(data)
            if digest in known.get(entry["file"], set()):
                continue

            try:
                # OCR is CPU-bound: run it in a thread so the event loop stays free.
                text = await asyncio.to_thread(extract_text, path.name, data)
            except ParsingError as e:
                logger.error("Archive file %s can't be read: %s", path.name, e)
                continue

            await save_document(
                session,
                title=entry["title"],
                scope=Scope(ScopeKind(entry["scope_kind"]), entry["scope_value"]),
                text=text,
                source=SOURCE_ARCHIVE,
                content_hash=digest,
                archive_key=entry["file"],
            )
            imported += 1
            logger.info("Imported archive document %s", path.name)
    return imported
