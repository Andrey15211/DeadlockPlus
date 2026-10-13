# MASTER-ПРОМПТ ДЛЯ РАЗРАБОТЧИКА (DSH / CLAUDE / CURSOR)
## Задача: Полная ликвидация бага неотзывчивости UI и доведение DeadlockForge до готового Desktop-приложения

---

### РЕЗЮМЕ ДЛЯ АГЕНТА: В ЧЁМ БЫЛ КОРЕНЬ ПРОБЛЕМЫ («НИЧЕГО НЕ НАЖИМАЕТСЯ»)

При запуске приложения интерфейс отображался, но **ни одна кнопка и ни один таб не реагировали на клики**. Причины:
1. **Фатальный SyntaxError в ES-модуле `web/js/drafter.js`**:
   - Строка 44 содержала недопустимую синтаксическую конструкцию: `(side === 'my' ? myTeam : enemyTeam) = ...` (`SyntaxError: Invalid left-hand side in assignment`).
   - В архитектуре ES Modules браузер/WebView2 валидирует весь граф зависимостей до выполнения. Из-за ошибки в импортируемом модуле `app.js` **не запустился вовсе**. Функция `boot()` не вызвалась, обработчики событий `addEventListener` не были зарегистрированы.
2. **Блокирующий порядок вызова функций в `web/js/app.js`**:
   - Вызов `initTabs()` находился внутри `boot()` **после** `await Promise.all([fetchHeroes(), fetchItems()])`. При любой задержке или сбое сети обработчики табов не регистрировались.
   - Инициализаторы экранов вызывались последовательно без изоляции `try/catch`. Падение одного экрана блокировало работу всех остальных.
3. **Отсутствие DevTools и агрессивный кэш WebView2**:
   - В `run_desktop.py` вызов `webview.start()` выполнялся без `debug=True`, из-за чего контекстное меню инспектора (F12 / Inspect) было отключено.
   - FastAPI отдавал статические JS-файлы без `Cache-Control: no-cache, no-store, must-revalidate`, в результате чего WebView2 кэшировал старый битый JS на диске.
4. **Краши в `run_desktop.py` в окне без консоли**:
   - В `--windowed` режиме PyInstaller `sys.stdout` и `sys.stderr` равны `None`, из-за чего uvicorn падал с `AttributeError: 'NoneType' object has no attribute 'isatty'` / `ValueError: Unable to configure formatter 'default'`.

---

# СКОПИРУЙТЕ ТЕКСТ НИЖЕ В ВАШ АГЕНТ (DSH / CLAUDE / CURSOR):

