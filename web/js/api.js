// api.js — единственный шов фронтенда к серверу. Никаких захардкоженных
// списков героев/предметов: всё через /api/*.

const BASE = '/api';

// TZ v1.4 F2: Человекочитаемое форматирование ошибок 429 и rate-limit.
export function formatErrorMessage(errOrDetail, status) {
  if (!errOrDetail) return 'Неизвестная ошибка';
  const raw = errOrDetail;
  const s = status ?? (typeof raw === 'object' && raw !== null ? raw.status : undefined);
  let text = typeof raw === 'string' ? raw : (raw.message || String(raw));

  let nextSec = null;
  if (typeof raw === 'object' && raw !== null) {
    if (typeof raw.next_request_in === 'number') nextSec = raw.next_request_in;
    else if (raw.detail && typeof raw.detail === 'object' && typeof raw.detail.next_request_in === 'number') {
      nextSec = raw.detail.next_request_in;
    }
  }

  if (nextSec == null && typeof text === 'string') {
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === 'object') {
        const val = parsed.next_request_in ?? parsed.retry_in ?? parsed.retry_after;
        if (typeof val === 'number') nextSec = val;
        else if (typeof parsed.detail === 'string') {
          try {
            const inner = JSON.parse(parsed.detail);
            if (inner && typeof inner.next_request_in === 'number') nextSec = inner.next_request_in;
          } catch { /* ignore */ }
        }
      }
    } catch { /* ignore */ }

    if (nextSec == null) {
      const m = text.match(/["']?next_request_in["']?\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)/i);
      if (m) nextSec = parseFloat(m[1]);
    }
  }

  if (nextSec != null) {
    const mins = Math.max(1, Math.round(nextSec / 60));
    return `Лимит API (3 запроса/час). Повторите через ${mins} мин.`;
  }

  if (s === 429 || /429/.test(text) || /rate[_\s-]?limit/i.test(text)) {
    if (!text || text === 'HTTP 429' || text === '429' || /rate[_\s-]?limit/i.test(text) || text.includes('next_request_in')) {
      return 'Лимит API (3 запроса/час). Повторите позже.';
    }
  }
  return text;
}

async function request(path, options = {}) {
  let resp;
  try {
    resp = await fetch(BASE + path, options);
  } catch (e) {
    throw new Error(`Сеть недоступна: ${e.message}`);
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      detail = data.detail || data.message || detail;
    } catch { /* ignore */ }
    const humanMsg = formatErrorMessage(detail, resp.status);
    const err = new Error(humanMsg);
    err.status = resp.status;
    throw err;
  }
  return resp.status === 204 ? null : resp.json();
}

async function getJson(path) {
  return request(path);
}

async function postJson(path, body, raw = false) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: raw ? body : JSON.stringify(body),
  });
}

async function deleteJson(path) {
  return request(path, { method: 'DELETE' });
}

// Кэширование справочников в sessionStorage с валидацией через If-None-Match (ETag)
export function getCachedReference(key) {
  try {
    if (typeof sessionStorage === 'undefined') return null;
    const raw = sessionStorage.getItem(`cache_${key}`);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setCachedReference(key, data, etag = null) {
  try {
    if (typeof sessionStorage === 'undefined') return;
    sessionStorage.setItem(`cache_${key}`, JSON.stringify(data));
    if (etag) {
      sessionStorage.setItem(`etag_${key}`, etag);
    }
  } catch { /* квота переполнена или приватный режим */ }
}

async function getJsonWithCache(path, cacheKey) {
  let etag = null;
  try {
    if (typeof sessionStorage !== 'undefined') {
      etag = sessionStorage.getItem(`etag_${cacheKey}`);
    }
  } catch { /* ignore */ }

  const headers = {};
  if (etag) {
    headers['If-None-Match'] = etag;
  }

  let resp;
  try {
    resp = await fetch(BASE + path, { headers });
  } catch (e) {
    // Если сеть недоступна, пытаемся вернуть кэш
    const cached = getCachedReference(cacheKey);
    if (cached) return cached;
    throw new Error(`Сеть недоступна: ${e.message}`);
  }

  if (resp.status === 304) {
    const cached = getCachedReference(cacheKey);
    if (cached) return cached;
    // Если по какой-то причине 304, но в кэше пусто — перезапрашиваем без ETag
    return getJson(path);
  }

  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      detail = data.detail || data.message || detail;
    } catch { /* ignore */ }
    const humanMsg = formatErrorMessage(detail, resp.status);
    const err = new Error(humanMsg);
    err.status = resp.status;
    throw err;
  }

  const data = await resp.json();
  const newEtag = resp.headers.get('etag');
  setCachedReference(cacheKey, data, newEtag);
  return data;
}

// --- каталог ---
export const fetchHeroes = () => getJsonWithCache('/heroes', 'heroes');
export const fetchItems = () => getJsonWithCache('/items', 'items');
export const fetchMap = () => getJson('/map');
export const fetchAdvice = (myHeroId, enemyIds, allowMirror = false) =>
  getJson(
    `/advice?my_hero=${encodeURIComponent(myHeroId)}` +
    `&enemies=${encodeURIComponent(enemyIds.join(','))}` +
    `&allow_mirror=${allowMirror ? 'true' : 'false'}`,
  );
