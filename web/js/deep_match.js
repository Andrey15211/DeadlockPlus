// deep_match.js — экран 4: разбор матча, кривая душ, AI-тренер.
// Canvas 2D без внешних библиотек; кликабельные/ховерящиеся маркеры событий.

import { postMatchAnalyze, postMatchAnalyzeStream, postMatchReanalyze, fetchMatchReviews,
  fetchMatchRoster, fetchProfile, fetchRecentMatches, fetchHeroes, fetchAIConfig, toast,
  formatErrorMessage } from './api.js';

const CHART_H = 260;           // логическая высота канваса (HTML height=260)
const HIT_RADIUS = 10;         // радиус попадания клика/ховера по маркеру

// ---------- состояние модуля (не DOM — безопасен при import) ----------

let currentReview = null;
let lastCurve = [];            // последняя кривая для redraw на resize
let eventPoints = [];          // [{x, y, idx, time, label}] маркеры для кликов
let hoverIdx = -1;             // индекс маркера под курсором
let activeIdx = -1;            // индекс выбранного кликом маркера (постоянный)
let rafId = 0;                 // rAF-троттлинг перерисовки

// Анимация плавного появления кривой душ и пульсации событий
let isAnimatingIntro = false;
let animProgress = 1.0;
let animStartTime = 0;
const ANIM_DURATION_MS = 1200; // 1.2с плавное появление слева направо
let pulseRafId = null;

// цвета оценок: S+ золото, S зелёный, A бирюзовый, B серый, C красный
const GRADE_STYLES = {
  'S+': { color: '#E5A93C', bg: 'rgba(229,169,60,.14)' },
  'S': { color: '#10B981', bg: 'rgba(16,185,129,.12)' },
  'A': { color: '#2DD4BF', bg: 'rgba(45,212,191,.12)' },
  'B': { color: '#8A91A0', bg: 'rgba(138,145,160,.12)' },
  'C': { color: '#EF4444', bg: 'rgba(239,68,68,.12)' },
};

// DMC5-стиль: шкала заполняется слева направо (C → S+), финальная буква
// «впечатывается» с пружинным отскоком scale(1.6)→1 + свечение.
const GRADE_ORDER = ['C', 'B', 'A', 'S', 'S+'];
const GRADE_SCALE_STEP_MS = 150;   // интервал между сегментами шкалы
const GRADE_IMPACT_DELAY_MS = 180; // пауза после финального сегмента до удара
let gradeAnimToken = 0;            // инвалидация таймеров при повторном рендере

// русские подписи бенчмарков; неизвестные поля получат ключ как подпись
const BENCH_LABELS = {
  souls_at_10: 'Фарм к 10-й минуте',
  lane_efficiency: 'Эффективность линии',
  fight_participation: 'Участие в файтах',
};

function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

// ---------- график ----------

// Двухуровневый кэш графика: статичный фон (сетка, оси, подписи) и Path2D кривой
let bgCanvasCache = null;      // OffscreenCanvas или HTMLCanvasElement для фона
let bgCacheKey = '';          // ключ валидации фона: `${W}_${H}_${dpr}_${maxT}_${maxAbs}`
let cachedFillPath = null;     // Path2D для заливки градиента преимущества
let cachedStrokePath = null;   // Path2D для золотой линии нетворса
let cachedCurveKey = '';       // ключ валидации геометрии путей: `${W}_${H}_${curve.length}_${maxT}_${maxAbs}`
let cachedMetrics = null;      // { W, H, iw, ih, pad, y0, maxT, maxAbs, xOf, yOf }

function getChartMetrics(W, H, curve) {
  const pad = { l: 60, r: 20, t: 20, b: 30 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const maxT = Math.max(...curve.map((p) => p.time_sec), 1);
  const maxAbs = Math.max(...curve.map((p) => Math.abs(p.networth_delta)), 1);
  const y0 = pad.t + ih / 2;
  const xOf = (t) => pad.l + (t / maxT) * iw;
  const yOf = (v) => y0 - (v / maxAbs) * (ih / 2);
  return { W, H, iw, ih, pad, y0, maxT, maxAbs, xOf, yOf };
}

function updateStaticBgCache(W, H, dpr, metrics) {
  const { pad, iw, ih, y0, maxT, maxAbs } = metrics;
  const key = `${W}_${H}_${dpr}_${maxT}_${maxAbs}`;
  if (bgCanvasCache && bgCacheKey === key) {
    return bgCanvasCache;
  }

  const bw = Math.round(W * dpr);
  const bh = Math.round(H * dpr);
  let offscreen;
  if (typeof OffscreenCanvas !== 'undefined') {
    offscreen = new OffscreenCanvas(bw, bh);
  } else {
    offscreen = document.createElement('canvas');
    offscreen.width = bw;
    offscreen.height = bh;
  }

  const bCtx = offscreen.getContext('2d');
  bCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // Сетка (вертикальные деления)
  bCtx.strokeStyle = 'rgba(42,51,70,0.6)';
  bCtx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const x = pad.l + (iw * i) / 4;
    bCtx.beginPath();
    bCtx.moveTo(x, pad.t);
    bCtx.lineTo(x, pad.t + ih);
    bCtx.stroke();
  }

  // Нулевая ось
  bCtx.strokeStyle = 'rgba(229,169,60,0.4)';
  bCtx.beginPath();
  bCtx.moveTo(pad.l, y0);
  bCtx.lineTo(W - pad.r, y0);
  bCtx.stroke();

  // Подписи Y
  bCtx.fillStyle = '#8a91a0';
  bCtx.font = '10.5px Oswald, Barlow, sans-serif';
  bCtx.textAlign = 'right';
  bCtx.fillText(`+${(maxAbs / 1000).toFixed(0)}k`, pad.l - 6, pad.t + 8);
  bCtx.fillText('0', pad.l - 6, y0 + 3);
  bCtx.fillText(`-${(maxAbs / 1000).toFixed(0)}`, pad.l - 6, pad.t + ih - 2);

  // Подписи X (минутные засечки времени)
  bCtx.textAlign = 'center';
  for (let i = 0; i <= 4; i++) {
    const t = (maxT * i) / 4;
    const mm = Math.floor(t / 60);
    bCtx.fillText(`${mm}:${String(Math.floor(t % 60)).padStart(2, '0')}`,
      pad.l + (iw * i) / 4, H - 8);
  }

  bgCanvasCache = offscreen;
  bgCacheKey = key;
  return bgCanvasCache;
}

function updateCurvePaths(curve, metrics) {
  const { W, H, y0, maxT, maxAbs, xOf, yOf } = metrics;
  const key = `${W}_${H}_${curve.length}_${maxT}_${maxAbs}`;
  if (cachedStrokePath && cachedFillPath && cachedCurveKey === key) {
    return { strokePath: cachedStrokePath, fillPath: cachedFillPath };
  }

  const strokePath = new Path2D();
  curve.forEach((p, i) => {
    const x = xOf(p.time_sec);
    const y = yOf(p.networth_delta);
    i === 0 ? strokePath.moveTo(x, y) : strokePath.lineTo(x, y);
  });

  const fillPath = new Path2D();
  fillPath.moveTo(xOf(curve[0].time_sec), y0);
  curve.forEach((p) => fillPath.lineTo(xOf(p.time_sec), yOf(p.networth_delta)));
  fillPath.lineTo(xOf(curve[curve.length - 1].time_sec), y0);
  fillPath.closePath();

  cachedStrokePath = strokePath;
  cachedFillPath = fillPath;
  cachedCurveKey = key;
  return { strokePath, fillPath };
}

