# MASTER-ТЗ: АВТОНОМНЫЙ ТАКТИЧЕСКИЙ КОМПЛЕКС И AI-ТРЕНЕР «DEADLOCK FORGE»

> **Проект:** DeadlockForge  
> **Директория:** `C:\Projects\DeadlockForge`  
> **Целевая аудитория/Исполнитель:** DeepSeek Harness (DSH) / Команда автономных субагентов  
> **Дата актуализации:** Октябрь 2026 года (Valve Deadlock: The Cursed Apple, 3 Lanes)  
> **Ключевой визуальный референс:** Sweet Dota Hub (`@sweetdotahub_bot`) — темный киберспортивный HUD, аналитика матчей, дерево предметов и выводы тренера.

---

## 1. АРХИТЕКТУРА И КЛЮЧЕВЫЕ ТРЕБОВАНИЯ

1. **Форм-фактор:** Автономное переносимое Desktop-приложение (`DeadlockForge.exe`).
   * **Движок интерфейса:** `PyWebView` на базе нативного Windows Evergreen WebView2 (встроен в Win 10/11, не требует 200 МБ Electron).
   * **Нулевой порог входа:** Пользователю **НЕ нужен** установленный Python, pip или компиляторы. Запуск в один клик.
2. **Локальный бэкенд:** `FastAPI` + `Uvicorn` в режиме локального IPC / loopback `127.0.0.1` со свободным портом.
   * Обязателен **Graceful Shutdown**: при закрытии окна процесс и порт 8377 (или динамический) освобождаются мгновенно без зависания процессов в памяти.
3. **Персистентное хранилище (SQLite):** `data/deadlockforge.db`.
   * Никакой потери данных при перезапуске приложения или очистке браузера.
   * Сохранение профиля игрока, сохраненных сборок (16 слотов), истории матчей и настроек API.
4. **Оригинальные игровые ассеты Valve (Zero AI Hallucinations):**
   * Все иконки предметов, способностей, героев и карта берутся **напрямую из игры Valve**.
   * Локальный источник: `C:\SteamLibrary\steamapps\common\Deadlock\game\citadel\pak01_dir.vpk` (парсинг через `vpk`).
   * Сетевой fallback: открытый CDN сообщества `https://api.deadlock-api.com/v1/assets/` и `https://assets-bucket.deadlock-api.com/`.
5. **Универсальный AI-модуль (Provider-Agnostic LLM Engine):**
   * Поддержка актуального стека моделей:
     * **DeepSeek:** `deepseek-v4.1-flash`, `deepseek-reasoner` (R1)
     * **OpenAI:** `gpt-6-astra`, `o3-mini`, `o1`
     * **Google:** `gemini-3.8-flash`, `gemini-4-argon`, `gemini-2.5-pro`
     * **Anthropic:** `claude-3-5-sonnet`, `claude-3-7-sonnet`
   * Настройка пользовательского `api_key`, кастомного `base_url` (прокси) и `thinking_budget`.
   * Строгий Structured JSON output для сборки виджетов без битой верстки.

---

## 2. СХЕМА БАЗЫ ДАННЫХ SQLITE (`data/deadlockforge.db`)

```sql
-- 1. Профиль игрока
CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    steam_id TEXT,
    account_id TEXT,
    player_name TEXT DEFAULT 'Deadlock Operator',
    rank_tier TEXT DEFAULT 'Ascendant',
    rank_subrank INTEGER DEFAULT 1,
    mmr INTEGER DEFAULT 1600,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Конфигурация нейросетей
CREATE TABLE IF NOT EXISTS ai_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL DEFAULT 'deepseek', -- 'deepseek' | 'openai' | 'gemini' | 'anthropic'
    model_name TEXT NOT NULL DEFAULT 'deepseek-v4.1-flash',
    api_key TEXT NOT NULL DEFAULT '',
    base_url TEXT,
    enable_thinking BOOLEAN DEFAULT 1,
    thinking_budget INTEGER DEFAULT 2048,
    temperature REAL DEFAULT 0.2,
    system_prompt_override TEXT
);

-- 3. Сохраненные билды (16 слотов)
CREATE TABLE IF NOT EXISTS builds (
    id TEXT PRIMARY KEY,
    hero_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    weapon_slots TEXT NOT NULL,   -- JSON array (4 item IDs)
    vitality_slots TEXT NOT NULL, -- JSON array (4 item IDs)
    spirit_slots TEXT NOT NULL,   -- JSON array (4 item IDs)
    flex_slots TEXT NOT NULL,     -- JSON array (4 item IDs)
    base64_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Разобранные матчи (Deep Match Cache)
CREATE TABLE IF NOT EXISTS match_reviews (
    match_id TEXT PRIMARY KEY,
    hero_id TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    result TEXT NOT NULL,         -- 'win' | 'loss'
    kda TEXT NOT NULL,            -- '14/3/9'
    souls_curve TEXT NOT NULL,    -- JSON: [{time_sec, networth_delta, event}]
    performance_grade TEXT NOT NULL, -- 'S+', 'S', 'A', 'B', 'C'
    key_mistake TEXT NOT NULL,    -- JSON: {timestamp, title, explanation, position_lane, counter_action}
    coach_conclusion TEXT NOT NULL, -- JSON: array из 3 пунктов
    stat_benchmarks TEXT NOT NULL,  -- JSON: {farm_percentile, dps_percentile, urn_participation}
    item_analysis TEXT NOT NULL,    -- JSON: {missed_core_items, counter_items_bought}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 3. СТРУКТУРА ЭКРАНОВ И UI (HUD В СТИЛЕ SWEET DOTA HUB)

Цветовая палитра: Оккультный ар-деко Deadlock (Slate 950 `#070A0F`, Card Surface `#111622`, Neon Amber `#E5A93C`, Spirit Violet `#8B5CF6`, Vitality Emerald `#10B981`, Gunfire Orange `#F97316`).

