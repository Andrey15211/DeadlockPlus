// drafter.js — экран 3: пик-ассистент 6v6 (Red Flags, синергии, контр-пики).
// Импорт-безопасен: DOM трогается только внутри initDrafter.

import { postDraftAnalyze, postDraftParseScreenshot, postDraftBuilds, fetchItems, fetchCounterpicks } from './api.js';
import { TAG_LABEL, ROLE_LABEL } from './ui.js';

const LANES = [
  { id: 'york', name: 'Йорк', color: '#E5A93C' },      // 1-я колонка (желтый): слоты 0, 1
  { id: 'broadway', name: 'Бродвей', color: '#3B82F6' }, // 2-я колонка (синий): слоты 2, 3
  { id: 'greenwich', name: 'Гринвич', color: '#10B981' }, // 3-я колонка (зеленый): слоты 4, 5
];
const TEAM_SIZE = 6;

let heroes = [];
let heroById = new Map();
let myTeam = new Array(TEAM_SIZE).fill(null);
let enemyTeam = new Array(TEAM_SIZE).fill(null);
let myPlayerHeroId = null; // Герой, за которого играет пользователь
let activeSide = 'my'; // куда кликают герои: 'my' | 'enemy'
let targetSlot = null; // если кликнули в конкретный слот
let searchQuery = '';
let selectedRole = '';
let itemById = null;   // Map<id, item> — лениво грузится перед первым анализом
let itemsPromise = null;
let cpData = null;     // Данные из data/counterpicks.json
let cpPromise = null;

let dom = {}; // кэш DOM-ссылок, заполняется в initDrafter

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function teamOf(side) {
  return side === 'my' ? myTeam : enemyTeam;
}

function setError(text) {
  if (dom.error) dom.error.textContent = text || '';
}

function isMirror() {
  return dom.mirror && dom.mirror.checked;
}

// Автоматически синхронизируем myPlayerHeroId с составом myTeam
function syncPlayerHero() {
  const myHeroes = myTeam.filter(Boolean);
  if (!myPlayerHeroId || !myHeroes.includes(myPlayerHeroId)) {
    myPlayerHeroId = myHeroes.length ? myHeroes[0] : null;
  }
}

// --- слоты 6v6 (3 колонки линий: Йорк, Бродвей, Гринвич) ------------------------

function renderSlots() {
  syncPlayerHero();

  for (const [side, container] of [['my', dom.mySlots], ['enemy', dom.enemySlots]]) {
    if (!container) continue;
    container.replaceChildren();

    LANES.forEach((lane, laneIdx) => {
      const col = document.createElement('div');
      col.className = `draft-lane-col lane-${lane.id}`;
      col.style.setProperty('--lane-color', lane.color);

      // Заголовок колонки линии
      const header = document.createElement('div');
      header.className = 'draft-lane-header';
      header.innerHTML = `
        <span class="lane-header-dot" style="background:${lane.color}; box-shadow: 0 0 8px ${lane.color}"></span>
        <span class="lane-header-name" style="color:${lane.color}">${lane.name}</span>
      `;
      col.append(header);

      const slotsWrap = document.createElement('div');
      slotsWrap.className = 'draft-lane-slots';

      // 2 слота на линию (слот laneIdx*2 и laneIdx*2 + 1)
      for (let offset = 0; offset < 2; offset++) {
        const idx = laneIdx * 2 + offset;
        const heroId = teamOf(side)[idx];
        const slot = document.createElement('div');
        const isTarget = activeSide === side && targetSlot === idx;
        const isMe = side === 'my' && heroId && heroId === myPlayerHeroId;

        slot.className = 'draft-slot'
          + (heroId ? ` filled ${side}-filled` : ' empty')
          + (isMe ? ' is-me' : '')
          + (side === activeSide ? ' active-side-slot' : '')
          + (isTarget ? ' slot-targeted' : '');
        slot.style.setProperty('--lane-color', lane.color);
        slot.dataset.side = side;
        slot.dataset.idx = String(idx);

        const hero = heroId ? heroById.get(heroId) : null;
        if (hero) {
          slot.title = side === 'my'
            ? `${hero.name} (${lane.name})${isMe ? ' — ВЫ (ваш герой)' : ' — клик чтобы выбрать себя'}`
            : `${hero.name} (${lane.name}) — клик для удаления`;
          let avatarHtml = '';
          if (hero.portrait) {
            avatarHtml = `<img class="ds-avatar" src="${esc(hero.portrait)}" alt="${esc(hero.name)}" loading="lazy">`;
          } else {
            avatarHtml = `<div class="ds-avatar-fallback">${esc((hero.name || '??').slice(0, 2))}</div>`;
          }

          slot.innerHTML = `
            ${avatarHtml}
            <div class="ds-info">
              <span class="ds-name">${esc(hero.name)}</span>
              <span class="ds-lane-tag" style="color:${lane.color}">${esc(lane.name)}</span>
              ${side === 'my' && !isMe ? `<button type="button" class="ds-set-me-btn" title="Сделать моим героем">Сделать моим</button>` : ''}
            </div>
            ${isMe ? `<span class="ds-me-star" title="Ваш персонаж">★ ВЫ</span>` : ''}
            <button type="button" class="ds-clear" title="Убрать героя">✕</button>
          `;

          const clearBtn = slot.querySelector('.ds-clear');
          if (clearBtn) {
            clearBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              removeHero(side, idx);
            });
          }

          const setMeBtn = slot.querySelector('.ds-set-me-btn');
          if (setMeBtn) {
            setMeBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              myPlayerHeroId = heroId;
              renderAll();
              if (dom.result && !dom.result.classList.contains('hidden')) {
                analyze();
              }
            });
          }

          slot.addEventListener('click', () => {
            if (side === 'my') {
              if (myPlayerHeroId !== heroId) {
                myPlayerHeroId = heroId;
                renderAll();
                if (dom.result && !dom.result.classList.contains('hidden')) {
                  analyze();
                }
              } else {
                removeHero(side, idx);
              }
            } else {
              removeHero(side, idx);
            }
          });
        } else {
          slot.title = `Клик — выбрать героя (${lane.name}, слот ${offset + 1})`;
          slot.innerHTML = `
            <div class="ds-empty-box">
              <span class="ds-plus" style="color:${lane.color}">+</span>
              <div class="ds-empty-info">
                <span class="ds-empty-text">Пусто</span>
                <span class="ds-lane-tag" style="color:${lane.color}">${esc(lane.name)}</span>
              </div>
            </div>
          `;
          slot.addEventListener('click', () => {
            activeSide = side;
            targetSlot = idx;
            setError('');
            renderAll();
            if (dom.heroSearch) dom.heroSearch.focus();
          });
        }

        slotsWrap.append(slot);
      }

      col.append(slotsWrap);
      container.append(col);
    });
  }
}

