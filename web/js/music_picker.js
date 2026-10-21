// music_picker.js — раскрывающийся список выбора трека в виджете музыки на Хабе.
// Отвечает только за рендер списка и переключение: сам SoundEngine не меняется.
// ВАЖНО: в ES-модуле return на верхнем уровне — SyntaxError (в отличие от CJS),
// поэтому вся инициализация вынесена в блок-стражу без ранних выходов.

import { soundEngine, trackTitle } from './audio.js';

const picker = document.getElementById('hub-music-picker');
const trigger = document.getElementById('hub-music-track');
const list = document.getElementById('hub-music-track-list');

// (1) Контейнера или контракта разметки нет — тихо отключаемся.
if (picker && trigger && list) {
  let isOpen = false;
  let lastState = '';

  function stateOf(engine) {
    return `${engine.playlist.join('\u0000')}\u0001${engine.currentTrack()}`;
  }

  /**
   * (2)+(3) Перерисовываем список только при изменении состава плейлиста
   * или текущего файла — изменение громкости/mute сюда не попадает.
   * Текущий трек помечаем классом "active" и aria-selected="true".
   */
  function render(engine) {
    const state = stateOf(engine);
    if (state === lastState) return;
    lastState = state;

    const current = engine.currentTrack();
    const fragment = document.createDocumentFragment();
    for (const file of engine.playlist) {
      const option = document.createElement('li');
      option.className = 'hub-music-option';
      option.setAttribute('role', 'option');
      option.dataset.file = file;
      option.textContent = trackTitle(file);
      const isCurrent = file === current;
      option.classList.toggle('active', isCurrent);
      option.setAttribute('aria-selected', String(isCurrent));
      fragment.appendChild(option);
    }
    list.replaceChildren(fragment);
  }

  function setOpen(open) {
    if (isOpen === open) return;
    isOpen = open;
    list.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
    picker.classList.toggle('is-open', open);
  }

  // (4) Клик по триггеру переключает список.
  trigger.addEventListener('click', (e) => {
    e.stopPropagation(); // (7) чтобы глобальный обработчик вкладок не сработал
    setOpen(!isOpen);
  });

  // (5) Клик по опции — выбрать трек и закрыть список.
  list.addEventListener('click', (e) => {
    e.stopPropagation(); // (7)
    const option = e.target.closest('.hub-music-option');
    if (!option) return;
    const file = option.dataset.file;
    if (typeof file === 'string') soundEngine.selectTrack(file);
    setOpen(false);
  });

  // (6) Клик вне пикера закрывает список.
  document.addEventListener('click', (e) => {
    if (isOpen && !picker.contains(e.target)) setOpen(false);
  });

  // (6) Escape закрывает список.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && isOpen) setOpen(false);
  });

  // Подписка: subscribe сразу вызывает callback с текущим состоянием —
  // список заполняется при инициализации и далее обновляется по событиям.
  soundEngine.subscribe(render);
}