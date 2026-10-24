// counterpicks.js — экран 7: контр-предметы по героям и матчапам.
// Импорт-безопасен: DOM трогается только внутри initCounterpicks.
//
// Данные: /api/counterpicks (датасет data/counterpicks.json, собран
// scripts/build_counterpicks.py с deadlockitembuilder.com и переведён на
// русский с официальной терминологией deadlock-api).

import { fetchCounterpicks } from './api.js';

const STAGE_LABEL = { early: 'Ранняя игра', mid: 'Середина игры', late: 'Поздняя игра' };
const STAGE_RANGE = { early: '0–10 мин', mid: '10–20 мин', late: '20+ мин' };

let data = null;          // Counterpicks с сервера
let heroById = new Map();
let itemById = new Map();
let selection = [];       // выбранные герои-противники (id, до 6)
let activeHero = null;    // открытый гайд героя
let activeItem = null;    // открытый гайд предмета
let tab = 'heroes';       // heroes | items | team
let dom = {};
let loadPromise = null;

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// **жирный** из источника уже приведён к <b>; остальное экранируется.
function rich(text) {
  return String(text ?? '')
    .split(/(<b>.*?<\/b>)/g)
    .map((part) => (/^<b>/.test(part)
      ? `<b>${esc(part.slice(3, -4))}</b>`
      : esc(part)))
    .join('');
}

function portrait(hero, cls = 'cp-portrait') {
  if (!hero || !hero.portrait) return `<div class="${cls} cp-portrait-empty">${esc(hero ? hero.name[0] : '?')}</div>`;
  return `<div class="${cls}"><img src="${esc(hero.portrait)}" alt="${esc(hero.name)}" loading="lazy"></div>`;
}

function itemImage(item, cls = 'cp-item-icon') {
  const src = item && item.image ? item.image : '';
  if (!src) return `<div class="${cls} cp-item-empty"></div>`;
  return `<div class="${cls}"><img src="${esc(src)}" alt="${esc(item.name)}" loading="lazy"></div>`;
}

// --- данные ----------------------------------------------------------------

function load() {
  if (!loadPromise) {
    loadPromise = fetchCounterpicks()
      .then((payload) => {
        data = payload;
        heroById = new Map((payload.heroes || []).map((h) => [h.id, h]));
        itemById = new Map((payload.items || []).map((i) => [i.id, i]));
        return payload;
      })
      .catch((err) => {
        loadPromise = null;   // даём повторить попытку после ошибки
        throw err;
      });
  }
  return loadPromise;
}

// --- рендер: список героев -------------------------------------------------

function renderHeroGrid() {
  if (!dom.grid) return;
  const query = (dom.search && dom.search.value || '').trim().toLowerCase();
  const heroes = (data.heroes || []).filter((h) => !query
    || h.name.toLowerCase().includes(query)
    || (h.name_en || '').toLowerCase().includes(query));

  if (!heroes.length) {
    dom.grid.innerHTML = '<p class="cp-empty">Ничего не найдено.</p>';
    return;
  }

  dom.grid.innerHTML = heroes.map((hero) => {
    const picked = selection.includes(hero.id);
    const top = (hero.top || []).slice(0, 4)
      .map((id) => itemById.get(id))
      .filter(Boolean);
    return `
      <div class="cp-hero-card ${picked ? 'cp-picked' : ''}">
        <button class="cp-hero-open" data-hero="${esc(hero.id)}" type="button">
          ${portrait(hero)}
          <div class="cp-hero-main">
            <div class="cp-hero-name">${esc(hero.name)}</div>
            <div class="cp-hero-roles">${esc((hero.roles || []).join(' · '))}</div>
          </div>
        </button>
        <div class="cp-hero-items">
          ${top.map((item) => itemImage(item, 'cp-chip-icon')).join('')}
        </div>
        <button class="cp-pick-mark" data-pick="${esc(hero.id)}" type="button"
                aria-pressed="${picked}" title="${picked ? 'Убрать из противников' : 'Добавить в противников'}">
          ${picked ? '✓' : '+'}
        </button>
      </div>`;
  }).join('');
}