function drawChart(canvas, curve, opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth || 800;
  const H = CHART_H;
  canvas.style.height = `${H}px`; // фиксируем layout-высоту от attr height
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  ctx.clearRect(0, 0, W, H);

  if (!curve || curve.length < 2) {
    ctx.fillStyle = '#8a91a0';
    ctx.font = '13.5px Oswald, Barlow, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Нет данных кривой душ', W / 2, H / 2);
    eventPoints = [];
    return;
  }

  const metrics = getChartMetrics(W, H, curve);
  cachedMetrics = metrics;
  const { pad, iw, ih, y0, maxT, xOf, yOf } = metrics;

  // 1. Отрисовка кэшированного статического фона (сетка, оси, подписи) за 1 blit drawImage
  const bgCanvas = updateStaticBgCache(W, H, dpr, metrics);
  if (bgCanvas) {
    ctx.drawImage(bgCanvas, 0, 0, W, H);
  }

  // 2. Отрисовка геометрии кривой с использованием переиспользуемого Path2D
  const { strokePath, fillPath } = updateCurvePaths(curve, metrics);

  const progress = Math.max(0, Math.min(1, opts.progress ?? 1.0));
  const currentDrawWidth = iw * progress;
  const currentMaxX = pad.l + currentDrawWidth;

  // Ограничиваем область отрисовки линии и заливки по текущему прогрессу анимации (слева направо)
  ctx.save();
  ctx.beginPath();
  ctx.rect(pad.l - 1, 0, currentDrawWidth + 1, H);
  ctx.clip();

  // заливка преимущества: вертикальный градиент от нулевой оси с Path2D
  const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + ih);
  grad.addColorStop(0, 'rgba(16,185,129,0.28)');
  grad.addColorStop(0.5, 'rgba(16,185,129,0)');
  grad.addColorStop(0.5, 'rgba(239,68,68,0)');
  grad.addColorStop(1, 'rgba(239,68,68,0.28)');
  ctx.fillStyle = grad;
  ctx.fill(fillPath);

  // золотая линия графика с мягким неоновым свечением через Path2D
  ctx.strokeStyle = '#E5A93C';
  ctx.lineWidth = 2.5;
  ctx.shadowColor = 'rgba(229, 169, 60, 0.55)';
  ctx.shadowBlur = 6;
  ctx.stroke(strokePath);
  ctx.shadowBlur = 0;
  ctx.restore();

  // Анимированная искра на фронте рисования линии
  if (progress > 0.01 && progress < 0.999 && curve.length >= 2) {
    const currentTime = progress * maxT;
    let p0 = curve[0];
    let p1 = curve[curve.length - 1];
    for (let i = 0; i < curve.length - 1; i++) {
      if (curve[i].time_sec <= currentTime && curve[i + 1].time_sec >= currentTime) {
        p0 = curve[i];
        p1 = curve[i + 1];
        break;
      }
    }
    const ratio = p1.time_sec > p0.time_sec ? (currentTime - p0.time_sec) / (p1.time_sec - p0.time_sec) : 0;
    const currentVal = p0.networth_delta + (p1.networth_delta - p0.networth_delta) * ratio;
    const headX = xOf(currentTime);
    const headY = yOf(currentVal);

    ctx.save();
    ctx.shadowColor = '#ffed79';
    ctx.shadowBlur = 10;
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(headX, headY, 3.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // маркеры событий (+ пульсирующий эффект, halo для ховера, кольцо для активного)
  eventPoints = [];
  const now = opts.now || performance.now();

  curve.forEach((p, idx) => {
    if (!p.event) return;
    const x = xOf(p.time_sec);
    const y = yOf(p.networth_delta);

    // Пока линия не дошла до маркера при анимации появления, не рисуем его
    if (x > currentMaxX + 2) return;

    eventPoints.push({ x, y, idx, time: p.time_sec, label: p.event });

    // Пульсирующий ореол ключевых событий (eventPoints pulse effect)
    const phase = ((now / 1000 + idx * 0.35) % 1.5) / 1.5;
    const pulseRadius = 5 + phase * 8;
    const pulseAlpha = Math.max(0, (1 - phase) * 0.75);

    ctx.strokeStyle = `rgba(229, 169, 60, ${pulseAlpha})`;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(x, y, pulseRadius, 0, Math.PI * 2);
    ctx.stroke();

    if (opts.hover === idx) {
      ctx.strokeStyle = '#ffed79';
      ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.arc(x, y, 10, 0, Math.PI * 2); ctx.stroke();
    }
    if (opts.active === idx) {
      ctx.strokeStyle = '#10B981';
      ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.stroke();
    }
    ctx.fillStyle = opts.active === idx ? '#10B981' : (opts.hover === idx ? '#ffed79' : '#E5A93C');
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#070A0F';
    ctx.lineWidth = 2;
    ctx.stroke();
  });
}

function startChartAnimation(canvas, curve) {
  if (pulseRafId) {
    cancelAnimationFrame(pulseRafId);
    pulseRafId = null;
  }
  isAnimatingIntro = true;
  animProgress = 0;
  animStartTime = performance.now();

  function animFrame(now) {
    const elapsed = now - animStartTime;
    const t = Math.min(1, elapsed / ANIM_DURATION_MS);
    // Smooth cubic ease-out
    animProgress = 1 - Math.pow(1 - t, 3);

    const c = canvas || document.getElementById('souls-chart');
    if (c && currentReview) {
      drawChart(c, curve, {
        hover: hoverIdx,
        active: activeIdx,
        progress: animProgress,
        now,
      });
    }

    if (t < 1) {
      requestAnimationFrame(animFrame);
    } else {
      isAnimatingIntro = false;
      animProgress = 1.0;
      startPulseLoop();
    }
  }

  requestAnimationFrame(animFrame);
}

function startPulseLoop() {
  if (pulseRafId) return;
  function pulseFrame(now) {
    const matchTab = document.getElementById('tab-match');
    const canvas = document.getElementById('souls-chart');
    if (!matchTab || matchTab.classList.contains('hidden') || !canvas || !currentReview) {
      pulseRafId = null;
      return;
    }
    drawChart(canvas, lastCurve, {
      hover: hoverIdx,
      active: activeIdx,
      progress: 1.0,
      now,
    });
    pulseRafId = requestAnimationFrame(pulseFrame);
  }
  pulseRafId = requestAnimationFrame(pulseFrame);
}

function scheduleRedraw() {
  if (rafId) return;
  rafId = requestAnimationFrame(() => {
    rafId = 0;
    const canvas = document.getElementById('souls-chart');
    if (canvas && currentReview) {
      if (!isAnimatingIntro && !pulseRafId) {
        startPulseLoop();
      } else {
        drawChart(canvas, lastCurve, {
          hover: hoverIdx,
          active: activeIdx,
          progress: isAnimatingIntro ? animProgress : 1.0,
          now: performance.now(),
        });
      }
    }
  });
}

function findHit(canvas, clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  return eventPoints.find((p) => Math.hypot(p.x - x, p.y - y) < HIT_RADIUS) || null;
}

// контекстная подсказка по знаку/размеру дельты (в точке события нет комментария)
function eventHint(delta) {
  const k = (Math.abs(delta) / 1000).toFixed(1);
  let head;
  let tail;
  if (delta >= 10000) {
    head = `перевес +${k}k душ`;
    tail = 'Конвертируй в объекты и пуш — преимущество тает само.';
  } else if (delta >= 0) {
    head = `перевес +${k}k душ`;
    tail = 'Держи темп, не умирай без размена.';
  } else if (delta <= -10000) {
    head = `отставание -${k}k душ`;
    tail = 'Играй от обороны: безопасный фарм и командная игра.';
  } else {
    head = `отставание -${k}k душ`;
    tail = 'Отставание отыгрывается аккуратной игрой без рисков.';
  }
  return `Ключевой момент: ${head}. ${tail}`;
}

function showEventCard(hit) {
  const card = document.getElementById('souls-event-card');
  if (!card) return;
  const point = (lastCurve || []).find((p) => p.time_sec === hit.time) || null;
  const delta = Number(point?.networth_delta ?? 0);
  card.classList.remove('hidden');
  card.innerHTML = `<span class="se-time">${fmtTime(hit.time)}</span>
    ${esc(hit.label)} — <strong>${delta >= 0 ? '+' : ''}${(delta / 1000).toFixed(1)}k душ</strong>.
    ${eventHint(delta)}`;
}

function hideEventCard() {
  const card = document.getElementById('souls-event-card');
  if (card) card.classList.add('hidden');
}

function bindChartEvents(canvas) {
  canvas.addEventListener('click', (e) => {
    const hit = findHit(canvas, e.clientX, e.clientY);
    if (hit) {
      activeIdx = hit.idx;
      showEventCard(hit);
    } else {
      activeIdx = -1;
      hideEventCard();
    }
    scheduleRedraw();
  });

  canvas.addEventListener('mousemove', (e) => {
    const hit = findHit(canvas, e.clientX, e.clientY);
    const idx = hit ? hit.idx : -1;
    canvas.style.cursor = hit ? 'pointer' : 'crosshair';
    if (idx !== hoverIdx) {
      hoverIdx = idx;
      scheduleRedraw();
    }
  });

  canvas.addEventListener('mouseleave', () => {
    if (hoverIdx !== -1) {
      hoverIdx = -1;
      scheduleRedraw();
    }
  });
}

// ---------- карточки ----------

function renderMistake(km) {
  const box = document.getElementById('key-mistake');
  if (!km || !km.time) {
    box.innerHTML = '<p class="section-hint">Ошибка не определена.</p>';
    return;
  }
  box.innerHTML = `
    <div class="km-time">${esc(km.time)}</div>
    <div class="km-loc">${esc(km.location ?? '—')}</div>
    <div class="km-event">${esc(km.event ?? '')}</div>
    <div class="km-why">${esc(km.why_fatal ?? '')}</div>
    <div class="km-correct">${esc(km.correct_play ?? '')}</div>`;
}

// DMC5 Style Titles
const DMC_RANK_NAMES = {
  'C': { title: 'DULL', desc: 'Базовый уровень' },
  'B': { title: 'BRUTAL', desc: 'Хороший темп' },
  'A': { title: 'ANARCHIC', desc: 'Отличная игра' },
  'S': { title: 'SAVAGE!', desc: 'Доминирование' },
  'S+': { title: "SMOKIN' SEXY STYLE!!", desc: 'Мастерство' },
};

// Ранги-«боги»: финальный удар + пламенное свечение
const GOD_RANKS = new Set(['S', 'S+']);

function gradeGradeFor(g) {
  return GRADE_ORDER.includes(g) ? g : (GRADE_STYLES[g] ? g : 'C');
}

// ---------- V2.0 М2: процедурный звук Style Rank (Web Audio, без файлов) ----------

let dmcAudioCtx = null;

// Ленивый контекст: создаётся при первом ударе; недоступность Web Audio
// (или запрет autoplay) не должна ломать анимацию.
function getDmcAudioCtx() {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!dmcAudioCtx) dmcAudioCtx = new Ctx();
    if (dmcAudioCtx.state === 'suspended') {
      dmcAudioCtx.resume().catch(() => {});
    }
    return dmcAudioCtx;
  } catch {
    return null;
  }
}

