// synergies.js — экран «Синергии»: сила дуо на одной линии.
//
// Данные: /api/synergies (data/synergies.json, собран scripts/build_synergies.py
// с mobalytics.gg/deadlock/tier-list/synergies). Имена и портреты героев уже
// переведены на русский внутри датасета, поэтому экран не зависит от /api/heroes.
//
// Показываем по 40 строк (как на сайте) и догружаем кнопкой «Показать ещё»:
// 946 дуо в DOM целиком — лишняя работа для WebView.

import { fetchSynergies } from './api.js';

const PAGE_SIZE = 40;

const state = {
  data: null,
  tier: '',
  query: '',
  sort: 'winrate',
  visible: PAGE_SIZE,
  loadPromise: null,
};

const dom = {};

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function tierClass(tier) {
  return `syn-tier-${String(tier || 'c').toLowerCase()}`;
}

function heroHtml(hero) {
  const portrait = hero.portrait
    ? `<img src="${esc(hero.portrait)}" alt="${esc(hero.name)}" loading="lazy">`
    : `<span class="syn-hero-fallback">${esc((hero.name || '?').slice(0, 1))}</span>`;
  return `<span class="syn-hero" title="${esc(hero.name_en || hero.name)}">${portrait}<span class="syn-hero-name">${esc(hero.name)}</span></span>`;
}

function signed(value) {
  const num = Number(value) || 0;
  return `${num > 0 ? '+' : ''}${num.toFixed(1)}%`;
}

function rowHtml(duo) {
  const wrfClass = duo.wrf > 0 ? 'syn-up' : (duo.wrf < 0 ? 'syn-down' : '');
  return `
    <div class="syn-row" data-tier="${esc(duo.tier)}">
      <div class="syn-rank">${duo.rank}</div>
      <div class="syn-tier ${tierClass(duo.tier)}">${esc(duo.tier)}</div>
      <div class="syn-duo">${heroHtml(duo.hero1)}<span class="syn-plus">+</span>${heroHtml(duo.hero2)}</div>
      <div class="syn-winrate">
        <span class="syn-wr-value">${duo.winrate.toFixed(1)}%</span>
        <span class="syn-wr-bar"><i style="width:${Math.max(0, Math.min(100, duo.winrate))}%"></i></span>
      </div>
      <div class="syn-metric ${wrfClass}" title="WRF — прирост винрейта дуо относительно среднего винрейта героев">${signed(duo.wrf)}</div>
      <div class="syn-metric">${duo.pickrate.toFixed(2)}%</div>
      <div class="syn-metric">${duo.cs_per_min.toFixed(1)}</div>
      <div class="syn-metric">${duo.denies.toFixed(1)}</div>
      <div class="syn-metric">${Math.round(duo.nw_per_min).toLocaleString('ru-RU')}</div>
      <div class="syn-metric">${duo.kda.toFixed(2)}</div>
    </div>`;
}

function matches(duo) {
  if (state.tier && duo.tier !== state.tier) return false;
  const q = state.query.trim().toLowerCase();
  if (!q) return true;
  return [duo.hero1, duo.hero2].some((hero) =>
    String(hero.name || '').toLowerCase().includes(q)
    || String(hero.name_en || '').toLowerCase().includes(q));
}

function filtered() {
  const duos = (state.data?.duos || []).filter(matches);
  const key = state.sort;
  return duos.slice().sort((a, b) => {
    const delta = (Number(b[key]) || 0) - (Number(a[key]) || 0);
    return delta !== 0 ? delta : a.rank - b.rank;
  });
}

function render() {
  const rows = filtered();
  const slice = rows.slice(0, state.visible);

  dom.rows.innerHTML = slice.length
    ? slice.map(rowHtml).join('')
    : '<div class="syn-empty">Под фильтр не попала ни одна связка. Измените героя или сорт.</div>';

  dom.visible.textContent = rows.length
    ? `Показано ${slice.length} из ${rows.length}`
    : '';
  dom.more.classList.toggle('hidden', slice.length >= rows.length);
  dom.more.textContent = `Показать ещё ${Math.min(PAGE_SIZE, rows.length - slice.length)}`;
}

function renderCounts() {
  const duos = state.data?.duos || [];
  const counts = { S: 0, A: 0, B: 0, C: 0 };
  for (const duo of duos) counts[duo.tier] = (counts[duo.tier] || 0) + 1;

  dom.count.textContent = `${duos.length} дуо`;
  dom.tierButtons.forEach((btn) => {
    const tier = btn.dataset.synTier || '';
    const label = tier || 'Все';
    btn.textContent = tier ? `${label} · ${counts[tier] || 0}` : `Все · ${duos.length}`;
  });
}

function renderMeta() {
  const collected = state.data?.collected_at || '';
  const [year, month, day] = collected.split('-');
  dom.date.textContent = day ? `данные: ${day}.${month}.${year}` : 'дата сбора неизвестна';
  dom.source.href = state.data?.source || '#';
  if (dom.method) {
    dom.method.innerHTML = (state.data?.methodology || [])
      .map((item) => `<li>${esc(item)}</li>`)
      .join('');
  }
}

function setTier(tier) {
  state.tier = tier;
  state.visible = PAGE_SIZE;
  dom.tierButtons.forEach((btn) => {
    btn.classList.toggle('syn-tier-btn-active', (btn.dataset.synTier || '') === tier);
  });
  render();
}

function bind() {
  dom.search.addEventListener('input', () => {
    state.query = dom.search.value;
    state.visible = PAGE_SIZE;
    render();
  });

  dom.sort.addEventListener('change', () => {
    state.sort = dom.sort.value;
    state.visible = PAGE_SIZE;
    render();
  });

  dom.tierButtons.forEach((btn) => {
    btn.addEventListener('click', () => setTier(btn.dataset.synTier || ''));
  });

  dom.more.addEventListener('click', () => {
    state.visible += PAGE_SIZE;
    render();
  });
}

function load() {
  if (!state.loadPromise) {
    dom.rows.innerHTML = '<div class="syn-empty">Загружаем синергии…</div>';
    state.loadPromise = fetchSynergies()
      .then((payload) => {
        state.data = payload;
        renderMeta();
        renderCounts();
        render();
        return payload;
      })
      .catch((err) => {
        state.loadPromise = null;
        dom.rows.innerHTML = `<div class="syn-empty syn-error">Не удалось загрузить синергии: ${esc(err.message)}</div>`;
        throw err;
      });
  }
  return state.loadPromise;
}

export function initSynergies() {
  const root = document.getElementById('tab-synergies');
  if (!root || dom.rows) return;

  dom.rows = document.getElementById('syn-rows');
  dom.count = document.getElementById('syn-count');
  dom.date = document.getElementById('syn-meta-date');
  dom.source = document.getElementById('syn-source');
  dom.search = document.getElementById('syn-search');
  dom.sort = document.getElementById('syn-sort');
  dom.more = document.getElementById('syn-more');
  dom.visible = document.getElementById('syn-visible');
  dom.method = document.getElementById('syn-method-list');
  dom.tierButtons = Array.from(root.querySelectorAll('[data-syn-tier]'));

  if (!dom.rows) return;
  bind();
  load().catch(() => {});
}