function renderSelectionBar() {
  if (!dom.selectionBar) return;
  const chips = selection.map((id) => {
    const hero = heroById.get(id);
    if (!hero) return '';
    return `<span class="cp-selected-chip">
      <img src="${esc(hero.portrait)}" alt="${esc(hero.name)}">
      <span>${esc(hero.name)}</span>
      <button type="button" data-unpick="${esc(id)}" aria-label="Убрать ${esc(hero.name)}">×</button>
    </span>`;
  }).join('');
  dom.selectionBar.innerHTML = `
    <span class="cp-selected-label">Противники: ${selection.length} из 6</span>
    <div class="cp-selected-chips">${chips || '<span class="cp-selected-empty">выберите героев</span>'}</div>
    ${selection.length ? '<button type="button" class="btn-ghost cp-clear" id="cp-clear">Очистить</button>' : ''}`;
}

// --- рендер: гайд героя ----------------------------------------------------

function renderOffers(offers) {
  if (!offers || !offers.length) return '<span class="cp-none">—</span>';
  return offers.map((offer) => {
    const item = itemById.get(offer.item_id);
    return `<button type="button" class="cp-offer ${offer.alt ? 'cp-offer-alt' : ''}" data-item="${esc(offer.item_id)}">
      ${itemImage(item, 'cp-offer-icon')}
      <span class="cp-offer-text">
        <span class="cp-offer-name">${esc(item ? item.name : offer.item_id)}</span>
        <span class="cp-offer-label">${esc(offer.label)}${offer.alt ? ' · альт' : ''}</span>
      </span>
    </button>`;
  }).join('');
}

function renderHeroGuide(hero) {
  const glance = hero.glance || {};
  const stages = hero.stages || {};
  const problems = hero.problems || [];
  const helper = hero.helper || {};
  const sits = helper.sits || [];

  // Свежий герой может уже быть в игре, но без гайда на источнике
  // (Медсестра Хэрроу). Пустые панели не рисуем — честно говорим об этом.
  if (!(hero.top || []).length && !problems.length) {
    return `
  <div class="cp-guide">
    <header class="cp-guide-head">
      ${portrait(hero, 'cp-guide-portrait')}
      <div class="cp-guide-titles">
        <span class="cp-eyebrow">Как играть против</span>
        <h2 class="cp-guide-name">${esc(hero.name)}</h2>
        <div class="cp-guide-tags">
          ${(hero.roles || []).map((r) => `<span class="cp-tag">${esc(r)}</span>`).join('')}
          ${(hero.threats || []).map((t) => `<span class="cp-tag cp-tag-danger">${esc(t.label)}</span>`).join('')}
        </div>
        <p class="cp-guide-intro">${rich(hero.intro)}</p>
      </div>
      <button type="button" class="btn-ghost cp-guide-close" id="cp-guide-close">Закрыть</button>
    </header>
    <section class="cp-panel">
      <p class="cp-empty">Разбор по предметам для этого героя ещё не собран: гайда нет
        на источнике. Карточка героя уже доступна — его можно выбрать противником
        в разборе состава.</p>
    </section>
  </div>`;
  }

  return `
  <div class="cp-guide">
    <header class="cp-guide-head">
      ${portrait(hero, 'cp-guide-portrait')}
      <div class="cp-guide-titles">
        <span class="cp-eyebrow">Как играть против</span>
        <h2 class="cp-guide-name">${esc(hero.name)}</h2>
        <div class="cp-guide-tags">
          ${(hero.roles || []).map((r) => `<span class="cp-tag">${esc(r)}</span>`).join('')}
          ${(hero.threats || []).map((t) => `<span class="cp-tag cp-tag-danger">${esc(t.label)}</span>`).join('')}
        </div>
        <p class="cp-guide-intro">${rich(hero.intro)}</p>
      </div>
      <button type="button" class="btn-ghost cp-guide-close" id="cp-guide-close">Закрыть</button>
    </header>

    <div class="cp-glance">
      <div class="cp-glance-cell"><span>Главная угроза</span><p>${rich(glance.main_threat)}</p></div>
      <div class="cp-glance-cell"><span>Ваше окно</span><p>${rich(glance.your_opening)}</p></div>
      <div class="cp-glance-cell"><span>Приоритет покупки</span><p>${rich(glance.buy_priority)}</p></div>
    </div>

    <section class="cp-panel">
      <h3 class="cp-h3">Лучшие предметы</h3>
      <div class="cp-stages">
        ${['early', 'mid', 'late'].map((stage) => `
          <div class="cp-stage cp-stage-${stage}">
            <div class="cp-stage-head">
              <span class="cp-stage-name">${STAGE_LABEL[stage]}</span>
              <span class="cp-stage-range">${STAGE_RANGE[stage]}</span>
            </div>
            <div class="cp-stage-offers">${renderOffers(stages[stage])}</div>
          </div>`).join('')}
      </div>
      <p class="cp-note">${rich(hero.note)}</p>
    </section>

    ${sits.length ? `
    <section class="cp-panel">
      <h3 class="cp-h3">Проблема → покупка</h3>
      ${sits.map((sit) => {
        const item = itemById.get(sit.item_id);
        return `<div class="cp-sit">
          <div class="cp-sit-problem">${rich(sit.problem)}</div>
          <div class="cp-sit-answer">
            ${itemImage(item, 'cp-sit-icon')}
            <div>
              <div class="cp-sit-item">${esc(item ? item.name : sit.item_id)}</div>
              <p>${rich(sit.how)}</p>
            </div>
          </div>
        </div>`;
      }).join('')}
    </section>` : ''}

    <section class="cp-panel">
      <h3 class="cp-h3">Разбор проблем матчапа</h3>
      ${problems.map((p) => {
        const item = itemById.get(p.item_id);
        return `<details class="cp-problem">
          <summary>
            <span class="cp-problem-title">${rich(p.problem)}</span>
            <span class="cp-problem-item">${esc(item ? item.name : p.item_id)}</span>
            <span class="cp-problem-label">${esc(p.label)}</span>
          </summary>
          <div class="cp-problem-body">
            <p><b>Когда покупать.</b> ${rich(p.when)}</p>
            <p><b>Чем помогает.</b> ${rich(p.how)}</p>
          </div>
        </details>`;
      }).join('')}
    </section>

    <div class="cp-two-col">
      <section class="cp-panel">
        <h3 class="cp-h3">Линия</h3>
        <ul class="cp-list">${(hero.lane || []).map((x) => `<li>${rich(x)}</li>`).join('')}</ul>
      </section>
      <section class="cp-panel">
        <h3 class="cp-h3">Командные бои</h3>
        <ul class="cp-list">${(hero.fight || []).map((x) => `<li>${rich(x)}</li>`).join('')}</ul>
      </section>
    </div>

    ${(hero.faq || []).length ? `
    <section class="cp-panel">
      <h3 class="cp-h3">Частые вопросы</h3>
      ${hero.faq.map(([q, a]) => `
        <details class="cp-faq">
          <summary>${rich(q)}</summary>
          <p>${rich(a)}</p>
        </details>`).join('')}
    </section>` : ''}

    ${(hero.related || []).length ? `
    <section class="cp-panel">
      <h3 class="cp-h3">Похожие матчапы</h3>
      <div class="cp-related">
        ${hero.related.map((id) => {
          const other = heroById.get(id);
          if (!other) return '';
          return `<button type="button" class="cp-related-card" data-hero="${esc(id)}">
            ${portrait(other, 'cp-related-portrait')}<span>${esc(other.name)}</span>
          </button>`;
        }).join('')}
      </div>
    </section>` : ''}
  </div>`;
}