// Щелчок нарастающей частоты при переключении букв C -> B -> A -> S -> S+.
// step — индекс ступени (0-based), maxSteps — число ступеней до целевого ранга.
function playRankTickSound(step, maxSteps) {
  try {
    const ctx = getDmcAudioCtx();
    if (!ctx) return;
    const total = Math.max(1, Number(maxSteps) || 1);
    const t = Math.min(1, Math.max(0, (Number(step) || 0) / total));
    const freq = 320 + t * 480;              // 320Hz -> 800Hz
    const now = ctx.currentTime;

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'triangle';
    osc.frequency.setValueAtTime(freq, now);
    osc.frequency.exponentialRampToValueAtTime(freq * 1.45, now + 0.06);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.14, now + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.12);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.14);
  } catch {
    // звук не критичен для анимации
  }
}

// Сокрушительный басовый спад в момент финального удара.
function playImpactSound(isGodRank) {
  try {
    const ctx = getDmcAudioCtx();
    if (!ctx) return;
    const now = ctx.currentTime;

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sawtooth';
    osc.frequency.setValueAtTime(isGodRank ? 150 : 120, now);
    osc.frequency.exponentialRampToValueAtTime(isGodRank ? 32 : 45, now + 0.55);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.3, now + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.7);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.72);
  } catch {
    // звук не критичен для анимации
  }
}

// ---------- V2.0 М2: анимация оценки в стиле DMC5 ----------

const GRADE_STEP_MS = 220;            // интервал ступеней C -> B -> ... (200-240ms)
const GRADE_START_DELAY_MS = 250;     // пауза до старта счётчика: отчёт уже перед глазами
const GAUGE_CIRCUMFERENCE = 314.16;   // 2 * PI * 50 — длина круговой шкалы

// Ступенчатое нарастание ранга: буква C -> B -> ... -> targetGrade, динамическое
// заполнение дуги dmc-gauge-fill, финальный «Style Slam» с ударной волной,
// для S/S+ — пламенное свечение. Токен gradeAnimToken гасит устаревшие таймеры.
function animateDmcGrade(targetGrade) {
  const badge = document.getElementById('grade-badge');
  if (!badge) return;

  const gaugeFill = document.getElementById('dmc-gauge-fill');
  const titleEl = document.getElementById('dmc-rank-title');
  const descEl = document.getElementById('dmc-rank-desc');

  const target = gradeGradeFor(targetGrade);
  const targetIdx = Math.max(0, GRADE_ORDER.indexOf(target));
  const steps = targetIdx + 1;
  const token = ++gradeAnimToken;

  function applyGradeAppearance(gKey, isFinal) {
    const st = GRADE_STYLES[gKey] || GRADE_STYLES.C;
    const meta = DMC_RANK_NAMES[gKey] || DMC_RANK_NAMES.C;
    badge.textContent = gKey;
    const cls = `grade-${gKey.toLowerCase().replace('+', '-plus')}`;
    badge.className = `grade-badge ${cls}`;
    badge.style.setProperty('--dmc-badge-color', st.color);
    badge.style.color = st.color;
    badge.style.borderColor = st.color;
    badge.style.boxShadow = `0 0 24px ${st.color}55, inset 0 0 12px ${st.color}22`;

    if (gaugeFill) {
      gaugeFill.style.setProperty('--dmc-gauge-color', st.color);
      gaugeFill.style.stroke = st.color;
    }
    if (titleEl) {
      titleEl.textContent = meta.title;
      titleEl.style.color = st.color;
      titleEl.style.textShadow = `0 0 12px ${st.color}88`;
    }
    if (descEl) {
      descEl.textContent = isFinal ? `Оценка ИИ: ${meta.desc}` : meta.desc;
    }
  }

  // Доля заполнения круговой шкалы для ступени idx: (idx + 1) / steps
  function fillGauge(idx) {
    if (!gaugeFill) return;
    const fraction = (idx + 1) / steps;
    const offset = GAUGE_CIRCUMFERENCE * (1 - fraction);
    gaugeFill.style.transition =
      `stroke-dashoffset ${GRADE_STEP_MS}ms cubic-bezier(0.2, 0.8, 0.2, 1)`;
    gaugeFill.style.strokeDashoffset = String(offset);
  }

  function triggerFinalImpact(st) {
    badge.classList.remove('dmc-letter-impact', 'dmc-final-impact',
      'dmc-letter-switch', 'dmc-flame-pulse');
    void badge.offsetWidth;                       // перезапуск анимаций
    badge.style.setProperty('--gold-color', st.color);
    badge.classList.add('dmc-letter-impact');
    if (GOD_RANKS.has(target)) badge.classList.add('dmc-flame-pulse');

    // Радиальная вспышка вокруг буквы
    badge.querySelector('.grade-flash')?.remove();
    const flash = document.createElement('div');
    flash.className = 'grade-flash';
    flash.style.setProperty('--g-color', st.color);
    badge.appendChild(flash);
    window.setTimeout(() => flash.remove(), 560);

    // Ударная волна от плашки ранга
    const host = badge.parentNode;
    host?.querySelector('.stamp-shockwave')?.remove();
    const wave = document.createElement('div');
    wave.className = 'stamp-shockwave dmc-shockwave';
    wave.style.borderColor = st.color;
    wave.style.setProperty('--gold-color', st.color);
    host?.appendChild(wave);
    window.setTimeout(() => wave.remove(), 700);

    spawnRankSparks(badge, st.color);
    playImpactSound(GOD_RANKS.has(target));
  }

  function runStep(idx) {
    if (gradeAnimToken !== token) return;
    const gKey = GRADE_ORDER[idx];
    const isFinal = idx === targetIdx;
    applyGradeAppearance(gKey, isFinal);

    if (gaugeFill) {
      if (idx === 0) {                            // старт с пустой шкалы
        gaugeFill.style.transition = 'none';
        gaugeFill.style.strokeDashoffset = String(GAUGE_CIRCUMFERENCE);
        void gaugeFill.offsetWidth;               // force reflow
      }
      fillGauge(idx);
    }

    // Щелчок нарастающей частоты на каждой смене буквы
    playRankTickSound(idx, steps);

    if (isFinal) {
      // Поп буквы до удара + сам удар через паузу
      badge.classList.remove('dmc-letter-switch');
      void badge.offsetWidth;
      badge.classList.add('dmc-letter-switch');
      window.setTimeout(() => {
        if (gradeAnimToken !== token) return;
        triggerFinalImpact(GRADE_STYLES[gKey] || GRADE_STYLES.C);
      }, GRADE_STEP_MS);
    } else {
      badge.classList.remove('dmc-letter-switch');
      void badge.offsetWidth;
      badge.classList.add('dmc-letter-switch');
      window.setTimeout(() => {
        if (gradeAnimToken !== token) return;
        runStep(idx + 1);
      }, GRADE_STEP_MS + 20);
    }
  }

  // V2.1: стартовая пауза — отчёт уже зафиксирован скроллом перед глазами
  window.setTimeout(() => {
    if (gradeAnimToken === token) runStep(0);
  }, GRADE_START_DELAY_MS);
}

// ---------- Процедурные золотые искры (Canvas 2D) ----------

function spawnRankSparks(badge, color) {
  try {
    const rect = badge.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const canvas = document.createElement('canvas');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    canvas.style.position = 'fixed';
    canvas.style.top = '0';
    canvas.style.left = '0';
    canvas.style.pointerEvents = 'none';
    canvas.style.zIndex = '9999';
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    if (!ctx) { canvas.remove(); return; }

    const sparkCount = 40;
    const sparks = [];
    for (let i = 0; i < sparkCount; i++) {
      const angle = Math.random() * Math.PI * 2;
      const speed = 3.5 + Math.random() * 8.5;
      sparks.push({
        x: cx,
        y: cy,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        size: 2 + Math.random() * 2.5,
        alpha: 1.0,
        decay: 0.02 + Math.random() * 0.03,
      });
    }

    let animId = null;
    function renderSparks() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      let alive = false;
      for (const p of sparks) {
        p.x += p.vx;
        p.y += p.vy;
        p.vy += 0.16; // гравитация
        p.vx *= 0.98;
        p.alpha -= p.decay;
        if (p.alpha > 0) {
          alive = true;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.globalAlpha = Math.max(0, p.alpha);
          ctx.shadowBlur = 8;
          ctx.shadowColor = color;
          ctx.fill();
        }
      }
      if (alive) {
        animId = requestAnimationFrame(renderSparks);
      } else {
        cancelAnimationFrame(animId);
        canvas.remove();
      }
    }
    renderSparks();
  } catch {
    // sparks fallback safely
  }
}

// ---------- Модуль 5: индикатор памяти тренера ----------

const COACH_MEMORY_WITH_HISTORY =
  'Учтена история ваших игр: частые паттерны и прогресс синхронизированы';
const COACH_MEMORY_FIRST_MATCH =
  'Первый разбор на герое: накапливаю базу ваших матчей для персональных советов';

// progress — объект из отчёта review (ai.progress.build_player_context):
// { trend: {count, previous}, weaknesses, auto_context }.
function renderCoachMemory(progress) {
  const badge = document.getElementById('coach-memory-badge');
  const text = document.getElementById('coach-memory-text');
  if (!badge || !text) return;

  const p = progress && typeof progress === 'object' ? progress : null;
  const autoContext = p && typeof p.auto_context === 'string' ? p.auto_context.trim() : '';
  // previous_matches_count бэкенд пока не отдаёт — читаем поля, которые есть:
  // trend.count (всего разборов, включая текущий) и trend.previous.count (прошлые).
  const trend = p && p.trend && typeof p.trend === 'object' ? p.trend : null;
  const count = Number(trend?.count);
  const previousCount = Number(
    p?.player_trend?.previous_matches_count ?? trend?.previous?.count,
  );
  const hasHistory = Boolean(autoContext)
    || (Number.isFinite(previousCount) && previousCount > 0)
    || (Number.isFinite(count) && count > 1);

  if (!p) {
    badge.classList.add('hidden');
    text.textContent = '';
    return;
  }

  text.textContent = hasHistory ? COACH_MEMORY_WITH_HISTORY : COACH_MEMORY_FIRST_MATCH;
  badge.classList.remove('hidden');
}

