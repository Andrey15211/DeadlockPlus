// state.js — состояние Билд-лаба и клиентское хранилище слотов.
// 12 универсальных слотов под любые предметы без разграничения на категории.

export const TOTAL_SLOTS = 12;

export function emptySlots() {
  return new Array(TOTAL_SLOTS).fill(null);
}

export const state = {
  heroes: [],
  items: [],
  itemById: new Map(),
  heroId: null,
  slots: emptySlots(),
};

// Проверка возможности экипировки в конкретный слот
export function validateEquip(slots, item, targetIndex) {
  const currentSlots = Array.isArray(slots) ? slots : equippedItems(slots);
  // Если в целевом слоте стоит тот же предмет — ок (перезапись/апгрейд)
  if (currentSlots[targetIndex] === item.id) {
    return null;
  }
  // Дубликат в других слотах
  if (currentSlots.includes(item.id)) {
    return `«${item.name}» уже стоит в билде — дубликаты запрещены.`;
  }
  return null;
}

// Куда предмет встаёт по клику в каталоге:
// 1. Интеллектуальная замена улучшений (Upgrade Swapping):
//    Если экипирован компонент (Т1 -> Т2 -> Т3), заменяем его в том же слоте.
// 2. Иначе — в первый свободный слот из 12.
export function autoTarget(slots, item) {
  const list = Array.isArray(slots) ? slots : (slots.items || emptySlots());

  // 1. Поиск экипированного компонента
  const compIds = new Set(item.components || []);
  if (item.upgrade_from) compIds.add(item.upgrade_from);

  if (compIds.size > 0) {
    for (let i = 0; i < list.length; i++) {
      const equippedId = list[i];
      if (equippedId && compIds.has(equippedId)) {
        return { index: i, isUpgrade: true, replacedId: equippedId };
      }
    }
  }

  // 2. Первый свободный слот
  const idx = list.indexOf(null);
  if (idx !== -1) return { index: idx, isUpgrade: false };
  return null; // все 12 слотов заняты
}

export function equippedItems(slots) {
  if (Array.isArray(slots)) {
    return slots.filter(Boolean);
  }
  if (!slots) return [];
  const ids = [];
  for (const g of ['weapon', 'vitality', 'spirit', 'flex']) {
    if (Array.isArray(slots[g])) {
      for (const id of slots[g]) if (id) ids.push(id);
    }
  }
  return ids;
}

export function soulsSpent(slots, itemById) {
  return equippedItems(slots).reduce((sum, id) => sum + (itemById.get(id)?.cost || 0), 0);
}
