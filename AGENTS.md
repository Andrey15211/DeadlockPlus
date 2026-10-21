# DeadlockForge — проектный контекст

Общие правила работы — в `~/.dsh/AGENTS.md`. Здесь только специфика проекта: не дублировать тон, бюджет шагов, сеть и PowerShell.

## Карта файлов (проект не сканировать с нуля)
- **Экран 4 — Разбор матча (Deep Match):** разметка `web/index.html` (контейнер `#tab-match`, карточки `#match-result`, `#recent-matches`, блок `#dmc-grade-block`); логика и анимации `web/js/deep_match.js` (`render`, `animateDmcGrade`, `playSpotlightReveal`); стили и HUD `web/css/hud.css`.
- **Экран 7 — Контрпики:** контейнер `#tab-counterpicks`, блоки `#cp-grid`/`#cp-guide`/`#cp-selected` в `web/index.html`; логика `web/js/counterpicks.js` (`initCounterpicks`, `renderHeroGuide`, `renderItemGuide`, `renderTeam`); стили `web/css/counterpicks.css`.
- **Остальные экраны:** 1 Герои → `web/js/app.js`; 2 Билды; 3 Драфт → `web/js/drafter.js`; 5 Билд-лаб → `web/js/app.js`.
- **Бэкенд:** роуты FastAPI `src/deadlockforge/api/app.py`; модели Pydantic `src/deadlockforge/models.py`; SQLite `src/deadlockforge/storage/db.py`.
- **AI-модуль:** `src/deadlockforge/ai/`.

## Контрпики: конвейер данных
Датасет `data/counterpicks.json` (в дистрибутив уходит только он) собирается из открытых
JSON deadlockitembuilder.com и официальных русских названий deadlock-api:
1. `python scripts/build_counterpicks.py --extract` — сайт → `data/counterpicks/source_en.json`
   + `glossary.json` (имена героев/предметов/способностей EN→RU). Идентификаторы полей
   (`top`, `related`, `stage_of`, `glance`-ярлыки) задаются английским источником.
2. `python scripts/translate_counterpicks.py [--only <hero>] [--items]` — чанки в
   `data/counterpicks/ru/`. Глоссарий идёт в системный промпт (только встреченные названия),
   после перевода — автопроверка имён с переспросом; брак копится в `review_needed.json`.
   Роутер по умолчанию `http://127.0.0.1:8089/v1`, модель `glm-5.3-flash`.
3. `python scripts/build_counterpicks.py --assemble` — сборка `data/counterpicks.json`.
4. `python scripts/build_counterpicks.py --report` — покрытие и объёмы.

Промежуточные файлы (`site_db.json`, `source_en.json`, `ru/`, `translation_cache.json`)
в сборку exe не попадают — их отсекает `_strip_secrets` в `DeadlockForge.spec`.

## Привычки проекта
- Правка UI — один файл за один вызов; проверка — только затронутого файла.
- Внешние данные (deadlock-api.com и прочие API) — через `curl.exe -sL` или Python; см. правило о блокировке fetch в глобальном AGENTS.md.
- Данные и справочники лежат в `data/`; разметка не дублируется в JS-строки.
- Скрипты с русским выводом в консоль обязаны вызывать `sys.stdout.reconfigure(encoding="utf-8")`:
  консоль Windows по умолчанию cp1251 и падает с UnicodeEncodeError.