function removeHero(side, idx) {
  const removedId = teamOf(side)[idx];
  teamOf(side)[idx] = null;
  if (side === 'my' && myPlayerHeroId === removedId) {
    const remaining = myTeam.filter(Boolean);
    myPlayerHeroId = remaining.length ? remaining[0] : null;
  }
  if (targetSlot === idx && activeSide === side) targetSlot = null;
  setError('');
  renderAll();
}
  teamOf(side)[idx] = null;
  if (targetSlot === idx && activeSide === side) targetSlot = null;
  setError('');
  renderAll();
}

// --- сетка и поиск героев --------------------------------------------------------

function fallbackFor(hero) {
  const fb = document.createElement('div');
  fb.className = 'hc-fallback';
  fb.textContent = (hero.name || hero.name_en || '??').slice(0, 2);
  return fb;
}

function renderHeroGrid() {
  if (!dom.heroGrid) return;
  dom.heroGrid.replaceChildren();

  const mirror = isMirror();
  const activeTeam = teamOf(activeSide);
  const otherTeam = teamOf(activeSide === 'my' ? 'enemy' : 'my');

  const q = searchQuery.toLowerCase().trim();
  const filtered = heroes.filter((hero) => {
    if (hero.in_development) return false;
    if (selectedRole) {
      const r = hero.roles || [];
      const match = selectedRole === 'support'
        ? (r.includes('support') || r.includes('controller'))
        : r.includes(selectedRole);
      if (!match) return false;
    }
    if (q) {
      const matchRu = (hero.name || '').toLowerCase().includes(q);
      const matchEn = (hero.name_en || '').toLowerCase().includes(q);
      const matchId = (hero.id || '').toLowerCase().includes(q);
      return matchRu || matchEn || matchId;
    }
    return true;
  });

  if (dom.countAll) {
    const totalAvail = heroes.filter((h) => !h.in_development).length;
    dom.countAll.textContent = String(totalAvail);
  }

  if (filtered.length === 0) {
    const emptyDiv = document.createElement('div');
    emptyDiv.className = 'draft-search-empty';
    emptyDiv.innerHTML = `
      <div class="dse-icon">🔍</div>
      <div class="dse-text">Герои не найдены по запросу «<strong>${esc(searchQuery)}</strong>»</div>
      <button type="button" class="btn-ghost btn-sm dse-reset">Сбросить фильтр</button>
    `;
    emptyDiv.querySelector('.dse-reset')?.addEventListener('click', () => {
      searchQuery = '';
      selectedRole = '';
      if (dom.heroSearch) dom.heroSearch.value = '';
      if (dom.searchClear) dom.searchClear.classList.add('hidden');
      updateRolePills();
      renderHeroGrid();
    });
    dom.heroGrid.append(emptyDiv);
    return;
  }

  for (const hero of filtered) {
    const inMy = myTeam.includes(hero.id);
    const inEnemy = enemyTeam.includes(hero.id);
    const inActive = activeTeam.includes(hero.id);
    const blocked = (!mirror && otherTeam.includes(hero.id));

    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'hero-cell'
      + (inMy ? ' picked-my' : '')
      + (inEnemy ? ' picked-enemy' : '')
      + (blocked ? ' disabled' : '');
    cell.dataset.heroId = hero.id;
    cell.title = inMy ? `${hero.name} (В вашей команде — клик для отмены)`
      : inEnemy ? `${hero.name} (В команде врага${activeSide === 'enemy' ? ' — клик для отмены' : ''})`
      : blocked ? `${hero.name} (Занят врагом)`
      : `${hero.name} — клик для пика`;

    if (hero.portrait) {
      const img = document.createElement('img');
      img.src = hero.portrait;
      img.alt = hero.name;
      img.loading = 'lazy';
      img.addEventListener('error', () => img.replaceWith(fallbackFor(hero)));
      cell.append(img);
    } else {
      cell.append(fallbackFor(hero));
    }

    const name = document.createElement('div');
    name.className = 'hc-name';
    name.textContent = hero.name;
    cell.append(name);

    if (inMy) {
      const badge = document.createElement('span');
      badge.className = 'hc-badge hc-badge-my';
      badge.textContent = 'ВАШ';
      cell.append(badge);
    } else if (inEnemy) {
      const badge = document.createElement('span');
      badge.className = 'hc-badge hc-badge-enemy';
      badge.textContent = 'ВРАГ';
      cell.append(badge);
    }

    if (!blocked || inActive) {
      cell.addEventListener('click', () => pickHero(hero.id));
    }
    dom.heroGrid.append(cell);
  }
}

function pickHero(heroId) {
  const team = teamOf(activeSide);

  // Клик по уже выбранному в активной команде герою снимает его
  const existingIdx = team.indexOf(heroId);
  if (existingIdx !== -1) {
    removeHero(activeSide, existingIdx);
    return;
  }

  // Определяем слот: либо целевой, либо первый свободный
  let slotIdx = -1;
  if (targetSlot !== null && targetSlot >= 0 && targetSlot < TEAM_SIZE && team[targetSlot] === null) {
    slotIdx = targetSlot;
  } else {
    slotIdx = team.indexOf(null);
  }

  if (slotIdx === -1) {
    const sideName = activeSide === 'my' ? 'Ваша команда' : 'Команда врага';
    setError(`${sideName} уже заполнена (6/6) — уберите героя или переключите сторону.`);
    return;
  }

  team[slotIdx] = heroId;
  targetSlot = null; // сброс целевого слота после успешного пика
  setError('');
  renderAll();
}

// --- активная сторона и счетчики -------------------------------------------------

