// app.js — каркас SPA: загрузка данных, 6 экранов, лаборатория, профиль, AI.

import {
  fetchHeroes, fetchItems, fetchProfile, postProfile,
  fetchProfileDetect, fetchProfileSteamInfo, fetchRecentMatches,
  fetchAIConfig, fetchAIModels, postAIConfig, postAITest,
  fetchBuilds, postSaveBuild, deleteBuild as apiDeleteBuild,
  postExportBuild, postImportBuild, postCalc,
  fetchMatchReviews,
  getCachedReference,
  toast,
} from './api.js';
import { state, emptySlots, validateEquip, autoTarget, equippedItems } from './state.js';
import { computeStats, createDebouncedStatsCalculator } from './calc.js';
import { copyToClipboard } from './builds.js';
import {
  $, el, renderSlotBoard, renderCatalog, updateCatalogEquipped, renderStats,
  renderBuildList, initTooltip, showToast, setInlineError, ROLE_LABEL,
} from './ui.js';
import { initTierList } from './tierlist.js';
import { initDrafter } from './drafter.js';
import { initDeepMatch } from './deep_match.js';
import { initCounterpicks } from './counterpicks.js';
import { initSynergies } from './synergies.js';
import { loadMapView } from './map_view.js';
import { soundEngine, initVolumeWidget, initHubMusicWidget } from './audio.js';

// --- загрузка ----------------------------------------------------------------

let modulesInitialized = false;

// Экран «Контрпики» грузит собственный датасет и не зависит от каталога
// героев/предметов, поэтому инициализируется сразу после табов: он работает,
// даже если /api/heroes или /api/items недоступны.
let counterpicksInitialized = false;

function initCounterpicksOnce() {
  if (counterpicksInitialized) return;
  counterpicksInitialized = true;
  try {
    initCounterpicks();
  } catch (e) {
    console.error('initCounterpicks error:', e);
  }
}

function initModulesOnce() {
  if (modulesInitialized) return;
  modulesInitialized = true;

  const steps = [
    ['initTooltip', () => initTooltip((id) => state.itemById.get(id))],
    ['initHub', initHub],
    ['initTierList', () => initTierList(state.heroes)],
    ['initDrafter', () => initDrafter(state.heroes)],
    ['initDeepMatch', initDeepMatch],
    ['initLab', initLab],
    ['initBuildManager', initBuildManager],
    ['loadMap', loadMap],
    ['loadProfile', loadProfile],
    ['loadAIConfig', loadAIConfig],
    ['initAudio', initVolumeWidget],
    ['initHubMusic', initHubMusicWidget],
  ];

  for (const [name, fn] of steps) {
    try {
      fn();
    } catch (err) {
      console.error(`Error initializing ${name}:`, err);
    }
  }

  scheduleIdleBackgroundVideo();
}

async function boot() {
  // 1. Инициализация табов сразу — клики работают даже при медленной сети или ошибках
  try { initTabs(); } catch (e) { console.error('initTabs error:', e); }

  // 1b. Контрпики не зависят от каталога — стартуют сразу.
  initCounterpicksOnce();

  // 1c. Синергии тоже самодостаточны: имена и портреты уже в датасете.
  try { initSynergies(); } catch (e) { console.error('initSynergies error:', e); }

  // 2. Мгновенная инициализация из кэша (0 мс), если справочники сохранены в sessionStorage
  const cachedHeroes = getCachedReference('heroes');
  const cachedItems = getCachedReference('items');
  const hasCache = Array.isArray(cachedHeroes) && Array.isArray(cachedItems) && cachedHeroes.length > 0;

  if (hasCache) {
    state.heroes = cachedHeroes;
    state.items = cachedItems;
    state.itemById = new Map(state.items.map((i) => [i.id, i]));
    state.heroById = new Map(state.heroes.map((h) => [h.id, h]));
    state.heroId = state.heroes[0]?.id || null;
    initModulesOnce();
  }

  // 3. Загрузка / фоновая валидация актуальности через ETag (Promise.allSettled)
  const [heroesRes, itemsRes] = await Promise.allSettled([fetchHeroes(), fetchItems()]);
  const failed = [];
  let updated = false;

  if (heroesRes.status === 'fulfilled') {
    const freshHeroes = Array.isArray(heroesRes.value) ? heroesRes.value : [];
    if (!hasCache || JSON.stringify(freshHeroes) !== JSON.stringify(state.heroes)) {
      state.heroes = freshHeroes;
      state.heroById = new Map(state.heroes.map((h) => [h.id, h]));
      if (!state.heroId) state.heroId = state.heroes[0]?.id || null;
      updated = true;
    }
  } else {
    console.error('fetchHeroes error:', heroesRes.reason);
    if (!hasCache) failed.push('герои');
  }

  if (itemsRes.status === 'fulfilled') {
    const freshItems = Array.isArray(itemsRes.value) ? itemsRes.value : [];
    if (!hasCache || JSON.stringify(freshItems) !== JSON.stringify(state.items)) {
      state.items = freshItems;
      state.itemById = new Map(state.items.map((i) => [i.id, i]));
      updated = true;
    }
  } else {
    console.error('fetchItems error:', itemsRes.reason);
    if (!hasCache) failed.push('предметы');
  }

  if (failed.length) {
    showToast(`Не загрузились: ${failed.join(', ')}. Экраны работают без этих данных.`);
  }

  if (!modulesInitialized) {
    initModulesOnce();
  } else if (updated) {
    // Если данные обновились в фоне — мягко синхронизируем UI
    try {
      initTierList(state.heroes);
      initDrafter(state.heroes);
      applyCatalogFilters();
      refreshLab();
    } catch (e) {
      console.warn('Background sync error:', e);
    }
  }
}

