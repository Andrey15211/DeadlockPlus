# DeadlockForge — проектный контекст

Общие правила работы — в `~/.dsh/AGENTS.md`. Здесь только специфика проекта: не дублировать тон, бюджет шагов, сеть и PowerShell.

## Карта файлов (проект не сканировать с нуля)
- **Экран 4 — Разбор матча (Deep Match):** разметка `web/index.html` (контейнер `#tab-match`, карточки `#match-result`, `#recent-matches`, блок `#dmc-grade-block`); логика и анимации `web/js/deep_match.js` (`render`, `animateDmcGrade`, `playSpotlightReveal`); стили и HUD `web/css/hud.css`.
- **Экран 7 — Контрпики:** контейнер `#tab-counterpicks`, блоки `#cp-grid`/`#cp-guide`/`#cp-selected` в `web/index.html`; логика `web/js/counterpicks.js` (`initCounterpicks`, `renderHeroGuide`, `renderItemGuide`, `renderTeam`); стили `web/css/counterpicks.css`.
- **Экран 8 — Синергии:** контейнер `#tab-synergies` (`#syn-rows`, `#syn-search`, `#syn-sort`, `#syn-more`) в `web/index.html`; логика `web/js/synergies.js` (`initSynergies`, `render`, `renderCounts`); стили `web/css/synergies.css`; данные `data/synergies.json` через `GET /api/synergies`.
- **Остальные экраны:** 1 Герои → `web/js/app.js`; 2 Билды; 3 Драфт → `web/js/drafter.js`; 5 Билд-лаб → `web/js/app.js`.
- **Live-плашки поверх игры (выключено по умолчанию):** окно создаёт `run_desktop.py` только при флаге `--overlay`/`DEADLOCK_OVERLAY=1` (`overlay_enabled`, `overlay_push`, `overlay_driver`, `make_click_through`, окно 560×150, `hidden=True`, `WS_EX_TRANSPARENT`); без флага `overlay_win=None`, драйвер не стартует, `app.state.overlay_ctl` не выставляется. Страница `web/overlay.html` (`window.dfToast`/`dfClear`, без fetch и `setInterval`); часы — `GET/POST /api/live/clock` в `src/deadlockforge/api/app.py`; панель `web/live.html`. Полная прозрачность WebView2 в этой сборке не работает — не пытаться снова. Проверка: `python scripts/check_overlay.py [--exe]`.
- **Трей и выход:** `src/deadlockforge/desktop/tray.py` (`TrayIcon` — `Shell_NotifyIconW` из **shell32**, окно `DeadlockForgeTrayWindow`, свой `GetMessageW`-поток, меню `TrackPopupMenu`: «Открыть»/«Выйти»); `run_desktop.py` вешает `main_win.events.closing` → `on_closing` (крестик прячет окно в трей), «Выйти» → `quit_app` (снять хоткей, убрать иконку, `destroy()` всех окон, `server.should_exit`, `os._exit(0)` — WebView2 иначе держит процесс). До этого крестик оставлял живым скрытое окно плашек, `webview.start()` не возвращался, и копии копились в фоне. Если трей не поднялся, крестик закрывает программу целиком.
- **Глобальный хоткей панели (CTRL+F1):** `src/deadlockforge/desktop/hotkey.py` (`parse_hotkey`, `format_hotkey`, `HotkeyListener` — `RegisterHotKey` + `GetMessageW` в своём потоке, без хуков клавиатуры); окно поднимает `run_desktop.py` (`panel_toggle`, `panel_show`/`panel_hide`, `_topmost`/`_raise`, возврат фокуса игре через `_focus`); комбинация хранится в таблице `app_settings` (`Database.get_setting`/`set_setting`) и меняется через `GET/POST /api/desktop/hotkey`; Esc прячет панель через `GET/POST /api/desktop/panel` (`app.state.hotkey_ctl`/`panel_ctl`); тумблер **«Поверх других окон»** (`GET/POST /api/desktop/topmost`, ключ `always_on_top`, `app.state.topmost_ctl`) выключает и перехват клавиш, и закреп окна; поле захвата и тумблер — в профиле `web/index.html` (`#hotkey-input`, `#topmost-switch`). Тесты: `tests/test_hotkey.py`.
- **Бэкенд:** роуты FastAPI `src/deadlockforge/api/app.py`; модели Pydantic `src/deadlockforge/models.py`; SQLite `src/deadlockforge/storage/db.py`.
- **Окно-оверлей (CTRL+F2):** `run_desktop.py` создаёт его скрытым всегда (`make_overlay_window`, `show_overlay`, `overlay_panel_show/hide/toggle`), грузит `web/live.html` (470×720, справа). Ex-стили `WS_EX_NOACTIVATE|WS_EX_TOOLWINDOW` + `WS_EX_LAYERED`/`LWA_ALPHA` (`DEADLOCK_OVERLAY_ALPHA`, по умолчанию 0.88): окно кликабельно, клик его не активирует, клавиатура и управление остаются у игры; `WS_EX_TRANSPARENT` для этого окна не ставится. Комбинация — `app_settings.overlay_hotkey` (по умолчанию `ctrl+f2`), живёт по тому же тумблеру «Поверх других окон»; `--live`/`DEADLOCK_LIVE=1` открывает оверлей при старте (отдельного окна Live больше нет). Показ/скрытие из UI — `GET/POST /api/desktop/overlay` (`app.state.overlay_panel_ctl`), кнопка ✕ в `live.html`, часы подтягиваются `window.dfSyncClock()`.
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