function renderSideToggle() {
  const myCount = myTeam.filter(Boolean).length;
  const enemyCount = enemyTeam.filter(Boolean).length;

  if (dom.sideMyBtn) dom.sideMyBtn.classList.toggle('active', activeSide === 'my');
  if (dom.sideEnemyBtn) dom.sideEnemyBtn.classList.toggle('active', activeSide === 'enemy');

  if (dom.sideMyCount) dom.sideMyCount.textContent = `${myCount}/6`;
  if (dom.sideEnemyCount) dom.sideEnemyCount.textContent = `${enemyCount}/6`;
  if (dom.myBadge) dom.myBadge.textContent = `${myCount}/6`;
  if (dom.enemyBadge) dom.enemyBadge.textContent = `${enemyCount}/6`;

  if (dom.myBoard) dom.myBoard.classList.toggle('board-active', activeSide === 'my');
  if (dom.enemyBoard) dom.enemyBoard.classList.toggle('board-active', activeSide === 'enemy');
}

function updateRolePills() {
  if (!dom.roleFilters) return;
  dom.roleFilters.querySelectorAll('.draft-role-pill').forEach((pill) => {
    pill.classList.toggle('active', (pill.dataset.role || '') === selectedRole);
  });
}

function renderAll() {
  renderSlots();
  renderHeroGrid();
  renderSideToggle();
}

// --- анализ -----------------------------------------------------------------------------

async function ensureItems() {
  if (itemById) return;
  if (!itemsPromise) itemsPromise = fetchItems();
  try {
    const items = await itemsPromise;
    itemById = new Map(items.map((i) => [i.id, i]));
  } catch {
    itemsPromise = null; // не фатально: имена предметов уже есть в ответе сервера
  }
}

async function ensureCounterpicksData() {
  if (cpData) return cpData;
  if (!cpPromise) cpPromise = fetchCounterpicks();
  try {
    cpData = await cpPromise;
    return cpData;
  } catch {
    cpPromise = null;
    return null;
  }
}

async function analyze() {
  setError('');
  syncPlayerHero();
  const my = myTeam.filter(Boolean);
  const enemy = enemyTeam.filter(Boolean);
  if (enemy.length === 0) {
    setError('Добавь хотя бы одного героя врага — без команды врага анализ невозможен.');
    return;
  }
  dom.analyzeBtn.disabled = true;
  dom.analyzeBtn.textContent = 'Анализирую…';
  try {
    const [analysis, cp] = await Promise.all([
      postDraftAnalyze(my, enemy, isMirror()),
      ensureCounterpicksData(),
      ensureItems(),
    ]);

    renderResults(analysis, cp);
    dom.result.classList.remove('hidden');
  } catch (e) {
    setError(`Анализ не удался: ${e.message}`);
  } finally {
    dom.analyzeBtn.disabled = false;
    dom.analyzeBtn.textContent = 'Анализировать драфт';
  }
}

function setCards(container, htmlParts, emptyText) {
  if (!container) return;
  container.innerHTML = htmlParts.length
    ? htmlParts.join('')
    : `<p class="section-hint">${esc(emptyText)}</p>`;
}

function heroName(id) {
  const h = heroById.get(id);
  // TZ v1.4 F3: без англ. дубля — показываем чистое локализованное имя.
  return h ? h.name : id;
}

// TZ v1.4 F3: очистка от raw-тегов и замена hero_id на русские имена
function localizeText(text) {
  if (!text) return '';
  let res = String(text);
  res = res.replace(/\bspirit_caster\b/gi, 'спирит-кастеров');
  res = res.replace(/\bbullet_dps\b/gi, 'пулевого урона');
  res = res.replace(/\bcc_chain\b/gi, 'контроля');
  res = res.replace(/\buninterruptible_ult\b/gi, 'непрерываемых ультов');
  for (const [tag, label] of Object.entries(TAG_LABEL)) {
    res = res.replaceAll(tag, label);
  }
  for (const hero of heroes) {
    if (hero.id && hero.name && hero.id.toLowerCase() !== hero.name.toLowerCase()) {
      const reg = new RegExp(`\\b${hero.id}\\b`, 'gi');
      res = res.replace(reg, hero.name);
    }
  }
  return res;
}

// Устранение дубликатов "против spirit_caster" и форматирование причин
function formatReason(reason) {
  if (!reason) return '';
  const parts = reason.split(';').map((p) => localizeText(p.trim())).filter(Boolean);
  const unique = [...new Set(parts)];
  return unique.join('; ');
}

// Вспомогательная функция для рендера бейджа героя с портретом
function heroPillHtml(heroId, isMy = false) {
  const h = heroById.get(heroId);
  const name = h ? h.name : heroId;
  const portrait = h && h.portrait ? `<img src="${esc(h.portrait)}" alt="${esc(name)}">` : '';
  const cls = isMy ? 'dmp-my-pill' : 'dmp-hero-pill';
  return `<span class="${cls}">${portrait}${esc(name)}</span>`;
}

function renderPlayerHeroBar() {
  if (!dom.playerHeroBar) return;
  syncPlayerHero();

  if (!myPlayerHeroId) {
    dom.playerHeroBar.innerHTML = `
      <div class="dph-info">
        <span class="dph-label">Персонаж не выбран</span>
        <div class="dph-name">Выберите героя в слотах «Ваша команда»</div>
        <span class="dph-tag">Контрмеры и сборка адаптируются под выбранного героя</span>
      </div>
    `;
    return;
  }

  const h = heroById.get(myPlayerHeroId);
  const name = h ? h.name : myPlayerHeroId;
  const avatar = h && h.portrait
    ? `<img class="dph-avatar" src="${esc(h.portrait)}" alt="${esc(name)}">`
    : `<div class="dph-avatar ds-avatar-fallback">${esc(name.slice(0, 2))}</div>`;

  const roles = (h && h.roles ? h.roles.map((r) => ROLE_LABEL[r] || r) : []).join(' · ');
  const tags = (h && h.threat_tags ? h.threat_tags.map((t) => TAG_LABEL[t] || t) : []);

  dom.playerHeroBar.innerHTML = `
    ${avatar}
    <div class="dph-info">
      <span class="dph-label">Ваш персонаж в этом матче</span>
      <div class="dph-name">${esc(name)} ${roles ? `<span style="font-size:14px; color:var(--text-dim); font-weight:normal;">(${esc(roles)})</span>` : ''}</div>
      <div class="dph-tags">
        <span class="dph-tag" style="color:var(--amber); font-weight:700;">★ Все предметы и тактика рассчитаны под вас</span>
        ${tags.map((t) => `<span class="dph-tag">${esc(t)}</span>`).join('')}
      </div>
    </div>
  `;
}

