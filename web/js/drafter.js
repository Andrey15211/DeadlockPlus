// drafter.js — экран 3: пик-ассистент 6v6 (Red Flags, синергии, контр-пики).
// Импорт-безопасен: DOM трогается только внутри initDrafter.

import { postDraftAnalyze, postDraftParseScreenshot, postDraftBuilds, fetchItems } from './api.js';
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
let activeSide = 'my'; // куда кликают герои: 'my' | 'enemy'
let targetSlot = null; // если кликнули в конкретный слот
let searchQuery = '';
let selectedRole = '';
let itemById = null;   // Map<id, item> — лениво грузится перед первым анализом
let itemsPromise = null;

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

// --- слоты 6v6 (3 колонки линий: Йорк, Бродвей, Гринвич) ------------------------

function renderSlots() {
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

        slot.className = 'draft-slot'
          + (heroId ? ` filled ${side}-filled` : ' empty')
          + (side === activeSide ? ' active-side-slot' : '')
          + (isTarget ? ' slot-targeted' : '');
        slot.style.setProperty('--lane-color', lane.color);
        slot.dataset.side = side;
        slot.dataset.idx = String(idx);

        const hero = heroId ? heroById.get(heroId) : null;
        if (hero) {
          slot.title = `${hero.name} (${lane.name}) — клик для удаления`;
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
            </div>
            <button type="button" class="ds-clear" title="Убрать героя">✕</button>
          `;

          const clearBtn = slot.querySelector('.ds-clear');
          if (clearBtn) {
            clearBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              removeHero(side, idx);
            });
          }
          slot.addEventListener('click', () => removeHero(side, idx));
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

async function analyze() {
  setError('');
  const my = myTeam.filter(Boolean);
  const enemy = enemyTeam.filter(Boolean);
  if (enemy.length === 0) {
    setError('Добавь хотя бы одного героя врага — без команды врага анализ невозможен.');
    return;
  }
  dom.analyzeBtn.disabled = true;
  dom.analyzeBtn.textContent = 'Анализирую…';
  try {
    const analysis = await postDraftAnalyze(my, enemy, isMirror());
    await ensureItems();
    renderResults(analysis);
    dom.result.classList.remove('hidden');
  } catch (e) {
    setError(`Анализ не удался: ${e.message}`);
  } finally {
    dom.analyzeBtn.disabled = false;
    dom.analyzeBtn.textContent = 'Анализировать драфт';
  }
}

function setCards(container, htmlParts, emptyText) {
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

function renderResults(a) {
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

  // counter_picks: [{hero_id, name, reason}]
  setCards(
    dom.counters,
    (a.counter_picks || []).map((c) => {
      const name = heroById.has(c.hero_id)
        ? heroName(c.hero_id)
        : (c.name || c.hero_id);
      return `
        <div class="counter-card">
          <div class="cc-name">${esc(name)}</div>
          <div class="cc-reason">${esc(formatReason(c.reason))}</div>
        </div>`;
    }),
    'Контр-пики не подобраны — добавь больше героев врага.',
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

  // recommended_items: [{item_id, name_en, name}]
  setCards(
    dom.items,
    (a.recommended_items || []).map((r) => {
      const item = itemById && itemById.get(r.item_id);
      // TZ v1.4 F3: чистое русское имя без английского дубля (англ. только если нет локала).
      const name = item ? item.name : (r.name || r.name_en);
      const extra = item ? ` · Tier ${item.tier} · ${item.cost} душ` : '';
      return `
        <div class="reco-card">
          <div class="rc-name">${esc(name)}<span>${esc(extra)}</span></div>
        </div>`;
    }),
    'Рекомендаций по предметам нет.',
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
    // Сборка строится под первого героя вашей команды (в матче вы пикаете себя).
    const plan = await postDraftBuilds(my[0], enemy, { role: selectedRole || null });
    await ensureItems();
    renderResults(plan);
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
    redFlags: document.getElementById('red-flags'),
    synergy: document.getElementById('synergy-block'),
    counters: document.getElementById('counter-picks'),
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
