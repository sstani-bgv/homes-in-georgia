# NDA для новых агентов: электронная подпись

Соглашение о конфиденциальности и неконкуренции (NDA) из файлов `NDA_Homes_in_Georgia_KA / KA-RU / KA-EN.pdf`
подписывается в CRM без бумаги. Идея та же, что в HiG Sign (PlatformDGE/hig-sign), только внутри CRM.

## Как работает

1. **Подпись компании — один раз.** Суперадмин (Давид) открывает «NDA агентов» (`/ndas`) → «Расписаться за компанию»
   и рисует подпись. Она хранится в `private/nda/company-signature.json` (хранилище CRM) и ставится в каждое новое NDA
   вместе со временем его создания.
2. **Сотрудник создаёт NDA** (`/ndas` → «Новое NDA»): ФИО и личный номер / паспорт агента (обязательно), телефон,
   Telegram (@username), роль, язык: KA, KA-RU, KA-EN. Текст собирается из `backend/app/services/nda_text.py`,
   данные агента подставляются, HTML замораживается (`doc_sha256`). CRM выдаёт личную ссылку `/nda/<токен>`.
3. **Агент открывает ссылку** (без входа в CRM), читает соглашение и:
   - **подтверждает себя через Telegram** — Telegram Login: Telegram просит подтверждение в приложении, ответ
     (id, username, auth_date, hash) сервер проверяет HMAC ключом бота (`LOGIN_BOT_TOKEN`, иначе `BOT_TOKEN`). Если в
     NDA указан @username — пройдёт только этот аккаунт. Подтверждение действует 1 час;
   - рисует подпись пальцем, ставит галочку согласия, «Подписать».
4. **Сервер печатает подписанный PDF** (headless Chrome, как договоры): обе подписи и время в таблице «Подписи
   сторон» + последняя страница — протокол: ФИО, личный номер, телефон, Telegram (id, @username, имя), код
   подтверждения (подписанный hash ответа Telegram — «код подтверждения» из п. 5 NDA), время подтверждения и подписи
   (Тбилиси), IP, устройство, SHA-256 текста; со стороны компании — подписант, время, кто отправил.
5. **PDF уходит**: сотруднику, создавшему NDA (Telegram, основной бот), агенту (Telegram, бот входа — если агент
   разрешил ему писать при подтверждении) и в Google Drive (папка договоров, если настроена). Агент может скачать его по
   той же ссылке. Если печать не удалась — подпись сохранена, PDF допечатает воркер (задача `nda_pdf`).

Отменить можно неподписанное NDA («Отменить» — ссылка перестаёт работать).

## Тестовый сервер

При `TEST_MODE` (флаг тестового стенда, если он задан в настройках) на странице агента есть кнопка «Подтвердить (тестовый режим)» вместо Telegram (на тестовом сервере
Telegram отключён). В протоколе такая проверка помечена `test-mode-…`. В рабочей CRM этой кнопки нет: сервер
отклоняет `{test: true}` без `TEST_MODE`.

## Файлы

| Что | Где |
|---|---|
| Текст NDA (ka / ru / en, жирный `**…**`, поля `{date} {name} {pid} {phone} {telegram} {role}`) | `backend/app/services/nda_text.py` |
| Логика: текст → HTML, подпись компании, создание, Telegram, подпись, PDF, рассылка, Drive | `backend/app/services/nda.py` |
| API | `backend/app/api/nda.py` (подключён в `app/api/__init__.py`) |
| Таблица `agent_ndas` | `backend/app/models/nda.py`, миграция `backend/alembic/versions/0017_agent_ndas.py` |
| Задачи воркера `nda_pdf`, `nda_drive` | `backend/app/worker.py` |
| Экран сотрудников `/ndas` (меню «NDA агентов», только STAFF) | `frontend/src/pages/Ndas.tsx` |
| Страница агента `/nda/:token` | `frontend/src/pages/NdaSign.tsx` |
| Холст подписи (общий с договорами и эксклюзивом) | `frontend/src/components/SignaturePad.tsx` |
| Тесты | `backend/tests/test_nda.py` |

## API

Сотрудники (STAFF; подпись компании меняет только SUPER):

- `GET /api/ndas?status=sent|signed|cancelled|all` — список
- `POST /api/ndas` `{lang, full_name, personal_id, phone, telegram, role}` → NDA с `path` (`/nda/<токен>`)
- `GET /api/ndas/{id}`, `POST /api/ndas/{id}/cancel`, `GET /api/ndas/{id}/pdf`
- `GET|PUT /api/ndas/company-signature` `{signature: "data:image/png;base64,…"}`

Агент (без входа, доступ по токену):

- `GET /api/public/nda/{token}` — текст и состояние
- `POST /api/public/nda/{token}/telegram` — ответ Telegram Login (`id, first_name, last_name, username, photo_url, auth_date, hash`)
- `POST /api/public/nda/{token}` `{signature, consent: true, doc_sha256}` — подпись
- `GET /api/public/nda/{token}/pdf` — подписанный PDF

## Что нужно для выкладки

- Миграция `0017`: `alembic upgrade head` — делает api при старте.
- Telegram Login уже работает для входа в CRM (бот входа `LOGIN_BOT_USERNAME`, домен задан в @BotFather `/setdomain`).
  Страница `/nda/…` на том же домене, ничего настраивать не нужно.
- Шрифты для PDF — те же, что у договоров (`deploy/Dockerfile`: `fonts-noto-core` для грузинского).

## Изменить текст

Правка в `nda_text.py` (все три языка в одном блоке) → новые NDA получают новый текст; уже отправленные и подписанные
хранят свой (`agent_ndas.html`, `doc_sha256`). В исходном PDF была пометка «Черновик для проверки юристом» — в
подписываемый документ она не входит; перед боевым запуском текст стоит показать юристу.
