// builds.js — LocalStorage-менеджер билдов + обмен кодами через /api/builds/*.
// Единственный шов к серверу — api.js; localStorage — только клиентские
// сохранения пользователя (ключ deadlockforge.builds).

const LS_KEY = 'deadlockforge.builds';

export function loadSavedBuilds() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveBuilds(builds) {
  localStorage.setItem(LS_KEY, JSON.stringify(builds));
}

export function putBuild({ heroId, slots, name }) {
  const builds = loadSavedBuilds();
  const entry = {
    id: `b${Date.now()}`,
    name: name.trim() || 'Билд без названия',
    heroId,
    slots: structuredClone(slots),
    timestamp: Date.now(),
  };
  builds.unshift(entry);
  saveBuilds(builds);
  return entry;
}

export function deleteBuild(id) {
  const remaining = loadSavedBuilds().filter((b) => b.id !== id);
  saveBuilds(remaining);
  return remaining;
}

// Копирование в буфер: navigator.clipboard может отсутствовать в http —
// fallback на скрытую textarea + execCommand.
export async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* переходим к fallback */ }
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.append(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { ok = false; }
  area.remove();
  return ok;
}