function renderMacroPlan(cp) {
  if (!dom.macroPlan) return;
  const my = myTeam.filter(Boolean);
  const enemy = enemyTeam.filter(Boolean);

  if (enemy.length === 0) {
    dom.macroPlan.innerHTML = '';
    return;
  }

  const cpHeroesMap = new Map((cp?.heroes || []).map((h) => [h.id, h]));

  // 1. Кого давить сразу (Early priority / не дать разогнаться)
  // Это гиперкерри врага с тегами bullet_dps, stealth, burst или ролями carry/duelist
  const rushTargets = enemy.filter((hid) => {
    const h = heroById.get(hid);
    const tags = h?.threat_tags || [];
    const roles = h?.roles || [];
    return roles.includes('carry') || tags.includes('bullet_dps') || tags.includes('stealth')
      || (roles.includes('duelist') && tags.includes('burst'));
  });

  // 2. Сила по стадиям (Early / Mid / Late)
  const earlyThreats = enemy.filter((hid) => {
    const h = heroById.get(hid);
    const tags = h?.threat_tags || [];
    return tags.includes('tank') || tags.includes('heal') || tags.includes('cc_chain');
  });

  const lateThreats = enemy.filter((hid) => {
    const h = heroById.get(hid);
    const roles = h?.roles || [];
    const tags = h?.threat_tags || [];
    return roles.includes('carry') || tags.includes('bullet_dps') || tags.includes('uninterruptible_ult');
  });

  // 3. Темп и синергия нашей команды
  const myCarries = my.filter((hid) => (heroById.get(hid)?.roles || []).includes('carry'));
  const mySupports = my.filter((hid) => {
    const r = heroById.get(hid)?.roles || [];
    return r.includes('support') || r.includes('controller');
  });
  const myFrontline = my.filter((hid) => {
    const r = heroById.get(hid)?.roles || [];
    const t = heroById.get(hid)?.threat_tags || [];
    return r.includes('initiator') || t.includes('tank');
  });

  let teamTempoText = '';
  if (myCarries.length >= 2) {
    teamTempoText = `У вашей команды <strong>жадный лейт-пик</strong> (${myCarries.map((id) => heroName(id)).join(', ')}). Не форсируйте хаотичные драки в начале 0–10 мин, защищайте вышки и давайте керри пространство для фарма.`;
  } else if (myFrontline.length >= 2) {
    teamTempoText = `У вашей команды <strong>агрессивный темповый пик</strong> с плотным фронтлайном (${myFrontline.map((id) => heroName(id)).join(', ')}). Навязывайте драки на таймингах 10–20 мин, захватывайте урны и сносите внешние стражи, не затягивая в глубокий лейт.`;
  } else {
    teamTempoText = `<strong>Сбалансированный темп</strong>: поддерживайте контроль над мид-боссом и урнами, играйте вокруг откатов ключевых ультимейтов.`;
  }

  // Кому помогать
  let supportText = '';
  if (myCarries.length > 0) {
    supportText = `Особое внимание уделите ${myCarries.map((id) => heroPillHtml(id, true)).join(' ')}: если враг начнёт их фокусить или гангать линии, немедленно стягивайтесь на помощь, чтобы они не отстали по душам.`;
  } else if (myFrontline.length > 0) {
    supportText = `Помогайте вашим инициаторам ${myFrontline.map((id) => heroPillHtml(id, true)).join(' ')}: не оставляйте их одних после врыва, вливайте фокус в законтроленную цель.`;
  } else {
    supportText = `Играйте парами, координируйте ганги через линии и обменивайтесь ресурсами.`;
  }

  // Общий стиль игры против вражеского сетапа
  const enemyHealers = enemy.filter((hid) => (heroById.get(hid)?.threat_tags || []).includes('heal'));
  const enemyCC = enemy.filter((hid) => (heroById.get(hid)?.threat_tags || []).includes('cc_chain'));
  const enemySpirit = enemy.filter((hid) => (heroById.get(hid)?.threat_tags || []).includes('spirit_caster'));

  let enemyStyleAdvice = [];
  if (enemyHealers.length >= 2) {
    enemyStyleAdvice.push(`Враги делают ставку на изнурение и хил (${enemyHealers.map((id) => heroName(id)).join(', ')}). <strong>Обязателен анти-хил</strong> на 8–12 минутах, иначе затяжные стычки вы проиграете.`);
  }
  if (enemyCC.length >= 2) {
    enemyStyleAdvice.push(`У врага цепочки жёсткого контроля (${enemyCC.map((id) => heroName(id)).join(', ')}). Не кучкуйтесь под ультимейты и заранее готовьте очищение или Неудержимость.`);
  }
  if (enemySpirit.length >= 2) {
    enemyStyleAdvice.push(`Высокий прокаст магии (${enemySpirit.map((id) => heroName(id)).join(', ')}). Инвестируйте в спирит-щиты и Разрушитель чар.`);
  }
  if (!enemyStyleAdvice.length) {
    enemyStyleAdvice.push('Стандартная позиционка: разменивайтесь по выносливости, используйте укрытия от прямого обстрела и следите за гангами через каналы.');
  }

  dom.macroPlan.innerHTML = `
    <!-- Карточка 1: Кого давить сразу -->
    <div class="dmp-card focus-rush">
      <div class="dmp-title">🎯 Кого давить сразу (Не дать разогнаться)</div>
      <div class="dmp-body">
        ${rushTargets.length
          ? `Главные угрозы сноубола и лейта: ${rushTargets.map((id) => heroPillHtml(id)).join(' ')}.<br>
             <strong>Что делать:</strong> Навязывайте агрессию на линии с первых минут, забирайте их крипов и зовите напарников на ганг до того, как они наберут 10k+ душ и купят ключевые артефакты.`
          : `У врага нет ярко выраженных гиперкерри-одиночек — держите равное давление по всем линиям и не отдавайте соло-киллы.`
        }
      </div>
    </div>

    <!-- Карточка 2: Пики силы врага (Power Spikes) -->
    <div class="dmp-card focus-spikes">
      <div class="dmp-title">⚡ Пики силы вражеского драфта</div>
      <div class="dmp-body">
        <strong>Ранняя игра (0–10 мин):</strong> ${earlyThreats.length ? earlyThreats.map((id) => heroName(id)).join(', ') : 'Низкая агрессия'} — опасны за счёт стартовой плотности, хуков или сустейна.<br>
        <strong>Лейтгейм (20+ мин):</strong> ${lateThreats.length ? lateThreats.map((id) => heroName(id)).join(', ') : 'Умеренный скейл'} — способны стирать команду за секунды при полной сборке.
      </div>
    </div>

    <!-- Карточка 3: Темп вашей команды и помощь союзникам -->
    <div class="dmp-card focus-tempo">
      <div class="dmp-title">⏳ Темп вашей команды и фокус помощи</div>
      <div class="dmp-body">
        ${teamTempoText}<br><br>
        <strong>Кому помогать:</strong> ${supportText}
      </div>
    </div>

    <!-- Карточка 4: Стиль игры против драфта врага -->
    <div class="dmp-card focus-team">
      <div class="dmp-title">🛡 Стиль игры против сетапа врага</div>
      <div class="dmp-body">
        ${enemyStyleAdvice.join('<br><br>')}
      </div>
    </div>
  `;
}

