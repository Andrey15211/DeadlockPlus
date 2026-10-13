// map_view.js — экран «Карта»: линии, таймлайн, минимапа с маркерами-объектами.
//
// TZ B2: маркеры берутся из data/map.json (markers[] с нормализованными
// координатами 0..1 — та же проекция, что match_loader._norm_coord для
// точек смертей Deep Match). Фолбэк на локальный хардкод — если в гайде
// маркеров нет (старый map.json).

import { el } from './ui.js';
import { fetchMap } from './api.js';

const LAYER_LABELS = {
  camps: 'Кемпы нейтралов',
  urn: 'Урна душ',
  midboss: 'Мидбосс',
  walkers: 'Шагоходы',
};

// Фолбэк-маркеры (используются, только если map.json их не содержит).
const FALLBACK_MARKERS = [
  { layer: 'camps', lane: 'york', x: 0.20, y: 0.30, label: 'Кемп Йорк-верх' },
  { layer: 'camps', lane: 'york', x: 0.23, y: 0.64, label: 'Кемп Йорк-низ' },
  { layer: 'camps', lane: 'broadway', x: 0.40, y: 0.22, label: 'Кемп Мид-верх' },
  { layer: 'camps', lane: 'broadway', x: 0.41, y: 0.78, label: 'Кемп Мид-низ' },
  { layer: 'urn', lane: 'broadway', x: 0.50, y: 0.50, label: 'Урна душ' },
  { layer: 'midboss', lane: 'broadway', x: 0.56, y: 0.42, label: 'Мидбосс' },
  { layer: 'walkers', lane: 'york', x: 0.24, y: 0.16, label: 'Шагоход Йорк' },
  { layer: 'walkers', lane: 'greenwich', x: 0.76, y: 0.16, label: 'Шагоход Гринвич' },
];

// --- тултип -------------------------------------------------------------------

let mapTip = null;
let mapTipWired = false;

function ensureMapTip() {
  if (!mapTip) {
    mapTip = el('div', { class: 'map-tip', role: 'tooltip' });
    document.body.append(mapTip);
  }
  if (!mapTipWired) {
    mapTipWired = true;
    // Клик мимо маркера закрывает закреплённый кликом тултип.
    document.addEventListener('click', (e) => {
      if (e.target.closest('.map-marker')) return;
      hideMapTip();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') hideMapTip();
    });
  }
  return mapTip;
}

function showMapTip(text, x, y) {
  ensureMapTip();
  mapTip.textContent = text;
  mapTip.classList.add('show');
  moveMapTip(x, y);
}

function moveMapTip(x, y) {
  if (!mapTip || !mapTip.classList.contains('show')) return;
  const pad = 14, margin = 8;
  const w = mapTip.offsetWidth || 220;
  const h = mapTip.offsetHeight || 48;
  let px = x + pad, py = y + pad;
  if (px + w > innerWidth - margin) px = x - w - pad;
  if (py + h > innerHeight - margin) py = y - h - pad;
  px = Math.min(Math.max(margin, px), innerWidth - margin - w);
  py = Math.min(Math.max(margin, py), innerHeight - margin - h);
  mapTip.style.left = `${px}px`;
  mapTip.style.top = `${py}px`;
}

function hideMapTip() {
  if (mapTip) mapTip.classList.remove('show');
}

// Формат подсказки объекта: точные тайминги из таймлайна гайда.
function markerTooltip(marker, timelineByTime, lanesById) {
  const laneName = lanesById[marker.lane] || '';
  switch (marker.layer) {
    case 'camps': {
      const ev = timelineByTime['3:00'];
      return `${marker.label}${laneName ? ` — ${laneName}` : ''}: спавн в 3:00` +
        (ev ? `. ${ev.description}` : '');
    }
    case 'urn': {
      const ev = timelineByTime['10:00'];
      return `${marker.label} — открывается в 10:00` +
        (ev ? `. ${ev.description}` : '');
    }
    case 'midboss': {
      const ev = timelineByTime['12:00'];
      return `${marker.label} — пробуждение в 12:00, взрослая фаза в 15:00` +
        (ev ? `. ${ev.description}` : '');
    }
    case 'walkers':
      return `${marker.label} — пушится для давления на линии (цель обороны/атаки фланга).`;
    default:
      return marker.label;
  }
}

// --- маркеры ------------------------------------------------------------------

function markerNode(marker, tooltipText) {
  return el(
    'div',
    {
      class: `map-marker layer-${marker.layer}`,
      'data-layer': marker.layer,
      'data-lane': marker.lane,
      // координаты уже в 0..1 (проекция map.json / _norm_coord)
      style: `left:${marker.x * 100}%;top:${marker.y * 100}%`,
      tabindex: 0,
      role: 'button',
      'aria-label': tooltipText,
      onclick: (e) => {
        e.stopPropagation();
        showMapTip(tooltipText, e.clientX, e.clientY);
      },
      onmouseenter: (e) => showMapTip(tooltipText, e.clientX, e.clientY),
      onmousemove: (e) => moveMapTip(e.clientX, e.clientY),
      onmouseleave: hideMapTip,
      onfocus: (e) => {
        const r = e.target.getBoundingClientRect();
        showMapTip(tooltipText, r.left + r.width / 2, r.top);
      },
      onblur: hideMapTip,
      onkeydown: (e) => {
        if (e.key === 'Escape') hideMapTip();
      },
    },
    el('span', { class: 'mm-dot' }),
    el('span', { class: 'mm-label' }, marker.label),
  );
}