export const postCalc = (heroId, itemIds) => postJson('/calc', { hero_id: heroId, item_ids: itemIds });
export const postValidate = (slots) => postJson('/build/validate', slots);
export const postExportBuild = (slots) => postJson('/builds/export', slots);
export const postImportBuild = (code) => postJson('/builds/import', { code });

// --- драфт 6v6 ---
export const postDraftAnalyze = (myTeam, enemyTeam, allowMirror) =>
  postJson('/draft/analyze', { my_team: myTeam, enemy_team: enemyTeam, allow_mirror: allowMirror });
// S5: быстрые сборки по драфту (локальный движок, без LLM на основном пути).
export const postDraftBuilds = (myHero, enemies, { role = null, mode = 'local' } = {}) =>
  postJson('/draft/builds', { my_hero: myHero, enemies, role, mode });
export const postDraftParseScreenshot = (imageB64) =>
  postJson('/draft/parse-screenshot', { image_b64: imageB64 });

// --- билды (SQLite) ---
export const fetchBuilds = (heroId) => getJson('/builds' + (heroId ? `?hero_id=${encodeURIComponent(heroId)}` : ''));
export const postSaveBuild = (build) => postJson('/builds', build);
export const deleteBuild = (id) => deleteJson(`/builds/${encodeURIComponent(id)}`);

// --- профиль ---
export const fetchProfile = () => getJson('/profile');
export const postProfile = (fields) => postJson('/profile', fields);
export const fetchProfileDetect = () => getJson('/profile/detect');
export const fetchProfileSteamInfo = () => getJson('/profile/steam-info');
export const fetchRecentMatches = (force = false) =>
  getJson('/profile/recent-matches' + (force ? '?force=true' : ''));

// --- AI конфиг ---
export const fetchAIConfig = () => getJson('/ai/config');
export const fetchAIModels = () => getJson('/ai/models');
export const postAIConfig = (fields) => postJson('/ai/config', fields);
export const postAITest = () => postJson('/ai/test', {});

// --- deep match ---
export const fetchMatchRoster = (matchId) => getJson(`/match/${encodeURIComponent(matchId)}/roster`);
export const postMatchAnalyze = (payload) => postJson('/match/analyze', payload);
export const fetchMatchReviews = (limit = 20) => getJson(`/match/reviews?limit=${limit}`);
// V2.0 М1: переразбор сохранённого матча из кэша (без повторного запроса к API).
export const postMatchReanalyze = (matchId, payload) =>
  postJson(`/match/reviews/${encodeURIComponent(matchId)}/reanalyze`, payload);

// TZ v1.4 D4/F: SSE-разбор с реальным прогрессом. События:
// {"type":"progress","seconds":N} | {"type":"chunk","received":K,"estimated":M}
// | {"type":"stage","stage":"fetch"} | {"type":"result",...} | {"type":"error",...}
export async function postMatchAnalyzeStream(body, { onEvent } = {}) {
  let resp;
  try {
    resp = await fetch(BASE + '/match/analyze/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    throw new Error(`Сеть недоступна: ${e.message}`);
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = await resp.json();
      detail = data.detail || data.message || detail;
    } catch { /* ignore */ }
    const humanMsg = formatErrorMessage(detail, resp.status);
    const err = new Error(humanMsg);
    err.status = resp.status;
    throw err;
  }
  if (!resp.body || !resp.body.getReader) {
    return postMatchAnalyze(body); // робастность: нет стрима — старый путь
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result = null;
  let error = null;

  const handleEvent = (raw) => {
    if (!raw.startsWith('data:')) return;
    const dataStr = raw.slice(5).trim();
    if (!dataStr) return;
    let ev;
    try { ev = JSON.parse(dataStr); } catch { return; }
    if (ev.type === 'result') { result = ev; return; }
    if (ev.type === 'error') {
      const detail = ev.detail || 'Ошибка разбора';
      const humanMsg = formatErrorMessage(detail, ev.status || (String(detail).includes('429') ? 429 : undefined));
      error = new Error(humanMsg);
      return;
    }
    if (onEvent) onEvent(ev);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, idx).replace(/\r$/, '');
      buffer = buffer.slice(idx + 1);
      handleEvent(line);
    }
  }
  buffer.split('\n').forEach(handleEvent);

  if (error) throw error;
  if (result) return result;
  throw new Error('Связь с сервером завершилась до получения результата анализа. Проверьте модель или повторите запрос.');
}

// --- ассеты (статус только для диагностики; sync — CLI: python -m deadlockforge.assets.ingest) ---
export const fetchAssetsStatus = () => getJson('/assets/status');

// --- контрпики (экран 7) ---
// Датасет большой (~700 КБ) и статичный, поэтому кэшируем в sessionStorage
// с ревалидацией по ETag: второй заход на экран не тянет его заново.
export const fetchCounterpicks = () => getJsonWithCache('/counterpicks', 'counterpicks');

// --- утилиты UI ---
export function toast(message, isError = false) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = formatErrorMessage(message);
  el.className = 'toast show' + (isError ? ' danger' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = 'toast'; }, 2600);
}