// --- рендер: гайд предмета -------------------------------------------------

function renderItemGuide(item) {
  const best = item.best || [];
  const alts = item.alts || [];
  return `
  <div class="cp-guide">
    <header class="cp-guide-head">
      ${itemImage(item, 'cp-guide-item-icon')}
      <div class="cp-guide-titles">
        <span class="cp-eyebrow">Предмет-контр</span>
        <h2 class="cp-guide-name">${esc(item.name)}</h2>
        <div class="cp-guide-tags">
          <span class="cp-tag">${esc(item.category)}</span>
          <span class="cp-tag">Tier ${esc(item.tier)}</span>
          <span class="cp-tag">${esc(item.cost)} душ</span>
          ${(item.roles || []).map((r) => `<span class="cp-tag cp-tag-danger">${esc(r)}</span>`).join('')}
        </div>
        <p class="cp-guide-intro">${rich(item.intro || item.short)}</p>
      </div>
      <button type="button" class="btn-ghost cp-guide-close" id="cp-guide-close">Закрыть</button>
    </header>

    <div class="cp-glance">
      <div class="cp-glance-cell"><span>Кому подходит</span><p>${rich(item.who)}</p></div>
      <div class="cp-glance-cell"><span>Когда брать</span><p>${rich(item.when)}</p></div>
      <div class="cp-glance-cell"><span>Средняя покупка</span><p>${item.avg_minutes ? esc(item.avg_minutes) + ' мин' : '—'}</p></div>
    </div>

    <section class="cp-panel">
      <h3 class="cp-h3">Против кого работает</h3>
      <div class="cp-best">
        ${best.map((row) => {
          const hero = heroById.get(row.hero_id);
          return `<div class="cp-best-row">
            <button type="button" class="cp-best-hero" data-hero="${esc(row.hero_id)}">
              ${portrait(hero, 'cp-best-portrait')}
              <span>${esc(hero ? hero.name : row.hero_id)}</span>
            </button>
            <div class="cp-best-body">
              <div class="cp-best-note">${rich(row.note)}</div>
              <ul class="cp-list">${(row.methods || []).map((m) => `<li>${rich(m)}</li>`).join('')}</ul>
            </div>
          </div>`;
        }).join('')}
      </div>
    </section>

    <div class="cp-two-col">
      <section class="cp-panel">
        <h3 class="cp-h3">Как использовать</h3>
        <p>${rich(item.buy)}</p>
        <p class="cp-note">${rich(item.watch)}</p>
      </section>
      <section class="cp-panel">
        <h3 class="cp-h3">Тайминги покупки</h3>
        <ul class="cp-list">${(item.timing || []).map(([phase, value]) => `<li><b>${esc(phase)}:</b> ${esc(value)}</li>`).join('')}</ul>
        <h3 class="cp-h3">Замены</h3>
        ${alts.map((alt) => {
          const other = itemById.get(alt.item_id);
          return `<div class="cp-alt">
            ${itemImage(other, 'cp-sit-icon')}
            <div><div class="cp-sit-item">${esc(other ? other.name : alt.item_id)}</div><p>${rich(alt.text)}</p></div>
          </div>`;
        }).join('')}
      </section>
    </div>
  </div>`;
}

