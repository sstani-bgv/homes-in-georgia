# Договоры: как устроен генератор и как добавить свой шаблон

CRM (Договоры, «+ Добавить → Договор») заполняет шаблоны Давида 2024 года данными сделки и печатает их как в
Google Docs: шапка «RENT IN TBILISI» с флагом и номер страницы сверху, строка инициалов внизу каждой страницы, бледный
круглый логотип (водяной знак) на каждой странице. Двуязычные шаблоны печатаются в две колонки. Перед печатью текст
можно поправить прямо в предпросмотре: правка уходит в печать и в скачиваемый .doc. Внешних сервисов нет.

Сейчас включена только аренда, 6 вариантов (`ENABLED_TYPES` в `templates.ts`):

| Вариант | Ключ | Google Doc |
|---|---|---|
| Аренда, грузинский | `rent_GEO` | DRAFT GEO 2024 |
| Аренда, грузинский, с животными | `pet_GEO` | Pet DRAFT GEO 2024 |
| Аренда, грузинский + русский | `rent_RU` | DRAFT (GEO+RUS) 2024 |
| Аренда, грузинский + русский, с животными | `pet_RU` | Pet DRAFT (GEO+RUS) 2024 |
| Аренда, грузинский + английский | `rent_EN` | DRAFT (GEO+ENG) 2024 |
| Аренда, грузинский + английский, с животными | `pet_EN` | Pet DRAFT (GEO+ENG) 2024 |

Шаблоны коммерции (`comm_GEO`, `comm_RU`) и продажи (`sale_GEO`) уже лежат в `templates-david.ts`, но в мастере
выключены: их доделывает Иракли. Папка драфтов на Drive: https://drive.google.com/drive/folders/1ofDKd0KnfJSCYmW94KdsP1GIaqQzFDxV
(сами документы там не меняем).

## Файлы

- `frontend/src/features/contracts/templates-david.ts` — СГЕНЕРИРОВАН, руками не править: тексты всех шаблонов.
  Абзац — строка (`**жирный**`), `{p, a: 'c'|'r'}` — по центру / справа, `{t: [[ячейки]]}` — таблица,
  `{s, c}` — ячейка с colspan. Поля в тексте: `[[field]]` или `{{field}}`.
- `frontend/src/features/contracts/templates.ts` — поля формы (`CxData`), типы (`CX_TYPES`), включённые типы
  (`ENABLED_TYPES`), языки типа (`langsFor`), выбор шаблона (`draftKey`), подстановка полей (`buildContract`:
  даты dd.mm.yyyy, `one_month` = конец первого оплаченного периода, остальное как введено).
- `frontend/src/pages/Contracts.tsx` — мастер (тип → язык → данные → проверка → документ), предпросмотр, печать, .doc.
- `frontend/scripts/contract-drafts/gen.py` + `parse.py` — конвертер Google Docs → `templates-david.ts`.
  `FIXES` в gen.py — наши исправления опечаток оригиналов, применяются при каждой генерации.
- `frontend/public/brand/contract-watermark.png`, `ge-flag.svg` — водяной знак и флаг шапки.

## Изменить текст существующего шаблона

1. Поправить Google Doc.
2. Скачать все шаблоны как HTML (Файл → Скачать → Веб-страница, или
   `https://docs.google.com/document/d/<id>/export?format=html`) в одну папку под именами ключей: `rent_GEO.html`,
   `pet_RU.html`, ... (id — в `DOCS` в gen.py и в комментариях `templates-david.ts`).
3. `cd frontend/scripts/contract-drafts && python3 gen.py <папка> ../../src/features/contracts/templates-david.ts`
4. `cd frontend && npx tsc -b` и проверить вариант в CRM (Договоры).

## Добавить / включить тип (коммерция, продажа, новый)

1. Шаблон — Google Doc с полями `[[field]]` из `CxData` (список полей и подписи — `templates.ts`). Новое поле: добавить
   в `CxData`, `EMPTY`, форму шага 3 в `Contracts.tsx` и в `reviewLabels`.
2. В gen.py добавить строку в `DOCS`: `('<key>', '<название>', '<id документа>')`; ключ вида `<тип>_<язык>`
   (`comm_EN`, `sale_RU`...). Добавить ключ в `DraftKey` и `DRAFT_NAMES` в `templates.ts`.
3. Новый тип: добавить его в `CxType` и `CX_TYPES` (название, подпись), иконку в `TYPE_ICON` (`Contracts.tsx`),
   обязательные поля в `requiredFields`.
4. Языки типа — `langsFor`, выбор шаблона — `draftKey`.
5. Включить тип: добавить его в `ENABLED_TYPES`.
6. Сгенерировать `templates-david.ts` (выше), `npx tsc -b`, проверить в CRM печать (Ctrl+P → PDF): шапка, номера
   страниц, инициалы внизу, водяной знак.
