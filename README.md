<p align="center">
  <img src="docs/banner.svg" alt="Healdragg — карта публичных чатов Telegram" width="100%">
</p>

<p align="center">
  <a href="https://github.com/syslikoffnet/s/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/syslikoffnet/s/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/syslikoffnet/s/actions/workflows/android.yml"><img alt="Android APK" src="https://github.com/syslikoffnet/s/actions/workflows/android.yml/badge.svg"></a>
  <a href="https://github.com/syslikoffnet/s/releases/latest"><img alt="Download APK" src="https://img.shields.io/badge/download-APK-3DDC84?logo=android&logoColor=white"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="Termux" src="https://img.shields.io/badge/Termux-ready-000000?logo=android&logoColor=3DDC84">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-bd93f9">
</p>

<h1 align="center">🩺 Healdragg</h1>
<p align="center"><b>Свой каталог публичных чатов Telegram — база, бот, терминал, веб и Android.</b></p>

---

## Что это

Healdragg собирает **собственную базу публичных групп и чатов** Telegram и отвечает на вопрос
«в каких открытых чатах состоит этот аккаунт» — с датой последнего сообщения, размером чата
и статусом (`админ` / `приватный` / `вышел`).

Ты присылаешь боту **id или @username**, жмёшь **👁 Группы** — и получаешь аккуратный список
с постраничной навигацией:

<p align="center">
  <img src="docs/bot.svg" alt="Сообщение бота со списком чатов" width="86%">
</p>

```
ㅤЧаты аккаунта 7126917488.

👮🏻‍♂️-админ, 🔒-приватный, ✖️-вышел

Посл. сοобщ - чат (всего сообщений)

 09 нояб. дд (301)
 🔒10 дek. Аkвариум кктека (2750)
 🔒24 нояб. NEFERPITU SeRv.. (1627)
 24 нояб. ꜰʟᴏᴡ ᴄʜᴀᴛ (485)
 04 нояб. EN/RU|POCO X6 PR.. (47)
 14 сент. Чaиковскии ☕ (1882)
 08 сент. Чат общения (5964)
 04 сент. 🤡 TROLLING|.. (16000)
 ✖️29 авг. Стак#эгоистов🥷 (2)
 25 авг. Соники💙 (7)
 25 авг. BFG | Общая бесе.. (12)
 ✖️25 авг. тролляки Flow ч.. (202)
 🔒24 авг. Чат пубг метро (5414)
 🔒24 авг. чат ℮бланов (12)
 24 авг. Ищю Парня Девуш.. (490)

Всего 115, cтρаница 1 из 8
```

Под сообщением — кнопки `👁 Группы`, `🔄 Обновить`, `📊 Профиль`, `🏠 Меню`
и листалка `1 ⬅ · 2/8 · ➡ 3` с номерами страниц.

<p align="center">
  <img src="docs/preview.svg" alt="Живая панель Healdragg в терминале" width="100%">
</p>

<p align="center">
  <img src="docs/groups.svg" alt="Полноэкранный список чатов" width="100%">
</p>

## Возможности

| | |
|---|---|
| 🗂 **Своя база** | Нормализованный SQLite-каталог групп: дедуп по `tg_id`, FTS-поиск, WAL, сжатие, экспорт/импорт. Сообщения **не** сохраняются — только метаданные. |
| 🔑 **Много сессий** | 1+ своих аккаунтов-сканеров. Каждая сессия хранит свои `access_hash`, запросы троттлятся, `FloodWait` уважается. |
| 🎯 **Цель по id / @username** | Сначала ищется «своя» сессия цели (точный список диалогов), иначе — проверка участия по каталогу + глобальный поиск сообщений. После добавления цели бот сразу запускает проверку. |
| 🤖 **@healdragg_bot** | Кнопки, пагинация, прогресс скана с отменой, allowlist владельца, антифлуд. |
| 🖥 **TUI** | Живая панель: сессии, размер базы, цель, журнал, полноэкранный список, управление клавишами. |
| 🌐 **Веб-панель + REST** | Тёмный адаптивный интерфейс, токен-доступ, тот же список и пагинация. |
| 📱 **Android APK** | Собирается в GitHub Actions (`.github/workflows/android.yml`) — WebView-клиент панели, настройка сервера и токена. |
| 📟 **Termux** | `install.sh` + `termux/termuxctl.sh`: запуск в фоне, wake-lock, автозапуск, логи. |
| 🧪 **Тесты** | 58 тестов: формат сообщений, БД, пагинация, API, клавиатуры. |