// --- фоновое видео -------------------------------------------------------------

function scheduleIdleBackgroundVideo() {
  const launch = () => {
    try {
      initBackgroundVideo();
    } catch (err) {
      console.error('Error initializing background video:', err);
    }
  };

  if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
    window.requestIdleCallback(launch, { timeout: 1500 });
  } else {
    setTimeout(launch, 200);
  }
}

function initBackgroundVideo() {
  const vid = document.getElementById('hud-bg-video');
  if (!vid) return;

  vid.muted = true;
  vid.defaultMuted = true;

  const tryPlay = () => {
    if (vid.paused) {
      vid.play().catch(() => {});
    }
  };

  tryPlay();

  // Гарантированный запуск при первом взаимодействии пользователя, если браузер заблокировал autoplay
  window.addEventListener('click', tryPlay, { once: true });
  window.addEventListener('keydown', tryPlay, { once: true });

  // Бесшовный цикл: если событие ended сработает, моментально перезапускаем
  vid.addEventListener('ended', () => {
    vid.currentTime = 0;
    tryPlay();
  });
}

// --- карта ------------------------------------------------------------------------

async function loadMap() {
  try {
    await loadMapView();
  } catch (e) {
    showToast(`Карта недоступна: ${e.message}`);
  }
}

// --- табы ---------------------------------------------------------------------

function initTabs() {
  document.querySelectorAll('.hud-tab').forEach((btn) => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });
  document.querySelectorAll('.hub-card').forEach((card) => {
    card.addEventListener('click', () => activateTab(card.dataset.goto));
  });
  document.addEventListener('goto-lab-hero', (e) => {
    activateTab('lab');
    const select = $('#lab-hero');
    if (select && e.detail) {
      select.value = e.detail;
      select.dispatchEvent(new Event('change'));
    }
  });
}