function renderCounterMeasures(cp) {
  if (!dom.counterMeasures) return;
  const enemy = enemyTeam.filter(Boolean);
  if (enemy.length === 0 || !cp || !cp.heroes || !cp.items) {
    dom.counterMeasures.innerHTML = '';
    return;
  }

  syncPlayerHero();
  const playerHero = myPlayerHeroId ? heroById.get(myPlayerHeroId) : null;
  const isSpiritHero = playerHero?.threat_tags?.includes('spirit_caster');
  const isBulletHero = playerHero?.threat_tags?.includes('bullet_dps') || playerHero?.roles?.includes('carry');

  const cpHeroesMap = new Map(cp.heroes.map((h) => [h.id, h]));
  const cpItemsMap = new Map(cp.items.map((i) => [i.id, i]));

  // Собираем все контрпредметы вражеских героев по стадиям
  // stage -> Map<itemId, { item, targets: [{heroId, label, why, when}] }>
  const stagesData = {
    early: new Map(),
    mid: new Map(),
    late: new Map(),
  };

  const STAGE_ORDER = ['early', 'mid', 'late'];

  enemy.forEach((enemyId) => {
    const cpHero = cpHeroesMap.get(enemyId);
    if (!cpHero || !cpHero.stages) return;

    const heroProblems = cpHero.problems || [];
    const heroWhy = cpHero.why || {};

    STAGE_ORDER.forEach((stageKey) => {
      const stageItems = cpHero.stages[stageKey] || [];
      stageItems.forEach((entry) => {
        const itemId = entry.item_id;
        const cpItem = cpItemsMap.get(itemId);
        if (!cpItem) return;

        // Фильтрация под профиль урона игрока
        if (itemId === 'toxic-bullets' && isSpiritHero && !isBulletHero) return; // спириту не нужны пули
        if (itemId === 'healbane' && isBulletHero && !isSpiritHero) return; // чистому пулевику лучше toxic-bullets

        if (!stagesData[stageKey].has(itemId)) {
          stagesData[stageKey].set(itemId, {
            item: cpItem,
            targets: [],
          });
        }

        const prob = heroProblems.find((p) => p.item_id === itemId);
        const whyText = heroWhy[itemId] || prob?.how || cpItem.short || '';
        const whenText = prob?.when || cpItem.when || '';

        stagesData[stageKey].get(itemId).targets.push({
          heroId: enemyId,
          label: entry.label || prob?.label || 'Контрмера',
          why: whyText,
          when: whenText,
        });
      });
    });
  });

  const stageTitles = {
    early: { title: 'Ранняя игра и линия (0–10 минут)', badge: 'Что купить первым на линию' },
    mid: { title: 'Середина игры и тимфайты (10–20 минут)', badge: 'Ключевые контр-артефакты' },
    late: { title: 'Поздняя игра (20+ минут)', badge: 'Финальное закрытие угроз врага' },
  };

  const blocksHtml = STAGE_ORDER.map((stageKey) => {
    const itemsMap = stagesData[stageKey];
    if (itemsMap.size === 0) return '';

    const itemsList = Array.from(itemsMap.values());
    // Сортируем: сначала предметы, закрывающие сразу нескольких врагов, затем по стоимости
    itemsList.sort((a, b) => {
      if (b.targets.length !== a.targets.length) return b.targets.length - a.targets.length;
      return (a.item.cost || 0) - (b.item.cost || 0);
    });

    const itemsHtml = itemsList.slice(0, 6).map(({ item, targets }) => {
      const imageSrc = item.image || (itemById.get(item.id)?.image) || '';
      const imgTag = imageSrc ? `<img class="dcm-item-icon" src="${esc(imageSrc)}" alt="${esc(item.name)}">` : '';
      const uniqueTargets = [...new Map(targets.map((t) => [t.heroId, t])).values()];
      const firstWhy = targets.find((t) => t.why)?.why || item.short || '';
      const firstWhen = targets.find((t) => t.when)?.when || '';

      return `
        <div class="dcm-item-card">
          <div class="dcm-item-head">
            ${imgTag}
            <div class="dcm-item-meta">
              <span class="dcm-item-name">${esc(item.name)}</span>
              <span class="dcm-item-tier">Tier ${esc(item.tier)} · ${esc(item.cost)} душ</span>
            </div>
          </div>
          <div class="dcm-targets-row">
            <span class="dcm-targets-label">Против кого:</span>
            ${uniqueTargets.map((t) => {
              const h = heroById.get(t.heroId);
              const p = h?.portrait ? `<img src="${esc(h.portrait)}" alt="${esc(h.name)}">` : '';
              return `<span class="dcm-target-pill">${p}${esc(h?.name || t.heroId)}</span>`;
            }).join(' ')}
          </div>
          ${firstWhy ? `<div class="dcm-item-why">💡 ${esc(firstWhy)}</div>` : ''}
          ${firstWhen ? `<div class="dcm-item-when">${esc(firstWhen)}</div>` : ''}
        </div>
      `;
    }).join('');

    return `
      <div class="dcm-stage-block">
        <div class="dcm-stage-header">
          <div class="dcm-stage-title">📦 ${esc(stageTitles[stageKey].title)}</div>
          <span class="dcm-stage-badge">${esc(stageTitles[stageKey].badge)}</span>
        </div>
        <div class="dcm-items-grid">
          ${itemsHtml}
        </div>
      </div>
    `;
  }).filter(Boolean).join('');

  dom.counterMeasures.innerHTML = `
    <h2 class="section-title good" style="font-size: 19px; margin-bottom: 12px;">🛡 Контрмеры и закуп по стадиям матча</h2>
    <p class="section-hint" style="font-size: 14.5px; margin-bottom: 16px;">
      Прямые контрпредметы из базы знаний Deadlock против вражеского пика с учётом вашего персонажа.
    </p>
    ${blocksHtml || '<p class="section-hint">Контр-предметы не требуются.</p>'}
  `;
}

