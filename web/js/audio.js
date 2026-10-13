// audio.js — Звуковой движок DeadlockForge (Zero-Overlap Instant Retrigger) & HUD Volume Control
// Спецификация: ТЗ v1.7 (Задачи 2.1, 2.2, 2.3)

export class SoundEngine {
  constructor() {
    // SFX покупки / экипировки
    this.buyAudio = new Audio('./assets/sounds/buy_sound.mp3');
    this.buyAudio.preload = 'auto';

    // Фоновая музыка
    this.bgMusic = new Audio('./assets/sounds/background_music.mp3');
    this.bgMusic.preload = 'metadata';
    this.bgMusic.loop = true;

    const savedVol = localStorage.getItem('df_volume');
    const parsedVol = savedVol !== null ? parseFloat(savedVol) : 0.5;
    this.volume = Number.isFinite(parsedVol) ? Math.max(0, Math.min(1, parsedVol)) : 0.5;

    const savedMusicVol = localStorage.getItem('df_music_volume');
    const parsedMusicVol = savedMusicVol !== null ? parseFloat(savedMusicVol) : 0.35;
    this.musicVolume = Number.isFinite(parsedMusicVol) ? Math.max(0, Math.min(1, parsedMusicVol)) : 0.35;

    this.muted = localStorage.getItem('df_muted') === 'true';
    this.musicMuted = localStorage.getItem('df_music_muted') === 'true';

    this.musicStarted = false;
    this.listeners = new Set();

    this.updateAudioSettings();
    this.setupMusicAutoPlay();
  }

  setupMusicAutoPlay() {
    const startMusic = () => {
      if (this.musicStarted) return;
      this.playMusic();
    };

    if (typeof window !== 'undefined') {
      window.addEventListener('click', startMusic, { once: true });
      window.addEventListener('keydown', startMusic, { once: true });
    }
  }

  playMusic() {
    if (this.musicMuted || this.musicVolume <= 0 || this.muted) {
      this.bgMusic.pause();
      return;
    }
    this.bgMusic.play().then(() => {
      this.musicStarted = true;
    }).catch(() => {
      // Игнорируем ограничения автовоспроизведения браузера до первого клика
    });
  }

  pauseMusic() {
    this.bgMusic.pause();
  }

  /**
   * Воспроизведение звука покупки предмета.
   * Zero-Overlap Instant Retrigger: при быстрых кликах звук моментально
   * сбрасывается в 0 без полифонического наслоения и треска.
   */
  playBuySound() {
    if (this.muted || this.volume <= 0) return;
    try {
      this.buyAudio.currentTime = 0;
      const playPromise = this.buyAudio.play();
      if (playPromise && typeof playPromise.catch === 'function') {
        playPromise.catch(() => {
          // Игнорируем блокировки автовоспроизведения браузера до первого жеста пользователя
        });
      }
    } catch (_) {}
  }

  setVolume(val) {
    const num = Number(val);
    this.volume = Number.isFinite(num) ? Math.max(0, Math.min(1, Math.round(num * 100) / 100)) : 0.5;
    try {
      localStorage.setItem('df_volume', String(this.volume));
    } catch (_) {}
    this.updateAudioSettings();
    this.notify();
  }

  setMusicVolume(val) {
    const num = Number(val);
    this.musicVolume = Number.isFinite(num) ? Math.max(0, Math.min(1, Math.round(num * 100) / 100)) : 0.35;
    try {
      localStorage.setItem('df_music_volume', String(this.musicVolume));
    } catch (_) {}
    this.updateAudioSettings();
    if (this.musicVolume > 0 && !this.musicMuted && !this.muted) {
      this.playMusic();
    }
    this.notify();
  }

  toggleMute() {
    this.muted = !this.muted;
    try {
      localStorage.setItem('df_muted', String(this.muted));
    } catch (_) {}
    this.updateAudioSettings();
    if (!this.muted) {
      this.playMusic();
    }
    this.notify();
    return this.muted;
  }

  toggleMusicMute() {
    this.musicMuted = !this.musicMuted;
    try {
      localStorage.setItem('df_music_muted', String(this.musicMuted));
    } catch (_) {}
    this.updateAudioSettings();
    if (!this.musicMuted && !this.muted && this.musicVolume > 0) {
      this.playMusic();
    }
    this.notify();
    return this.musicMuted;
  }

  updateAudioSettings() {
    if (this.buyAudio) {
      this.buyAudio.volume = this.muted ? 0 : this.volume;
    }
    if (this.bgMusic) {
      const effectiveMusicVol = (this.muted || this.musicMuted) ? 0 : this.musicVolume;
      this.bgMusic.volume = effectiveMusicVol;
      if (effectiveMusicVol === 0) {
        this.bgMusic.pause();
      } else if (this.musicStarted) {
        this.bgMusic.play().catch(() => {});
      }
    }
  }

  subscribe(fn) {
    if (typeof fn === 'function') {
      this.listeners.add(fn);
      fn(this);
    }
    return () => this.listeners.delete(fn);
  }

  notify() {
    for (const fn of this.listeners) {
      try {
        fn(this);
      } catch (err) {
        console.error('SoundEngine listener error:', err);
      }
    }
  }
}

export const soundEngine = new SoundEngine();
if (typeof window !== 'undefined') {
  window.soundEngine = soundEngine;
}