function activateTab(name) {
  document.querySelectorAll('.hud-tab').forEach((t) =>
    t.classList.toggle('tab-active', t.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach((p) => {
    const active = p.id === `tab-${name}`;
    if (active) {
      // Перезапуск entry-анимации: класс .hidden вешается/снимается мгновенно,
      // а каскадное появление с карточками внутри запускается через CSS var(--index)
      p.classList.remove('hidden');
      const items = p.querySelectorAll('.hub-card, .tier-row, .item-card, .slot-group-card, .panel, .match-cards > .panel');
      items.forEach((item, idx) => {
        item.classList.add('cascade-item');
        item.style.setProperty('--index', idx);
      });
    } else {
      p.classList.add('hidden');
    }
  });
  const volWidget = document.getElementById('volume-widget');
  if (volWidget) {
    volWidget.classList.toggle('hidden', name !== 'lab');
  }
  window.dispatchEvent(new Event('resize')); // канвасы пересчитать
}

// --- хаб ----------------------------------------------------------------------

// Сводка для ХАБа (TZ V2.0 4.3): «Последний матч» + «Сегодня хорошо заходят».
// Экранирование в одну строку — хелпер для подстановки данных сервера в HTML.
function escHtml(s) {
  return String(s ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function gradeChipClass(grade) {
  const g = String(grade || 'C').toLowerCase().replace('+', '-plus');
  return `grade-${g}`;
}

function findHero(id) {
  if (!id) return null;
  const strId = String(id).toLowerCase();
  if (state.heroById?.has(strId)) return state.heroById.get(strId);
  const numId = Number(id);
  const heroes = Array.isArray(state.heroes) ? state.heroes : [];
  for (const h of heroes) {
    if (String(h.id).toLowerCase() === strId) return h;
    if (h.api_id != null && h.api_id === numId) return h;
    if (String(h.name_en || '').toLowerCase() === strId) return h;
  }
  return null;
}

// Блок «Последний матч»: детальный обзор с иконкой, KDA, результатом и оценкой
async function renderHubLastReview() {
  const box = $('#hub-last-review');
  if (!box) return;

  let reviews = [];
  try {
    const res = await fetchMatchReviews(10);
    if (Array.isArray(res)) reviews = res;
  } catch (_) {}

  let recentMatches = [];
  try {
    const resRecent = await fetchRecentMatches();
    if (resRecent && Array.isArray(resRecent.matches)) {
      recentMatches = resRecent.matches;
    }
  } catch (_) {}

  let matchId = null;
  let isReviewed = false;
  let review = null;
  let recentMatch = null;
  let hero = null;
  let heroName = '—';
  let isWin = false;
  let kdaStr = '—';
  let mistake = '';
  let grade = null;

  if (recentMatches.length > 0) {
    recentMatch = recentMatches[0];
    matchId = String(recentMatch.match_id ?? '');
    review = reviews.find((r) => String(r.match_id) === matchId);
    isReviewed = !!review;

    hero = findHero(recentMatch.hero_id) || (review ? findHero(review.hero_id) : null);
    heroName = hero?.name || (recentMatch.hero_id ? `Герой #${recentMatch.hero_id}` : '—');
    isWin = Number(recentMatch.player_match_outcome) === 1 || review?.result === 'win';

    const k = recentMatch.player_kills ?? 0;
    const d = recentMatch.player_deaths ?? 0;
    const a = recentMatch.player_assists ?? 0;
    kdaStr = `${k} / ${d} / ${a}`;

    if (isReviewed) {
      grade = review.performance_grade || 'C';
      const km = review.key_mistake || {};
      mistake = km.event || km.why_fatal || '';
      if (review.kda && typeof review.kda === 'string') {
        kdaStr = review.kda.replace(/\//g, ' / ');
      }
    }
  } else if (reviews.length > 0) {
    review = reviews[0];
    matchId = String(review.match_id ?? '');
    isReviewed = true;
    hero = findHero(review.hero_id);
    heroName = hero?.name || review.hero_id || '—';
    isWin = review.result === 'win';
    grade = review.performance_grade || 'C';
    const km = review.key_mistake || {};
    mistake = km.event || km.why_fatal || '';

    if (review.kda) {
      if (typeof review.kda === 'string') {
        kdaStr = review.kda.replace(/\//g, ' / ');
      } else if (typeof review.kda === 'object') {
        const { kills = 0, deaths = 0, assists = 0 } = review.kda;
        kdaStr = `${kills} / ${deaths} / ${assists}`;
      }
    }
  }

  if (!matchId && !review && !recentMatch) {
    box.innerHTML = '<span class="hub-empty-hint">Разборов ещё нет — откройте <b>Deep Match</b> и разберите первый матч.</span>';
    box.classList.add('empty');
    return;
  }

  box.classList.remove('empty');
  const portrait = hero?.portrait || (hero?.id ? `./assets/heroes/${hero.id}.webp` : './assets/heroes/abrams.webp');
  const fallbackLetters = escHtml(heroName.slice(0, 2).toUpperCase());

  box.innerHTML = `
    <div class="hub-last-match-card" data-match-id="${escHtml(matchId)}" title="Открыть разбор матча #${escHtml(matchId)}">
      <div class="hlm-portrait-wrap">
        <img class="hlm-portrait" src="${escHtml(portrait)}" alt="${escHtml(heroName)}"
             onerror="this.style.display='none';this.nextElementSibling?.classList.remove('hidden');">
        <span class="hlm-portrait-fallback hidden">${fallbackLetters}</span>
      </div>
      <div class="hlm-details">
        <div class="hlm-row-top">
          ${isReviewed && grade ? `<span class="hub-grade-chip ${escHtml(gradeChipClass(grade))}" title="Оценка тренера">${escHtml(grade)}</span>` : ''}
          <span class="hlm-hero-name">${escHtml(heroName)}</span>
          <span class="hlm-result ${isWin ? 'ok' : 'bad'}">${isWin ? 'Победа' : 'Поражение'}</span>
          <span class="hlm-kda"><span class="hlm-kda-label">KDA:</span> ${escHtml(kdaStr)}</span>
        </div>
        ${isReviewed && mistake
          ? `<div class="hlm-mistake" title="${escHtml(mistake)}"><span class="hlm-mistake-label">Ошибка:</span> ${escHtml(mistake)}</div>`
          : (!isReviewed
              ? `<div class="hlm-unreviewed"><span class="hlm-unreviewed-badge">Матч не разобран</span> <span class="hlm-cta">Разобрать в Deep Match →</span></div>`
              : '')
        }
      </div>
    </div>`;

  const card = box.querySelector('.hub-last-match-card');
  if (card && matchId) {
    card.addEventListener('click', () => {
      const tabBtn = document.querySelector('.hud-nav [data-tab="match"]');
      if (tabBtn) tabBtn.click();
      const matchInput = document.getElementById('match-id-input');
      if (matchInput) {
        matchInput.value = matchId;
        matchInput.dispatchEvent(new Event('input'));
      }
    });
  }
}

// Блок «Сегодня хорошо заходят»: топ-5 героев списком с компактными иконками
function renderHubMetaPicks() {
  const box = $('#hub-meta-picks');
  if (!box) return;
  const ranked = (Array.isArray(state.heroes) ? state.heroes : [])
    .filter((h) => !h.in_development && typeof h.winrate === 'number' && Number.isFinite(h.winrate))
    .slice()
    .sort((a, b) => (b.winrate || 0) - (a.winrate || 0))
    .slice(0, 5);
  if (!ranked.length) {
    box.innerHTML = 'Мета ещё не загружена — данные героев недоступны.';
    box.classList.add('empty');
    return;
  }
  box.classList.remove('empty');
  box.innerHTML = `
    <div class="hub-meta-list">
      ${ranked.map((h, idx) => {
        const roles = (h.roles || []).map((r) => ROLE_LABEL[r] || r).join(' / ') || '—';
        const portrait = h.portrait || `./assets/heroes/${h.id}.webp`;
        const wr = (h.winrate * 100).toFixed(1);
        const pr = h.pickrate != null ? (h.pickrate * 100).toFixed(1) : null;
        return `
          <div class="hub-meta-row" data-hero-id="${escHtml(h.id)}" title="Перейти к ${escHtml(h.name)} в тир-листе">
            <span class="hmr-num">${idx + 1}</span>
            <div class="hmr-icon-wrap">
              <img class="hmr-icon" src="${escHtml(portrait)}" alt="${escHtml(h.name)}"
                   onerror="this.style.display='none';this.nextElementSibling?.classList.remove('hidden');">
              <span class="hmr-icon-fallback hidden">${escHtml(h.name.slice(0, 2).toUpperCase())}</span>
            </div>
            <span class="hmr-name">${escHtml(h.name)}</span>
            <span class="hmr-role">${escHtml(roles)}</span>
            <div class="hmr-stats">
              <span class="hmr-wr">${wr}%</span>
              ${pr ? `<span class="hmr-pr">${pr}% пик</span>` : ''}
            </div>
          </div>
        `;
      }).join('')}
    </div>`;

  box.querySelectorAll('.hub-meta-row').forEach((row) => {
    row.addEventListener('click', () => {
      const tabBtn = document.querySelector('.hud-nav [data-tab="tier"]');
      if (tabBtn) tabBtn.click();
    });
  });
}

function renderHubSummary() {
  renderHubLastReview();
  renderHubMetaPicks();
}

async function initHub() {
  try {
    const profile = await fetchProfile();
    $('#hub-nick').textContent = profile.player_name || 'Deadlock Operator';
    $('#hub-rank-badge').textContent =
      `${profile.rank_tier || 'Ascendant'} ${['I', 'II', 'III', 'IV', 'V', 'VI'][((profile.rank_subrank || 1) - 1) % 6]}`;
    $('#hub-mmr').textContent = `MMR ${profile.mmr ?? 1600}`;
    // TZ v1.4 D2: аватар из кэша (если подтянут) вместо букв.
    const acc = String(profile.account_id || '');
    if (acc && profile.avatar_url) {
      $('#hub-avatar').textContent = '';
      $('#hub-avatar').style.background =
        `center/cover no-repeat url(${profile.avatar_url})`;
    } else {
      $('#hub-avatar').style.background = '';
      $('#hub-avatar').textContent =
        (profile.player_name || 'DO').slice(0, 2).toUpperCase();
    }
  } catch { /* профиль ещё не создан — дефолты уже в разметке */ }
  const btn = $('#hub-refresh');
  if (btn && !btn.dataset.wired) {
    btn.dataset.wired = '1';
    btn.addEventListener('click', () => initHub());
  }
  refreshBuildsCount();
  renderHubSummary();
}

async function refreshBuildsCount() {
  try {
    const builds = await fetchBuilds();
    const el = $('#hub-builds-count');
    if (el) el.textContent = `Билдов сохранено: ${builds.length}`;
  } catch { /* не критично */ }
}

// --- лаборатория ------------------------------------------------------------------

function syncCustomHeroTrigger(heroId) {
  const hero = state.heroById?.get(heroId);
  if (!hero) return;
  const portrait = $('#hero-trigger-portrait');
  const nameEl = $('#hero-trigger-name');
  const roleEl = $('#hero-trigger-role');
  if (portrait) portrait.src = `./assets/heroes/${hero.id}.webp`;
  if (nameEl) nameEl.textContent = hero.name;
  if (roleEl) roleEl.textContent = (hero.roles || []).map((r) => ROLE_LABEL[r] || r).join(' / ');
}

function initCustomHeroDropdown(heroes) {
  const trigger = $('#hero-select-trigger');
  const dropdown = $('#hero-select-dropdown');
  const search = $('#hero-search-input');
  const list = $('#hero-options-list');
  if (!trigger || !dropdown || !list) return;

  function renderList(filter = '') {
    const q = filter.toLowerCase().trim();
    const visible = heroes.filter((h) =>
      !q || h.name.toLowerCase().includes(q) || (h.name_en && h.name_en.toLowerCase().includes(q))
    );
    list.replaceChildren(
      ...visible.map((h) => {
        const item = el('div', {
          class: `hero-option ${h.id === state.heroId ? 'selected' : ''}`,
          onclick: () => {
            state.heroId = h.id;
            const sel = $('#lab-hero');
            if (sel) sel.value = h.id;
            syncCustomHeroTrigger(h.id);
            dropdown.classList.add('hidden');
            trigger.setAttribute('aria-expanded', 'false');
            onHeroChange({ target: { value: h.id } });
          },
        },
          el('img', {
            class: 'hero-opt-img',
            src: `./assets/heroes/${h.id}.webp`,
            alt: h.name,
            onerror: (e) => { e.target.style.display = 'none'; },
          }),
          el('div', { class: 'hero-opt-info' },
            el('span', { class: 'hero-opt-name' }, h.name),
            el('span', { class: 'hero-opt-role' }, (h.roles || []).map((r) => ROLE_LABEL[r] || r).join(' / ')),
          ),
        );
        return item;
      })
    );
  }

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = !dropdown.classList.contains('hidden');
    dropdown.classList.toggle('hidden', isOpen);
    trigger.setAttribute('aria-expanded', String(!isOpen));
    if (!isOpen) {
      if (search) { search.value = ''; search.focus(); }
      renderList();
    }
  });

  if (search) {
    search.addEventListener('input', () => renderList(search.value));
    search.addEventListener('click', (e) => e.stopPropagation());
  }

  document.addEventListener('click', (e) => {
    if (!dropdown.contains(e.target) && !trigger.contains(e.target)) {
      dropdown.classList.add('hidden');
      trigger.setAttribute('aria-expanded', 'false');
    }
  });

  renderList();
}

function initLab() {
  const select = $('#lab-hero');
  const availableHeroes = state.heroes.filter((h) => !h.in_development);

  select.replaceChildren(
    ...availableHeroes.map((h) =>
      el('option', { value: h.id },
        `${h.name} — ${h.roles.map((r) => ROLE_LABEL[r] || r).join('/')}`)),
  );
  if (!select.options.length) {
    select.append(el('option', { value: '' }, '— герои не загружены —'));
  }
  select.value = state.heroId;
  select.addEventListener('change', onHeroChange);

  initCustomHeroDropdown(availableHeroes);
  onHeroChange({ target: select });

  $('#btn-reset-build').addEventListener('click', () => {
    state.slots = emptySlots();
    updateSlotBadge();
    refreshLab();
  });
  $('#item-search').addEventListener('input', applyCatalogFilters);
  $('#filter-category').addEventListener('change', applyCatalogFilters);
  $('#filter-tier').addEventListener('change', applyCatalogFilters);
  applyCatalogFilters();
  updateSlotBadge();
}

function onHeroChange(e) {
  state.heroId = e.target.value || null;
  syncCustomHeroTrigger(state.heroId);
  const hero = state.heroById.get(state.heroId);
  $('#lab-hero-signature').textContent = hero?.signature || '';
  refreshLab();
}

// Живой бейдж заполненных слотов 0/12
function updateSlotBadge() {
  const badge = $('#slot-count');
  if (!badge) return;
  const filled = equippedItems(state.slots).length;
  badge.textContent = `${filled}/12`;
  badge.classList.toggle('full', filled === 12);
}

// Порог стоимости по тирам (T1 ~500, T2 ~1250, T3 ~3000, T4 ~6200, T5 ~9999).
const TIER_COST = { 1: 500, 2: 1250, 3: 3000, 4: 6200, 5: 9999 };

function matchesQuery(item, q) {
  if (!q) return true;
  if (item.name.toLowerCase().includes(q) || (item.name_en && item.name_en.toLowerCase().includes(q))) {
    return true;
  }
  const tMatch = q.match(/^(?:t|тир|tier)\s*(\d)$/i);
  if (tMatch) return item.tier === Number(tMatch[1]);
  if (/^\d+$/.test(q)) {
    const n = Number(q);
    if (item.tier === n) return true;
    if (item.cost === n) return true;
    const tierByCost = Object.entries(TIER_COST).find(([, c]) => c === n);
    if (tierByCost && item.tier === Number(tierByCost[0])) return true;
    return false;
  }
  return false;
}

function applyCatalogFilters() {
  const q = ($('#item-search').value || '').toLowerCase().trim();
  const cat = $('#filter-category').value;
  const tier = $('#filter-tier').value;
  const visible = state.items.filter((it) =>
    (!cat || it.category === cat) &&
    (!tier || String(it.tier) === tier) &&
    matchesQuery(it, q));
  const equippedSet = new Set(equippedItems(state.slots));
  renderCatalog($('#item-grid'), visible, { onEquip: equipItem, equippedIds: equippedSet });
  const counter = $('#catalog-count');
  if (counter) {
    const emptyHint = state.items.length === 0
      ? 'Каталог пуст — данные не загрузились'
      : `Найдено: ${visible.length} из ${state.items.length}`;
    counter.textContent = emptyHint;
    counter.classList.toggle('empty', visible.length === 0);
  }
}

function equipItem(item) {
  const target = autoTarget(state.slots, item);
  if (!target) {
    setInlineError('#lab-error', 'Все 12 слотов билда заняты — сними предмет или сбрось билд.');
    return;
  }
  const err = validateEquip(state.slots, item, target.index);
  if (err) {
    setInlineError('#lab-error', err);
    return;
  }
  setInlineError('#lab-error', '');
  soundEngine.playBuySound();
  const prevId = state.slots[target.index];
  state.slots[target.index] = item.id;
  updateSlotBadge();
  refreshLab();
  if (target.isUpgrade && prevId) {
    const prevItem = state.itemById.get(prevId);
    showToast(`Улучшено: «${prevItem ? prevItem.name : prevId}» → «${item.name}» (заменено в слоте ${target.index + 1})`);
    const slotEl = $(`#slots-universal .slot:nth-child(${target.index + 1})`);
    if (slotEl) {
      slotEl.classList.remove('upgrade-spark');
      void slotEl.offsetWidth; // reflow trigger
      slotEl.classList.add('upgrade-spark');
    }
  }
}

function unequipItem(idx) {
  state.slots[idx] = null;
  updateSlotBadge();
  refreshLab();
}

async function _executeRefreshLab() {
  renderSlotBoard(state.slots, state.itemById, { onUnequip: unequipItem });
  const equippedSet = new Set(equippedItems(state.slots));
  updateCatalogEquipped($('#item-grid'), equippedSet);
  const hero = state.heroById.get(state.heroId);
  if (!hero) {
    // Нет героя (справочник не загрузился) — панель статов в базовое состояние,
    // чтобы dl#stats-panel не оставался от прошлого рендера.
    try { renderStats(computeStats(null, [])); } catch { /* панель уже пуста */ }
    return;
  }
  const items = equippedItems(state.slots).map((id) => state.itemById.get(id));

  // мгновенный локальный пересчёт
  const local = computeStats(hero, items);
  renderStats(local, computeStats(hero, []));

  // сервер — источник истины (async, без блокировки UI)
  try {
    const server = await postCalc(state.heroId, equippedItems(state.slots));
    renderStats(server, computeStats(hero, []));
  } catch { /* локальный пересчёт уже показан */ }
}

// Дебаунс и батчинг через requestAnimationFrame: предотвращает каскадные перерисовки
// слотов и пересчеты статов при пакетном заполнении слотов или смене героя
export const refreshLab = createDebouncedStatsCalculator(_executeRefreshLab);

// --- менеджер билдов (SQLite) -------------------------------------------------------

async function initBuildManager() {
  $('#btn-save-build').addEventListener('click', saveCurrentBuild);
  $('#btn-copy-code').addEventListener('click', copyBuildCode);
  $('#btn-import-code').addEventListener('click', importBuildCode);
  await refreshBuildList();
}

async function refreshBuildList() {
  try {
    const builds = await fetchBuilds();
    renderBuildList(builds, state.heroById, state.itemById, {
      onLoad: loadBuild,
      onDelete: removeBuild,
    });
    refreshBuildsCount();
  } catch (e) {
    showToast(`Билды недоступны: ${e.message}`);
  }
}

async function saveCurrentBuild() {
  const title = ($('#build-name').value || '').trim() || `Билд ${new Date().toLocaleDateString('ru-RU')}`;
  if (!state.heroId) return;
  try {
    const items = equippedItems(state.slots);
    await postSaveBuild({
      hero_id: state.heroId,
      title,
      description: '',
      weapon_slots: items.slice(0, 4),
      vitality_slots: items.slice(4, 8),
      spirit_slots: items.slice(8, 12),
      flex_slots: [],
    });
    $('#build-name').value = '';
    showToast('Билд сохранён в базу');
    await refreshBuildList();
  } catch (e) {
    showToast(`Не сохранено: ${e.message}`, true);
  }
}

function loadBuild(b) {
  const allIds = (b.weapon_slots || []).concat(
    b.vitality_slots || [],
    b.spirit_slots || [],
    b.flex_slots || []
  ).filter(Boolean);

  const slots = new Array(12).fill(null);
  for (let i = 0; i < Math.min(12, allIds.length); i++) {
    slots[i] = allIds[i];
  }
  state.slots = slots;
  updateSlotBadge();
  if (b.hero_id && state.heroById.has(b.hero_id)) {
    state.heroId = b.hero_id;
    $('#lab-hero').value = b.hero_id;
    onHeroChange({ target: $('#lab-hero') });
  }
  activateTab('lab');
  showToast(`Билд «${b.title}» загружен`);
}

async function removeBuild(b) {
  try {
    await apiDeleteBuild(b.id);
    showToast('Билд удалён');
    await refreshBuildList();
  } catch (e) {
    showToast(`Не удалено: ${e.message}`, true);
  }
}

async function copyBuildCode() {
  try {
    const items = equippedItems(state.slots);
    const code = btoa(JSON.stringify({ items, hero: state.heroId }));
    const ok = await copyToClipboard(code);
    showToast(ok ? 'Код билда скопирован' : 'Код: ' + code, !ok);
  } catch (e) {
    showToast(`Экспорт не удался: ${e.message}`, true);
  }
}

async function importBuildCode() {
  const code = ($('#build-code').value || '').trim();
  if (!code) return;
  try {
    let importedIds = [];
    try {
      const parsed = JSON.parse(atob(code));
      if (Array.isArray(parsed)) importedIds = parsed;
      else if (parsed && Array.isArray(parsed.items)) importedIds = parsed.items;
      else if (parsed && typeof parsed === 'object') {
        importedIds = (parsed.weapon || []).concat(parsed.vitality || [], parsed.spirit || [], parsed.flex || []).filter(Boolean);
      }
    } catch {
      // Fallback на серверный импорт
      const s = await postImportBuild(code);
      importedIds = (s.weapon || []).concat(s.vitality || [], s.spirit || []).filter(Boolean);
    }
    const slots = new Array(12).fill(null);
    for (let i = 0; i < Math.min(12, importedIds.length); i++) {
      slots[i] = importedIds[i];
    }
    state.slots = slots;
    updateSlotBadge();
    refreshLab();
    showToast('Билд импортирован');
  } catch (e) {
    showToast(`Импорт не удался: ${e.message}`, true);
  }
}

// --- профиль (TZ B4: автодетект + ручной режим в аккордеоне) ------------------

async function loadProfile() {
  try {
    const p = await fetchProfile();
    $('#profile-name').value = p.player_name || '';
    $('#profile-steam').value = p.steam_id || '';
    $('#profile-account').value = p.account_id || '';
    $('#profile-rank').value = p.rank_tier || 'Ascendant';
    $('#profile-mmr').value = p.mmr ?? 1600;
    renderIdCard(p.steam_autodetect, p.account_id, p.avatar_url);
    const hubAv = $('#hub-avatar');
    if (hubAv) {
      if (p.avatar_url) {
        hubAv.textContent = '';
        hubAv.style.background = `center/cover no-repeat url(${p.avatar_url})`;
      } else {
        hubAv.style.background = '';
        hubAv.textContent = (p.player_name || 'DO').slice(0, 2).toUpperCase();
      }
    }
  } catch { /* дефолты в разметке */ }

  const detectBtn = $('#btn-detect-profile');
  if (detectBtn && !detectBtn.dataset.wired) {
    detectBtn.dataset.wired = '1';
    detectBtn.addEventListener('click', detectProfile);
  }

  // TZ v1.4 D2: автоподтягивание ника, аватара и ранга (одна кнопка).
  const steamBtn = $('#btn-steam-info');
  if (steamBtn && !steamBtn.dataset.wired) {
    steamBtn.dataset.wired = '1';
    steamBtn.addEventListener('click', pullSteamInfo);
  }

  $('#btn-save-profile').addEventListener('click', async () => {
    try {
      await postProfile({
        player_name: $('#profile-name').value,
        steam_id: $('#profile-steam').value,
        account_id: $('#profile-account').value,
        rank_tier: $('#profile-rank').value,
        mmr: Number($('#profile-mmr').value) || 1600,
      });
      showToast('Профиль сохранён');
      initHub();
    } catch (e) {
      showToast(`Не сохранено: ${e.message}`, true);
    }
  });
}

function renderIdCard(autodetect, accountId, avatarUrl) {
  const status = $('#profile-detect-status');
  const idtext = $('#profile-idtext');
  const avatar = $('#profile-avatar');
  if (accountId) {
    status.textContent = 'Профиль активен';
    status.classList.remove('muted');
    idtext.textContent = `Account ID: ${accountId}`;
  } else if (autodetect) {
    status.textContent = `Steam: ${autodetect.persona_name || autodetect.account_name || 'найден'}`;
    status.classList.remove('muted');
    idtext.textContent = `Account ID: ${autodetect.account_id} (${autodetect.steam_id32})`;
  } else {
    status.textContent = 'Steam не найден — заполните ID вручную в «Расширенных»';
    status.classList.add('muted');
    idtext.textContent = '';
  }
  if (avatar) {
    if (avatarUrl) {
      avatar.style.background = `center/cover no-repeat url(${avatarUrl})`;
      avatar.textContent = '';
    } else {
      avatar.style.background = '';
      const name = ($('#profile-name').value || 'DO').trim();
      avatar.textContent = name.slice(0, 2).toUpperCase();
    }
  }
}

// TZ v1.4 D2: автоподтягивание Steam-профиля (ник+аватар+ранг/плашка).
async function pullSteamInfo() {
  const btn = $('#btn-steam-info');
  const status = $('#profile-detect-status');
  btn.disabled = true;
  btn.textContent = 'Тяну из Steam…';
  try {
    const info = await fetchProfileSteamInfo();
    if (info.persona_name) {
      $('#profile-name').value = info.persona_name;
    }
    renderIdCard({ persona_name: info.persona_name },
      String(info.account_id || ''), info.avatar_url);
    const hubAv = $('#hub-avatar');
    if (hubAv) {
      if (info.avatar_url) {
        hubAv.textContent = '';
        hubAv.style.background = `center/cover no-repeat url(${info.avatar_url})`;
      } else {
        hubAv.style.background = '';
        hubAv.textContent = (info.persona_name || $('#profile-name').value || 'DO').slice(0, 2).toUpperCase();
      }
    }
    const rank = info.rank;
    if (rank && !rank.rank_hidden && rank.rank > 0) {
      if (rank.badge) {
        const tier = rank.badge.split(' ')[0];
        const sel = $('#profile-rank');
        if (sel && [...sel.options].some((o) => o.value === tier || o.textContent === tier)) {
          sel.value = tier;
        }
      }
      showToast(`Ранг подтянут: ${rank.badge || rank.rank}`);
    }
    if (rank && rank.rank_hidden) {
      const box = $('#rank-hidden-note');
      if (box) box.classList.remove('hidden');
    }
    initHub();
  } catch (e) {
    status.textContent = `Steam-инфо: ${e.message}`;
    status.classList.add('muted');
    showToast(`Не подтянуто: ${e.message}`, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Подтянуть ник и ранг';
  }
}

async function detectProfile() {  const btn = $('#btn-detect-profile');
  const status = $('#profile-detect-status');
  btn.disabled = true;
  btn.textContent = 'Ищу…';
  try {
    const d = await fetchProfileDetect();
    $('#profile-steam').value = d.steam_id32 || '';
    $('#profile-account').value = d.account_id || '';
    if (!($('#profile-name').value || '').trim() && d.persona_name) {
      $('#profile-name').value = d.persona_name;
    }
    // автосохранение выбора — Deep Match сразу находит «себя»
    await postProfile({
      steam_id: d.steam_id32 || '',
      account_id: d.account_id || '',
      ...(d.persona_name && !($('#profile-name').value || '').trim()
        ? { player_name: d.persona_name } : {}),
    });
    renderIdCard(d, d.account_id, d.avatar_url);
    showToast(`Профиль найден: ${d.persona_name || d.account_name || d.account_id}`);
    initHub();
  } catch (e) {
    status.textContent = `Не найдено: ${e.message}`;
    status.classList.add('muted');
    showToast('Steam не определён — используйте ручной ввод', true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Найти мой профиль';
  }
}

// --- AI конфиг ---

async function loadAIConfig() {
  const statusCard = $('#ai-status-card');
  const statusText = $('#ai-status-text');
  const keyInput = $('#ai-key');
  const baseUrlInput = $('#ai-base-url');
  const modelSelect = $('#ai-model-select');
  const reasoningSelect = $('#ai-reasoning-select');
  const refreshModelsBtn = $('#btn-refresh-models');
  let cfg = null;
  try {
    cfg = await fetchAIConfig();
  } catch { /* дефолты */ }

  const hasKey = Boolean(cfg?.has_key);
  const fromEnv = cfg?.key_source === 'env';
  if (statusCard) statusCard.classList.remove('hidden');

  const curModel = cfg?.model_name || 'deepseek-v4.1-flash';
  const curReasoning = cfg?.reasoning_effort || 'low';
  if (reasoningSelect && curReasoning) {
    reasoningSelect.value = curReasoning;
  }
  if (fromEnv) {
    if (statusText) statusText.textContent = `AI активен (env-ключ) · ${curModel} · CoT: ${curReasoning}`;
    if (statusCard) statusCard.className = 'ai-status-card ok';
  } else if (hasKey) {
    if (statusText) statusText.textContent = `AI активен · ${curModel} · CoT: ${curReasoning} · ${cfg.api_key_masked}`;
    if (statusCard) statusCard.className = 'ai-status-card ok';
  } else {
    if (statusText) statusText.textContent = 'AI отключён — укажите API-ключ (роутер/OpenAI)';
    if (statusCard) statusCard.className = 'ai-status-card';
  }
  if (keyInput) keyInput.placeholder = (cfg && cfg.api_key_masked) || 'Введите API-ключ (напр. sk-...)';
  if (baseUrlInput && cfg?.base_url) baseUrlInput.value = cfg.base_url;

  // Обновление опции текущей модели
  if (modelSelect) {
    let exists = false;
    for (const opt of modelSelect.options) {
      if (opt.value === curModel) {
        exists = true;
        break;
      }
    }
    if (!exists && curModel) {
      const opt = document.createElement('option');
      opt.value = curModel;
      opt.textContent = curModel;
      modelSelect.appendChild(opt);
    }
    modelSelect.value = curModel;
  }

  const loadModelsList = async () => {
    if (!refreshModelsBtn) return;
    refreshModelsBtn.disabled = true;
    refreshModelsBtn.textContent = '⏳';
    try {
      const res = await fetchAIModels();
      const models = res.models || [];
      if (models.length && modelSelect) {
        const cur = res.current || modelSelect.value;
        modelSelect.innerHTML = '';
        models.forEach((m) => {
          const opt = document.createElement('option');
          opt.value = m;
          opt.textContent = m;
          if (m === cur) opt.selected = true;
          modelSelect.appendChild(opt);
        });
        showToast(`Выгружено ${models.length} проверенных рабочих моделей`);
      } else {
        showToast('Рабочие модели не найдены или провайдер недоступен', true);
      }
    } catch (e) {
      showToast(`Ошибка загрузки моделей: ${e.message}`, true);
    } finally {
      refreshModelsBtn.disabled = false;
      refreshModelsBtn.textContent = '🔄 Модели';
    }
  };

  if (!loadAIConfig._bound) {
    loadAIConfig._bound = true;

    refreshModelsBtn?.addEventListener('click', loadModelsList);

    $('#btn-save-ai')?.addEventListener('click', async () => {
      const key = keyInput?.value?.trim();
      const baseUrl = baseUrlInput?.value?.trim();
      const selectedModel = modelSelect?.value?.trim();
      const selectedReasoning = reasoningSelect?.value;
      try {
        const payload = {};
        if (key) payload.api_key = key;
        if (baseUrl !== undefined) payload.base_url = baseUrl || null;
        if (selectedModel) payload.model_name = selectedModel;
        if (selectedReasoning) payload.reasoning_effort = selectedReasoning;

        // Если указан роутер или ключ от стороннего сервиса
        if (baseUrl && baseUrl.includes('/v1')) {
          payload.provider = 'openai';
        } else if (key && key.startsWith('sk-')) {
          payload.provider = 'openai';
          payload.base_url = baseUrl || 'http://127.0.0.1:8089/v1';
        }

        await postAIConfig(payload);
        if (keyInput) keyInput.value = '';
        showToast('Настройки AI сохранены');
        await loadAIConfig();
      } catch (e) {
        showToast(`Не сохранено: ${e.message}`, true);
      }
    });

    $('#btn-test-ai')?.addEventListener('click', async () => {
      const status = $('#ai-status');
      if (!status) return;
      status.className = 'ai-status';
      status.textContent = 'Проверяю соединение с нейросетью…';
      try {
        const r = await postAITest();
        if (r.ok) {
          status.className = 'ai-status ok';
          status.textContent = `✓ OK — ${r.detail || 'соединение установлено'} (${r.latency_ms} мс)`;
        } else {
          status.className = 'ai-status err';
          status.textContent = `✗ ${r.detail || 'ошибка соединения'}`;
        }
      } catch (e) {
        status.className = 'ai-status err';
        status.textContent = `✗ ${e.message}`;
      }
    });
  }
}

boot();