function renderResults(a, cp) {
  renderPlayerHeroBar();
  renderMacroPlan(cp);
  renderCounterMeasures(cp);

  // red_flags: [{severity, title, detail, fix}]
  setCards(
    dom.redFlags,
    (a.red_flags || []).map((f) => `
      <div class="red-flag sev-${esc(f.severity)}">
        <div class="rf-title">${esc(localizeText(f.title))}</div>
        <div class="rf-detail">${esc(localizeText(f.detail))}</div>
        <div class="rf-fix">${esc(localizeText(f.fix))}</div>
      </div>`),
    'Red Flags не найдены — драфт выглядит сбалансированным.',
  );

  // synergy: [{title, detail}]
  setCards(
    dom.synergy,
    (a.synergy || []).map((s) => `
      <div class="synergy-card">
        <div class="sc-title">${esc(localizeText(s.title))}</div>
        <div class="sc-detail">${esc(localizeText(s.detail))}</div>
      </div>`),
    'Заметных синергий в вашей команде нет.',
  );

  // threat_summary: [{tag, score, contributors: [hero_id]}]
  setCards(
    dom.threats,
    (a.threat_summary || []).map((t) => `
      <div class="threat-card">
        <div class="tc-tag">${esc(TAG_LABEL[t.tag] || t.tag)} · <span class="tc-score">${esc(t.score)}</span></div>
        <div class="tc-detail">Герои врага: ${(t.contributors || []).map((id) => esc(heroName(id))).join(', ')}</div>
      </div>`),
    'Явных угроз у врага не обнаружено.',
  );
}

function renderTactics(tactics) {
  if (!dom.tacticsPanel) return;
  if (!tactics || (!tactics.summary && !tactics.core_counters?.length)) {
    dom.tacticsPanel.classList.add('hidden');
    return;
  }
  dom.tacticsPanel.classList.remove('hidden');

  if (dom.tacticsSummary) {
    dom.tacticsSummary.textContent = tactics.summary || 'Матчап разобран нейросетью.';
  }

  if (dom.tacticsCounters) {
    const counters = tactics.core_counters || [];
    dom.tacticsCounters.innerHTML = counters.length ? counters.map((c) => `
      <div class="dt-counter-item">
        <div class="dt-counter-name">🛡 ${esc(c.item || c.name)}</div>
        ${c.targets ? `<div class="dt-counter-targets">Против: <strong>${esc(c.targets)}</strong></div>` : ''}
        ${c.reason ? `<div class="dt-counter-reason">${esc(c.reason)}</div>` : ''}
      </div>
    `).join('') : '<p class="section-hint">Нет специфических контр-предметов</p>';
  }

  if (dom.tacticsLane) {
    const tips = tactics.lane_tips || [];
    dom.tacticsLane.innerHTML = tips.length ? tips.map((t) => `
      <div class="dt-list-item">${esc(t)}</div>
    `).join('') : '<p class="section-hint">Стандартный ластхит и контроль линий.</p>';
  }

  if (dom.tacticsThreats) {
    const threats = tactics.threats || [];
    dom.tacticsThreats.innerHTML = threats.length ? threats.map((t) => `
      <div class="dt-list-item">${esc(t)}</div>
    `).join('') : '<p class="section-hint">Явных критических угроз не выявлено.</p>';
  }
}

// --- Обработка скриншотов драфта ---

async function handleDraftScreenshot(fileOrBlob) {
  if (!fileOrBlob) return;
  setError('');
  if (dom.loader) dom.loader.classList.remove('hidden');

  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      const b64 = (e.target.result || '').split(',')[1];
      if (!b64) throw new Error('Не удалось прочитать изображение');
      const res = await postDraftParseScreenshot(b64);

      // Заполняем команды
      myTeam.fill(null);
      enemyTeam.fill(null);
      (res.my_team || []).forEach((hid, i) => { if (i < 6) myTeam[i] = hid; });
      (res.enemy_team || []).forEach((hid, i) => { if (i < 6) enemyTeam[i] = hid; });

      renderAll();

      // Показываем тактический план от AI
      renderTactics(res.tactics);

      // Если вернулся эвристический анализ, также отображаем Red Flags и предметы
      if (res.heuristic && (res.heuristic.red_flags || res.heuristic.recommended_items)) {
        await ensureItems();
        renderResults(res.heuristic);
        if (dom.result) dom.result.classList.remove('hidden');
      }

    } catch (err) {
      setError(`Ошибка AI-разбора скриншота: ${err.message}`);
    } finally {
      if (dom.loader) dom.loader.classList.add('hidden');
    }
  };
  reader.readAsDataURL(fileOrBlob);
}

// --- S5: быстрые сборки по драфту (локальный движок, 30-40 с) ---------------

function itemName(itemId, fallback) {
  const item = itemById && itemById.get(itemId);
  return item ? item.name : (fallback || itemId);
}