function renderConclusion(conclusion, grade, progress) {
  const list = document.getElementById('coach-conclusion');
  if (list) {
    list.innerHTML = (Array.isArray(conclusion) ? conclusion : [])
      .filter((c) => c != null)
      .map((c) => `<li>${esc(c)}</li>`).join('');
  }
  renderCoachMemory(progress);
  animateDmcGrade(grade);
}

function renderBenchmarks(bench) {
  const box = document.getElementById('benchmarks');
  const b = bench || {};
  const primaryKeys = ['souls_at_10', 'lane_efficiency', 'fight_participation'];
  const entries = primaryKeys
    .filter((k) => typeof b[k] === 'number' && Number.isFinite(b[k]))
    .map((k) => [k, b[k]]);

  if (!entries.length) {
    box.innerHTML = '<p class="section-hint">Бенчмарки недоступны.</p>';
    return;
  }
  box.innerHTML = entries.map(([key, v]) => {
    const label = BENCH_LABELS[key] ?? key;
    let pct;
    let textValue;
    let subHint = '';

    if (key === 'souls_at_10') {
      const actual = b.actual_souls || (v > 10 ? Math.round(v) : null);
      const target = b.target_souls || 4200;
      if (actual && target) {
        pct = Math.round((actual / target) * 100);
        textValue = `${pct}% (${actual.toLocaleString('ru-RU')} / ${target.toLocaleString('ru-RU')} душ)`;
      } else if (v > 10) {
        pct = Math.round((v / target) * 100);
        textValue = `${pct}% (${Math.round(v).toLocaleString('ru-RU')} душ)`;
      } else {
        pct = Math.round(v * 100);
        textValue = `${pct}%`;
      }
      if (pct > 100) {
        subHint = ` (+${pct - 100}% сверх эталона роли)`;
      }
    } else if (key === 'fight_participation') {
      pct = v > 10 ? Math.round(v) : Math.round(v * 100);
      if (b.team_kills && b.player_ka != null) {
        textValue = `${pct}% (${b.player_ka} из ${b.team_kills} киллов)`;
      } else {
        textValue = `${pct}%`;
      }
    } else {
      pct = v > 10 ? Math.round(v) : Math.round(v * 100);
      textValue = `${pct}%`;
    }

    const shown = Math.max(0, pct);
    const fill = Math.min(100, shown);
    const above = pct >= 100;
    const valueStyle = above ? 'color:var(--emerald,#10B981)' : '';
    return `<div class="bench-bar">
      <div class="bb-label">
        <span>${esc(label)}<span class="bb-subhint" style="color:var(--muted,#8A91A0);font-size:11px;font-weight:normal;margin-left:5px">${esc(subHint)}</span></span>
        <span class="bb-value" style="${valueStyle}">${esc(textValue)}</span>
      </div>
      <div class="bb-track"><div class="bb-fill" style="width:${fill}%"></div></div>
    </div>`;
  }).join('');
}

function renderCorrections(review) {
  const box = document.getElementById('item-corrections');
  const items = (review.item_analysis?.corrections) || [];
  box.innerHTML = items.length
    ? items.map((c) => `<div class="item-correction">
        <div class="ic-slot">${esc(c.slot ?? '')}</div>
        <div>${esc(c.sold_or_skipped ?? '')} → <span class="ic-reco">${esc(c.recommended ?? '')}</span></div>
        <div class="ic-reason">${esc(c.reason ?? '')}</div>
      </div>`).join('')
    : '<p class="section-hint">Коррекция предметов не требуется / данных нет.</p>';
}

// ---------- V2.0 М3: личный прогресс на герое ----------

// Дельта печатается со знаком: 650 -> "+650", -2 -> "-2", 1.5 -> "+1.5".
function fmtDelta(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '0';
  const abs = Math.abs(n);
  const body = Number.isInteger(abs) ? String(abs) : abs.toFixed(1);
  if (n > 0) return `+${body}`;
  if (n < 0) return `-${body}`;
  return '0';
}

// good: true — улучшение (зелёный), false — просадка (красный),
// null — без изменений (нейтральный).
function trendCard(label, text, good) {
  const cls = good === null ? 'flat' : (good ? 'good' : 'danger');
  const icon = good === null ? '⚪' : (good ? '🟢' : '🔴');
  return `<div class="trend-card ${cls}">
    <div class="tc-label">${esc(label)}</div>
    <div class="tc-value">${icon} ${esc(text)}</div>
  </div>`;
}

function renderPersonalTrend(benchmark) {
  const panel = document.getElementById('personal-trend-panel');
  if (!panel) return;
  const cards = document.getElementById('trend-cards');
  const summary = document.getElementById('trend-summary');

  const b = benchmark && typeof benchmark === 'object' ? benchmark : null;
  const games = Number(b?.games);
  if (!b || !Number.isFinite(games) || games < 1) {
    panel.classList.add('hidden');
    if (cards) cards.innerHTML = '';
    if (summary) summary.textContent = '';
    return;
  }
  panel.classList.remove('hidden');

  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const souls = num(b.souls_at_10_diff);
  const deaths = num(b.early_deaths_diff);
  const kda = num(b.kda_diff);

  const dSouls = souls;
  const dDeaths = deaths === null ? null : -deaths; // меньше смертей = рост
  const dKda = kda;

  const html = [];
  html.push(trendCard('Души к 10м',
    dSouls === null ? '— нет данных' : `${fmtDelta(dSouls)} душ`,
    dSouls === null ? null : dSouls > 0));
  html.push(trendCard('Ранние смерти до 10м',
    dDeaths === null ? '— нет данных' : `${fmtDelta(dDeaths)} смерти`,
    dDeaths === null ? null : dDeaths > 0));
  html.push(trendCard('Дельта KDA',
    dKda === null ? '— нет данных' : `KDA ${fmtDelta(dKda)}`,
    dKda === null ? null : dKda > 0));

  if (cards) cards.innerHTML = html.join('');
  if (summary) summary.textContent = b.trend_summary ? String(b.trend_summary) : '';
}

// ---------- Модуль 4: карта смертей ----------

const DEATH_MARKER_LIMIT = 60;
let currentDeaths = [];
const killerNameCache = new Map(); // слаг героя -> отображаемое имя
let killerNamesRequested = false;

// слаг убийцы ("haze") -> человекочитаемое имя, если каталог героев уже подгружен
function prettyKiller(slug) {
  const s = String(slug ?? '').trim();
  if (!s) return 'неизвестный убийца';
  const cached = killerNameCache.get(s.toLowerCase());
  if (cached) return cached;
  const pretty = s.replaceAll('-', ' ').replaceAll('_', ' ');
  return pretty.charAt(0).toUpperCase() + pretty.slice(1);
}

// Каталог героев тянется лениво и один раз: до его прихода показываем слаг,
// после — перерисовываем открытую карту смертей.
function loadKillerNames() {
  if (killerNamesRequested) return;
  killerNamesRequested = true;
  fetchHeroes()
    .then((data) => {
      const list = Array.isArray(data) ? data
        : Array.isArray(data?.heroes) ? data.heroes
          : data && typeof data === 'object' ? Object.values(data) : [];
      for (const h of list) {
        const slug = h?.slug ?? h?.name_id ?? h?.id;
        const name = h?.name ?? h?.localized_name;
        if (slug && name) killerNameCache.set(String(slug).toLowerCase(), String(name));
      }
      if (killerNameCache.size) renderDeathMap(currentReview?.deaths_geo);
    })
    .catch(() => { /* без каталога показываем слаг */ });
}

function deathTimeLabel(t) {
  return Number.isFinite(t) ? fmtTime(t) : '—';
}

function deathCardHtml(d, idx) {
  return `<div class="death-item-card" data-idx="${idx}" role="button" tabindex="0">
    <div class="dic-row">
      <span class="dic-num">${idx + 1}</span>
      <span class="dic-time">${esc(deathTimeLabel(d.t))}</span>
      <span class="dic-killer">${esc(prettyKiller(d.killer))}</span>
    </div>
    ${d.reason ? `<div class="dic-reason">${esc(d.reason)}</div>` : ''}
  </div>`;
}

// Подсветка «маркер <-> строка списка»: класс ставится обеим половинам пары.
function highlightDeath(idx, cls) {
  const layer = document.getElementById('death-markers-layer');
  const list = document.getElementById('death-details-list');
  for (const el of layer ? layer.querySelectorAll('[data-idx]') : []) {
    el.classList.toggle(cls, Number(el.dataset.idx) === idx);
  }
  for (const el of list ? list.querySelectorAll('.death-item-card') : []) {
    el.classList.toggle(cls, Number(el.dataset.idx) === idx);
  }
}

function selectDeath(idx) {
  const d = currentDeaths[idx];
  if (!d) return;
  highlightDeath(idx, 'active');
  const box = document.getElementById('death-details');
  if (box) {
    box.classList.remove('hidden');
    box.innerHTML = `<div class="death-detail">
      <div class="dd-head">
        <span class="dd-time">Смерть ${idx + 1} · ${esc(deathTimeLabel(d.t))}</span>
        <span class="dd-killer">Убийца: ${esc(prettyKiller(d.killer))}</span>
      </div>
      <div class="dd-reason">${esc(d.reason || 'Вердикт по этой смерти не указан.')}</div>
    </div>`;
  }
  const list = document.getElementById('death-details-list');
  const card = list?.querySelector(`.death-item-card[data-idx="${idx}"]`);
  if (card && typeof card.scrollIntoView === 'function') card.scrollIntoView({ block: 'nearest' });
}