/**
 * Инициализация плавающего виджета громкости в HUD
 * Размещение: bottom: 20px, right: 24px, z-index: 100
 */
export function initVolumeWidget() {
  let widget = document.getElementById('volume-widget');
  if (!widget) {
    widget = document.createElement('div');
    widget.id = 'volume-widget';
    widget.className = 'hud-volume-widget';
    widget.setAttribute('role', 'region');
    widget.setAttribute('aria-label', 'Управление звуком');
    widget.innerHTML = `
      <button id="volume-mute-btn" class="volume-mute-btn" type="button" title="Переключить звук (Mute)" aria-label="Переключить звук">
        <span id="volume-icon" class="volume-icon">🔊</span>
      </button>
      <div class="volume-slider-wrap">
        <input type="range" id="volume-slider" class="volume-slider" min="0" max="1" step="0.05" value="0.5" aria-label="Громкость">
        <span id="volume-value" class="volume-value" title="Уровень громкости">50%</span>
      </div>
    `;
    document.body.appendChild(widget);
  }

  const currentTab = document.querySelector('.hud-tab.tab-active')?.dataset?.tab || 'hub';
  widget.classList.toggle('hidden', currentTab !== 'lab');

  const muteBtn = widget.querySelector('#volume-mute-btn');
  const icon = widget.querySelector('#volume-icon');
  const slider = widget.querySelector('#volume-slider');
  const valueLabel = widget.querySelector('#volume-value');

  function render(engine) {
    const isMuted = engine.muted || engine.volume <= 0;
    widget.classList.toggle('is-muted', isMuted);

    if (slider) {
      slider.value = String(engine.volume);
      const pct = Math.round(engine.volume * 100);
      slider.style.setProperty('--vol-fill', `${pct}%`);
    }

    if (icon) {
      if (engine.muted || engine.volume <= 0) {
        icon.textContent = '🔇';
      } else if (engine.volume < 0.45) {
        icon.textContent = '🔉';
      } else {
        icon.textContent = '🔊';
      }
    }

    if (valueLabel) {
      if (engine.muted) {
        valueLabel.textContent = 'MUTE';
      } else {
        valueLabel.textContent = `${Math.round(engine.volume * 100)}%`;
      }
    }

    if (muteBtn) {
      muteBtn.title = engine.muted ? 'Включить звук (Unmute)' : 'Выключить звук (Mute)';
    }
  }

  if (muteBtn && !muteBtn.dataset.wired) {
    muteBtn.dataset.wired = '1';
    muteBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      soundEngine.toggleMute();
      if (!soundEngine.muted && soundEngine.volume > 0) {
        soundEngine.playBuySound();
      }
    });
  }

  if (slider && !slider.dataset.wired) {
    slider.dataset.wired = '1';
    slider.addEventListener('input', (e) => {
      const val = parseFloat(e.target.value);
      if (soundEngine.muted && val > 0) {
        soundEngine.muted = false;
        try { localStorage.setItem('df_muted', 'false'); } catch (_) {}
      }
      soundEngine.setVolume(val);
    });
    slider.addEventListener('change', () => {
      if (!soundEngine.muted && soundEngine.volume > 0) {
        soundEngine.playBuySound();
      }
    });
  }

  soundEngine.subscribe(render);
  return widget;
}

/**
 * Инициализация отдельного тумблера/слайдера фоновой музыки на вкладке «Хаб»
 */
export function initHubMusicWidget() {
  const container = document.getElementById('hub-music-widget');
  if (!container) return;

  const toggleBtn = container.querySelector('#hub-music-toggle-btn');
  const icon = container.querySelector('#hub-music-icon');
  const slider = container.querySelector('#hub-music-slider');
  const valLabel = container.querySelector('#hub-music-val');

  function renderHub(engine) {
    const isMuted = engine.musicMuted || engine.musicVolume <= 0 || engine.muted;
    container.classList.toggle('is-muted', isMuted);

    if (slider) {
      slider.value = String(engine.musicVolume);
      const pct = Math.round(engine.musicVolume * 100);
      slider.style.setProperty('--vol-fill', `${pct}%`);
    }

    if (icon) {
      if (isMuted) {
        icon.textContent = '🎵 ✕';
      } else {
        icon.textContent = '🎵 ▶';
      }
    }

    if (valLabel) {
      if (isMuted) {
        valLabel.textContent = 'ВЫКЛ';
      } else {
        valLabel.textContent = `${Math.round(engine.musicVolume * 100)}%`;
      }
    }

    if (toggleBtn) {
      toggleBtn.title = isMuted ? 'Включить фоновую музыку' : 'Выключить фоновую музыку';
    }
  }

  if (toggleBtn && !toggleBtn.dataset.wired) {
    toggleBtn.dataset.wired = '1';
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      soundEngine.toggleMusicMute();
    });
  }

  if (slider && !slider.dataset.wired) {
    slider.dataset.wired = '1';
    slider.addEventListener('input', (e) => {
      const val = parseFloat(e.target.value);
      if (soundEngine.musicMuted && val > 0) {
        soundEngine.musicMuted = false;
        try { localStorage.setItem('df_music_muted', 'false'); } catch (_) {}
      }
      soundEngine.setMusicVolume(val);
    });
  }

  soundEngine.subscribe(renderHub);
}
