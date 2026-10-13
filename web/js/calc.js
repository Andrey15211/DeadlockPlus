// calc.js — клиентское зеркало engine/calculator.compute().
//
// ВАЖНО: серверный /api/calc — источник истины; формулы ниже обязаны
// совпадать с engine/calculator.py. Зеркало существует только ради
// мгновенного пересчёта статов в Билд-лабе без сетевого запроса на каждый
// клик. Отличие: Python round() — банковское (к чётному), JS Math.round
// округляет .5 вверх, поэтому на отдельных значениях клиент может
// разойтись с сервером на 0.01 — при расхождении верь серверу.

export const CDR_CAP = 0.55;

// round to 2dp (JS .5-up; серверное Python-округление — банковское)
const round2 = (v) => Math.round(v * 100) / 100;

// Effective HP против одного типа урона: щит — отдельный пул, точка за
// точку поглощающий урон своего типа до касания HP, поэтому бюджет
// выживаемости аддитивен (см. engine/calculator.effective_hp).
const effectiveHp = (hp, shield) => hp + shield;

export function computeStats(hero, items) {
  // Герой может отсутствовать (справочник не загрузился) — нулевые базы,
  // чтобы лаборатория не падала на пустых данных.
  const base = hero?.stats || {};
  const b = (field, dflt = 0) => (base[field] ?? dflt);
  const s = (field) => items.reduce((acc, it) => acc + (it.stats?.[field] || 0), 0);

  const hpU = b('hp') + s('base_hp');
  const wdU = b('weapon_damage') + s('weapon_damage');
  const frU = b('fire_rate', 1) * (1 + s('fire_rate_pct'));
  const weaponDps = round2(wdU * frU);
  const bulletShield = b('bullet_shield') + s('bullet_shield');
  const spiritShield = b('spirit_shield') + s('spirit_shield');

  return {
    hp: round2(hpU),
    bullet_shield: round2(bulletShield),
    spirit_shield: round2(spiritShield),
    effective_hp_bullet: round2(effectiveHp(hpU, bulletShield)),
    effective_hp_spirit: round2(effectiveHp(hpU, spiritShield)),
    weapon_damage: round2(wdU),
    fire_rate: round2(frU),
    weapon_dps: weaponDps,
    spirit_power: round2(b('spirit_power') + s('spirit_power')),
    stamina: b('stamina') + Math.round(s('stamina')),
    move_speed: round2(b('move_speed') + s('move_speed')),
    cooldown_reduction_pct: round2(Math.min(s('cooldown_reduction_pct'), CDR_CAP)),
    souls_spent: items.reduce((acc, it) => acc + (it.cost || 0), 0),
  };
}

export function createDebouncedStatsCalculator(callback) {
  let rafId = null;
  return function schedule(...args) {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(() => {
      rafId = null;
      callback(...args);
    });
  };
}
