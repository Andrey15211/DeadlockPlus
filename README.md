# DeadlockForge

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

## Тесты

```bash
python -m pytest -q
```

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
| `web/` | SPA: 6 экранов (ХАБ, ТИР-ЛИСТ, ПИК-АССИСТЕНТ, DEEP MATCH, ЛАБОРАТОРИЯ, ПРОФИЛЬ) |
| `run_desktop.py` | PyWebView shell: сервер в daemon-потоке, graceful shutdown |
| `scripts/build_standalone.py` | PyInstaller-сборка автономного каталога |
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
| POST | `/api/match/analyze` | разбор матча (Match ID или скриншот) |
| GET | `/api/match/reviews` | история разборов |
| GET/POST | `/api/assets/status`, `/api/assets/sync` | состояние пайплайна ассетов |

## Данные и ассеты

- Ростер, предметы, винрейты — из открытого [deadlock-api](https://api.deadlock-api.com),
  зафиксированы статикой в `data/*.json` (офлайн-режим).
- Оригинальные иконки — из локальной установки игры
  (`C:\SteamLibrary\...\pak01_dir.vpk`, парсинг через библиотеку `vpk`)
  с CDN-fallback на `assets-bucket.deadlock-api.com`.
