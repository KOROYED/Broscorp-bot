import io
import shutil
from pathlib import Path

import docx
import pytest

from bot.parsing import ParsingError, check_extension, extract_text

ARCHIVE = Path(__file__).parent.parent / "data" / "archive"


def test_txt_with_bom():
    assert extract_text("a.txt", "﻿Привіт\n\n  ліс ".encode()) == "Привіт\nліс"


def test_txt_in_windows_encoding():
    assert extract_text("a.txt", "Привіт".encode("cp1251")) == "Привіт"


def test_docx():
    document = docx.Document()
    document.add_paragraph("Членські внески")
    buffer = io.BytesIO()
    document.save(buffer)
    assert extract_text("a.docx", buffer.getvalue()) == "Членські внески"


def test_pdf_with_text_layer():
    text = extract_text("map.pdf", (ARCHIVE / "Карта лісу.pdf").read_bytes())
    assert "Під вербою" in text


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_scanned_pdf_goes_through_ocr():
    data = (ARCHIVE / "Членські внески рукавички Під ялиною.pdf").read_bytes()
    assert "шишки" in extract_text("scan.pdf", data)


@pytest.mark.parametrize("name", ["photo.jpg", "voice.ogg", "table.xlsx"])
def test_unsupported_format(name):
    with pytest.raises(ParsingError, match="PDF"):
        check_extension(name)


def test_old_doc_format_has_specific_message():
    with pytest.raises(ParsingError, match=r"\.doc"):
        check_extension("old.doc")


def test_empty_file():
    with pytest.raises(ParsingError, match="нічого, що я міг би прочитати"):
        extract_text("empty.txt", b"   \n ")


def test_broken_pdf():
    with pytest.raises(ParsingError, match="пошкоджена"):
        extract_text("broken.pdf", b"not a pdf")