// --- рендер: разбор команды ------------------------------------------------

function renderTeam() {
  if (!dom.guide) return;
  if (!selection.length) {
    dom.guide.innerHTML = `<p class="cp-empty">Отметьте до шести противников — покажем предметы, которые закрывают сразу несколько угроз.</p>`;
    return;
  }

  const heroes = selection.map((id) => heroById.get(id)).filter(Boolean);
  const score = new Map();
  const covers = new Map();

  heroes.forEach((hero) => {
    (hero.top || []).forEach((itemId, index) => {
      score.set(itemId, (score.get(itemId) || 0) + (hero.top.length - index));
      if (!covers.has(itemId)) covers.set(itemId, []);
      covers.get(itemId).push(hero.name);
    });
  });

  const ranked = [...score.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 12);

  dom.guide.innerHTML = `
  <div class="cp-guide">
    <header class="cp-guide-head">
      <div class="cp-guide-titles">
        <span class="cp-eyebrow">Общий закуп</span>
        <h2 class="cp-guide-name">Против ${heroes.length} ${heroes.length === 1 ? 'героя' : 'героев'}</h2>
        <div class="cp-guide-tags">${heroes.map((h) => `<span class="cp-tag">${esc(h.name)}</span>`).join('')}</div>
        <p class="cp-guide-intro">Предметы отсортированы по числу закрываемых угроз: чем выше, тем больше противников он контрит.</p>
      </div>
    </header>
    <section class="cp-panel">
      <div class="cp-team-list">
        ${ranked.map(([itemId, value]) => {
          const item = itemById.get(itemId);
          if (!item) return '';
          return `<div class="cp-team-row">
            <button type="button" class="cp-team-item" data-item="${esc(itemId)}">
              ${itemImage(item, 'cp-offer-icon')}
              <span>${esc(item.name)}</span>
            </button>
            <div class="cp-team-covers">${covers.get(itemId).map((n) => `<span class="cp-tag">${esc(n)}</span>`).join('')}</div>
            <div class="cp-team-score">${value}</div>
          </div>`;
        }).join('')}
      </div>
    </section>
  </div>`;
}

// --- вкладки и обработчики -------------------------------------------------

function renderItemGrid() {
  if (!dom.grid) return;
  const query = (dom.search && dom.search.value || '').trim().toLowerCase();
  const items = (data.items || []).filter((i) => !query
    || i.name.toLowerCase().includes(query)
    || (i.name_en || '').toLowerCase().includes(query));

  if (!items.length) {
    dom.grid.innerHTML = '<p class="cp-empty">Ничего не найдено.</p>';
    return;
  }

  dom.grid.innerHTML = items.map((item) => `
    <button class="cp-item-card" data-item="${esc(item.id)}" type="button">
      ${itemImage(item, 'cp-item-card-icon')}
      <div class="cp-item-card-body">
        <div class="cp-item-card-name">${esc(item.name)}</div>
        <div class="cp-item-card-roles">${esc((item.roles || []).join(' · '))}</div>
        <div class="cp-item-card-short">${esc(item.short)}</div>
      </div>
      <span class="cp-item-card-tier">T${esc(item.tier)}</span>
    </button>`).join('');
}

