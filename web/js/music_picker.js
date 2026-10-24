// music_picker.js — поповер выбора трека в плеере на Хабе.
// Отвечает только за рендер списка, открытие/закрытие и клавиатуру:
// плейлист и воспроизведение живут в SoundEngine.
// ВАЖНО: в ES-модуле return на верхнем уровне — SyntaxError (в отличие от CJS),
// поэтому вся инициализация вынесена в блок-стражу без ранних выходов.

import { soundEngine, trackTitle } from './audio.js';

const picker = document.getElementById('hub-music-picker');
const trigger = document.getElementById('hub-music-track');
const list = document.getElementById('hub-music-track-list');
const widget = document.getElementById('hub-music-widget');

if (picker && trigger && list) {
  let isOpen = false;
  let lastState = '';

  function stateOf(engine) {
    return `${engine.playlist.join('\u0000')}\u0001${engine.currentTrack()}`;
  }

  function options() {
    return Array.from(list.querySelectorAll('.mp-option'));
  }

  function scrollToCurrent() {
    const active = list.querySelector('.mp-option.active');
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  /**
   * Перерисовываем список только при смене состава плейлиста или текущего
   * трека — громкость и mute сюда не попадают.
   */
  function render(engine) {
    const state = stateOf(engine);
    if (state === lastState) return;
    lastState = state;

    const current = engine.currentTrack();
    const fragment = document.createDocumentFragment();
    engine.playlist.forEach((file, index) => {
      const option = document.createElement('li');
      option.className = 'mp-option';
      option.id = `hub-music-option-${index}`;
      option.dataset.file = file;
      option.setAttribute('role', 'option');
      option.setAttribute('tabindex', '-1');

      const isCurrent = file === current;
      option.setAttribute('aria-selected', String(isCurrent));
      option.classList.toggle('active', isCurrent);

      const num = document.createElement('span');
      num.className = 'mp-option-n';
      num.textContent = String(index + 1).padStart(2, '0');

      const name = document.createElement('span');
      name.className = 'mp-option-name';
      name.textContent = trackTitle(file);

      const eq = document.createElement('span');
      eq.className = 'mp-option-eq';
      eq.setAttribute('aria-hidden', 'true');
      eq.append(document.createElement('i'), document.createElement('i'), document.createElement('i'));

      option.append(num, name, eq);
      fragment.appendChild(option);
    });

    list.replaceChildren(fragment);
    if (isOpen) scrollToCurrent();
  }

  function setOpen(open) {
    if (isOpen === open) return;
    isOpen = open;
    list.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
    picker.classList.toggle('is-open', open);
    if (widget) widget.classList.toggle('picker-open', open);

    if (open) {
      scrollToCurrent();
      const active = list.querySelector('.mp-option.active') || list.firstElementChild;
      if (active) active.focus({ preventScroll: true });
    }
  }

  // (1) Клик по названию трека открывает/закрывает список.
  trigger.addEventListener('click', (e) => {
    e.stopPropagation(); // чтобы глобальный обработчик вкладок не сработал
    setOpen(!isOpen);
  });

  // (2) Навигация с клавиатуры до открытия: ArrowDown раскрывает список.
  trigger.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' && !isOpen) {
      e.preventDefault();
      setOpen(true);
    }
  });

  // (3) Клик по опции — выбрать трек, закрыть список, вернуть фокус триггеру.
  list.addEventListener('click', (e) => {
    e.stopPropagation();
    const option = e.target.closest('.mp-option');
    if (!option) return;
    const file = option.dataset.file;
    if (typeof file === 'string') soundEngine.selectTrack(file);
    setOpen(false);
    trigger.focus({ preventScroll: true });
  });

  // (4) Клавиатура внутри списка: стрелки/Home/End, Enter/Space, Escape.
  list.addEventListener('keydown', (e) => {
    const items = options();
    if (!items.length) return;
    const currentIndex = Math.max(0, items.indexOf(document.activeElement));
    let next = null;

    if (e.key === 'ArrowDown') {
      next = items[(currentIndex + 1) % items.length];
    } else if (e.key === 'ArrowUp') {
      next = items[(currentIndex - 1 + items.length) % items.length];
    } else if (e.key === 'Home') {
      next = items[0];
    } else if (e.key === 'End') {
      next = items[items.length - 1];
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const focused = document.activeElement;
      if (focused && focused.classList.contains('mp-option')) focused.click();
      return;
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      trigger.focus({ preventScroll: true });
      return;
    } else {
      return;
    }

    e.preventDefault();
    if (next) next.focus({ preventScroll: true });
  });

  // (5) Клик вне поповера закрывает список.
  document.addEventListener('click', (e) => {
    if (isOpen && !picker.contains(e.target)) setOpen(false);
  });

  // (6) Escape закрывает список и возвращает фокус на триггер.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen) {
      setOpen(false);
      trigger.focus({ preventScroll: true });
    }
  });

  // Подписка: subscribe сразу вызывает callback с текущим состоянием —
  // список заполняется при инициализации и далее обновляется по событиям.
  soundEngine.subscribe(render);
}
