# Forest advice bot (Мишка-шкряботушка's RAG bot)

A Telegram bot for the forest community. Animals send documents with advice; anyone can ask a
question in plain text and gets an answer **only from those documents, always with the source**:

> **Q:** Скільки внесок під дубом і до якого числа?
> **A:** 4 жолуді з дорослого звіра на місяць, здати до 10 числа.
> — з документа «Членські внески» рукавички «Під дубом»

All requirements gathered from the client, each confirmed by her, are in
[REQUIREMENTS.md](REQUIREMENTS.md).

## Run

```bash
cp .env.example .env   # then fill in BOT_TOKEN and GROQ_API_KEY
docker compose up --build
```

That's it: PostgreSQL starts, the bot creates its tables, imports «Лісовий архів» (first start
OCRs ~20 scanned PDFs, about a minute), and starts polling.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `BOT_TOKEN` | yes | | Telegram bot token from @BotFather |
| `GROQ_API_KEY` | yes | | Groq API key (free tier is enough) |
| `GROQ_MODEL` | no | `openai/gpt-oss-120b` | Model that writes answers |
| `GROQ_ROUTER_MODEL` | no | `openai/gpt-oss-20b` | Model that picks documents and classifies uploads |
| `GROQ_REASONING_EFFORT` | no | `low` | `low` / `medium` / `high` |
| `TIMEZONE` | no | `Europe/Kyiv` | Timezone for "надіслав …, 12.10 о 14:30" |
| `POSTGRES_PASSWORD` | no | `bot` | DB password (the DB isn't exposed outside Docker) |

Missing or empty required variables stop the bot at startup with a clear error.

## Using the bot

There are no commands: **any text message is a question, any file is advice.**

- **Ask**: name the place, e.g. «що їсти біля мурашника?». Follow-ups work: «а до якого числа?»
  keeps the place from the previous question.
- **Share advice**: send a PDF, Word (.docx) or .txt file. On the first upload the bot asks your
  name. It works out the place from the document; if it's unclear it **asks you** (buttons), never guesses.

## How it works

```
question ─► scope filter ─► router LLM (by titles) ┐
            (code)          BM25 search (by text)  ┴─► pick 1 doc ─► answer from it ─► 1 citation
```

1. **Scope filter** (`bot/retrieval.py`, `bot/places.py`). A document belongs to the whole forest,
   one area (coniferous / deciduous / river), or one place. If the question names a place,
   only that place's documents + its areas' + whole-forest documents are candidates. «Під вербою»
   is in two areas; «На узліссі» in none. Plain code: exact, free, testable.
2. **Hybrid retrieval.** A small model (`gpt-oss-20b`) picks documents by *meaning* from their titles
   («поїсти» → «Чим поживитись»); BM25 finds *words inside* the text («тиша» is a section of
   «Правила співжиття»). Groq has no embeddings model, and the corpus is small, so no vector DB.
3. **Pick, then answer** (`gpt-oss-120b`). One answer comes from **one document**, never a mix:
   - *Pick*: the model sees the candidates and returns only `{status, source, question}`: the number
     of the one most relevant document (place-specific outranks general, the client's rule) and the
     question made standalone from earlier *questions* (never earlier answers). It writes no answer.
   - *Answer*: a second call sees **only that document** and the question, so there is nothing to mix
     in. Direct facts or what logically follows («6 жолудів більше за потрібні 5»), or `found: false`.
   - The **code** appends that single citation. Not found, «покажи всі поради», comparisons and
     questions that need an unnamed place get fixed replies written by code, with no citation.
4. **Uploads** (`bot/parsing.py`, `bot/llm.py`). Text is extracted (pypdf, python-docx, Tesseract OCR
   for scans). The router model returns a title and scope; `unclear` → the bot asks the sender.
5. **Versioning** (`bot/documents.py`). Same normalized title + same scope = newer version: the old one
   is deactivated (kept for history), and answers cite the new sender and time. The exact same file
   sent twice is detected by hash.

### «Лісовий архів»

The client's Google Drive folder is snapshotted in `data/archive/` (78 files). `manifest.json` records
each file's title and scope. I read every document to fill it in, because titles mislead:
«Коли падають жолуді й горіхи» is for the deciduous forest, and «Як не заблукати серед однакових
сосен» is for all coniferous forest, not the place «У соснах». Import is idempotent (by file hash):
restarts don't re-run OCR, and an archive document replaced by an animal isn't brought back.

### Design choices

- **AsyncGroq, one client** for the app: Groq calls are I/O-bound (async keeps the bot responsive);
  one client reuses HTTPS connections. OCR is CPU-bound, so it runs in `asyncio.to_thread`.
- **Free-tier limits** (8k tokens/minute per model): two models with separate limits, short context
  (≤10 small documents), `reasoning_effort=low`. If Groq fails, the user gets a friendly message.
- **Prompt injection**: uploaded documents are untrusted. Prompts tell the model that document text is
  data, not instructions, and citations are produced by code, not by the model.
- **PostgreSQL + Docker volume**: documents and names survive restarts. Tables are created with
  `create_all`; a real project would add Alembic migrations.
- **Conversation memory** is in-process (last 3 turns per chat): it only serves follow-up questions.
- **Replace first, undo on «Ні»**: a likely new version under another title replaces the old one at
  once (the client's rule: a newer version always replaces the old one), and the sender gets one button
  to undo it. Undo works only for the sender's latest replacement, and only while the new version is
  still the latest; older undo buttons expire, and the button carries no ids.

## Limitations and scaling

Sized for the client's numbers: a few hundred documents per season and tens of animals at once.
Known limitations, and what I'd change next:

- **Router prompt growth:** the router sees the title of every candidate document, so its prompt
  grows with each new upload. At a few hundred documents this is the first limit to hit the free
  tier's 8k tokens/minute. Fix: let BM25 shortlist 30–40 titles first and give the router only
  those, so the prompt stays the same size however big the archive gets.
- **FSM state and chat memory** live in the bot's RAM. If the bot restarts while waiting for an
  animal's answer (their name, the place, or «Так/Ні»), the pending upload is lost and follow-up
  questions lose their context. Fix: aiogram's `RedisStorage` and a Redis-backed memory, so both
  survive restarts.
- **Tests run on SQLite**, production on Postgres. SQLite is fast and needs no server, but it's more
  forgiving: by default it doesn't enforce foreign keys, so a document pointing to a user who doesn't
  exist passes the tests and fails in production. Fix: run the same tests against Postgres with
  testcontainers, rolling back each test's transaction to keep them isolated.
- **Long documents:** each document is cut to its first 6000 characters before the model sees it, so
  advice deep inside a long document can't be answered: the animal is told «нічого не знаю» about
  something that is stored, and no error shows. Fix: split documents into chunks by section when they
  are saved, search the chunks, and give the model the best one, still citing the whole document.
- **Missed versions:** a new version under a different title is detected by the LLM. If it misses the
  match, or the check fails, both versions stay active. Fix: also compare the text with BM25 and flag
  very similar advice in the same place.

## Development

```bash
uv sync
uv run pytest            # tests use SQLite and a mocked LLM; no network needed
uv run python -m bot.main  # needs DATABASE_URL, e.g. sqlite+aiosqlite:///bot.db, and Tesseract for scans
```

```
bot/
  main.py        startup: DB, archive import, polling
  handlers.py    Telegram layer only
  qa.py          question → documents → answer + citations
  retrieval.py   scope filter + BM25
  llm.py         Groq calls (answer, router, classifier); prompts.py holds the prompts
  documents.py   storage, versioning, citations
  parsing.py     PDF / DOCX / TXT + OCR
  archive.py     «Лісовий архів» import
  places.py      the 8 places and 3 areas
  db.py, config.py, memory.py
data/archive/    the archive snapshot + manifest.json
tests/
```
