// ui.js — генерация DOM для лаборатории и общих утилит (карта — в map_view.js).

export const CATEGORY_LABEL = { weapon: 'Оружие', vitality: 'Живучесть', spirit: 'Дух' };
export const ROLE_LABEL = {
  carry: 'Керри', support: 'Саппорт', initiator: 'Инициатор',
  duelist: 'Дуэлянт', controller: 'Контроллер',
};
export const TAG_LABEL = {
  heal: 'Сустейн и лечение',
  uninterruptible_ult: 'Не прерываемый ульт',
  bullet_dps: 'Пулемётный DPS',
  spirit_caster: 'Спирит-кастер',
  burst: 'Бёрст',
  mobility: 'Мобильность',
  stealth: 'Невидимость',
  tank: 'Танк',
  cc_chain: 'Цепной контроль',
};

// Оптимизированная иконка предмета:
// Иконки хранятся в формате .webp (или прямом url/ext из item.icon).
// При первой ошибке загрузки сразу подставляется текстовая заглушка без каскадных 404 на .png/.svg.
export function itemIconNode(item, { cls = 'it-icon', showFallbackName = false } = {}) {
  const wrap = el('span', { class: `${cls}-wrap` });
  const fallback = el('span', { class: `${cls}-fallback`, 'aria-hidden': 'true' },
    showFallbackName ? (item.name || '?').slice(0, 1) : (item.name || '?').slice(0, 1));

  // Если item.icon содержит готовый путь или расширение, используем его; по умолчанию — локальный /assets/items/{id}.webp
  const src = item?.icon
    ? (item.icon.startsWith('/') || item.icon.startsWith('http') ? item.icon : `/assets/items/${item.icon}`)
    : `/assets/items/${item.id}.webp`;

  const img = el('img', {
    class: cls,
    src,
    alt: '',
    loading: 'lazy',
    'aria-hidden': 'true',
  });

  // Однократная обработка ошибки: сразу заменяем на fallback-букву без промежуточных 404
  img.addEventListener('error', () => {
    img.replaceWith(fallback);
  }, { once: true });

  wrap.append(img);
  return wrap;
}

const $ = (sel) => document.querySelector(sel);
export { $ };

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on') && typeof v === 'function') {
      node.addEventListener(k.slice(2), v);
    } else if (k === 'html') node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.append(c instanceof Node ? c : document.createTextNode(c));
  }
  return node;
}

// --- слоты билда (12 универсальных слотов) ---------------------------------

export function renderSlotBoard(slots, itemById, { onUnequip }) {
  const slotList = Array.isArray(slots) ? slots : (slots.items || new Array(12).fill(null));
  const container = $('#slots-universal');
  if (container) {
    container.replaceChildren();
    slotList.forEach((itemId, idx) => {
      const item = itemId ? itemById.get(itemId) : null;
      const cat = item ? item.category : '';
      const slot = el(
        'div',
        {
          class: `slot ${item ? 'filled ' + cat : ''}`,
          'data-item-id': item ? item.id : null,
          title: item
            ? `${item.name} (${CATEGORY_LABEL[item.category] || item.category}) — клик снимает (или заменится при покупке апгрейда)`
            : `Слот ${idx + 1} — клик по предмету в каталоге`,
          onclick: () => { if (item) onUnequip(idx); },
        },
        item
          ? el('span', { class: 'slot-main' },
              itemIconNode(item, { cls: 'slot-icon' }),
              el('span', { class: 'slot-label' }, item.name))
          : el('span', { class: 's-empty' }, `Слот ${idx + 1}`),
        item ? el('span', { class: 'slot-cost' }, `§ ${item.cost}`) : null,
        item ? el('span', { class: 's-clear', title: 'Снять предмет' }, '✕') : null,
      );
      container.append(slot);
    });
  }
}

// --- каталог предметов -----------------------------------------------------