### Экран 1: [ХАБ] (Dashboard)
* **User Header:** Аватар игрока, никнейм, ранг с оригинальной плашкой из игры, кнопка «Сменить Steam / Обновить».
* **Карточки быстрого доступа:**
  1. **DEEP MATCH (Разобрать матч):** Карточка с кривой душ, статусом последнего матча и кнопкой «Разобрать новый матч ->».
  2. **ТИР-ЛИСТ:** Плашка с рангами S+, S, A, B и индикацией актуального патча.
  3. **ПИК-АССИСТЕНТ 6v6:** «Кого пикнуть, что собрать» / «Собери драфт врага -> контр-пики и Red-Flags».
  4. **ЛАБОРАТОРИЯ БИЛДОВ:** 16-слотовый интерактивный конструктор.

### Экран 2: [ТИР-ЛИСТ] (Tier List)
* **Табы ролей:** `[Все]`, `[Gun DPS / Керри]`, `[Танки / Инициаторы]`, `[Сплитпуш / Роуминг]`, `[Саппорт / Контроль]`.
* **Переключатель:** `Винрейты (Статистика)` vs `Мета от Тренера`.
* **Строка персонажа:** Бейдж тира (`S+`, `S`, `A`, `B`), оригинальная иконка героя Valve, имя героя, тег `★ Топ Меты`, процент побед (зеленый %), пикрейт, кнопка быстрого перехода к сборкам героя.

### Экран 3: [ПИК-АССИСТЕНТ 6v6] (Draft & Matchup Engine)
* Сетка драфта 6 на 6 с распределением по линиям (*York, Broadway, Greenwich*).
* Поддержка соревновательного режима (зеркальные пики строго запрещены) и песочницы (`Allow Mirror` для 1v1).
* **Детектор критических уязвимостей (Red Flags):**
  * *«Вражеский пик перегружен лечением (Abrams/Infernus) — нет Decay / Toxic Bullets»*.
  * *«У врагов неуязвимости и сильный ульт (Seven/Yamato) — необходимы Knockdown / Curse»*.
  * *«80%+ урона вашей команды — пулевой. Вражеский Metal Skin выиграет файт»*.

### Экран 4: [DEEP MATCH] (Разбор матча & AI-Тренер)
* **Способы ввода:**
  1. Ввод `Match ID` (автоматическая загрузка телеметрии из API).
  2. Загрузка файла реплея `.dem` (локальный парсер логов событий).
  3. Загрузка скриншота послематчевой статистики (Multimodal Vision).
* **Интерактивный таймлайн преимущества душ (Souls Timeline):**
  * Кривая преимущества команды по душам от 0:00 до конца матча.
  * Кликабельные точки событий: драки, сдача Soul Urn, убийство Midboss, падение Walkers.
* **Карточка «Главная ошибка»:**
  * Точный таймкод (например, `13:20`), линия/место на карте, описание фатального действия и верный сценарий.
* **Карточка «Вывод тренера»:** 3 четких выжимки по макро-игре, таймингам и распределению фарма.
* **Бенчмарки:** Процентили игрока в сравнении с пулом ранга («Фарм лучше 94% игроков», «Участие в убийствах 72%»).