function renderDraftBuilds(plan) {
  if (!dom.buildsPanel) return;

  const blocks = [];

  if (plan.starter && plan.starter.length) {
    blocks.push(`
      <div class="db-block">
        <h3 class="db-block-title">🛒 Стартовый набор · <span class="db-cost">${esc(plan.starter_cost)} душ</span></h3>
        <div class="db-starter">
          ${plan.starter.map((s) => `
            <div class="db-starter-item">
              <span class="db-si-name">${esc(s.name)}</span>
              <span class="db-si-cost">${esc(s.cost)}</span>
              <span class="db-si-reason">${esc(s.reason)}</span>
            </div>`).join('')}
        </div>
      </div>`);
  }

  if (plan.variants && plan.variants.length) {
    blocks.push(`
      <div class="db-block">
        <h3 class="db-block-title">🧩 Варианты сборки</h3>
        <div class="db-variants">
          ${plan.variants.map((v) => `
            <div class="db-variant">
              <div class="db-v-head">
                <span class="db-v-title">${esc(v.title)}</span>
                <span class="db-v-cost">${esc(v.total_cost)} душ</span>
              </div>
              <div class="db-v-items">
                ${v.items.map((i) => `
                  <span class="db-item" title="Tier ${esc(i.tier)} · ${esc(i.tier_band)}">${esc(itemName(i.item_id, i.name))}</span>
                `).join('')}
              </div>
              <div class="db-v-stats">
                DPS ${esc(v.stats.weapon_dps)} · дух ${esc(v.stats.spirit_power)} ·
                EHP пули ${esc(v.stats.effective_hp_bullet)} · EHP спирит ${esc(v.stats.effective_hp_spirit)}
              </div>
              <ul class="db-v-reasons">
                ${(v.rationale || []).map((r) => `<li>${esc(r)}</li>`).join('')}
                ${v.llm_note ? `<li class="db-v-llm">🤖 ${esc(v.llm_note)}</li>` : ''}
              </ul>
            </div>`).join('')}
        </div>
      </div>`);
  }

  if (plan.counters && plan.counters.length) {
    blocks.push(`
      <div class="db-block">
        <h3 class="db-block-title">🛡 Контр-предметы под их драфт</h3>
        <div class="db-counters">
          ${plan.counters.map((c) => `
            <div class="db-counter">
              <span class="db-co-name">${esc(itemName(c.item_id, c.name))}</span>
              <span class="db-co-when">${esc(c.timing)}</span>
              <span class="db-co-tags">${(c.covered_tags || []).map((t) => esc(TAG_LABEL[t] || t)).join(', ')}</span>
            </div>`).join('')}
        </div>
      </div>`);
  }

  if (plan.timing_hint && (plan.timing_hint.bands || []).length) {
    blocks.push(`
      <div class="db-block">
        <h3 class="db-block-title">⏱ Тайминг покупок</h3>
        <div class="db-timing">${esc(plan.timing_hint.summary || '')}</div>
        <div class="db-timing-bands">
          ${plan.timing_hint.bands.map((b) => `
            <div class="db-band">
              <span class="db-band-when">T${esc(b.tier)} · ${esc(b.window)}</span>
              <span class="db-band-cost">${esc(b.cost_range)}</span>
              <span class="db-band-items">${(b.item_ids || []).map((id) => esc(itemName(id))).join(', ')}</span>
            </div>`).join('')}
        </div>
      </div>`);
  }

  if (plan.notes && plan.notes.length) {
    blocks.push(`<ul class="db-notes">${plan.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>`);
  }

  if (!blocks.length) {
    dom.buildsPanel.classList.add('hidden');
    return;
  }
  dom.buildsPanel.innerHTML = blocks.join('');
  dom.buildsPanel.classList.remove('hidden');
}

async function buildForMe() {
  syncPlayerHero();
  const my = myTeam.filter(Boolean);
  const enemy = enemyTeam.filter(Boolean);
  setError('');
  if (my.length === 0) {
    setError('Выбери своего героя — сборка строится под конкретного персонажа.');
    return;
  }
  if (enemy.length === 0) {
    setError('Добавь хотя бы одного героя врага — без вражеского драфта сборка не нужна.');
    return;
  }
  if (dom.buildsBtn) {
    dom.buildsBtn.disabled = true;
    dom.buildsBtn.textContent = 'Собираю…';
  }
  try {
    // Сборка строится под выбранного игроком персонажа
    const targetHero = myPlayerHeroId || my[0];
    const [plan, cp] = await Promise.all([
      postDraftBuilds(targetHero, enemy, { role: selectedRole || null }),
      ensureCounterpicksData(),
      ensureItems(),
    ]);

    renderResults(plan, cp);
    renderDraftBuilds(plan);
    dom.result.classList.remove('hidden');
  } catch (e) {
    setError(`Сборка не удалась: ${e.message}`);
  } finally {
    if (dom.buildsBtn) {
      dom.buildsBtn.disabled = false;
      dom.buildsBtn.textContent = '⚙️ Быстрая сборка';
    }
  }
}

// S5: элементы быстрых сборок живут в index.html, но модуль не должен
// падать, если разметку ещё не добавили — создаём их сами как fallback.
function ensureBuildsUi() {
  let btn = document.getElementById('btn-draft-builds');
  if (!btn) {
    const actions = document.querySelector('.draft-actions');
    if (actions) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn-ghost btn-draft-builds';
      btn.id = 'btn-draft-builds';
      btn.textContent = '⚙️ Быстрая сборка';
      actions.insertBefore(btn, actions.querySelector('#btn-draft-analyze')?.nextSibling || null);
    }
  }
  let panel = document.getElementById('draft-builds-panel');
  if (!panel) {
    const result = document.getElementById('draft-result');
    if (result && result.parentNode) {
      panel = document.createElement('div');
      panel.id = 'draft-builds-panel';
      panel.className = 'panel draft-builds hidden';
      result.parentNode.insertBefore(panel, result);
    }
  }
  return { btn, panel };
}

// --- init ---------------------------------------------------------------------------------