export function renderCatalog(container, items, { onEquip, equippedIds = new Set() }) {
  const equippedSet = equippedIds instanceof Set ? equippedIds : new Set(equippedIds || []);
  const fragment = document.createDocumentFragment();
  items.forEach((item, idx) => {
    const isEquipped = equippedSet.has(item.id);
    const statsLines = Object.entries(item.stats || {})
      .filter(([, v]) => v)
      .map(([k, v]) => `${statShortName(k)} ${v > 0 ? '+' : ''}${v}`)
      .join(', ');
    const card = el(
      'div',
      {
        class: `item-card cascade-item ${item.category}${isEquipped ? ' is-equipped' : ''}`,
        'data-item-id': item.id,
        'data-tier': item.tier,
        style: `--index: ${idx};`,
        onclick: () => onEquip(item),
      },
      isEquipped ? el('div', { class: 'item-card-equipped-badge' }, '✓ В БИЛДЕ') : null,
      el('span', { class: 'it-tier' }, `T${item.tier}`),
      el('div', { class: 'it-head' },
        itemIconNode(item, { cls: 'it-icon' }),
        el('span', { class: 'it-name', title: item.name }, item.name)),
      statsLines ? el('div', { class: 'it-stats' }, statsLines) : null,
      el('div', { class: 'it-cost' }, `§ ${item.cost.toLocaleString('ru-RU')} душ`),
    );
    fragment.append(card);
  });
  container.replaceChildren(fragment);
}

export function updateCatalogEquipped(container, equippedIds = new Set()) {
  if (!container) return;
  const set = equippedIds instanceof Set ? equippedIds : new Set(equippedIds || []);
  const cards = container.querySelectorAll('.item-card[data-item-id]');
  cards.forEach((card) => {
    const itemId = card.getAttribute('data-item-id');
    const isEquipped = set.has(itemId);
    card.classList.toggle('is-equipped', isEquipped);
    let badge = card.querySelector('.item-card-equipped-badge');
    if (isEquipped && !badge) {
      badge = el('div', { class: 'item-card-equipped-badge' }, '✓ В БИЛДЕ');
      card.prepend(badge);
    } else if (!isEquipped && badge) {
      badge.remove();
    }
  });
}

const STAT_NAMES = {
  weapon_damage: 'Урон оружия', fire_rate_pct: 'Скорострельность', base_hp: 'Здоровье',
  bullet_shield: 'Пулевой щит', spirit_shield: 'Спиритический щит', stamina: 'Стамина',
  cooldown_reduction_pct: 'Перезарядка', spirit_power: 'Мощь духа', move_speed: 'Скорость',
};
const statShortName = (k) => STAT_NAMES[k] || k;

// Формат одной строки стата в тултипе: «+15% Урон оружия», «+200 Здоровье».
function formatStatLine(key, value) {
  const isPct = key === 'fire_rate_pct' || key === 'cooldown_reduction_pct';
  const shown = isPct
    ? `${value > 0 ? '+' : ''}${Math.round(value * 100)}%`
    : `${value > 0 ? '+' : ''}${value}`;
  return `${shown} ${statShortName(key)}`;
}

// --- аутентичный тултип Statlocker (screens 6-9) ------------------------------

let tooltipNode = null;
let tooltipResolveItem = null;

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

