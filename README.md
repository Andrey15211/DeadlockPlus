# Deadlock Plus

> Переименован из DeadlockForge.

Автономное desktop-приложение для Valve Deadlock (карта The Cursed Apple):
полный ростер героев и предметов с оригинальными ассетами Valve, тир-лист,
пик-ассистент 6v6 с детектором Red Flags, лаборатория билдов на 16 слотов
с живым пересчётом статов и SQLite-персистентностью, разбор матчей
(DEEP MATCH) с AI-тренером на выборке LLM-провайдеров.

## Запуск (dev)

```bash
python -m pip install -e .        # fastapi, uvicorn, pydantic, pywebview, vpk, pillow
python run_desktop.py             # окно PyWebView 1440x900 (или браузер-fallback)
python run_server.py              # только сервер: http://127.0.0.1:8377
```

Требования: Windows 10/11 (WebView2 встроен), Python 3.10+.

Рекомендуемое окружение — uv-venv проекта (`.venv`), он же используется
для сборки exe:

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e . pytest pillow numpy httpx2
.venv/Scripts/python.exe -m pytest -q
```

## Плашки поверх игры (Live, выключено по умолчанию)

Комментатор матча работает без LLM: расписание событий карты (`/api/live/schedule`)
раскрыто по секундам, тексты — шаблоны. Часы матча ведутся **вручную**
(`/api/live/clock`): панель `live.html` присылает точку отсчёта, сервер дальше
считает сам. Автоматически определить начало матча нельзя — в Deadlock нет ни
API, ни GSI с игровым временем, только послематчевая история.

Плашки включаются флагом `--overlay` (или `DEADLOCK_OVERLAY=1`) и **только вместе
с ним**: окно не создаётся, драйвер не стартует, `GET /api/live/overlay` отвечает
`supported: false`. Когда включены: окно без рамки, `on_top`,
`WS_EX_TRANSPARENT` (клики уходят в игру), 560×150, появляется на 5 секунд и
скрывается. Нужен режим **Borderless Window** в Deadlock
(`setting.fullscreen 0`, `setting.nowindowborder 1`).

Оценка ценности фичи отрицательная, и это осознанное решение: тайминги карты
детерминированы и запоминаются за пару десятков матчей, момент старта жмётся
руками и ошибка набегает молча, а окно висит в нижней трети экрана там, где идёт
игра. Тот же таймлайн уже показывает панель In-Game Assistant (`--live`) —
постоянно, на втором мониторе и без перекрытия вида. Поэтому код оставлен под
флагом для экспериментов, но не рекламируется тумблером в интерфейсе.

Полная прозрачность окна не используется: WebView2 в этой сборке рисует
`transparent=True` сплошным фоном. Живая проверка —
`.venv/Scripts/python.exe scripts/check_overlay.py` (`--exe` проверяет собранный
`dist/DeadlockForge.exe`); скрипт сначала убеждается, что без флага окна нет.

## Панель по горячей клавише (CTRL+F1)

Глобальный хоткей поднимает главное окно поверх игры, не сворачивая её: окно
получает фокус и `HWND_TOPMOST`, повторное нажатие (или `Esc`) прячет его и
возвращает фокус окну, которое было на переднем плане. Регистрация —
`RegisterHotKey` + `GetMessageW` в отдельном потоке (`src/deadlockforge/desktop/hotkey.py`),
без хуков клавиатуры и без вмешательства в процесс игры.

Комбинация настраивается в профиле: поле **«Хоткей панели»** ловит нажатие и
сохраняет его через `POST /api/desktop/hotkey` в таблицу `app_settings`; там же
видно состояние регистрации. Если комбинацию занял Discord/Steam, API вернёт
409 с текстом ошибки, а прежний хоткей останется рабочим. Нужен **Borderless
Window** в Deadlock — эксклюзивный fullscreen не перекрывается.

Рядом — тумблер **«Поверх других окон»** (`GET/POST /api/desktop/topmost`,
ключ `always_on_top`). Включён (по умолчанию): хоткей зарегистрирован, окно по
нему закрепляется над игрой. Выключен: перехват клавиш снят, закреп снят, окно
закрытого хоткеем приложения возвращает фокус игре — программа ведёт себя как
обычное приложение и ничего не перекрывает. Выбор сохраняется между запусками.

## Окно-оверлей по горячей клавише (CTRL+F2)

Второй хоткей открывает поверх игры окно In-Game Assistant (`live.html`):
470×720 справа, без рамки, полупрозрачное (85–90 %, `DEADLOCK_OVERLAY_ALPHA`),
повторное нажатие или кнопка ✕ в шапке прячет его. Комбинация — ключ
`overlay_hotkey` в `app_settings` (по умолчанию `ctrl+f2`), тот же тумблер
живёт по переключателю «Поверх других окон». `--live` / `DEADLOCK_LIVE=1`
открывает оверлей сразу при старте.

Ключевое отличие от панели CTRL+F1: окно имеет ex-стиль `WS_EX_NOACTIVATE`, поэтому
клик по нему **не активирует** его — игра остаётся foreground-окном, и WASD,
пробел и шифт продолжают управлять персонажем. `WS_EX_TOOLWINDOW` убирает окно
из Alt+Tab, `WS_EX_LAYERED` + `LWA_ALPHA` дают равномерную полупрозрачность
(попиксельная прозрачность WebView2 в этой сборке не работает). `WS_EX_TRANSPARENT`
не ставится: с ним окно было бы некликабельным.

Ограничения, заложенные в саму схему: нужен **Borderless Window** в Deadlock
(эксклюзивный fullscreen не перекрывается); клавиатура окну не достаётся вовсе —
Esc/Space в странице не работают, навигация мышью и хоткеем; пока курсор над
окном, игра не получает движения мыши. Проверка окна на живой игре —
в конце работы, вручную: `python run_desktop.py`, затем CTRL+F2.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `DEADLOCKFORGE_AI_KEY` | ключ LLM; **высший приоритет**. Если задана (в т.ч. пустой), файлы с ключом игнорируются |
| `DEADLOCK_DATA_DIR` | каталог `data/` (задаётся `run_desktop.py` для frozen-сборки) |
| `DEADLOCK_WEB_DIR` | каталог `web/` со SPA |
| `DEADLOCK_DEBUG=1` | включает DevTools в PyWebView |
| `DEADLOCK_OVERLAY_ALPHA` | непрозрачность окна-оверлея (0.3–1.0, по умолчанию 0.88) |
| `DEADLOCK_LIVE=1` | открыть окно-оверлей сразу при старте (то же, что `--live`) |
| `DEADLOCK_API_KEY` | Bearer для deadlock-api (повышает лимиты) |
| `DEADLOCK_VPK` | путь к `pak01_dir.vpk` для извлечения ассетов |

**Секреты не хранятся в репозитории.** Ключ читается из
`DEADLOCKFORGE_AI_KEY` либо из `api.txt` **вне** пакета (рядом с exe или в
его `data/`). Внутрипакетный `src/deadlockforge/ai/api.txt` намеренно
исключён: раньше он упаковывался в exe и утекал в дистрибутив. Шаблон —
[`.env.example`](.env.example).

## Тесты

```bash
python -m pytest -q
```

Live-тесты AI (`tests/test_gemini_coach_live.py`) обращаются к внешнему
провайдеру. Они **пропускаются**, если ключ не задан или сервис недоступен
(429/5xx/model_not_found): состояние внешней среды не должно красить сборку.

## Архитектура

| Модуль | Назначение |
|---|---|
| `src/deadlockforge/models.py` | замороженные Pydantic-контракты: Hero, Item, CoachReport, MatchReview, AIConfig, RedFlag |
| `src/deadlockforge/engine/dataset.py` | загрузка и валидация `data/*.json` → `Catalog` |
| `src/deadlockforge/engine/advisor.py` | контр-рекомендации по тегам угроз |
| `src/deadlockforge/engine/calculator.py` | статы билда (EHP, DPS, CDR-кап 55%) |
| `src/deadlockforge/engine/builds.py` | валидация 16 слотов билда |
| `src/deadlockforge/engine/drafter.py` | 6v6 драфт-движок: Red Flags, синергии, контр-пики |
| `src/deadlockforge/engine/match_loader.py` | загрузка и нормализация телеметрии матча |
| `src/deadlockforge/ai/client.py` | универсальный LLM-клиент: DeepSeek / OpenAI / Gemini / Anthropic |
| `src/deadlockforge/ai/coach.py` | промпт-тренер + эвристический fallback без API |
| `src/deadlockforge/storage/db.py` | SQLite: профиль, 16-слотовые билды, разборы матчей, AI-конфиг |
| `src/deadlockforge/assets/ingest.py` | пайплайн ассетов Valve: локальный VPK + CDN fallback |
| `src/deadlockforge/api/app.py` | REST-шов + раздача SPA |
| `web/` | SPA: 7 экранов (ХАБ, ТИР-ЛИСТ, ПИК-АССИСТЕНТ, DEEP MATCH, БИЛД-ЛАБ, КАРТА, ПРОФИЛЬ) |
| `web/live.html` | панель In-Game Assistant: часы матча, лента анонсов, советы по врагам |
| `web/overlay.html` | окно плашек поверх игры: `window.dfToast(...)` из драйвера, без опроса API |
| `run_desktop.py` | PyWebView shell: сервер в daemon-потоке, graceful shutdown, окно плашек |
| `scripts/check_overlay.py` | живая проверка плашек: видимость 5 с, `WS_EX_TRANSPARENT`, скрытие |
| `scripts/build_standalone.py` | PyInstaller-сборка автономного exe (с иконкой, без секретов) |
| `DeadlockForge.spec` | спека сборки: `_strip_secrets` не даёт упаковать ключ и `.db` |
| `scripts/build_map_markers.py` | маркеры карты из `/v1/assets/map` (лагеря, объективы, урна) |
| `scripts/prepare_map_asset.py` | обрезка подложки карты до квадрата проекции мира |
| `data/` | JSON-датасет: герои, предметы, контр-правила, карта |

## REST API

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/api/heroes`, `/api/items`, `/api/map` | каталог |
| GET | `/api/advice?my_hero=&enemies=` | угрозы + контр-рекомендации |
| POST | `/api/draft/analyze` | Red Flags 6v6 драфта |
| POST | `/api/calc` | статы билда |
| GET/POST/DELETE | `/api/builds` | CRUD билдов в SQLite |
| GET/POST | `/api/profile` | профиль игрока |
| GET/POST | `/api/ai/config`, POST `/api/ai/test` | конфиг и пинг LLM |
| POST | `/api/match/analyze` | разбор матча (Match ID или скриншот; `depth: short|medium|deep`) |
| POST | `/api/match/analyze/stream` | SSE-разбор с прогрессом |
| GET | `/api/match/reviews` | история разборов |
| POST | `/api/match/reviews/{id}/reanalyze` | переразбор матча (новая глубина/инструкции) |
| POST | `/api/draft/builds` | быстрые сборки по драфту (стартер + 3 варианта, <40 с) |
| GET/POST | `/api/assets/status`, `/api/assets/sync` | состояние пайплайна ассетов |
| GET/POST | `/api/live/clock` | игровое время матча (точка отсчёта от панели) |
| GET/POST | `/api/live/overlay` | тумблер плашек поверх игры (`supported` — только в десктопе) |
| GET | `/api/live/schedule`, `/api/live/timers` | расписание комментатора и тайминги карты |
| GET/POST | `/api/live/state`, POST `/api/live/advice` | ручной ввод панели и локальные советы |

## Данные и ассеты

- Ростер, предметы, винрейты — из открытого [deadlock-api](https://api.deadlock-api.com),
  зафиксированы статикой в `data/*.json` (офлайн-режим).
- Оригинальные иконки — из локальной установки игры
  (`C:\SteamLibrary\...\pak01_dir.vpk`, парсинг через библиотеку `vpk`)
  с CDN-fallback на `assets-bucket.deadlock-api.com`.
