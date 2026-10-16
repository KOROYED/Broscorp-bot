"""Turn an uploaded file (PDF, Word .docx, plain text) into plain text.

PDFs without a text layer (scans) go through OCR with Tesseract: several
documents in «Лісовий архів» are scans like that.
"""

import io
import logging
from pathlib import PurePath

import docx
import pypdfium2
import pytesseract
from pypdf import PdfReader

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}
# A page with fewer characters than this is treated as a scan.
MIN_CHARS_PER_PAGE = 20
OCR_LANGUAGES = "ukr+eng"
OCR_DPI = 300


class ParsingError(Exception):
    """The file can't be turned into text. The message is shown to the user."""


def check_extension(filename: str) -> str:
    """Return the lowercase extension or raise ParsingError if it isn't supported."""
    ext = PurePath(filename).suffix.lower()
    if ext == ".doc":
        raise ParsingError(
            "Старий формат .doc я не читаю. Збережи файл як .docx або PDF і надішли ще раз."
        )
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParsingError("Я приймаю лише PDF, Word (.docx) або текстові файли (.txt).")
    return ext


def extract_text(filename: str, data: bytes) -> str:
    """Return the document's text or raise ParsingError with a friendly reason.

    CPU-bound (OCR can take seconds), so async code calls it via asyncio.to_thread.
    """
    ext = check_extension(filename)

    try:
        if ext == ".pdf":
            text = _pdf_text(data)
        elif ext == ".docx":
            text = _docx_text(data)
        else:
            text = _txt_text(data)
    except ParsingError:
        raise
    except pytesseract.TesseractNotFoundError as e:
        # Our problem, not the user's file: Tesseract must be installed (it is in Docker).
        logger.error("Tesseract is not installed, can't OCR %s", filename)
        raise ParsingError("Зараз я не можу розпізнати скан. Спробуй надіслати файл з текстом.") from e
    except Exception as e:
        logger.exception("Failed to parse %s", filename)
        raise ParsingError("Не вдалося відкрити файл: можливо, він пошкоджений.") from e

    text = _clean(text)
    if not text:
        raise ParsingError("У файлі немає тексту, який я можу прочитати.")
    return text


def _pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if len(text.strip()) < MIN_CHARS_PER_PAGE:
            text = _ocr_pdf_page(data, i)
        pages.append(text)
    return "\n".join(pages)


def _ocr_pdf_page(data: bytes, page_index: int) -> str:
    pdf = pypdfium2.PdfDocument(data)
    try:
        image = pdf[page_index].render(scale=OCR_DPI / 72).to_pil()
    finally:
        pdf.close()
    return pytesseract.image_to_string(image, lang=OCR_LANGUAGES)


def _docx_text(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _txt_text(data: bytes) -> str:
    # utf-8-sig also strips the BOM some Windows editors add.
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParsingError("Не вдалося прочитати текстовий файл: збережи його в кодуванні UTF-8.")


def _clean(text: str) -> str:
    """Drop empty lines and trailing spaces, keep line structure."""
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)