function wireDeathMap() {
  const panel = document.getElementById('death-map-panel');
  if (!panel || panel.dataset.wired === '1') return;
  panel.dataset.wired = '1';
  const idxOf = (e) => {
    const el = e.target instanceof Element && e.target.closest('.death-marker, .death-item-card');
    return el ? Number(el.dataset.idx) : -1;
  };
  panel.addEventListener('click', (e) => {
    const idx = idxOf(e);
    if (idx >= 0) selectDeath(idx);
  });
  panel.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const idx = idxOf(e);
    if (idx >= 0) { e.preventDefault(); selectDeath(idx); }
  });
  panel.addEventListener('mouseover', (e) => {
    const idx = idxOf(e);
    if (idx >= 0) highlightDeath(idx, 'hover');
  });
  panel.addEventListener('mouseout', (e) => {
    const idx = idxOf(e);
    if (idx >= 0) highlightDeath(idx, 'hover');
  });
}

// deaths_geo: [{ t, pos_x, pos_y, killer, reason }] — координаты в долях 0..1.
// Пусто/нет данных (разбор по скриншоту) — панель скрывается целиком.
function renderDeathMap(deathsGeo) {
  const panel = document.getElementById('death-map-panel');
  const layer = document.getElementById('death-markers-layer');
  const list = document.getElementById('death-details-list');
  if (!panel || !layer || !list) return;

  const raw = Array.isArray(deathsGeo) ? deathsGeo : [];
  currentDeaths = raw
    .map((d) => ({
      t: Number(d?.t),
      x: Number(d?.pos_x),
      y: Number(d?.pos_y),
      killer: d?.killer,
      reason: d?.reason,
    }))
    .filter((d) => Number.isFinite(d.x) && Number.isFinite(d.y))
    .slice(0, DEATH_MARKER_LIMIT);

  if (!currentDeaths.length) {
    panel.classList.add('hidden');
    layer.innerHTML = '';
    list.innerHTML = '<p class="section-hint">Кликните по маркеру смерти на карте для деталей.</p>';
    return;
  }

  panel.classList.remove('hidden');
  wireDeathMap();
  loadKillerNames();

  const pct = (v) => Math.min(98.5, Math.max(1.5, v * 100)).toFixed(2);
  layer.innerHTML = currentDeaths.map((d, i) => (
    `<button type="button" class="death-marker" data-idx="${i}"
      style="left: calc(${pct(d.x)}% - 12px); top: calc(${pct(d.y)}% - 12px);"
      title="Смерть ${i + 1} · ${esc(deathTimeLabel(d.t))} · ${esc(prettyKiller(d.killer))}"
      aria-label="Смерть ${i + 1} на ${esc(deathTimeLabel(d.t))}">${i + 1}</button>`
  )).join('');

  list.innerHTML = '<p class="section-hint">Кликните по маркеру смерти на карте для деталей.</p>'
    + '<div class="death-details hidden" id="death-details"></div>'
    + currentDeaths.map(deathCardHtml).join('');
}

function renderLaneComparison(lane) {
  const panel = document.getElementById('lane-comparison-panel');
  const box = document.getElementById('lane-comparison-content');
  if (!panel || !box) return;
  if (!lane || (!lane.opponent_hero && !lane.verdict)) {
    panel.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  panel.classList.remove('hidden');
  const heroSlug = String(lane.opponent_hero || '').toLowerCase().replace(/[^a-z0-9_-]/g, '');
  const souls10 = lane.opponent_souls_at_10 ? `${Math.round(lane.opponent_souls_at_10).toLocaleString('ru-RU')} душ` : '—';
  const nw = lane.opponent_net_worth ? `${Math.round(lane.opponent_net_worth).toLocaleString('ru-RU')} душ` : '—';
  box.innerHTML = `
    <div class="lane-hero-card">
      <div class="lane-hero-portrait-wrap">
        <img class="lane-hero-img" src="./assets/heroes/${heroSlug}.webp" alt="${esc(lane.opponent_hero)}"
             onerror="this.style.display='none';this.nextElementSibling?.classList.remove('hidden');">
        <div class="lane-hero-fallback hidden">⚔</div>
      </div>
      <div class="lane-hero-meta">
        <div class="lane-hero-title">Соперник: <span class="lane-hero-name">${esc(lane.opponent_hero)}</span></div>
        <div class="lane-stats-row">
          <span class="lane-stat-badge">KDA: <b>${esc(lane.opponent_kda || '—')}</b></span>
          <span class="lane-stat-badge">Души к 10-й: <b>${esc(souls10)}</b></span>
          ${lane.opponent_net_worth ? `<span class="lane-stat-badge">Итог: <b>${esc(nw)}</b></span>` : ''}
        </div>
      </div>
    </div>
    <div class="lane-verdict-card">
      <div class="lane-verdict-title">Вердикт по линии:</div>
      <div class="lane-verdict-text">${esc(lane.verdict || 'Анализ линии завершён.')}</div>
    </div>
  `;
}

function renderDeathBreakdown(deaths) {
  const panel = document.getElementById('death-breakdown-panel');
  const list = document.getElementById('death-breakdown-list');
  if (!panel || !list) return;
  const items = Array.isArray(deaths) ? deaths.filter(Boolean) : [];
  if (!items.length) {
    panel.classList.add('hidden');
    list.innerHTML = '';
    return;
  }
  panel.classList.remove('hidden');
  list.innerHTML = items.map((d, i) => `
    <div class="db-item-card">
      <div class="db-item-head">
        <span class="db-num">#${i + 1}</span>
        <span class="db-time">⏱ ${esc(d.time || '—')}</span>
        <span class="db-loc">📍 ${esc(d.location || '—')}</span>
      </div>
      <div class="db-cause"><b>Причина / Убийца:</b> ${esc(d.cause || '—')}</div>
      ${d.lesson ? `<div class="db-lesson"><b>Как избежать:</b> ${esc(d.lesson)}</div>` : ''}
    </div>
  `).join('');
}

function renderTeamfights(teamfights) {
  const panel = document.getElementById('teamfights-panel');
  const list = document.getElementById('teamfights-list');
  if (!panel || !list) return;
  const items = Array.isArray(teamfights) ? teamfights.filter(Boolean) : [];
  if (!items.length) {
    panel.classList.add('hidden');
    list.innerHTML = '';
    return;
  }
  panel.classList.remove('hidden');
  list.innerHTML = items.map((tf, i) => `
    <div class="tf-item-card">
      <div class="tf-item-head">
        <span class="tf-badge">Стычка #${i + 1}</span>
        <span class="tf-time">⏱ ${esc(tf.time || '—')}</span>
      </div>
      <div class="tf-outcome"><b>Исход:</b> ${esc(tf.outcome || '')}</div>
      ${tf.lesson ? `<div class="tf-lesson"><b>Урок / Действие:</b> ${esc(tf.lesson)}</div>` : ''}
    </div>
  `).join('');
}

function renderPurchaseTimings(timings) {
  const panel = document.getElementById('purchase-timings-panel');
  const list = document.getElementById('purchase-timings-list');
  if (!panel || !list) return;
  const items = Array.isArray(timings) ? timings.filter(Boolean) : [];
  if (!items.length) {
    panel.classList.add('hidden');
    list.innerHTML = '';
    return;
  }
  panel.classList.remove('hidden');
  list.innerHTML = items.map((pt) => {
    const verdict = String(pt.verdict || 'вовремя').toLowerCase();
    const isLate = verdict.includes('поздн') || verdict.includes('запозда');
    const isGood = verdict.includes('воврем') || verdict.includes('ранн') || verdict.includes('отлич');
    const badgeCls = isLate ? 'pt-badge-late' : (isGood ? 'pt-badge-good' : 'pt-badge-neutral');
    return `
      <div class="pt-item-card">
        <div class="pt-item-head">
          <span class="pt-time">⏱ ${esc(pt.time || '—')}</span>
          <span class="pt-item-name">${esc(pt.item || 'Предмет')}</span>
          <span class="pt-verdict ${badgeCls}">${esc(pt.verdict || 'вовремя')}</span>
        </div>
        ${pt.comment ? `<div class="pt-comment">${esc(pt.comment)}</div>` : ''}
      </div>
    `;
  }).join('');
}

// ---------- V2.1: кинематографичный фокус отчёта (Spotlight) ----------

const SPOTLIGHT_DIM_MS = 2000;   // сколько держится затемнение панели ввода
let spotlightTimer = null;

// Появление отчёта: приглушаем панель ввода, архив матчей и верхний интерфейс
// и включаем янтарное свечение. Позиция прокрутки не меняется: страница
// остаётся там, где её оставил пользователь.
function playSpotlightReveal(resultPanel) {
  if (!resultPanel) return;

  document.querySelectorAll('.match-setup, #recent-matches').forEach((el) => {
    el.classList.add('is-dimmed');
  });
  document.body.classList.add('match-spotlight');

  resultPanel.classList.remove('is-revealing');
  void resultPanel.offsetWidth;                 // перезапуск анимации
  resultPanel.classList.add('is-revealing');

  window.clearTimeout(spotlightTimer);
  spotlightTimer = window.setTimeout(endSpotlightReveal, SPOTLIGHT_DIM_MS);
}

// Возврат яркости интерфейса после показа ранга.
function endSpotlightReveal() {
  window.clearTimeout(spotlightTimer);
  spotlightTimer = null;
  document.querySelectorAll('.is-dimmed').forEach((el) => el.classList.remove('is-dimmed'));
  document.body.classList.remove('match-spotlight');
  document.getElementById('match-result')?.classList.remove('is-revealing');
}

function render(review) {
  currentReview = review;
  lastCurve = review?.souls_curve || [];
  hoverIdx = -1;
  activeIdx = -1;
  const resultPanel = document.getElementById('match-result');
  resultPanel.classList.remove('hidden');
  playSpotlightReveal(resultPanel);
  const canvas = document.getElementById('souls-chart');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label',
    'Кривая преимущества команды по душам по ходу матча. ' +
    'Маркеры — ключевые события (Мидбосс, Шагоход, командные файты), ' +
    'клик по маркеру показывает детали.');
  startChartAnimation(canvas, lastCurve);
  renderMistake(review.key_mistake);
  renderConclusion(review.coach_conclusion, review.performance_grade, review.progress);
  renderBenchmarks(review.stat_benchmarks);
  renderPersonalTrend(review.personal_benchmark);
  renderLaneComparison(review.lane_comparison);
  renderDeathMap(review.deaths_geo);
  renderDeathBreakdown(review.death_breakdown);
  renderTeamfights(review.teamfights);
  renderCorrections(review);
  renderPurchaseTimings(review.purchase_timings);
}

