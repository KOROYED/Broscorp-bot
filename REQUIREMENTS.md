# Requirements

Gathered in conversation with the client, Мишка-шкряботушка. **Confirmed** items are her answers;
**Decision** items are ones she delegated («зроби, як вважаєш правильним, тільки скажи мені»);
they still need her confirmation.

## Purpose and users

- **Confirmed.** Animals send documents with advice; others ask questions and get answers taken
  from those documents, **always with the source**.
- **Confirmed.** Users are all forest animals. Everyone is equal: no admins, no permissions.

## Documents

- **Confirmed.** Formats: PDF, Word, plain text. No photos of documents, no voice messages.
- **Confirmed.** On an animal's first upload the bot asks «Як тебе звати?» and remembers the real
  name. The date and time of sending are stored.
- **Confirmed.** A fixed list of 8 places (рукавички); new ones never appear:

  | Place | Area |
  |---|---|
  | Під ялиною, У соснах, Біля мурашника | coniferous forest |
  | Під дубом, У ліщині | deciduous forest |
  | Під вербою | deciduous forest **and** by the river |
  | Біля бобрової греблі | by the river |
  | На узліссі | none of them |

- **Confirmed.** Each document applies to exactly one level: **the whole forest, one area, or one
  place**. Specific advice outranks general advice; general advice may be added as secondary.
- **Confirmed.** The place is taken from the document's content or title («під ялинами» = coniferous
  forest). **If unclear, the bot asks the sender. It never guesses.**
- **Confirmed.** Truthfulness of advice is not checked.
- **Confirmed.** Documents don't expire. A newer version of the same advice for the same place replaces
  the old one. Anyone may replace; the answer cites the new sender and time.
- **Decision.** "Same document" = same title (ignoring the place name, case and punctuation) + same
  place/area. The bot tells the sender when their upload replaced an older version.
- **Confirmed.** No commands: a plain text message is a question, an uploaded file is advice.
  A wrong document is fixed by sending a corrected version, which replaces it.
- **Decision.** Unsupported or unreadable files get an explanation: wrong format (and which formats
  work), old `.doc` (save as .docx/PDF), empty file, damaged file. Scanned PDFs are read with OCR.
- **Decision.** The exact same file sent twice is recognized and not stored again.

## «Лісовий архів»

- **Confirmed.** Standard documents are in the Google Drive folder «Лісовий архів». The bot knows them
  from the start; nobody uploads them via Telegram. Citations show the title (and place) only.
- **Decision.** The folder (78 files, about 20 of them scans) is snapshotted into the repository and
  imported at startup.

## Questions and answers

- **Confirmed.** A plain text message is a question. Animals aren't bound to a place: they name it in
  the question.
- **Confirmed.** Answers are in Ukrainian, simple and to the point, never invented. If nothing is
  found, the bot says so.
- **Confirmed.** Citation format:
  `— з документа «Членські внески» рукавички «Під дубом», надіслав Їжак, 12.10 о 14:30`;
  archive documents have no author or time.
- **Decision.** Conversation memory: the last 3 questions/answers per chat, so follow-ups work
  («а до якого числа?» keeps the place).
- **Decision.** If a question names no place and the answer differs by place, the bot lists the answer
  for each place or asks which one.
- **Decision.** Comparison questions («де найменший внесок?») compare all places that have documents.
- **Decision.** If the LLM is unavailable: «Зараз не можу відповісти… спробуй за хвилину». The error
  is logged.

## Open questions

1. Is «Лісовий архів» static, or will it be updated (does the bot need to sync with Drive)?
2. Private chats only, or also a group chat? (Currently private chats.)
3. If someone sends advice as plain text instead of a document? (Currently treated as a question.)
4. Expected number and size of documents?
5. Timezone for times (Kyiv assumed).
