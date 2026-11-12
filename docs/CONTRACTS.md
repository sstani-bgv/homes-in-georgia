# Договоры: как устроен генератор и как добавить свой шаблон

CRM (Договоры, «+ Добавить → Договор») заполняет шаблоны Давида 2024 года данными сделки и печатает их как в
Google Docs: шапка «RENT IN TBILISI» с флагом и номер страницы сверху, строка инициалов внизу каждой страницы, бледный
круглый логотип (водяной знак) на каждой странице. Двуязычные шаблоны печатаются в две колонки. Перед печатью текст
можно поправить прямо в предпросмотре: правка уходит в печать, в .doc и в PDF (ниже: «Сохранённые договоры»).

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
- `frontend/src/pages/Contracts.tsx` — `/contracts`: список (активные / сделки / «Сделка не состоялась» / все),
  `?id=` договор, `?new=1` (или `?lead_id=` / `?deal_id=`) мастер (тип → язык → данные → проверка → документ),
  предпросмотр, «Сохранить PDF», печать, .doc.
- `frontend/src/features/contracts/ContractCard.tsx` — договор: PDF (открыть / скачать / в Telegram), «Поделиться»
  (файл через системное меню телефона, иначе ссылка), «Договор подписан», «Сделка не состоялась», список.
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

## Сохранённые договоры: PDF, статусы, «Поделиться», Google Drive