## Синергии: конвейер данных
`python scripts/build_synergies.py` собирает `data/synergies.json` с
mobalytics.gg/deadlock/tier-list/synergies. Страница закрыта Cloudflare, поэтому
скрипт сначала пробует прямой запрос, а при отказе берёт тот же URL через
`https://r.jina.ai/` с `x-respond-with: html`: в HTML лежит dehydrated-кэш
react-query со всеми 946 парами (44 героя). Сорт не приходит с сервера — он
считается по винрейту той же формулой, что в чанке сайта 29910
(S ≥ 55%, A ≥ 50%, B ≥ 45%, иначе C). Имена и портреты подставляются из
`data/heroes.json` по `name_en`, поэтому в датасете сразу русские названия.
Проверка экрана: `python scripts/check_synergies_ui.py` (Playwright, скриншоты в
`docs/synergies/`) и `python -m pytest tests/test_synergies.py -q`.

## Ростер героев и портреты
`data/heroes.json` — единственный источник имён/портретов для всех экранов.
`in_development: true` скрывает героя из тир-листа, пик-ассистента и билд-лаба,
но Deep Match и контрпики берут ростер целиком. Герой, вышедший в релиз
(Медсестра Хэрроу, api_id 87), обязан иметь `portrait: assets/heroes/<id>.webp`
в `web/`: официальная иконка — `icon_image_small_webp` из
`https://api.deadlock-api.com/v1/assets/heroes/<api_id>` (128×128). Статы и
`winrate`/`pickrate` берутся из `/v1/analytics/hero-stats` тем же расчётом, что в
`scripts/refresh_meta.py`. Регрессию ловят `tests/test_qa_spec.py`
(`test_released_heroes_have_portraits_on_disk`) и `python scripts/check_nurse_ui.py`.
Соперник по линии в Deep Match приходит парой `opponent_hero` (русское имя) +
`opponent_hero_id` (слаг): портрет строится только по слагу.

## Привычки проекта
- Правка UI — один файл за один вызов; проверка — только затронутого файла.
- Внешние данные (deadlock-api.com и прочие API) — через `curl.exe -sL` или Python; см. правило о блокировке fetch в глобальном AGENTS.md.
- Данные и справочники лежат в `data/`; разметка не дублируется в JS-строки.
- Скрипты с русским выводом в консоль обязаны вызывать `sys.stdout.reconfigure(encoding="utf-8")`:
  консоль Windows по умолчанию cp1251 и падает с UnicodeEncodeError.