function renderMarkers(container, markers, timelineByTime, lanesById) {
  container.replaceChildren();
  for (const m of markers) {
    container.append(markerNode(m, markerTooltip(m, timelineByTime, lanesById)));
  }
}

function setLayerVisibility(layer, visible) {
  document.querySelectorAll(`#map-markers [data-layer="${layer}"]`)
    .forEach((n) => n.classList.toggle('off', !visible));
}

function initLayerToggles() {
  document.querySelectorAll('.map-layer-toggle').forEach((btn) => {
    const layer = btn.dataset.layer;
    if (btn.dataset.wired === '1') return; // защита от повторного вызова
    btn.dataset.wired = '1';
    btn.setAttribute('aria-pressed', 'true');
    btn.addEventListener('click', () => {
      const nowVisible = !btn.classList.contains('layer-off');
      btn.classList.toggle('layer-off', nowVisible);
      btn.setAttribute('aria-pressed', String(!nowVisible));
      setLayerVisibility(layer, !nowVisible);
    });
  });
}

// --- основной рендер ----------------------------------------------------------

let wiredLaneToggle = false;

export function renderMapView(mapGuide) {
  const lanes = document.querySelector('#lane-cards');
  if (lanes) {
    lanes.replaceChildren();
    const lanesById = {};
    for (const lane of mapGuide.lanes || []) {
      lanesById[lane.id] = lane.name;
      lanes.append(
        el(
          'article',
          {
            class: 'lane-card',
            'data-lane': lane.id,
            role: 'button', tabindex: 0,
            style: `border-top: 3px solid ${lane.color}`,
          },
          el('h3', { style: `color:${lane.color}` }, lane.name),
          el('div', { class: 'lc-char' }, lane.character),
          el('p', { class: 'lc-notes' }, lane.notes),
        ),
      );
    }
    // Подсветка линии: клик по карточке — подсветить её маркеры на карте.
    if (!wiredLaneToggle) {
      wiredLaneToggle = true;
      lanes.addEventListener('click', (e) => {
        const card = e.target.closest('.lane-card');
        if (!card) return;
        const laneId = card.dataset.lane;
        const active = card.classList.contains('lane-hl');
        lanes.querySelectorAll('.lane-card').forEach((c) => c.classList.remove('lane-hl'));
        document.querySelectorAll('#map-markers [data-lane]')
          .forEach((n) => n.classList.remove('active'));
        if (!active) {
          card.classList.add('lane-hl');
          document.querySelectorAll(`#map-markers [data-lane="${laneId}"]`)
            .forEach((n) => n.classList.add('active'));
        }
      });
    }
  }

  const tl = document.querySelector('#map-timeline');
  if (tl) {
    tl.replaceChildren();
    for (const ev of mapGuide.timeline || []) {
      tl.append(
        el('li', {},
          el('span', { class: 'tl-time' }, ev.time),
          el('div', { class: 'tl-title' }, ev.title),
          el('div', { class: 'tl-desc' }, ev.description),
        ),
      );
    }
  }

  const markers = document.querySelector('#map-markers');
  if (markers) {
    const timelineByTime = {};
    for (const ev of mapGuide.timeline || []) timelineByTime[ev.time] = ev;
    const lanesById = {};
    for (const lane of mapGuide.lanes || []) lanesById[lane.id] = lane.name;
    // TZ B2: маркеры из map.json (нормализованные координаты, единая
    // проекция с точками смертей); фолбэк — локальный список.
    const list = (mapGuide.markers && mapGuide.markers.length)
      ? mapGuide.markers
      : FALLBACK_MARKERS;
    renderMarkers(markers, list, timelineByTime, lanesById);
    initLayerToggles();
    renderProjectionNote(mapGuide.coordinate_system);
  }
}

// Подпись координатной привязки: маркеры гайда и точки смертей из Deep Match
// живут в одной проекции (мир ±10752 → 0..1).
function renderProjectionNote(cs) {
  let note = document.getElementById('map-projection-note');
  if (!note) {
    note = el('p', { class: 'map-projection-note', id: 'map-projection-note' });
    const hint = document.querySelector('.map-hint');
    if (hint) hint.after(note);
  }
  note.textContent = (cs && cs.projection === 'world_norm_0_1')
    ? 'Координатная привязка: мир ±10752 → 0..1. Маркеры гайда и точки смертей из Deep Match — в одной проекции.'
    : 'Маркеры показаны по приблизительной привязке (map.json без coordinate_system).';
}

// Загрузка и рендер карты одним вызовом (используется из app.js).
export async function loadMapView() {
  const guide = await fetchMap();
  renderMapView(guide);
  return guide;
}

// Точки входа: loadMapView() (async, дергает /api/map) или renderMapView(guide).
export function initMapView() {
  return loadMapView();
}