Шаг «Документ» мастера → «Сохранить PDF»: HTML документа (с правками из предпросмотра) уходит на сервер
(`POST /api/contracts`), сервер чистит его (только текстовые теги, картинки только data:, без url()/file://) и печатает
headless Chrome (`CHROME_BIN`, в образе — chromium + шрифты Noto Georgian / Montserrat): тот же print CSS, что у
«Печати» — шапка с флагом, номера страниц, строка инициалов, водяной знак. Код: `backend/app/services/contracts.py`,
API `backend/app/api/contracts.py`, таблица `contracts` (миграция 0007).

### Договор в сделке: черновик, автосохранение, перегенерация (миграция 0012)

Договор всегда привязан к сделке: к заявке (`lead_id`, «сделка» на этапе «Договор», запись `deals` появляется только
после подписи) или к сделке (`deal_id`); без них `POST /contracts`, `PUT /contracts/{id}` и автосохранение отвечают
400 `no_deal`. В мастере шаг «Тип» не открывается, пока не выбрана заявка.

- **Автосохранение.** Мастер с первого изменённого поля (или подстановки из заявки) раз в 0,8 с шлёт
  `POST /contracts/draft` `{id?, lead_id | deal_id, kind?, lang?, data}`: без `id` берётся открытый черновик этой
  заявки / сделки (без PDF) или создаётся новый; `data` — поля формы (`CxData`) плюс шаг и флаги мастера
  (`_step`, `_dep`, `_end`). `kind`, `lang`, `html` у такого черновика могут быть пустыми. URL мастера получает `?edit=<id>`.
- **Восстановление.** `/contracts?lead_id=` (кнопка в заявке) находит открытый черновик заявки и открывает его
  (`?edit=`) на том же шаге; `/contracts?edit=<id>` открывает любой черновик или сгенерированный договор в той же форме.
- **Генерация / перегенерация.** «Сохранить PDF» = `PUT /contracts/{id}` (или `POST /contracts`, если черновика ещё нет).
  Повторно после правки — «Обновить PDF»: новый PDF, прошлый остаётся в `versions` (`GET /contracts/{id}/pdf?version=N`,
  N с 0) и на странице договора («Прошлые версии PDF»). Ссылка «Поделиться» отдаёт текущий PDF.
- **PDF устарел.** Правка данных сгенерированного договора автосохраняется и ставит `pdf_stale` (данные новее
  `generated_at`); подписать такой договор нельзя (409 `pdf_stale`), пока PDF не обновлён. Смена одного шага мастера
  правкой не считается.
- **Подписанный / закрытый** договор форма не меняет (409); новый автосейв по той же заявке начнёт новый черновик, а
  договором заявки (`for_lead`, проверка сделки) остаётся подписанный.
- **Список.** Кабинет → «Договоры» (и «Мои договоры» в мобильном кабинете), `/contracts`: статус («Черновик» —
  заполняется, «Сгенерирован», «Договор подписан», …), дата последнего изменения, ссылка на заявку / сделку, черновик
  открывается сразу в форме, сгенерированный — на своей странице или «Изменить». Сотрудник смотрит кабинет агента:
  `GET /contracts?agent_id=`.

Статусы (`status`, подпись `status_label`):

| status | Что значит | Как попадает |
|---|---|---|
| `draft` | «Черновик» (заполняется, без PDF) или «Сгенерирован» (PDF есть, можно править и перегенерировать) | `POST /contracts/draft`, `POST /contracts` |
| `signed` | Договор подписан | `POST /contracts/{id}/sign` (multipart, `file` — скан PDF, необязателен) |
| `deal` | Сделка | оператор подтвердил сделку заявки (`deals.confirm` → `contracts.mark_deal`) |
| `closed_no_deal` | Сделка не состоялась | `POST /contracts/{id}/close` `{reason, comment}` |

`reason`: `deal_failed` (сделка сорвалась), `client_changed_mind` (клиент передумал), `owner_refused` (собственник
отказался), `other` (другое — `comment` обязателен). Закрыть можно `draft` / `signed`. Закрытый пропадает из активных
списков (`GET /contracts` = `status=active` = draft + signed), но остаётся в истории: `?status=closed_no_deal` / `all`
с причиной, датой и кто закрыл (`closed_by`). Агент видит свои договоры (создал или его заявка), операторы — все.

Заявка: `GET /leads/{id}` и `GET /leads/{id}/contract` отдают `contract: {id, status, status_label, signed_at,
has_signed_pdf, close_reason, close_reason_label, closed_at}` (живой договор заявки, иначе последний закрытый) или null.

PDF: `GET /contracts/{id}/pdf` (inline, `?download=1` — файлом, `?signed=1` — подписанный скан, если есть).
Mini App: `POST /contracts/{id}/send` — бот присылает PDF в чат. «Поделиться»: `POST /contracts/{id}/share` →
`share.url` — ссылка без логина `/api/public/contracts/<токен>/pdf`, токен случайный (не перебирается), только чтение,
живёт `CONTRACT_SHARE_DAYS` (7) дней, у закрытого договора не работает.

Google Drive: «Договор подписан» ставит задачу воркера `contract_drive`: PDF (скан, иначе сгенерированный) грузится в
папку `GDRIVE_CONTRACTS_FOLDER` под именем «<кадастровый код> <дата> <клиент>.pdf», при ошибке — повторы с
нарастающей паузой (5 попыток). Без ключа (`GOOGLE_SA_JSON`) задача пропускается со строкой в логе
`drive: not configured`. Настройка:

1. Google Cloud Console → проект → включить Google Drive API → IAM → Service accounts → создать аккаунт → Keys →
   Add key → JSON, скачать.
2. Положить JSON на сервер в том `media` (например `/app/data/google-sa.json`, права 600) и в `.env`:
   `GOOGLE_SA_JSON=/app/data/google-sa.json` (папка по умолчанию `GDRIVE_CONTRACTS_FOLDER=1DDD3V7EzEIPZHuTgNXV0nKzc3K3wwLFy`).
3. Папку на Drive «Поделиться» с email сервисного аккаунта (`…@….iam.gserviceaccount.com`) как Редактор.
4. У сервисного аккаунта нет своего места на Drive: в папку обычного «Мой диск» Google не даёт ему загрузить файл
   (403 storageQuotaExceeded). Папка должна лежать на общем диске (Shared drive, Google Workspace) с аккаунтом в
   участниках, либо в Workspace включить делегирование домена и указать `GOOGLE_SA_SUBJECT=<email владельца папки>`.
5. Пересобрать образ (chromium в Dockerfile) и перезапустить api и worker.