```markdown
# ТЕХНИЧЕСКОЕ ЗАДАНИЕ ДЛЯ КОМАНДЫ СУБАГЕНТОВ: ДОРАБОТКА И СТАБИЛИЗАЦИЯ DEADLOCKFORGE

Ты — ведущий инженер-архитектор и команда из 4 профильных субагентов.
Твоя цель: превратить проект `DeadlockForge` (`C:\Projects\DeadlockForge`) в надежное, 100% рабочее переносимое Windows Desktop-приложение (`DeadlockForge.exe`).

## КРИТИЧЕСКИЕ ПРАВИЛА
1. Никаких «заглушек», неработающих кнопок и пустых обработчиков (`console.log('TODO')`).
2. Каждый кликабельный элемент обязан давать немедленный визуальный отклик в интерфейсе.
3. Код должен быть отказоустойчивым (Fault-Tolerant): ни одна сетевая задержка или ошибка бэкенда не имеет права парализовать переключение вкладок или кликабельность UI.
4. Приложение распространяется как автономный `.exe` (PyInstaller) с нулевым порогом входа (не требует установленного Python у конечного пользователя).

---

### СУБАГЕНТ 1: BACKEND, API & STORAGE SPECIALIST
**Зона ответственности:** `src/deadlockforge/`, `data/deadlockforge.db`
1. **Защита от кэширования статики в `src/deadlockforge/api/app.py`:**
   - Для всех статических роутов (`/`, `/js/{file_name}`, `/css/{file_name}`) настроить заголовки:
     `headers={"Cache-Control": "no-cache, no-store, must-revalidate"}`.
   - Это гарантирует, что WebView2 не будет кэшировать устаревший JavaScript.
2. **Надежность базы данных SQLite:**
   - Обеспечить инициализацию таблиц `user_profile`, `ai_config`, `saved_builds`, `match_reviews`.
   - Защитить сохранение и загрузку профиля игрока, сборок и настроек AI.
3. **Безопасные mock-ответы:**
   - Если пользователь не настроил AI-ключ или находится оффлайн, эндпоинты `/api/match/analyze` и `/api/draft/analyze` должны возвращать структурированные эвристические данные из движка (не 500-ю ошибку).

---

### СУБАГЕНТ 2: FRONTEND RESILIENCE & INTERACTION SPECIALIST
**Зона ответственности:** `web/index.html`, `web/js/*.js`, `web/css/hud.css`
1. **Абсолютная защита навигации в `web/index.html`:**
   - Внедрить в `web/index.html` перед закрывающим тегом `</body>` независимый нативный скрипт делегирования событий:
     ```javascript
     document.addEventListener('click', function(e) {
       const tabBtn = e.target.closest('.hud-tab');
       if (tabBtn && tabBtn.dataset.tab) {
         document.querySelectorAll('.hud-tab').forEach(t => t.classList.toggle('tab-active', t.dataset.tab === tabBtn.dataset.tab));
         document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('hidden', p.id !== 'tab-' + tabBtn.dataset.tab));
         window.dispatchEvent(new Event('resize'));
       }
       const card = e.target.closest('.hub-card');
       if (card && card.dataset.goto) {
         const target = card.dataset.goto;
         document.querySelectorAll('.hud-tab').forEach(t => t.classList.toggle('tab-active', t.dataset.tab === target));
         document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('hidden', p.id !== 'tab-' + target));
         window.dispatchEvent(new Event('resize'));
       }
     });
     ```
   - Это гарантирует 100% переключение всех вкладок («Хаб», «Тир-лист», «Пик-ассистент», «Deep Match», «Билд-лаб», «Карта», «Профиль») даже при сбоях в модулях.
2. **Отказоустойчивая загрузка в `web/js/app.js`:**
   - Вызывать `initTabs()` первым действием, до ожидания промисов `fetchHeroes()` и `fetchItems()`.
   - Обернуть запуск каждого экрана (`initTooltip`, `initHub`, `initTierList`, `initDrafter`, `initDeepMatch`, `initLab`, `initBuildManager`, `loadMap`, `loadProfile`, `loadAIConfig`, `loadAssetsStatus`) в индивидуальный блок `try { ... } catch (err) { console.error(err); }`.
3. **Интерактивность экранов:**
   - **Билд-лаб (`web/js/app.js`, `web/js/ui.js`):** клик по карточке предмета в каталоге экипирует его в первый свободный слот соответствующей категории (или Flex). Клик по экипированному слоту снимает предмет. Статы (HP, Weapon Damage, Spirit Power) пересчитываются мгновенно. Кнопка «Сбросить билд» очищает слоты. Сохранение и копирование кода билда работают.
   - **Пик-ассистент (`web/js/drafter.js`):** убедиться в отсутствии синтаксических ошибок (никаких `(cond ? a : b) = val`). Клик по герою добавляет его в активную команду. Кнопка переключения стороны («Моя команда» / «Враг») меняет целевой слот. Кнопка «Рассчитать синергии и контр-пики» формирует анализ без ошибок.
   - **Deep Match (`web/js/deep_match.js`):** поля ввода Match ID, загрузка скриншота таблицы, отрисовка графика разницы душ на Canvas 2D, кликабельные маркеры событий.
   - **Тир-лист (`web/js/tierlist.js`):** фильтры ролей («Керри», «Инициаторы», «Дуэлянты», «Саппорты»), переключатель «Мета от тренера», кнопка «В билд-лаб» с передачей героя.

---

### СУБАГЕНТ 3: AI COACH & MATCH PIPELINE SPECIALIST
**Зона ответственности:** `src/deadlockforge/ai/`, `src/deadlockforge/engine/`
1. **Мультипровайдерный клиент (`ai/client.py`):**
   - Корректная поддержка `deepseek-v4.1-flash`, `deepseek-reasoner`, `gpt-6-astra`, `gemini-3.8-flash`, `claude-3-5-sonnet`.
   - Безопасная валидация ключа в `POST /api/ai/test` с замером latency.
   - Fallback в mock-режим, если API недоступен, чтобы приложение оставалось функциональным при тестировании.
2. **Анализ матча и вердикт тренера (`ai/coach.py`):**
   - Генерация структурированного JSON: ключевая ошибка матча, разбор тимфайтов, тайминги покупки ключевых предметов.

---

### СУБАГЕНТ 4: PACKAGING, DESKTOP INTEGRATION & QA
**Зона ответственности:** `run_desktop.py`, `scripts/build_standalone.py`, PyInstaller
1. **Стабилизация `run_desktop.py`:**
   - Защита от отсутствия консоли в `--windowed` режиме:
     ```python
     if sys.stdout is None:
         sys.stdout = open(os.devnull, "w", encoding="utf-8")
     if sys.stderr is None:
         sys.stderr = open(os.devnull, "w", encoding="utf-8")
     ```
   - Запуск Uvicorn строго с `log_config=None` во избежание `AttributeError: 'NoneType' object has no attribute 'isatty'`.
   - Ожидание открытия сокета Uvicorn перед инициализацией PyWebView (защита от race condition).
   - Включение режима отладки и приватного режима: `webview.start(debug=True, private_mode=True)`.
2. **Сборка Portable EXE:**
   - Команда сборки:
     `python -m PyInstaller --noconfirm --clean --onefile --windowed --name DeadlockForge --paths src --add-data "web;web" --add-data "data;data" --collect-all deadlockforge --collect-all pywebview run_desktop.py`
   - Исполняемый файл должен запускаться в один клик и не оставлять зомби-процессов при закрытии окна.
3. **Автоматическая верификация (QA):**
   - Запустить `pytest` (все 166 тестов обязаны проходить).
   - Проверить через Chrome DevTools / headless браузер, что консоль чиста (отсутствуют SyntaxError, ReferenceError и Unhandled Promise Rejections).

---

### ИТОГОВЫЙ ЧЕКЛИСТ ПРИЕМКИ
- [ ] При клике на табы «Хаб», «Тир-лист», «Пик-ассистент», «Deep Match», «Билд-лаб», «Карта», «Профиль & AI» экраны мгновенно переключаются.
- [ ] При клике на карточки в Хабе происходит переход в соответствующий раздел.
- [ ] В Билд-лабе предметы из каталога по клику надеваются в слоты, статы обновляются.
- [ ] В Пик-ассистенте герои выбираются в союзную и вражескую команды, анализ рассчитывает угрозы и контрпики.
- [ ] В Профиле сохраняются никнейм и MMR, тест AI-соединения возвращает результат.
- [ ] `DeadlockForge.exe` запускается без ошибок и открывает готовое окно приложения.
```
