// tierlist.js — экран 2: тир-лист героев с фильтрами ролей.
// TZ B3: coachMode-блок удалён; сортировка только по винрейту;
// заголовки колонок и дата свежести меты (meta_updated_at).

import { ROLE_LABEL } from './ui.js';

let heroes = [];
let activeRole = '';

function tierOf(hero) {
  const wr = hero.winrate;
  if (wr == null) return 'B';
  if (wr >= 0.55) return 'S+';
  if (wr >= 0.52) return 'S';
  if (wr >= 0.49) return 'A';
  return 'B';
}

function tierClass(t) {
  return { 'S+': 'tier-s-plus', 'S': 'tier-s', 'A': 'tier-a', 'B': 'tier-b' }[t];
}

function portraitHtml(hero) {
  if (hero.portrait) {
    return `<img class="tier-portrait" src="${hero.portrait}"
      alt="${hero.name}" loading="lazy"
      onerror="this.outerHTML='<div class=\\'tier-portrait-fallback\\'>${hero.name_en ? hero.name_en.slice(0, 2) : '??'}</div>'">`;
  }
  return `<div class="tier-portrait-fallback">${(hero.name_en || '??').slice(0, 2)}</div>`;
}

function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

// Дата свежести меты: максимум meta_updated_at по видимым героям,
// отформатированный «дд.мм.гггг». Пусто, если поле нигде не задано.
function renderMetaDate() {
  const node = document.getElementById('tier-meta-date');
  if (!node) return;
  const dates = heroes
    .map((h) => h.meta_updated_at)
    .filter(Boolean)
    .sort();
  if (!dates.length) {
    node.textContent = 'мета: дата неизвестна (запусти scripts/refresh_meta.py)';
    return;
  }
  const iso = dates[dates.length - 1];
  const [y, m, d] = iso.slice(0, 10).split('-');
  node.textContent = `мета: ${d}.${m}.${y}`;
}

function render() {
  const rows = document.getElementById('tier-rows');
  const filtered = heroes
    .filter((h) => !h.in_development)
    .filter((h) => !activeRole || h.roles.includes(activeRole))
    .sort((a, b) => (b.winrate ?? -1) - (a.winrate ?? -1));

  const topIds = new Set(
    [...filtered].sort((a, b) => (b.winrate ?? -1) - (a.winrate ?? -1)).slice(0, 5).map((h) => h.id),
  );

  rows.innerHTML = filtered.map((h, idx) => {
    const t = tierOf(h);
    const wr = h.winrate;
    const wrHtml = wr == null
      ? '<span class="tier-winrate" style="color:var(--text-dim)">—</span>'
      : `<span class="tier-winrate ${wr >= 0.5 ? 'wr-good' : 'wr-bad'}">${(wr * 100).toFixed(1)}%</span>`;
    const prHtml = h.pickrate != null
      ? `<span class="tier-pickrate" style="color: #ffed79; font-weight: 600;">${(h.pickrate * 100).toFixed(1)}%</span>`
      : `<span class="tier-pickrate" style="color: var(--text-dim); font-weight: 400;">—</span>`;
    return `
      <div class="tier-row cascade-item" style="--index: ${idx};" data-hero="${esc(h.id)}">
        <div class="tier-badge ${tierClass(t)}">${t}</div>
        ${portraitHtml(h)}
        <div class="tier-name">${esc(h.name)}
          ${topIds.has(h.id) ? '<span class="tier-top-meta"> ★ Топ Меты</span>' : ''}</div>
        ${wrHtml}
        ${prHtml}
        <button class="tier-goto" data-goto-lab="${esc(h.id)}" title="Открыть билд-лаб с этим героем">В билд-лаб</button>
      </div>`;
  }).join('');

  rows.querySelectorAll('.tier-goto').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const heroId = btn.dataset.gotoLab;
      const event = new CustomEvent('goto-lab-hero', { detail: heroId });
      document.dispatchEvent(event);
    });
  });
}

export function initTierList(heroesData) {
  heroes = heroesData;
  document.querySelectorAll('.tier-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tier-tab').forEach((t) => t.classList.remove('tier-tab-active'));
      tab.classList.add('tier-tab-active');
      activeRole = tab.dataset.role;
      render();
    });
  });
  renderMetaDate();
  render();
}