### Экран 5: [ЛАБОРАТОРИЯ БИЛДОВ] (16 Слотов)
* Полноценная сетка слотов: 4 Weapon (Оранжевые), 4 Vitality (Зеленые), 4 Spirit (Фиолетовые), 4 Flex (Золотые).
* Дерево улучшений предметов (`upgrade_from` / `upgrade_to`).
* Живой расчет параметров с учетом капов: Effective HP (Bullet/Spirit), Cooldown Reduction (с капом 55%), DPS, Spirit Scaling.
* Сохранение в локальный SQLite, экспорт Base64 кода сборки.

### Экран 6: [ПРОФИЛЬ & НАСТРОЙКИ AI]
* Переключатель модели: `DeepSeek v4.1 Flash`, `DeepSeek-R1`, `GPT-6 Astra`, `o3-mini`, `Gemini 3.8 Flash`, `Gemini 4 Argon`, `Claude 3.5 Sonnet`.
* Поля ввода: `API Key`, `Base URL`, `Thinking Budget`.
* Кнопка «Проверить соединение» с выводом пинга и статуса.

---

## 4. ДЕКОМПОЗИЦИЯ ЗАДАЧИ ДЛЯ 3 СУБАГЕНТОВ DSH

```
┌────────────────────────────────────────────────────────────────────────┐
│                        ОРКЕСТРАЦИЯ DSH                                │
├───────────────────────┬────────────────────────┬───────────────────────┤
│     СУБАГЕНТ 1        │      СУБАГЕНТ 2        │      СУБАГЕНТ 3       │
│ Desktop Shell, SQLite │ Roster 40+4, Map, HUD  │ Match Replay Analyzer │
│ & Asset Pipeline      │ & 6v6 Draft Engine     │ & Universal AI Coach  │
└───────────────────────┴────────────────────────┴───────────────────────┘
```

---

### СУБАГЕНТ 1: Desktop Shell, SQLite Engine & Asset Pipeline

**Файлы реализации:**
* `src/deadlockforge/assets/ingest.py`
* `src/deadlockforge/storage/db.py`
* `src/deadlockforge/storage/models.py`
* `run_desktop.py`
* `scripts/build_standalone.py`

**Требования к исполнению:**
1. **Пайплайн ассетов (`ingest.py`):**
   * Проверить локальный путь `C:\SteamLibrary\steamapps\common\Deadlock\game\citadel\pak01_dir.vpk`.
   * Извлечь оригинальные векторные SVG (`.vsvg_c`) и текстуры в `web/assets/`.
   * При отсутствии локального файла — инициировать параллельный fallback-скачиватель с `https://api.deadlock-api.com/v1/assets/` и `https://assets-bucket.deadlock-api.com/`.
   * Структурировать ассеты:
     * `web/assets/heroes/{hero_id}.webp`
     * `web/assets/items/{item_id}.png`
     * `web/assets/abilities/{hero_id}/{ability_name}.png`
     * `web/assets/map/cursed_apple_minimap.png`
2. **База данных SQLite (`storage/db.py`):**
   * Реализовать менеджер сессий SQLite с автоматическим применением схемы (DDL).
   * Реализовать методы CRUD: `save_build()`, `get_builds()`, `save_review()`, `get_review()`, `get_ai_config()`, `update_ai_config()`.
3. **Desktop Runner (`run_desktop.py`):**
   * Запуск FastAPI сервера в отдельном демоническом потоке на свободном порту.
   * Инициализация `webview.create_window` с заголовком "Deadlock Forge", размером 1440x900, минимальным размером 1200x768, темным фоном `#070A0F`.
   * Регистрация события `window.events.closing` для гарантированной остановки сервера и очистки ресурсов.
4. **Скрипт сборки (`build_standalone.py`):**
   * Настройка сборщика Nuitka / PyInstaller для создания одного автономного каталога/exe со всеми вшитыми ресурсами `web/` и `data/`.

---

### СУБАГЕНТ 2: Roster 40+4, The Cursed Apple Map & Sweet-Style UI HUD

**Файлы реализации:**
* `src/deadlockforge/models.py`
* `data/heroes.json`
* `data/items.json`
* `web/index.html`
* `web/css/hud.css`
* `web/js/app.js`
* `web/js/drafter.js`
* `web/js/map_view.js`

**Требования к исполнению:**
1. **База героев и предметов:**
   * Сформировать актуальный датасет на все 40 активных персонажей и Hero Labs (включая Holliday, Calico, Billy, Drifter, Mina, Victor, Baba, Rat King и др.) + 4 готовящихся.
   * Каждый герой содержит: базовые статы, 4 способности, кулдауны, скалирование от Spirit Power.
   * Каждый предмет содержит: слот, тир (500/1250/3000/6200), пассивные/активные статы, свойства `upgrade_from` и `upgrade_to`.