function renderAll() {
  const isItems = tab === 'items';
  const isTeam = tab === 'team';
  if (dom.selectionWrap) dom.selectionWrap.classList.toggle('hidden', isItems);
  if (dom.search) dom.search.placeholder = isItems ? 'Поиск предметов…' : 'Поиск героев…';
  if (dom.count) {
    dom.count.textContent = isItems
      ? `предметов: ${(data.items || []).length}`
      : `героев: ${(data.heroes || []).length}`;
  }

  if (isItems) renderItemGrid();
  else renderHeroGrid();

  if (isTeam) renderTeam();
  else if (activeItem) dom.guide.innerHTML = renderItemGuide(itemById.get(activeItem));
  else if (activeHero) dom.guide.innerHTML = renderHeroGuide(heroById.get(activeHero));
  else {
    dom.guide.innerHTML = `<p class="cp-empty">
      ${isItems
        ? 'Выберите предмет — покажем, против кого он работает и когда его брать.'
        : 'Выберите героя — покажем полный разбор матчапа: угрозы, предметы по фазам и план боя.'}
    </p>`;
  }
  renderSelectionBar();
}

function openGuide({ hero = null, item = null }) {
  activeHero = hero;
  activeItem = item;
  if (hero && tab === 'team') {
    tab = 'heroes';
    document.querySelectorAll('.cp-tab').forEach((b) =>
      b.classList.toggle('cp-tab-active', b.dataset.cpTab === tab));
  }
  renderAll();
  dom.guide.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function togglePick(heroId) {
  const index = selection.indexOf(heroId);
  if (index >= 0) selection.splice(index, 1);
  else if (selection.length < 6) selection.push(heroId);
  renderAll();
}

function bind() {
  dom.root.addEventListener('click', (event) => {
    const tabBtn = event.target.closest('.cp-tab');
    if (tabBtn) {
      tab = tabBtn.dataset.cpTab;
      if (tab === 'items') { activeHero = null; }
      activeItem = null;
      if (tab !== 'team') {
        document.querySelectorAll('.cp-tab').forEach((b) =>
          b.classList.toggle('cp-tab-active', b.dataset.cpTab === tab));
      }
      renderAll();
      return;
    }

    const unpick = event.target.closest('[data-unpick]');
    if (unpick) { togglePick(unpick.dataset.unpick); return; }

    // Отметка противника — отдельная кнопка на карточке героя.
    const pick = event.target.closest('[data-pick]');
    if (pick) { togglePick(pick.dataset.pick); return; }

    if (event.target.closest('#cp-clear')) {
      selection = [];
      renderAll();
      return;
    }

    if (event.target.closest('#cp-guide-close')) {
      activeHero = null;
      activeItem = null;
      renderAll();
      return;
    }

    const itemBtn = event.target.closest('[data-item]');
    if (itemBtn) { openGuide({ item: itemBtn.dataset.item }); return; }

    // Клик по герою (карточка, родственный матчап, строка предмета) открывает гайд.
    const heroBtn = event.target.closest('[data-hero]');
    if (heroBtn) openGuide({ hero: heroBtn.dataset.hero });
  });

  if (dom.search) {
    dom.search.addEventListener('input', () => {
      if (tab === 'items') renderItemGrid();
      else renderHeroGrid();
    });
  }
}

export function initCounterpicks() {
  dom.root = document.getElementById('tab-counterpicks');
  if (!dom.root) return;

  dom.grid = document.getElementById('cp-grid');
  dom.guide = document.getElementById('cp-guide');
  dom.search = document.getElementById('cp-search');
  dom.selectionBar = document.getElementById('cp-selected');
  dom.selectionWrap = document.getElementById('cp-selection');
  dom.count = document.getElementById('cp-count');

  bind();
  dom.guide.innerHTML = '<p class="cp-empty">Загрузка данных контрпиков…</p>';

  load()
    .then(() => renderAll())
    .catch((err) => {
      dom.guide.innerHTML = `<p class="cp-empty cp-error">${esc(err.message || 'Не удалось загрузить контрпики')}</p>`;
    });
}