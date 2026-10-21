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

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `DEADLOCKFORGE_AI_KEY` | ключ LLM; **высший приоритет**. Если задана (в т.ч. пустой), файлы с ключом игнорируются |
| `DEADLOCK_DATA_DIR` | каталог `data/` (задаётся `run_desktop.py` для frozen-сборки) |
| `DEADLOCK_WEB_DIR` | каталог `web/` со SPA |
| `DEADLOCK_DEBUG=1` | включает DevTools в PyWebView |
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
| `run_desktop.py` | PyWebView shell: сервер в daemon-потоке, graceful shutdown |
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

## Данные и ассеты

- Ростер, предметы, винрейты — из открытого [deadlock-api](https://api.deadlock-api.com),
  зафиксированы статикой в `data/*.json` (офлайн-режим).
- Оригинальные иконки — из локальной установки игры
  (`C:\SteamLibrary\...\pak01_dir.vpk`, парсинг через библиотеку `vpk`)
  с CDN-fallback на `assets-bucket.deadlock-api.com`.
