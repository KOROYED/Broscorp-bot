# Manual end-to-end test

Start from a clean database (`docker compose down -v && docker compose up --build`), open the bot in
Telegram and send the files from [documents/](documents/) **in order**. Then ask the questions.

## 1. Uploads

| # | File | Format | What it tests | Expected bot behaviour |
|---|---|---|---|---|
| 01 | Членські внески рукавички Під дубом | txt | first upload; replacing an archive document | asks «Як тебе звати?»; after your name, saves for «Під дубом» and says the older version was replaced |
| 02 | Осінній ярмарок | docx | whole-forest document | saved for «увесь ліс» |
| 03 | Шишки взимку | pdf | area from content («під ялинами та соснами») | saved for «хвойний ліс» |
| 04 | Збори мешканців | txt | place unclear: never guess | shows place buttons → choose **У ліщині** |
| 05 | Нічна варта Під вербою | docx | place from title | saved for «Під вербою» (Їжак guards) |
| 06 | Правила риболовлі | pdf | area: river | saved for «біля річки» |
| 07 | Тиша в хвойному лісі | txt | general (area) advice | saved for «хвойний ліс» |
| 08 | Тиша в рукавичці У соснах | docx | specific advice next to general | saved for «У соснах» |
| 09 | Нічна варта Під вербою | txt | newer version of #05 | saved; says the older version was replaced (now Видра guards) |
| 10 | Поради про мед у ліщині | txt | prompt injection inside a document | **rejected**: «У документі є вказівки для бота…» |

Also try: a photo, a voice message, any `.doc`/`.xlsx` file, an empty `.txt`, and sending #02 a
second time. Each one should get a short explanation; #02 sent again → «Такий документ уже є».

## 2. Questions

Ask them in this order (2 is a follow-up to 1, 8 is a follow-up to 7).

| # | Question | Expected answer | Expected citation |
|---|---|---|---|
| 1 | Скільки внесок під дубом і до якого числа? | 5 жолудів (or 3 горіхи), до 15 числа: the **new** version, not the archive's 4 жолуді / 10 число | «Членські внески» рукавички «Під дубом», надіслав *you*, *date/time* |
| 2 | А хто не платить? | пташенята й поросята до року (follow-up keeps «Під дубом») | same as 1 |
| 3 | Коли буде осінній ярмарок? | друга неділя листопада, на великій галявині | «Осінній ярмарок», надіслав *you* |
| 4 | Де взимку шукати шишки біля мурашника? | на південних схилах (coniferous advice applies to «Біля мурашника») | «Шишки взимку» / «Де взимку шукати шишки», надіслав *you* |
| 5 | Коли збори у ліщині? | щочетверга на заході сонця біля входу (+ maybe the archive Статут's monthly meeting) | «Збори мешканців» рукавички «У ліщині», надіслав *you* |
| 6 | Хто вартує вночі під вербою? | **Видра** (#09), not Їжак (#05 was replaced) | «Нічна варта» рукавички «Під вербою», *time of #09* |
| 7 | Чи можна рибалити в п'ятницю біля бобрової греблі? | ні: бобри відкривають греблю (river advice applies to the dam) | «Правила риболовлі» (біля річки) |
| 8 | А на узліссі можна рибалити? | **no information**: «На узліссі» is not by the river | none |
| 9 | Коли тиша у соснах? | від світанку до обіду (#08) first; may add the archive «Правила співжиття» rule; general coniferous advice only as secondary | «Тиша в рукавичці» рукавички «У соснах» |
| 10 | Скільки внесок у ліщині? | the archive's value from the **scanned** PDF (OCR), never "100 жолудів" | «Членські внески» рукавички «У ліщині» |

Answers are written by an LLM, so the wording will differ; the facts and citations should match.
Citation times are in Kyiv time.