export function highlightKeywords(text) {
  if (!text) return '';
  const escaped = escapeHtml(text);

  const greenPatterns = [
    'барьер[а-я]*',
    'здоровь[а-я]*',
    'исцелен[а-я]*',
    'исцеля[а-я]*',
    'хил[а-я]*',
    'регенерац[а-я]*',
    'регенерир[а-я]*',
    'лечен[а-я]*',
    'вампиризм[а-я]*',
    'barriers?',
    'health',
    'heal(?:ing|s|ed)?',
    'regen(?:eration)?',
    'lifesteal',
  ];

  const orangePatterns = [
    'урон[а-я]*\\s+оружи[а-я]*',
    'weapon\\s+damage',
    'дальност[а-я]*\\s+стрельб[а-я]*',
    'weapon\\s+range',
    'скорострельност[а-я]*',
    'fire\\s+rate',
    'патрон[а-я]*',
    'пул(?:я|и|ю|ей|е|ями|ях|ь|евой|евая|евое|евые|евого|евому|евых|евым)[а-я]*',
    'bullets?',
    'ammo',
  ];

  const purplePatterns = [
    'мощ[а-я]*\\s+дух[а-я]*',
    'spirit\\s+power',
    'спиритизм[а-я]*',
    'спиритическ[а-я]*',
    'спирит[а-я]*',
    'перезарядк[а-я]*',
    'перезаряд[а-я]*',
    'длительност[а-я]*',
    'cooldowns?',
    'duration',
    'spirit',
  ];

  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}])(?:(?<orange>${orangePatterns.join('|')})|(?<green>${greenPatterns.join('|')})|(?<purple>${purplePatterns.join('|')}))(?![\\p{L}\\p{N}])`,
    'giu'
  );

  return escaped.replace(pattern, (match, orange, green, purple) => {
    let color = '';
    let cls = '';
    if (green) {
      color = '#00f794';
      cls = 'kw-green';
    } else if (orange) {
      color = '#e58a00';
      cls = 'kw-orange';
    } else if (purple) {
      color = '#ce90ff';
      cls = 'kw-purple';
    }
    return `<span class="${cls}" style="color: ${color}; font-weight: 600;">${match}</span>`;
  });
}

function resolveItem(ref, getItemById) {
  if (!ref || typeof ref !== 'string') return null;
  const fn = getItemById || tooltipResolveItem;
  return fn ? fn(ref) : null;
}

export function initTooltip(getItemById) {
  tooltipResolveItem = getItemById;
  if (tooltipNode) return;

  tooltipNode = el('div', { class: 'tooltip statlocker-tooltip', role: 'tooltip' });
  document.body.append(tooltipNode);

  let currentCard = null;

  document.addEventListener('mouseover', (e) => {
    const card = e.target.closest('[data-item-id]');
    if (!card || card !== currentCard) currentCard = card;
    if (!currentCard) return;
    const item = getItemById(currentCard.dataset.itemId);
    if (!item) return;

    // Сборка аутентичного тултипа Statlocker
    const children = [];

    // 1. Шапка цвета категории со стоимостью душ
    const head = el('div', { class: `st-head ${item.category}` },
      el('div', { class: 'st-title-group' },
        el('span', { class: 'st-name' }, item.name),
        el('span', { class: 'st-cat-tier' },
          `${(CATEGORY_LABEL[item.category] || item.category).toUpperCase()} · ТИР ${item.tier}`)),
      el('span', { class: 'st-cost-badge' }, `§ ${item.cost.toLocaleString('ru-RU')}`),
    );
    children.push(head);

    // 2. Тело карточки
    const bodyChildren = [];

    // Статы (пассивные характеристики белым цветом)
    const statsEntries = Object.entries(item.stats || {}).filter(([, v]) => v);
    if (statsEntries.length > 0) {
      const statList = el('ul', { class: 'st-stat-list' },
        ...statsEntries.map(([k, v]) => el('li', {}, formatStatLine(k, v))),
      );
      bodyChildren.push(statList);
    }

    // Эффекты (Пассивное / Активное / Описание) с таймером перезарядки и подсветкой
    if (item.passive) {
      const topRow = el('div', { class: 'st-effect-top' },
        el('span', { class: 'st-effect-type passive' }, 'Пассивное'),
      );
      const descEl = el('div', { class: 'st-effect-desc', html: highlightKeywords(item.passive) });
      bodyChildren.push(el('div', { class: 'st-effect-card passive' }, topRow, descEl));
    }

    if (item.active) {
      const cd = Number(item.cooldown);
      const topRow = el('div', { class: 'st-effect-top' },
        el('span', { class: 'st-effect-type active' }, 'Активное'),
        (cd && cd > 0) ? el('span', { class: 'st-cooldown' }, `⏱ ${item.cooldown} с.`) : null,
      );
      const descEl = el('div', { class: 'st-effect-desc', html: highlightKeywords(item.active) });
      bodyChildren.push(el('div', { class: 'st-effect-card active' }, topRow, descEl));
    }

    if (!item.passive && !item.active && item.description) {
      const descEl = el('div', { class: 'st-effect-desc', html: highlightKeywords(item.description) });
      bodyChildren.push(el('div', { class: 'st-effect-card' }, descEl));
    }

    if (bodyChildren.length > 0) {
      children.push(el('div', { class: 'st-body' }, ...bodyChildren));
    }

    // 3. Цепочка компонентов / улучшений (в нижней плашке)
    const comps = (item.components || []).map((id) => resolveItem(id, getItemById)).filter(Boolean);
    const upgFrom = item.upgrade_from ? resolveItem(item.upgrade_from, getItemById) : null;
    const allComps = comps.length ? comps : (upgFrom ? [upgFrom] : []);

    const upgsTo = (item.upgrades_to || item.upgrade_to || [])
      .map((id) => resolveItem(id, getItemById)).filter(Boolean);

    if (allComps.length > 0) {
      const compItems = allComps.map((c) =>
        el('span', { class: 'st-comp-name' },
          itemIconNode(c, { cls: 'st-comp-icon' }),
          el('span', {}, c.name),
        ),
      );
      children.push(el('div', { class: 'st-comp-bar' },
        el('span', { class: 'st-comp-label' }, 'Компоненты:'),
        ...compItems,
      ));
    } else if (upgsTo.length > 0) {
      const upgItems = upgsTo.map((u) =>
        el('span', { class: 'st-comp-name' },
          itemIconNode(u, { cls: 'st-comp-icon' }),
          el('span', {}, u.name),
        ),
      );
      children.push(el('div', { class: 'st-comp-bar' },
        el('span', { class: 'st-comp-label' }, 'Улучшается в:'),
        ...upgItems,
      ));
    }

    tooltipNode.replaceChildren(...children);
    tooltipNode.style.display = 'block';
    positionTooltip(e);
  });

  document.addEventListener('mouseout', (e) => {
    if (!currentCard) return;
    const from = e.target.closest('[data-item-id]');
    if (from !== currentCard) return;
    const to = e.relatedTarget;
    if (to && currentCard.contains(to)) return;
    currentCard = null;
    tooltipNode.style.display = 'none';
  });

  document.addEventListener('mousemove', positionTooltip);
}

function positionTooltip(e) {
  if (!tooltipNode || tooltipNode.style.display === 'none') return;
  const pad = 12;
  const margin = 8;
  const rect = tooltipNode.getBoundingClientRect();
  let x = e.clientX + pad;
  let y = e.clientY + pad;
  if (x + rect.width > innerWidth - margin) x = e.clientX - rect.width - pad;
  if (y + rect.height > innerHeight - margin) y = e.clientY - rect.height - pad;
  x = Math.min(Math.max(margin, x), innerWidth - margin - rect.width);
  y = Math.min(Math.max(margin, y), innerHeight - margin - rect.height);
  tooltipNode.style.left = `${x}px`;
  tooltipNode.style.top = `${y}px`;
}

// --- панель статов -----------------------------------------------------------

const STAT_DEFS = [
  ['weapon_dps', 'Оружейный DPS', true],
  ['bullet_shield', 'Пулевой щит', false],
  ['spirit_shield', 'Спирит-щит', false],
  ['hp', 'Макс. HP', false],
  ['effective_hp_bullet', 'Эффективное HP (пули)', false],
  ['effective_hp_spirit', 'Эффективное HP (дух)', false],
  ['stamina', 'Выносливость', false],
  ['cooldown_reduction_pct', 'Сокращение КД', true],
  ['spirit_power', 'Сила духа', false],
];

function formatStatValue(key, value) {
  if (key === 'cooldown_reduction_pct') return `${Math.round(value * 100)}%`;
  if (key === 'weapon_dps') return value.toFixed(1);
  if (Number.isFinite(value) && !Number.isInteger(value)) return value.toFixed(1);
  return String(value);
}

function formatDelta(key, delta) {
  if (Math.abs(delta) < 0.05) return null;
  const sign = delta > 0 ? '+' : '-';
  if (key === 'cooldown_reduction_pct') return `${sign}${Math.round(Math.abs(delta) * 100)}%`;
  const rounded = Math.round(Math.abs(delta) * 10) / 10;
  return `${sign}${rounded}`;
}

export function renderStats(stats, baseStats = null) {
  const panel = $('#stats-panel');
  panel.replaceChildren();
  for (const [key, label] of STAT_DEFS) {
    const row = el('div', {}, el('dt', {}, label));
    const isCdr = key === 'cooldown_reduction_pct';
    // Значение уже пришло из движка зажатым до капа (calc: CDR_CAP = 0.55);
    // UI только визуализирует заполнение шкалы и сам факт достижения капа.
    const cdrCap = 0.55;
    const cdrPct = isCdr ? Math.min(stats[key] ?? 0, cdrCap) / cdrCap : 0;
    const atCap = isCdr && (stats[key] ?? 0) >= cdrCap - 1e-9;
    const dd = el('dd', { class: 'flex items-center gap-2' },
      el('span', {}, formatStatValue(key, stats[key])));
    if (baseStats) {
      const deltaText = formatDelta(key, stats[key] - baseStats[key]);
      if (deltaText) {
        const span = el('span', {},
          deltaText);
        span.style.color = stats[key] > baseStats[key] ? '#10B981' : '#ef4444';
        dd.append(span);
      }
    }
    if (isCdr) {
      dd.append(
        el('span', {
          class: `cdr-track${atCap ? ' at-cap' : ''}`,
          title: atCap ? 'Достигнут кап 55%' : 'Кап КД — 55%',
        },
          el('span', {
            class: 'cdr-fill',
            style: `width:${Math.round(Math.min(cdrPct, 1) * 100)}%`,
          })),
      );
      if (atCap) {
        dd.append(el('span', { class: 'cdr-cap-badge' }, 'КАП 55%'));
        row.classList.add('cdr-capped');
      }
    }
    row.append(dd);
    panel.append(row);
  }
  $('#stat-souls').textContent = stats.souls_spent.toLocaleString('ru-RU');
}

// --- менеджер билдов ----------------------------------------------------------

export function renderBuildList(builds, heroById, itemById, { onLoad, onDelete }) {
  const list = $('#build-list');
  if (!list) return;
  if (!builds.length) {
    // Оставляем список пустым — срабатывает CSS .build-list:empty::after
    // с иконкой «⬡» и контрастным текстом
    list.replaceChildren();
    return;
  }
  list.replaceChildren(
    ...builds.map((b) => {
      const heroName = (heroById.get(b.hero_id) || {}).name || b.hero_id;
      const allIds = (b.weapon_slots || []).concat(b.vitality_slots || [],
        b.spirit_slots || [], b.flex_slots || []).filter(Boolean);
      const souls = allIds.reduce(
        (sum, id) => sum + ((itemById.get(id) || {}).cost || 0), 0);
      const date = new Date(b.created_at || Date.now()).toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
      });
      return el(
        'li', { class: 'build-item' },
        el('span', { class: 'bi-hero' }, heroName),
        el('span', { class: 'bi-title' },
          `${b.title} · ${allIds.length} предм. · ${souls.toLocaleString('ru-RU')} душ · ${date}`),
        el('span', { class: 'bi-actions' },
          el('button', { type: 'button', onclick: () => onLoad(b) }, 'Загрузить'),
          el('button', { type: 'button', onclick: () => onDelete(b) }, 'Удалить')),
      );
    }),
  );
}

// --- прочее ------------------------------------------------------------------

let toastTimer = null;
export function showToast(message, isError = false, duration = 2600) {
  const toast = $('#toast');
  if (!toast) return;
  toast.replaceChildren();
  const msgText = document.createTextNode(message);
  const timerBar = el('div', { class: 'toast-timer-bar' });
  toast.append(msgText, timerBar);
  toast.style.setProperty('--toast-duration', `${duration}ms`);
  toast.className = 'toast show' + (isError ? ' danger' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.className = 'toast';
  }, duration);
}

export function setInlineError(selector, message) {
  const node = $(selector);
  if (node) node.textContent = message || '';
}