// ---------- последние матчи (TZ A2.1) ----------

const RECENT_LIMIT = 10;

// матч от deadlock-api /v1/players/{id}/match-history: hero_id, match_id,
// start_time (unix-сек), player_kills/player_deaths/player_assists,
// player_match_outcome: 1 = победа, иначе поражение. hero_id — числовой
// api_id, каталог героев хранит его в поле api_id.
function heroName(heroId, heroesCache) {
  const h = heroesCache[heroId];
  return h?.name || h?.localized_name || `Герой ${heroId}`;
}

function fmtDate(unixSec) {
  const d = new Date(Number(unixSec) * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  const day = pad(d.getDate());
  const month = pad(d.getMonth() + 1);
  const year = d.getFullYear();
  const hours = pad(d.getHours());
  const mins = pad(d.getMinutes());
  return `${day}.${month}.${year} ${hours}:${mins}`;
}

// ссылка на analyze(), выставляется в initDeepMatch: renderRecentMatches
// живёт на уровне модуля и не видит замыкание initDeepMatch.
let analyzeRef = null;
// V2.0 М1: переразбор сохранённого матча — тот же шов.
let reanalyzeRef = null;
let isMatchAnalyzing = false;

// ---------- V2.0 М1: глубина разбора (segmented control) ----------

let selectedDepth = 'medium';
const DEPTH_LABELS = { short: 'Короткий', medium: 'Стандартный', deep: 'Глубокий' };

function setSelectedDepth(depth) {
  if (!DEPTH_LABELS[depth]) return;
  selectedDepth = depth;
  document.querySelectorAll('#match-depth-selector .depth-btn').forEach((btn) => {
    const isOn = btn.dataset.depth === depth;
    btn.classList.toggle('active', isOn);
    btn.setAttribute('aria-checked', isOn ? 'true' : 'false');
  });
}

function initDepthSelector() {
  const group = document.getElementById('match-depth-selector');
  if (!group) return;
  group.querySelectorAll('.depth-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (isMatchAnalyzing) return;
      setSelectedDepth(btn.dataset.depth);
    });
  });
  setSelectedDepth(selectedDepth);
}

function renderRecentMatches(payload, heroesCache) {
  const box = document.getElementById('recent-matches-list');
  const panel = document.getElementById('recent-matches');
  const matches = (payload && Array.isArray(payload.matches)) ? payload.matches : [];
  // Панель «Последние матчи» видима ВСЕГДА: до анализа она стоит сразу под
  // панелью ввода, после анализа уезжает вниз под раскрытый разбор матча.
  panel.classList.remove('hidden');
  if (!matches.length) {
    box.innerHTML = payload && payload.account_id
      ? '<p class="section-hint">Матчи не найдены или профиль скрыт в Deadlock.</p>'
      : '<p class="section-hint">Укажите Steam ID в профиле или нажмите «Обновить», чтобы загрузить историю матчей.</p>';
    return;
  }
  // TZ v1.4 F2: строка = [W/L] KDA · герой · дата. W/L — акцентный цвет,
  // герой — нейтральный (пользователь принимал свои матчи за чужие).
  // V2.0 М1: кнопка 🔄 — быстрый переразбор из кэша (depth берётся из
  // переключателя .depth-selector). Ряд — div: вложенный button внутри
  // button невалиден, а внешний клик по строке открывает обычный разбор.
  box.innerHTML = matches.slice(0, RECENT_LIMIT).map((m) => {
    const mid = String(m.match_id ?? '');
    if (!mid) return '';
    const kills = Number(m.player_kills ?? 0);
    const deaths = Number(m.player_deaths ?? 0);
    const assists = Number(m.player_assists ?? 0);
    const won = Number(m.player_match_outcome) === 1;
    const hero = heroName(Number(m.hero_id), heroesCache);
    return `<div class="rm-row" role="button" tabindex="0" data-match-id="${esc(mid)}">
      <span class="rm-result ${won ? 'rm-win' : 'rm-loss'}">${won ? 'W' : 'L'}</span>
      <span class="rm-kda">${kills}/${deaths}/${assists}</span>
      <span class="rm-hero">${esc(hero)}</span>
      <span class="rm-date">${esc(fmtDate(m.start_time))}</span>
      <span class="rm-action">Разобрать</span>
      <button type="button" class="rm-re-btn" data-reanalyze-id="${esc(mid)}"
              title="Переразобрать этот матч с выбранной глубиной">🔄</button>
    </div>`;
  }).join('');
  const openMatch = (row) => {
    if (isMatchAnalyzing) {
      toast('Разбор матча уже выполняется. Пожалуйста, дождитесь окончания.', true);
      return;
    }
    const input = document.getElementById('match-id-input');
    input.value = row.dataset.matchId || '';
    if (analyzeRef) analyzeRef();
  };
  box.querySelectorAll('.rm-row').forEach((row) => {
    row.addEventListener('click', () => openMatch(row));
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openMatch(row);
      }
    });
  });
  box.querySelectorAll('.rm-re-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      // внешний .rm-row не должен запускать обычный разбор
      e.stopPropagation();
      if (isMatchAnalyzing) {
        toast('Разбор матча уже выполняется. Пожалуйста, дождитесь окончания.', true);
        return;
      }
      if (reanalyzeRef) reanalyzeRef(btn.dataset.reanalyzeId);
    });
  });
}

async function loadRecentMatches(force = false) {
  const panel = document.getElementById('recent-matches');
  const refreshBtn = document.getElementById('btn-refresh-matches');
  const spinner = refreshBtn ? refreshBtn.querySelector('.refresh-spinner') : null;
  if (force && refreshBtn) {
    refreshBtn.disabled = true;
    if (spinner) spinner.classList.add('spinning');
  }
  try {
    const [payload, heroes] = await Promise.all([
      fetchRecentMatches(force),
      fetchHeroes().catch(() => null),
    ]);
    const heroesCache = {};
    (Array.isArray(heroes) ? heroes : (heroes?.heroes || [])).forEach((h) => {
      if (h && h.api_id != null) heroesCache[Number(h.api_id)] = h;
    });
    renderRecentMatches(payload, heroesCache);
    if (force) {
      toast('История матчей обновлена');
    }
  } catch (e) {
    if (force) {
      const msg = formatErrorMessage(e.message || e, e.status);
      toast(msg, true);
    }
    // отсутствие сети/профиля — экран не ломается: панель остаётся видимой
    // и показывает подсказку вместо пустоты (422 = Steam ID не заполнен).
    const box = document.getElementById('recent-matches-list');
    panel.classList.remove('hidden');
    box.innerHTML = e.status === 422
      ? '<p class="section-hint">Укажите Steam ID в профиле или нажмите «Обновить», чтобы загрузить историю матчей.</p>'
      : `<p class="section-hint">${esc(formatErrorMessage(e.message || e, e.status))}</p>`;
  } finally {
    if (refreshBtn) {
      refreshBtn.disabled = false;
      if (spinner) spinner.classList.remove('spinning');
    }
  }
}

// ---------- инициализация ----------

