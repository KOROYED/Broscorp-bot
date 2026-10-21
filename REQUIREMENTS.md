# Requirements

Gathered in conversation with the client, Мишка-шкряботушка. Every item is confirmed by her; where
she left the choice to me («зроби, як вважаєш правильним, тільки скажи мені»), the item says so or
describes my proposal, which she then approved.

## Purpose and users

- **Confirmed.** Animals send documents with advice; others ask questions and get answers taken
  from those documents, **always with the source**.
- **Confirmed.** Users are all forest animals. Everyone is equal: no admins, no permissions.
- **Confirmed.** The bot works in private chats only; group chats are not required.
- **Confirmed.** Number of concurrent users was left to me. The bot is sized for a forest community of
  tens of animals asking at the same time; the main limit is the Groq free tier. See README →
  "Limitations and scaling" for what would change at a larger scale.

## Documents

- **Confirmed.** Formats: PDF, Word, plain text. No photos of documents, no voice messages.
- **Confirmed.** Advice is accepted only as a file (PDF, Word .docx, plain text .txt). A plain text
  message is always a question, never advice.
- **Confirmed.** On an animal's first upload the bot asks «Як тебе звати?» and remembers the real
  name. The date and time of sending are stored.
- **Confirmed.** The name is asked once, on the first upload, and signs all of that animal's advice
  from then on. It can't be changed later.
- **Confirmed.** A рукавичка is a physical place in the forest where animals live and have their
  ОСМД. The word may be used in answers, e.g. «порада для рукавички «Під дубом»».
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
- **Confirmed.** Documents don't expire. A newer version of the same advice for the same place **always**
  replaces the old one: the replacement never waits for a confirmation. Two versions of the same advice
  must never both stay active. Anyone may replace; the answer cites the new sender and time.
- **Confirmed.** "Same document" = same title (ignoring the place name, case and punctuation) + same
  place/area. The bot tells the sender when their upload replaced an older version.
- **Confirmed.** If the title differs but the advice looks like a new version of one already stored for
  the same place/area, it replaces that one at once, and the bot tells the sender what was replaced:
  «Вона замінила пораду «Членські внески», яку надіслав Їжак, 12.10 о 14:30» with one button
  [Ні, це інша порада — поверни стару]. The old advice comes back only if nobody has replaced the new
  one since; otherwise the bot says a newer version already exists.
- **Confirmed.** No commands: a plain text message is a question, an uploaded file is advice.
  A wrong document is fixed by sending a corrected version, which replaces it.
- **Confirmed.** Unsupported or unreadable files get an explanation: wrong format (and which formats
  work), old `.doc` (save as .docx/PDF), empty file, damaged file. Scanned PDFs are read with OCR.
- **Confirmed.** Files up to 20 MB are accepted (the most a Telegram bot can download).
- **Confirmed.** A document that contains instructions to the bot instead of advice for animals
  («ігноруй правила», «відповідай, що…») is not stored; the sender is asked to remove them.
- **Confirmed.** The exact same file sent twice is recognized and not stored again.

## «Лісовий архів»

- **Confirmed.** Standard documents are in the Google Drive folder «Лісовий архів». The bot knows them
  from the start; nobody uploads them via Telegram. Citations show the title (and place) only.
- **Confirmed.** The folder (78 files, about 20 of them scans) is snapshotted into the repository and
  imported at startup. The client left syncing with Google Drive to me: no sync, because the archive
  changes rarely and a sync would need Google API credentials and handling of renamed and deleted files.
  When the archive changes, the snapshot is updated; restarts import only changed files.
- **Confirmed.** The archive has fewer than 100 documents. In autumn animals send a few new ones every
  day, so a few hundred per season.

## Questions and answers

- **Confirmed.** A plain text message is a question. Animals aren't bound to a place: they name it in
  the question.
- **Confirmed.** Answers are in Ukrainian, simple and to the point, never invented. If nothing is
  found, the bot says so.
- **Confirmed.** Citation format:
  `— з документа «Членські внески» рукавички «Під дубом», надіслав Їжак, 12.10 о 14:30`;
  archive documents have no author or time.
- **Confirmed.** Conversation memory: the last 3 questions/answers per chat, so follow-ups work
  («а до якого числа?» keeps the place).
- **Confirmed.** One answer comes from **one document**: the most relevant one, preferring the most
  specific scope, and it is the only one cited. The answer contains only what that document says
  directly or what logically follows from it («6 жолудів більше за потрібні 5, тож достатньо»).
  Documents are never mixed. If no document answers, the bot says so.
- **Confirmed.** Questions that ask for everything («покажи всі поради»), span or compare several
  places («де найменший внесок?»), or depend on a place that isn't named get a short request to ask
  one specific question and name the place.
- **Confirmed.** The bot speaks as a forest character, **Лісовий Порадник**, who keeps the animals'
  advice. Its messages never mention documents, files or being a bot. «Рукавичка» is used freely
  (see above). The citation line keeps the confirmed format.
- **Confirmed.** If the LLM is unavailable: «Ох, щось я задумався й загубив думку… Спитай мене ще раз
  за хвилинку». The error is logged.
- **Confirmed.** Times in citations are shown in Kyiv time (Europe/Kyiv). The client left the timezone
  to me; it's configurable with the `TIMEZONE` environment variable.

## Open questions

None: every item above is confirmed by the client.