export function initDrafter(heroesData) {
  heroes = Array.isArray(heroesData) ? heroesData : [];
  heroById = new Map(heroes.map((h) => [h.id, h]));

  const buildsUi = ensureBuildsUi();

  dom = {
    mySlots: document.getElementById('draft-my-slots'),
    enemySlots: document.getElementById('draft-enemy-slots'),
    myBoard: document.getElementById('draft-my-board'),
    enemyBoard: document.getElementById('draft-enemy-board'),
    myHeader: document.getElementById('draft-my-header'),
    enemyHeader: document.getElementById('draft-enemy-header'),
    myBadge: document.getElementById('draft-my-badge'),
    enemyBadge: document.getElementById('draft-enemy-badge'),
    sideMyBtn: document.getElementById('btn-side-my'),
    sideEnemyBtn: document.getElementById('btn-side-enemy'),
    sideMyCount: document.getElementById('side-my-count'),
    sideEnemyCount: document.getElementById('side-enemy-count'),
    heroSearch: document.getElementById('draft-hero-search'),
    searchClear: document.getElementById('draft-hero-search-clear'),
    countAll: document.getElementById('draft-count-all'),
    roleFilters: document.getElementById('draft-role-filters'),
    heroGrid: document.getElementById('draft-hero-grid'),
    analyzeBtn: document.getElementById('btn-draft-analyze'),
    buildsBtn: buildsUi.btn || document.getElementById('btn-draft-builds'),
    buildsPanel: buildsUi.panel || document.getElementById('draft-builds-panel'),
    mirror: document.getElementById('mirror-toggle'),
    error: document.getElementById('draft-error'),
    result: document.getElementById('draft-result'),
    playerHeroBar: document.getElementById('draft-player-hero-bar'),
    macroPlan: document.getElementById('draft-macro-plan'),
    counterMeasures: document.getElementById('draft-counter-measures'),
    redFlags: document.getElementById('red-flags'),
    synergy: document.getElementById('synergy-block'),
    threats: document.getElementById('draft-threats'),
    items: document.getElementById('draft-items'),
    screenshotInput: document.getElementById('draft-screenshot'),
    pasteBtn: document.getElementById('btn-draft-paste'),
    clearBtn: document.getElementById('btn-draft-clear'),
    loader: document.getElementById('draft-ai-loader'),
    tacticsPanel: document.getElementById('draft-tactics-panel'),
    tacticsSummary: document.getElementById('draft-tactics-summary'),
    tacticsCounters: document.getElementById('draft-tactics-counters'),
    tacticsLane: document.getElementById('draft-tactics-lane'),
    tacticsThreats: document.getElementById('draft-tactics-threats'),
  };

  // Переключение активной стороны через кнопки тулбара
  if (dom.sideMyBtn) {
    dom.sideMyBtn.addEventListener('click', () => {
      activeSide = 'my';
      targetSlot = null;
      setError('');
      renderAll();
    });
  }
  if (dom.sideEnemyBtn) {
    dom.sideEnemyBtn.addEventListener('click', () => {
      activeSide = 'enemy';
      targetSlot = null;
      setError('');
      renderAll();
    });
  }

  // Клик по шапке доски команды также делает сторону активной
  if (dom.myHeader) {
    dom.myHeader.addEventListener('click', () => {
      activeSide = 'my';
      targetSlot = null;
      setError('');
      renderAll();
    });
  }
  if (dom.enemyHeader) {
    dom.enemyHeader.addEventListener('click', () => {
      activeSide = 'enemy';
      targetSlot = null;
      setError('');
      renderAll();
    });
  }

  // Поиск героев
  if (dom.heroSearch) {
    dom.heroSearch.addEventListener('input', () => {
      searchQuery = dom.heroSearch.value.trim();
      if (dom.searchClear) {
        dom.searchClear.classList.toggle('hidden', !dom.heroSearch.value);
      }
      renderHeroGrid();
    });

    dom.heroSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        dom.heroSearch.value = '';
        searchQuery = '';
        if (dom.searchClear) dom.searchClear.classList.add('hidden');
        renderHeroGrid();
      } else if (e.key === 'Enter') {
        // Если при поиске остался ровно 1 доступный герой — выбираем его по нажатию Enter
        const mirror = isMirror();
        const activeTeam = teamOf(activeSide);
        const otherTeam = teamOf(activeSide === 'my' ? 'enemy' : 'my');
        const q = searchQuery.toLowerCase().trim();
        const available = heroes.filter((h) => {
          if (h.in_development) return false;
          if (selectedRole) {
            const r = h.roles || [];
            const match = selectedRole === 'support'
              ? (r.includes('support') || r.includes('controller'))
              : r.includes(selectedRole);
            if (!match) return false;
          }
          if (q) {
            const matchRu = (h.name || '').toLowerCase().includes(q);
            const matchEn = (h.name_en || '').toLowerCase().includes(q);
            const matchId = (h.id || '').toLowerCase().includes(q);
            if (!matchRu && !matchEn && !matchId) return false;
          }
          const blocked = (!mirror && otherTeam.includes(h.id));
          return !blocked;
        });
        if (available.length === 1) {
          pickHero(available[0].id);
        }
      }
    });
  }

  // Кнопка очистки строки поиска
  if (dom.searchClear) {
    dom.searchClear.addEventListener('click', () => {
      if (dom.heroSearch) {
        dom.heroSearch.value = '';
        dom.heroSearch.focus();
      }
      searchQuery = '';
      dom.searchClear.classList.add('hidden');
      renderHeroGrid();
    });
  }

  // Фильтр по ролям героев
  if (dom.roleFilters) {
    dom.roleFilters.addEventListener('click', (e) => {
      const pill = e.target.closest('.draft-role-pill');
      if (!pill) return;
      selectedRole = pill.dataset.role || '';
      updateRolePills();
      renderHeroGrid();
    });
  }

  if (dom.mirror) dom.mirror.addEventListener('change', renderAll);
  if (dom.analyzeBtn) dom.analyzeBtn.addEventListener('click', analyze);
  if (dom.buildsBtn) dom.buildsBtn.addEventListener('click', buildForMe);

  if (dom.clearBtn) {
    dom.clearBtn.addEventListener('click', () => {
      myTeam.fill(null);
      enemyTeam.fill(null);
      targetSlot = null;
      setError('');
      if (dom.result) dom.result.classList.add('hidden');
      if (dom.tacticsPanel) dom.tacticsPanel.classList.add('hidden');
      if (dom.buildsPanel) {
        dom.buildsPanel.classList.add('hidden');
        dom.buildsPanel.replaceChildren();
      }
      renderAll();
    });
  }

  if (dom.screenshotInput) {
    dom.screenshotInput.addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      if (file) handleDraftScreenshot(file);
    });
  }

  if (dom.pasteBtn) {
    dom.pasteBtn.addEventListener('click', async () => {
      try {
        const items = await navigator.clipboard.read();
        for (const item of items) {
          const imgType = item.types.find((t) => t.startsWith('image/'));
          if (imgType) {
            const blob = await item.getType(imgType);
            handleDraftScreenshot(blob);
            return;
          }
        }
        setError('В буфере обмена нет изображения. Сделайте скриншот (Win+Shift+S) и нажмите кнопку снова.');
      } catch (err) {
        setError(`Доступ к буферу обмена заблокирован: ${err.message}. Используйте кнопку «📷 Скриншот драфта».`);
      }
    });
  }

  // Глобальный Ctrl+V, когда активен таб драфта
  window.addEventListener('paste', (e) => {
    const tabDraft = document.getElementById('tab-draft');
    if (!tabDraft || tabDraft.classList.contains('hidden')) return;
    const items = (e.clipboardData || e.originalEvent.clipboardData)?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) handleDraftScreenshot(file);
        break;
      }
    }
  });

  renderAll();
}