export function initDeepMatch() {
  const container = document.getElementById('tab-match');
  if (!container) {
    console.warn('[deep_match] контейнер #tab-match не найден');
    return;
  }

  const btn = document.getElementById('btn-match-analyze');
  const btnOffline = document.getElementById('btn-match-offline');
  const input = document.getElementById('match-id-input');
  const fileInput = document.getElementById('match-screenshot');
  const errEl = document.getElementById('match-error');
  const keyWarning = document.getElementById('ai-key-warning');
  const profileLink = document.getElementById('link-goto-profile');

  if (profileLink) {
    profileLink.addEventListener('click', () => {
      document.querySelector('[data-tab="profile"]')?.click();
    });
  }

  let aiHasKey = false;
  async function checkAIStatus() {
    try {
      const cfg = await fetchAIConfig();
      aiHasKey = Boolean(cfg?.has_key);
      if (keyWarning) {
        keyWarning.classList.toggle('hidden', aiHasKey);
      }
    } catch {
      aiHasKey = false;
    }
  }
  checkAIStatus();

  let pendingImage = null;
  let myAccountId = null;
  fetchProfile().then((p) => {
    const acc = Number(p.account_id);
    if (Number.isFinite(acc) && acc > 0) myAccountId = acc;
  }).catch(() => { /* профиль ещё не создан */ });

  let progressTimer = null;
  let progressStart = 0;
  let maxProgressSec = 0;

  function showProgress() {
    const box = document.getElementById('match-progress');
    box.classList.remove('hidden');
    const vid = document.getElementById('match-progress-video');
    if (vid) {
      vid.currentTime = 0;
      vid.play().catch(() => {});
      if (!vid._hasLoopFix) {
        vid._hasLoopFix = true;
        vid.addEventListener('ended', () => {
          vid.currentTime = 0;
          vid.play().catch(() => {});
        });
      }
    }
    progressStart = Date.now();
    maxProgressSec = 0;
    const text = document.getElementById('match-progress-text');
    if (text) {
      text.textContent = 'Разбираю… 0с';
    }
    progressTimer = setInterval(() => {
      if (text) {
        const sec = Math.floor((Date.now() - progressStart) / 1000);
        if (sec > maxProgressSec) {
          maxProgressSec = sec;
          text.textContent = `Разбираю… ${maxProgressSec}с`;
        }
      }
    }, 500);
    const fill = document.getElementById('match-progress-fill');
    if (fill) fill.style.width = '10%';
  }

  function updateProgress(ev) {
    const text = document.getElementById('match-progress-text');
    const fill = document.getElementById('match-progress-fill');
    if (!fill) return;

    if (ev.type === 'stage') {
      const stageMap = {
        fetch: 25,
        stats: 50,
        llm: 75,
        report: 90,
        done: 100,
      };
      const pct = stageMap[ev.stage];
      if (pct) {
        fill.style.width = `${pct}%`;
      }
    } else if (ev.type === 'chunk') {
      const received = Number(ev.received) || 0;
      const estimated = Number(ev.estimated) || 0;
      if (estimated > 0) {
        const pct = Math.min(95, Math.round((received / estimated) * 100));
        fill.style.width = `${pct}%`;
      }
    } else if (ev.type === 'progress') {
      const sec = typeof ev.seconds === 'number' ? ev.seconds : Math.floor((Date.now() - progressStart) / 1000);
      if (ev.detail) {
        // V2.0 М4: «модель остывает — пробую другую» / «разбираю моделью X»
        if (text) text.textContent = ev.detail + ` (${sec}с)`;
        maxProgressSec = sec;
      } else if (text && sec > maxProgressSec) {
        maxProgressSec = sec;
        text.textContent = `Разбираю… ${maxProgressSec}с`;
      }
    }
  }

  function hideProgress() {
    clearInterval(progressTimer);
    progressTimer = null;
    maxProgressSec = 0;
    const box = document.getElementById('match-progress');
    box.classList.add('hidden');
    const vid = document.getElementById('match-progress-video');
    if (vid) {
      vid.pause();
    }
    const text = document.getElementById('match-progress-text');
    if (text) text.textContent = '';
  }

  // --- Выбор героя из состава матча ---
  const heroPicker = document.getElementById('match-hero-picker');
  const mhpTeams = document.getElementById('mhp-teams');
  const btnMhpConfirm = document.getElementById('btn-mhp-confirm');
  const btnMhpCancel = document.getElementById('btn-mhp-cancel');
  const btnMhpCancelBottom = document.getElementById('btn-mhp-cancel-bottom');

  let selectedPlayer = null;     // { account_id, hero_slug, hero_name }
  let selectedMatchId = null;
  let currentRosterMode = 'llm';

  function hideHeroPicker() {
    if (heroPicker) heroPicker.classList.add('hidden');
  }

  if (btnMhpCancel) btnMhpCancel.addEventListener('click', hideHeroPicker);
  if (btnMhpCancelBottom) btnMhpCancelBottom.addEventListener('click', hideHeroPicker);

  if (btnMhpConfirm) {
    btnMhpConfirm.addEventListener('click', () => {
      if (!selectedPlayer) return;
      hideHeroPicker();
      executeAnalyze(currentRosterMode, selectedPlayer);
    });
  }

  input.addEventListener('input', () => {
    selectedPlayer = null;
    selectedMatchId = null;
    hideHeroPicker();
  });

  async function promptHeroPicker(matchId, mode) {
    errEl.textContent = '';
    currentRosterMode = mode;

    if (btn) btn.disabled = true;
    if (btnOffline) btnOffline.disabled = true;
    if (btn && mode === 'llm') btn.textContent = 'Загрузка состава…';
    if (btnOffline && mode === 'offline') btnOffline.textContent = 'Загрузка состава…';

    try {
      const roster = await fetchMatchRoster(matchId);
      const players = Array.isArray(roster?.players) ? roster.players : [];
      if (!players.length) {
        throw new Error('В матче не найден список игроков');
      }

      const team0 = players.filter((p) => p.team === 0);
      const team1 = players.filter((p) => p.team === 1);

      function renderTeamGrid(teamPlayers, teamTitle, teamClass) {
        return `
          <div class="mhp-team-column">
            <div class="mhp-team-title ${teamClass}">
              <span>🛡 ${esc(teamTitle)}</span>
              <span style="font-size:11px; opacity:0.8; font-weight:normal;">(${teamPlayers.length} игроков)</span>
            </div>
            <div class="mhp-grid">
              ${teamPlayers.map((p) => {
                const isMySteam = myAccountId && Number(p.account_id) === Number(myAccountId);
                const isSelected = selectedPlayer && (
                  (selectedPlayer.account_id && selectedPlayer.account_id === p.account_id) ||
                  (selectedPlayer.hero_slug === p.hero_slug)
                );
                return `
                  <div class="mhp-card ${isSelected || isMySteam ? 'is-selected' : ''}"
                       data-account-id="${p.account_id}"
                       data-hero-slug="${esc(p.hero_slug)}"
                       data-hero-name="${esc(p.hero_name)}">
                    <span class="mhp-check-badge">✓</span>
                    <div class="mhp-portrait-wrap">
                      <img class="mhp-portrait" src="./assets/heroes/${p.hero_slug}.webp"
                           alt="${esc(p.hero_name)}"
                           onerror="this.style.display='none';this.nextElementSibling?.classList.remove('hidden');">
                      <span class="mhp-portrait-fallback hidden">${esc((p.hero_name || p.hero_slug || '?').trim().charAt(0).toUpperCase())}</span>
                    </div>
                    <div class="mhp-hero-name">${esc(p.hero_name)}</div>
                    <div class="mhp-stats">${p.kills}/${p.deaths}/${p.assists} · ${Math.round(p.net_worth / 100) / 10}k</div>
                    ${isMySteam ? '<div style="font-size:10px; color:#ffed79; font-weight:700; margin-top:2px;">★ Ваш Steam ID</div>' : ''}
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        `;
      }

      mhpTeams.innerHTML = renderTeamGrid(team0, 'Команда Сапфир', 'team-0') +
                           renderTeamGrid(team1, 'Команда Эмбер', 'team-1');

      let defaultPlayer = null;
      if (myAccountId) {
        defaultPlayer = players.find((p) => Number(p.account_id) === Number(myAccountId));
      }
      if (defaultPlayer) {
        selectedPlayer = {
          account_id: defaultPlayer.account_id,
          hero_slug: defaultPlayer.hero_slug,
          hero_name: defaultPlayer.hero_name,
        };
        selectedMatchId = matchId;
        if (btnMhpConfirm) {
          btnMhpConfirm.disabled = false;
          btnMhpConfirm.textContent = `Начать разбор за ${defaultPlayer.hero_name} ➔`;
        }
      } else {
        selectedPlayer = null;
        if (btnMhpConfirm) {
          btnMhpConfirm.disabled = true;
          btnMhpConfirm.textContent = 'Выберите героя из списка';
        }
      }

      mhpTeams.querySelectorAll('.mhp-card').forEach((card) => {
        card.addEventListener('click', () => {
          mhpTeams.querySelectorAll('.mhp-card').forEach((c) => c.classList.remove('is-selected'));
          card.classList.add('is-selected');
          const accId = Number(card.dataset.accountId) || null;
          const heroSlug = card.dataset.heroSlug;
          const heroName = card.dataset.heroName;
          selectedPlayer = { account_id: accId, hero_slug: heroSlug, hero_name: heroName };
          selectedMatchId = matchId;
          if (btnMhpConfirm) {
            btnMhpConfirm.disabled = false;
            btnMhpConfirm.textContent = `Начать разбор за ${heroName} ➔`;
          }
        });
        card.addEventListener('dblclick', () => {
          const accId = Number(card.dataset.accountId) || null;
          const heroSlug = card.dataset.heroSlug;
          const heroName = card.dataset.heroName;
          selectedPlayer = { account_id: accId, hero_slug: heroSlug, hero_name: heroName };
          selectedMatchId = matchId;
          hideHeroPicker();
          executeAnalyze(mode, selectedPlayer);
        });
      });

      if (heroPicker) {
        heroPicker.classList.remove('hidden');
        heroPicker.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    } catch (e) {
      const msg = formatErrorMessage(e.message || e, e.status);
      errEl.textContent = msg;
      toast(msg, true);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Разобрать матч';
      }
      if (btnOffline) {
        btnOffline.disabled = false;
        btnOffline.textContent = 'Быстрый оффлайн-анализ';
      }
    }
  }

  async function analyze(mode = 'llm') {
    if (isMatchAnalyzing) {
      toast('Разбор матча уже выполняется. Пожалуйста, дождитесь окончания.', true);
      return;
    }

    errEl.textContent = '';
    const matchId = input.value.trim();
    if (!matchId && !pendingImage) {
      const emptyErr = 'Введи Match ID или загрузи скриншот.';
      errEl.textContent = emptyErr;
      toast(emptyErr, true);
      return;
    }

    if (mode === 'llm') {
      await checkAIStatus();
      if (!aiHasKey) {
        const keyErr = 'Для анализа матча нейросетью укажите API-ключ в профиле или используйте «Быстрый оффлайн-анализ».';
        errEl.textContent = keyErr;
        toast(keyErr, true);
        if (keyWarning) keyWarning.classList.remove('hidden');
        return;
      }
    }

    if (matchId) {
      if (!selectedPlayer || selectedMatchId !== matchId) {
        await promptHeroPicker(matchId, mode);
        return;
      }
    }

    executeAnalyze(mode, selectedPlayer);
  }

  async function executeAnalyze(mode = 'llm', player = null) {
    if (isMatchAnalyzing) {
      toast('Разбор матча уже выполняется. Пожалуйста, дождитесь окончания.', true);
      return;
    }

    errEl.textContent = '';
    const matchId = input.value.trim();
    if (!matchId && !pendingImage) {
      const emptyErr = 'Введи Match ID или загрузи скриншот.';
      errEl.textContent = emptyErr;
      toast(emptyErr, true);
      return;
    }

    if (mode === 'llm') {
      await checkAIStatus();
      if (!aiHasKey) {
        const keyErr = 'Для анализа матча нейросетью укажите API-ключ в профиле или используйте «Быстрый оффлайн-анализ».';
        errEl.textContent = keyErr;
        toast(keyErr, true);
        if (keyWarning) keyWarning.classList.remove('hidden');
        return;
      }
    }

    isMatchAnalyzing = true;
    hideHeroPicker();
    const matchSetup = document.querySelector('.match-setup');
    const recentPanel = document.getElementById('recent-matches');
    if (matchSetup) matchSetup.classList.add('is-busy');
    if (recentPanel) recentPanel.classList.add('is-busy');

    if (input) input.disabled = true;
    if (fileInput) fileInput.disabled = true;
    if (btn) {
      btn.disabled = true;
      if (mode === 'llm') btn.textContent = 'Разбираю…';
    }
    if (btnOffline) {
      btnOffline.disabled = true;
      if (mode === 'offline') btnOffline.textContent = 'Разбираю…';
    }
    document.getElementById('match-result').classList.add('hidden');
    endSpotlightReveal();          // сброс затемнения от прошлого разбора
    showProgress();
    try {
      const body = {};
      if (matchId) body.match_id = matchId;
      if (pendingImage) body.image_b64 = pendingImage;
      if (player?.account_id) body.account_id = player.account_id;
      else if (myAccountId) body.account_id = myAccountId;
      if (player?.hero_slug) body.hero_slug = player.hero_slug;
      if (mode === 'offline') body.mode = 'offline';
      // V2.0 М4: берём модель из селекта «Профиль → Модель», чтобы смена
      // применялась сразу (без отдельного «Сохранить»). Сервер её запомнит.
      const sel = document.getElementById('ai-model-select');
      if (sel?.value) body.model_name = sel.value;

      body.depth = selectedDepth;   // V2.0 М1: переключатель .depth-selector

      const reasoningSel = document.getElementById('ai-reasoning-select');
      if (reasoningSel?.value && reasoningSel.value !== 'auto') {
        body.reasoning_effort = reasoningSel.value;
      }

      const review = await postMatchAnalyzeStream(body, {
        onEvent: updateProgress,
      });
      if (!review) {
        throw new Error('Не удалось получить отчёт анализа матча от сервера.');
      }
      render(review);
      lastPerspective = {
        account_id: player?.account_id ?? myAccountId,
        hero_slug: player?.hero_slug || review.hero_id || null,
      };
      selectedPlayer = null;
    } catch (e) {
      const msg = formatErrorMessage(e.message || e, e.status);
      // V2.0 М4: при ошибке LLM подсказываем попробовать другую модель
      // (опрос: «показывать ошибку и предложить попробовать другую модель»).
      const hint = /Ошибка анализа нейросетью|Превышено время ожидания|timed out|timeout|model_not_found|unsupported_model/i.test(msg)
        ? ' 💡 Попробуйте выбрать другую модель (Профиль → Модель → 🔄) или проверить ключ/роутер.'
        : '';
      const full = msg + hint;
      errEl.textContent = full;
      toast(full, true);
    } finally {
      hideProgress();
      isMatchAnalyzing = false;
      const matchSetup = document.querySelector('.match-setup');
      const recentPanel = document.getElementById('recent-matches');
      if (matchSetup) matchSetup.classList.remove('is-busy');
      if (recentPanel) recentPanel.classList.remove('is-busy');

      if (input) input.disabled = false;
      if (fileInput) fileInput.disabled = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Разобрать матч';
      }
      if (btnOffline) {
        btnOffline.disabled = false;
        btnOffline.textContent = 'Быстрый оффлайн-анализ';
      }
    }
  }

  analyzeRef = (m) => analyze(m || 'llm');

  // --- V2.0 М1: переразбор матча (шапка отчёта + 🔄 в истории) -------------
  // Точка зрения (account_id/hero_slug) последнего разбора — переразбор должен
  // смотреть на игру того же игрока, а не сваливаться на просмотр со стороны.
  let lastPerspective = null;

  async function reanalyze(matchId) {
    if (isMatchAnalyzing) {
      toast('Разбор матча уже выполняется. Пожалуйста, дождитесь окончания.', true);
      return;
    }
    const mid = String(matchId || currentReview?.match_id || input.value.trim() || '');
    if (!mid) {
      toast('Нет матча для переразбора: сначала разберите игру.', true);
      return;
    }

    errEl.textContent = '';
    await checkAIStatus();
    if (!aiHasKey) {
      const keyErr = 'Для переразбора нейросетью укажите API-ключ в профиле или разберите матч заново в оффлайн-режиме.';
      errEl.textContent = keyErr;
      toast(keyErr, true);
      if (keyWarning) keyWarning.classList.remove('hidden');
      return;
    }

    isMatchAnalyzing = true;
    hideHeroPicker();
    const matchSetup = document.querySelector('.match-setup');
    const recentPanel = document.getElementById('recent-matches');
    if (matchSetup) matchSetup.classList.add('is-busy');
    if (recentPanel) recentPanel.classList.add('is-busy');
    if (btn) btn.disabled = true;
    const reBtn = document.getElementById('btn-match-reanalyze');
    if (reBtn) {
      reBtn.disabled = true;
      reBtn.textContent = 'Переразбираю…';
    }
    showProgress();
    try {
      const body = { depth: selectedDepth, mode: 'ai' };
      const acc = lastPerspective?.account_id ?? myAccountId;
      if (acc) body.account_id = acc;
      if (lastPerspective?.hero_slug) body.hero_slug = lastPerspective.hero_slug;

      const review = await postMatchReanalyze(mid, body);
      if (!review) {
        throw new Error('Сервер не вернул отчёт переразбора.');
      }
      input.value = String(review.match_id || mid);
      render(review);
      toast(`Матч переразобран: ${DEPTH_LABELS[selectedDepth] || selectedDepth}`);
    } catch (e) {
      const msg = formatErrorMessage(e.message || e, e.status);
      errEl.textContent = msg;
      toast(msg, true);
    } finally {
      hideProgress();
      isMatchAnalyzing = false;
      if (matchSetup) matchSetup.classList.remove('is-busy');
      if (recentPanel) recentPanel.classList.remove('is-busy');
      if (btn) btn.disabled = false;
      if (reBtn) {
        reBtn.disabled = false;
        reBtn.textContent = '🔄 Переразобрать';
      }
    }
  }

  reanalyzeRef = reanalyze;
  initDepthSelector();

  const reanalyzeBtn = document.getElementById('btn-match-reanalyze');
  if (reanalyzeBtn) {
    reanalyzeBtn.addEventListener('click', () => reanalyze(currentReview?.match_id));
  }

  loadRecentMatches();

  const refreshBtn = document.getElementById('btn-refresh-matches');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => {
      if (!isMatchAnalyzing) loadRecentMatches(true);
    });
  }

  fileInput.addEventListener('change', () => {
    const f = fileInput.files[0];
    if (!f) { pendingImage = null; return; }
    document.getElementById('match-file-name').textContent = `Выбран: ${f.name}`;
    const reader = new FileReader();
    reader.onload = () => { pendingImage = reader.result.split(',')[1]; };
    reader.readAsDataURL(f);
  });

  if (btn) btn.addEventListener('click', () => analyze('llm'));
  if (btnOffline) btnOffline.addEventListener('click', () => analyze('offline'));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      if (!isMatchAnalyzing) analyze('llm');
    }
  });

  bindChartEvents(document.getElementById('souls-chart'));

  // перерисовка на resize (app.js диспатчит resize при активации таба)
  window.addEventListener('resize', scheduleRedraw);

  // история разборов в подсказке хаба
  fetchMatchReviews(1).then((reviews) => {
    if (reviews && reviews.length) {
      const r = reviews[0];
      const hub = document.getElementById('hub-last-match');
      if (hub) {
        hub.textContent = `Последний матч: ${r.match_id} — ${r.result === 'win' ? 'победа' : 'поражение'} (${r.performance_grade})`;
      }
    }
  }).catch(() => { /* сеть/дб недоступны — не критично */ });
}
