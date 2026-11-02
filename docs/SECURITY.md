# Безопасность: что было открыто в старых репозиториях и что сделать

Старые репозитории PlatformDGE публичные. В них в открытом виде лежат ключи и доступы. В новой системе всё это
читается только из `.env` на сервере (см. `.env.example`), в коде и во фронтенде секретов нет.

## Что утекло (нужно сменить/закрыть сразу, до запуска новой системы)

| Что | Где | Что сделать |
|---|---|---|
| Токен Telegram-бота `8724734832:AA…` (генератор постов) | `-rentintbilisi-crm/post-generator-modern.html`, `rent-tbilisi/post-generator.html`, `rent-tbilisi/Post Generator · rent_in_tbilisi.html`, `rent_in_tbilisi_90-10/post-generator.html`, `post-generation.html` | @BotFather → `/revoke` для этого бота, новый токен только в `.env` |
| URL-ы Google Apps Script (`script.google.com/macros/s/AKfycb…/exec`) — открытый API к таблицам без авторизации: любой может писать сделки, отчёты, добавлять агентов (`admin_add_agent`), читать базу собственников (`getOwners`) | CRM `index.html`, `rent_in_tbilisi_90-10/miniapp*.html`, `grishashvili/*.html`, `daily_rent_bot/index.html`, `rent-tbilisi/index.html` | после переноса данных: в Apps Script → Deploy → Manage deployments → Archive все деплойменты |
| ID Google-таблицы и GID листов | генератор постов | закрыть доступ «по ссылке» у таблицы, оставить только сервисный аккаунт |
| Пароль админки `grishashvili/admin.html` (в JS) | `grishashvili/admin.html` | считать скомпрометированным |
| Telegram ID суперадминов, ID чатов/каналов/топиков | CRM, Apps Script | не секрет сам по себе, но проверка доступа «по ID на клиенте» не защищает — в новой системе доступ проверяет сервер (initData HMAC + JWT) |
| Пароли `Platform_Desk_Users` хранились в таблице открытым текстом | Google Sheets | при импорте хэшируются bcrypt; пользователям сменить пароли |

## Как устроено в новой системе

- Все токены, ID чатов, пароли — в `.env` (не в git). `.env.example` — только описания.
- Telegram Mini App: сервер проверяет подпись `initData` токеном бота, выдаёт JWT; роль берётся из базы, а не из клиента.
- Веб-вход: Telegram Login Widget (подпись проверяется на сервере) или логин/пароль (bcrypt).
- Генератор постов публикует через серверный прокси `/api/tools/telegram/*`: токен бота в браузер не попадает,
  разрешены только нужные методы и чаты из `.env`, нужна авторизация.
- Телефоны собственников видит только агент объекта и операторы; контакты клиента агент видит только после выбора
  времени просмотра (журнал, шаги C2a/C8).
- PostgreSQL не открыт наружу, доступ только из сети docker-compose.