## Установка

### Termux (Android)

```bash
pkg install git
git clone https://github.com/syslikoffnet/s.git
cd s
bash install.sh
nano .env            # API_ID, API_HASH, BOT_TOKEN
```

Установщик сам поставит `python`, `rust`/`clang` (если понадобится сборка `pydantic-core`),
создаст `.venv`, поставит зависимости, сделает команду `healdragg` и папку `data/`.

Дальше:

```bash
healdragg demo            # посмотреть, как всё выглядит, без Telegram
healdragg session add main
healdragg bootstrap       # собрать базу публичных чатов
healdragg bot             # бот
```

Фоновый режим и автозапуск:

```bash
bash termux/termuxctl.sh start all     # бот + панель
bash termux/termuxctl.sh status
bash termux/termuxctl.sh logs bot
bash termux/termuxctl.sh autostart     # после перезагрузки (нужен termux-boot)
```

### Linux / macOS

```bash
git clone https://github.com/syslikoffnet/s.git && cd s
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env && nano .env
healdragg init
```

### Где взять ключи

1. **API_ID / API_HASH** — <https://my.telegram.org> → *API development tools*.
2. **BOT_TOKEN** — у [@BotFather](https://t.me/BotFather) (`/newbot`), имя `@healdragg_bot`.
3. **Свой id** — команда `/id` в боте; впиши в `BOT_ALLOWED_USERS`.

> Бот по умолчанию **закрыт**: если allowlist пуст, владельцем становится тот, кто первым
> отправит `/start`. Остальные получат отказ. Это защищает чужие запросы к твоей базе.

## Быстрый старт

```bash
healdragg init                  # .env + база + токен панели
healdragg demo                  # демо-данные (115 чатов) — посмотреть формат
healdragg session add main      # вход в свой аккаунт (код из Telegram)
healdragg bootstrap             # диалоги + поиск публичных чатов по словам
healdragg target add 7126917488
healdragg scan 7126917488       # проверить участие цели по каталогу
healdragg cover 7126917488      # с нуля: база + вступления + проверка + список
healdragg groups 7126917488 -i  # листать стрелками
healdragg bot                   # @healdragg_bot
healdragg tui                   # живая панель
healdragg api                   # веб-панель для Android
```

## Как наполняется база

```
bootstrap
 ├─ диалоги всех сессий       → публичные и приватные чаты, где состоишь ты
 ├─ contacts.search по словам → новые публичные чаты (без вступления)
 └─ --join (опционально)      → вступление в найденные чаты (лимит/сутки, пауза)
```

Когда база пуста — это нормально. Порядок: `session add` → `bootstrap` → `target add` → `scan`.
В боте для этого есть кнопка **🚀 Собрать базу** и онбординг-подсказки.

Если хочется одной командой «найти всё про человека с нуля»:

```bash
healdragg cover 7126917488 --join-limit 15
```

Она собирает базу, вступает в найденные публичные чаты в пределах дневного лимита,
проверяет цель и сразу печатает список. В боте то же поведение включается
`JOIN_ON_BOOTSTRAP=true`.

### Как ищется цель

1. **Своя сессия цели** — если добавил сессию самого аккаунта, список берётся из её диалогов:
   это самый точный и быстрый путь (даты и счётчики сообщений).
2. **Проверка по каталогу** — `channels.getParticipant` по чатам, где состоят твои сессии.
   Статус `админ/создатель/участник`, а `✖️ вышел` — если цель была найдена раньше и пропала.
3. **Глобальный поиск** — `messages.search` с `from_id` (best-effort: сервер может запретить).
   Найденные чаты добираются в каталог.

Числовой id без `access_hash` иногда не резолвится сразу — хеш подтянется, как только цель
встретится в общих публичных чатах. Если есть `@username` — резолв мгновенный.

## Бот

| Кнопка / команда | Что делает |
|---|---|
| `👁 Группы` | Список чатов цели, 15 на страницу, кнопки `1 ⬅ · 1/8 · ➡ 2` |
| `🎯 Цели` | Все добавленные цели, переключение одним нажатием |
| `🔄 Обновить` | Запускает скан цели с прогрессом и кнопкой отмены |
| `📊 Профиль` | Счётчики: всего, админ, приватные, вышел |
| `🚀 Собрать базу` | `bootstrap` прямо из чата |
| `/start`, `/help` | Меню и справка |
| `/target <id\|@user>` | Карточка цели |
| `/targets` | Список целей |
| `/scan [id]` | Перепроверить цель (по умолчанию последнюю) |
| `/find <слово>` | Поиск по каталогу (FTS5) |
| `/stats` | Размер базы, покрытие, цели |
| `/id` | Твой Telegram id |
| `/stop` | Остановить сканы |

## CLI

```
healdragg init                     .env, каталоги, база
healdragg demo [--clear]           демо-данные
healdragg session add|list|remove|import
healdragg bootstrap [--join] [--queries ...] [--no-meta]
healdragg target add|list|remove
healdragg scan <query> [--deep] [--max-groups N]
healdragg cover <query> [--no-join] [--join-limit N] [--queries ...]
healdragg groups <query> [-i] [--page N] [--size N] [--plain] [--json] [--html]
healdragg catalog [--query Q] [--page N]
healdragg stats | doctor
healdragg db optimize|vacuum|check|stats|export|import
healdragg bot | api | tui
```

### TUI

`←/→` — страницы, `↑/↓` — цель, `g` — полноэкранный список, `b` — собрать базу,
`r` — проверить цель, `d` — демо, `q` — выход.

## Веб-панель и API

```bash
healdragg api --host 0.0.0.0 --port 8080 --show-token
```

Открой `http://<ip>:8080`, введи `API_TOKEN`. Токен можно передать сразу:
`http://<ip>:8080/?token=...`.

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/api/health` | живость (без токена) |
| `GET` | `/api/stats` | база, покрытие, размер |
| `GET` | `/api/sessions` | сессии |
| `GET`/`POST` | `/api/targets` | список / добавить цель |
| `GET` | `/api/targets/{id}/groups?page=&size=` | чаты + готовый текст |
| `POST` | `/api/targets/{id}/scan` | скан в фоне |
| `POST` | `/api/bootstrap` | сбор базы |
| `GET` | `/api/jobs` | прогресс задач |
| `GET` | `/api/catalog?q=&page=` | каталог |
| `GET` | `/api/find?q=` | поиск |
| `GET` | `/api/export/{id}` | JSON-экспорт |

Заголовок `X-Api-Token` или `?token=`. Документация Swagger — `/api/docs`.

## Android APK

APK собирается автоматически в **GitHub Actions** → workflow **Android APK** и лежит в
[**Releases**](https://github.com/syslikoffnet/s/releases/latest):

1. Скачай `healdragg-debug.apk` (или `healdragg-release.apk`).
2. Поставь на телефон (разреши установку из неизвестных источников).
3. Введи адрес панели и `API_TOKEN`.

Артефакты сборки также доступны во вкладке **Actions** → **Android APK**.

Локально:

```bash
cd android && gradle assembleDebug     # нужен Android SDK + JDK 17
```

Приложение — тонкий клиент панели: при первом запуске вводишь адрес сервера
(`http://192.168.1.10:8080`) и `API_TOKEN`. Дальше — обычный интерфейс панели,
pull-to-refresh, кнопка ⚙ для смены сервера. HTTP по локальной сети разрешён
(`network_security_config.xml`), для внешнего доступа ставь панель за HTTPS-прокси.

## Хранение: как оптимизировано

```
sessions ──┐
           ├── session_groups ── groups ── target_groups ── targets
scans      │                     │
events     └─────────────────────┴── groups_fts (FTS5)
```

* **Одна запись на чат** (`groups.tg_id UNIQUE`) — названия и счётчики не дублируются между целями.
* **Связь «цель × чат»** — компактная строка `(target_id, group_id, status, source, даты)`.
* **Целые timestamp'ы**, никаких строковых дат и JSON-полей в горячих таблицах.
* **WAL + NORMAL + mmap + auto_vacuum + checkpoint**, `db optimize` / `db vacuum`.
* **FTS5** по названию и username (триггеры держат индекс в актуальном состоянии).
* **Сообщения не сохраняются.** Только метаданные чатов.
* **Дневные счётчики** вступлений — в `meta`, чтобы лимит не сбрасывался при перезапуске.
* **«Вышел»** — если чат был в диалогах цели, а теперь пропал, связь помечается `left`, а не удаляется.
* **Basic-группы** проверяются через `messages.getFullChat`, а не `channels.getParticipant`.
* Ориентир: **10 000 групп + связи ≈ 5,5 МБ**, заполнение ~2,5 секунды (проверено в CI-окружении).
* `groups.meta_checked_at` + `META_TTL_HOURS` — не дёргаем Telegram лишний раз.

## Конфигурация

Всё в `.env` (шаблон — `.env.example`):

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `API_ID`, `API_HASH` | — | ключи my.telegram.org |
| `BOT_TOKEN` | — | токен @BotFather |
| `BOT_ALLOWED_USERS` | — | id через запятую |
| `BOT_ALLOW_ALL` | `false` | открыть бота всем (не рекомендуется) |
| `API_TOKEN` | генерируется | доступ к панели/API |
| `API_HOST`, `API_PORT` | `0.0.0.0`, `8080` | панель |
| `SCAN_MIN_DELAY`, `SCAN_JITTER` | `1.4`, `0.6` | пауза между запросами |
| `SCAN_MAX_GROUPS` | `2000` | лимит проверок за прогон |
| `JOIN_LIMIT` | `20` | вступлений за сутки **на сессию** (0 — не вступать) |
| `AUTO_SCAN_ON_ADD` | `true` | сразу проверять цель после добавления в боте |
| `JOIN_ON_BOOTSTRAP` | `false` | вступать в найденные чаты при сборе базы из бота/панели |
| `META_TTL_HOURS` | `12` | как часто обновлять счётчики чата |
| `PAGE_SIZE`, `TITLE_LIMIT` | `15`, `15` | вид списка |
| `TZ` | `Europe/Moscow` | часовой пояс дат |
| `SEARCH_QUERIES` | встроенные | слова для поиска чатов |

## Ограничения и честные предупреждения

* Видны **только открытые чаты** и только те, где состоят твои сессии. Приватные чаты цели
  можно увидеть лишь при наличии её собственной сессии.
* Telegram может ответить `FloodWait` или ограничить аккаунт за агрессивный скрейпинг.
  Скрипт спит ровно столько, сколько просят, но **риск бана сессии остаётся** — не гоняй
  тысячи проверок в минуту и не используй основной аккаунт.
* Даты и счётчики кешируются (`META_TTL_HOURS`), поэтому не «живые» до следующего обновления.
* Числовой id без `access_hash` может не резолвиться — добавь `@username` или общие чаты.
* Сессии — это твои аккаунты. Не передавай `.session` файлы никому: это полный доступ к аккаунту.

## Этика и право

Инструмент работает с **публичными** данными: названия открытых чатов, факт участия,
дата последнего сообщения и счётчик сообщений. Он **не читает переписку**, не сохраняет
контент и не обходит приватность закрытых чатов.

* Используй только свои аккаунты-сессии.
* Соблюдай [Terms of Service Telegram](https://telegram.org/tos) и законы своей страны.
* Не используй для травли, слежки за людьми без согласия и любых противоправных действий.
* Ответственность за использование — на операторе.

## Структура

```
healdragg/
  config.py     настройки (.env)
  db.py         SQLite: схема, запросы, экспорт/импорт
  tg.py         пул сессий, троттлинг, resolve, getParticipant
  scan.py       bootstrap / probe / global search, прогресс
  textfmt.py    «телеграмный» формат, профили, статистика
  bot/          aiogram: handlers, keyboards, middlewares, deps
  tui.py        живая панель (rich)
  cli.py        команды
  api.py        REST + веб-панель
  web/          интерфейс панели
android/        Kotlin/WebView клиент + Gradle
termux/         управление фоновыми процессами
scripts/        запуск и генерация превью
legacy/         исходный syntax.py (сохранён для истории)
docs/           SVG-превью и баннер
```

## Тесты

```bash
pip install -r requirements-dev.txt
pytest -q
ruff check healdragg tests
```

58 тестов покрывают точный формат сообщения, пагинацию, БД (дедуп, каскады, FTS, экспорт),
API с токеном, клавиатуры и лимиты Telegram-сообщений.

## Roadmap

* несколько целей одновременно и очередь сканов;
* сравнение «было/стало» (новые/потерянные чаты);
* SQLite → PostgreSQL для нескольких воркеров;
* нативные уведомления в Android-приложении;
* экспорт в CSV/GraphML для графа пересечений.

---

<p align="center"><sub>Сделано с 🩺 и уважением к публичности. Healdragg не связан с Telegram.</sub></p>