2. **Интерактивная карта *The Cursed Apple* (`map_view.js`):**
   * Интеграция реальной миникарты Valve с 3 линиями (York, Broadway, Greenwich).
   * Интерактивные маркеры с таймингами:
     * `0:00`: Спавн крипов, Midboss в Broadway Pit.
     * `2:00`: Малые лагеря нейтралов (respawn 1:25).
     * `3:00`: Base Soul Wells, коробки Tier 1.
     * `5:00`: Средние лагеря (respawn 4:50), Руны усиления.
     * `8:00`: Большие лагеря (respawn 5:35), Sinner's Sacrifice Vaults.
     * `10:00`: **Soul Urn** (12.5s спуск, спавн каждые 5 мин).
     * Walkers: уничтожение открывает по 1 Flex-слоту (максимум 3 слота за линии).
     * Midboss: респавны 7m / 6m / 5m после убийств.
3. **UI HUD в стиле Sweet Dota Hub:**
   * Полноценная адаптация навигации и экранов со скриншотов референса.
   * Компонент Тир-Листа с фильтрацией по ролям и винрейтам.
   * 16-слотовая панель сборки с подсчетом стат-дельты.
4. **Пик-Ассистент 6v6 (`drafter.js`):**
   * Сетка драфта, синергии, контр-пики и модуль детекции Red Flags.

---

### СУБАГЕНТ 3: Match Replay Analyzer & Universal Multi-Model AI Coach

**Файлы реализации:**
* `src/deadlockforge/engine/match_loader.py`
* `src/deadlockforge/ai/client.py`
* `src/deadlockforge/ai/coach.py`
* `web/js/deep_match.js`
* `tests/test_ai_coach.py`

**Требования к исполнению:**
1. **Загрузчик матчей (`match_loader.py`):**
   * Запрос к API Deadlock: `/v1/matches/{match_id}/metadata`.
   * Нормализация таймлайна душ, списка смертей с координатами и таймкодами, урона по башням/Walkers.
   * Поддержка Multimodal входа: передача скриншота таблицы матча в формате base64.
2. **Универсальный AI-клиент (`ai/client.py`):**
   * Единый интерфейс `LLMClient.generate_coach_report(prompt, match_data, image_bytes=None)`.
   * Провайдеры:
     * `OpenAIClient` (эндпоинты OpenAI / DeepSeek API с поддержкой `reasoning_content`).
     * `GoogleGenAIClient` (Gemini API через официальный SDK).
     * `AnthropicClient` (Claude API с параметром `thinking`).
   * Строгая валидация ответа через Pydantic-схему `CoachReport`.
3. **Промпт и RAG Тренера (`coach.py`):**
   * Системная персона: High-MMR аналитик Deadlock, циничный, тактически безупречный, без «воды» и шаблонных фраз.
   * Обязательные поля ответа:
     * `performance_grade`: `S+` | `S` | `A` | `B` | `C`
     * `key_mistake`: {`time`: "MM:SS", `location`: str, `event`: str, `why_fatal`: str, `correct_play`: str}
     * `coach_conclusion`: list[str] (ровно 3 емких пункта)
     * `benchmarks`: {`souls_at_10`: float, `lane_efficiency`: float, `fight_participation`: float}
     * `item_corrections`: list[{`slot`: str, `sold_or_skipped`: str, `recommended`: str, `reason`: str}]
4. **Отрисовка в UI (`deep_match.js`):**
   * Интерактивный Canvas/Chart.js график динамики душ с маркерами ошибок.
   * Виджеты карточек «Главная ошибка» и «Вывод тренера».

---

## 5. ПРОТОКОЛ ТЕСТИРОВАНИЯ И ВЕРИФИКАЦИИ

1. **Unit & Regression тесты:**
   * Сохранить 100% работоспособность существующих тестов (`pytest -v`, 76/76 passed).
   * Добавить тесты для `test_db.py`, `test_asset_ingest.py`, `test_match_loader.py`, `test_ai_client.py` (с моками сетевых вызовов).
2. **Портативность:**
   * Проверить запуск через `python run_desktop.py` — окно открывается без ошибок в консоли.
   * Проверить закрытие окна — порт не занят, процесс завершен.
3. **Целостность данных:**
   * Создать билд через UI -> закрыть приложение -> открыть приложение -> билд на месте из `deadlockforge.db`.
