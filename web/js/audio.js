// audio.js — Звуковой движок Deadlock Plus (Zero-Overlap Instant Retrigger)
// & HUD Volume Control. Плейлист фоновой музыки: манифест
// web/assets/sounds/tracks.json (маска *.mp3 / *.opus) с фолбэком на
// background_music.opus; SFX исключаются чёрным списком.
// Спецификация: ТЗ v1.7 (2.1–2.3) + ТЗ Правок Deadlock Plus V2.0 (4.4)

const DEFAULT_TRACKS = ['background_music.opus'];
const SFX_BLACKLIST = new Set(['buy_sound.mp3']);
const REPEAT_CYCLE = ['off', 'all', 'one'];

/** Имя трека из имени файла: убрать расширение, _/- → пробелы. */
export function trackTitle(file) {
  return String(file || '')
    .replace(/\.(mp3|opus)$/i, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim() || 'Музыка';
}

export class SoundEngine {
  constructor() {
    // SFX покупки / экипировки
    this.buyAudio = new Audio('./assets/sounds/buy_sound.mp3');
    this.buyAudio.preload = 'auto';

    // Фоновая музыка: один <audio>, src переключается по треку плейлиста
    this.bgMusic = new Audio();
    this.bgMusic.preload = 'metadata';

    // Громкость: новый ключ audio.volume, фолбэк на df_volume (легаси)
    const savedVol = localStorage.getItem('audio.volume') ?? localStorage.getItem('df_volume');
    const parsedVol = savedVol !== null ? parseFloat(savedVol) : 0.5;
    this.volume = Number.isFinite(parsedVol) ? Math.max(0, Math.min(1, parsedVol)) : 0.5;

    const savedMusicVol = localStorage.getItem('audio.musicVolume') ?? localStorage.getItem('df_music_volume');
    const parsedMusicVol = savedMusicVol !== null ? parseFloat(savedMusicVol) : 0.35;
    this.musicVolume = Number.isFinite(parsedMusicVol) ? Math.max(0, Math.min(1, parsedMusicVol)) : 0.35;

    this.muted = localStorage.getItem('df_muted') === 'true';
    this.musicMuted = localStorage.getItem('df_music_muted') === 'true';

    // Плейлист (манифест tracks.json обновит список асинхронно)
    this.playlist = DEFAULT_TRACKS.slice();
    this.trackIndex = 0;
    this._currentFile = null;
    this.shuffle = false;
    this.repeat = 'off'; // 'off' | 'all' | 'one'

    this.musicStarted = false;
    this.userPaused = localStorage.getItem('audio.userPaused') === 'true';
    this.autoPlayCleaned = false;
    this._autoPlayHandler = null;
    this.listeners = new Set();

    // Автопереход на следующий трек по окончании
    this.bgMusic.addEventListener('ended', () => {
      if (this.userPaused || !this.musicStarted) return;
      if (this.repeat === 'one') {
        this.bgMusic.currentTime = 0;
        this.playMusic();
        return;
      }
      const isLast = this.trackIndex >= this.playlist.length - 1;
      if (this.repeat === 'all' || !isLast) {
        this.nextTrack();
      } else {
        // repeat=off и список закончился — останавливаемся
        this.musicStarted = false;
        this.notify();
      }
    });

    this.updateAudioSettings();
    if (!this.userPaused) {
      this.setupMusicAutoPlay();
      this.tryImmediateAutoplay();
    }
    this.discoverTracks(); // fire-and-forget: обновит плейлист, если есть манифест
  }

  /** Плейлист из web/assets/sounds/tracks.json (маска *.mp3 / *.opus), фолбэк на дефолт. */
  async discoverTracks() {
    let list = null;
    try {
      const resp = await fetch('./assets/sounds/tracks.json', { cache: 'no-store' });
      if (resp.ok) {
        const data = await resp.json();
        const raw = Array.isArray(data) ? data
          : (data && Array.isArray(data.tracks) ? data.tracks : null);
        if (raw) {
          list = raw
            .filter((f) => typeof f === 'string' && /(\.mp3|\.opus)$/i.test(f.trim())
              && !SFX_BLACKLIST.has(f.trim().toLowerCase()))
            .map((f) => f.trim().replace(/^.*[\\/]/, ''));
        }
      }
    } catch {
      /* нет манифеста — остаёмся на дефолтном плейлисте */
    }
    if (Array.isArray(list) && list.length) this.playlist = list;
    if (!this.playlist.length) this.playlist = DEFAULT_TRACKS.slice();
    this.trackIndex = Math.min(this.trackIndex, Math.max(0, this.playlist.length - 1));
    this.notify();
  }

  currentTrack() {
    return this.playlist[this.trackIndex] || DEFAULT_TRACKS[0] || '';
  }

  /** Выбор трека из плейлиста по имени файла. */
  selectTrack(file) {
    if (typeof file !== 'string' || !this.playlist.includes(file)) return false;
    this.trackIndex = this.playlist.indexOf(file);
    this.loadCurrentTrack();
    this.playMusic();
    this.notify();
    return true;
  }

  isPlaying() {
    return this.musicStarted && !this.bgMusic.paused;
  }

  /** Подгрузка файла текущего трека (без перезапуска, если тот же). */
  loadCurrentTrack() {
    const file = this.currentTrack();
    if (this._currentFile !== file) {
      this._currentFile = file;
      this.bgMusic.src = `./assets/sounds/${file}`;
      this.bgMusic.load();
    }
  }

  /**
   * Autoplay-политика браузера: WebView2 может разрешить звук сразу —
   * пробуем немедленно; иначе первый жест пользователя (клик/клавиша/тач).
   */
  tryImmediateAutoplay() {
    if (this.userPaused || this.muted || this.musicMuted || this.musicVolume <= 0) return;
    this.playMusic();
  }

  setupMusicAutoPlay() {
    if (typeof window === 'undefined' || this.userPaused) return;
    this._autoPlayHandler = () => {
      this.removeAutoPlayListeners();
      if (!this.userPaused && !this.musicMuted && !this.muted && this.musicVolume > 0) {
        if (this.bgMusic.paused) {
          this.playMusic();
        }
      }
    };
    window.addEventListener('click', this._autoPlayHandler, { passive: true });
    window.addEventListener('keydown', this._autoPlayHandler, { passive: true });
    window.addEventListener('pointerdown', this._autoPlayHandler, { passive: true });
    window.addEventListener('touchstart', this._autoPlayHandler, { passive: true });
  }

  removeAutoPlayListeners() {
    if (this.autoPlayCleaned || typeof window === 'undefined') return;
    this.autoPlayCleaned = true;
    if (this._autoPlayHandler) {
      window.removeEventListener('click', this._autoPlayHandler);
      window.removeEventListener('keydown', this._autoPlayHandler);
      window.removeEventListener('pointerdown', this._autoPlayHandler);
      window.removeEventListener('touchstart', this._autoPlayHandler);
      this._autoPlayHandler = null;
    }
  }

  playMusic() {
    this.userPaused = false;
    try { localStorage.setItem('audio.userPaused', 'false'); } catch (_) {}
    this.removeAutoPlayListeners();
    this.musicStarted = true;
    if (this.musicMuted || this.musicVolume <= 0 || this.muted) {
      this.bgMusic.pause();
      this.notify();
      return;
    }
    this.loadCurrentTrack();
    this.bgMusic.play().then(() => {
      this.musicStarted = true;
      this.notify();
    }).catch(() => {
      // Игнорируем ограничения автовоспроизведения до первого жеста пользователя
    });
  }

  pauseMusic() {
    this.userPaused = true;
    try { localStorage.setItem('audio.userPaused', 'true'); } catch (_) {}
    this.removeAutoPlayListeners();
    this.musicStarted = false;
    this.bgMusic.pause();
    this.notify();
  }

  togglePlay() {
    if (this.isPlaying()) {
      this.pauseMusic();
    } else {
      this.playMusic();
    }
    this.notify();
  }

  pickNextIndex() {
    if (this.playlist.length < 2) return this.trackIndex;
    if (this.shuffle) {
      let ni = this.trackIndex;
      while (ni === this.trackIndex) {
        ni = Math.floor(Math.random() * this.playlist.length);
      }
      return ni;
    }
    return (this.trackIndex + 1) % this.playlist.length;
  }

  nextTrack() {
    if (!this.playlist.length) return;
    this.trackIndex = this.pickNextIndex();
    this.loadCurrentTrack();
    this.playMusic();
    this.notify();
  }

  prevTrack() {
    if (!this.playlist.length) return;
    if (this.shuffle) {
      let ni = this.trackIndex;
      while (ni === this.trackIndex) {
        ni = Math.floor(Math.random() * this.playlist.length);
      }
      this.trackIndex = ni;
    } else {
      this.trackIndex = (this.trackIndex - 1 + this.playlist.length) % this.playlist.length;
    }
    this.loadCurrentTrack();
    this.playMusic();
    this.notify();
  }

  toggleShuffle() {
    this.shuffle = !this.shuffle;
    this.notify();
    return this.shuffle;
  }

  cycleRepeat() {
    const i = REPEAT_CYCLE.indexOf(this.repeat);
    this.repeat = REPEAT_CYCLE[(i + 1) % REPEAT_CYCLE.length];
    this.notify();
    return this.repeat;
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
          // Игнорируем блокировки автовоспроизведения до первого жеста пользователя
        });
      }
    } catch (_) {}
  }

  setVolume(val) {
    const num = Number(val);
    this.volume = Number.isFinite(num) ? Math.max(0, Math.min(1, Math.round(num * 100) / 100)) : 0.5;
    try {
      localStorage.setItem('audio.volume', String(this.volume));
      localStorage.setItem('df_volume', String(this.volume));
    } catch (_) {}
    this.updateAudioSettings();
    this.notify();
  }

  setMusicVolume(val) {
    const num = Number(val);
    this.musicVolume = Number.isFinite(num) ? Math.max(0, Math.min(1, Math.round(num * 100) / 100)) : 0.35;
    try {
      localStorage.setItem('audio.musicVolume', String(this.musicVolume));
      localStorage.setItem('df_music_volume', String(this.musicVolume));
    } catch (_) {}
    this.updateAudioSettings();
    if (!this.userPaused && this.musicStarted && this.musicVolume > 0 && !this.musicMuted && !this.muted) {
      this.bgMusic.play().catch(() => {});
    }
    this.notify();
  }

  toggleMute() {
    this.muted = !this.muted;
    try {
      localStorage.setItem('df_muted', String(this.muted));
    } catch (_) {}
    this.updateAudioSettings();
    if (!this.muted && !this.userPaused && this.musicStarted) {
      this.bgMusic.play().catch(() => {});
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
    if (!this.musicMuted && !this.muted && !this.userPaused && this.musicStarted && this.musicVolume > 0) {
      this.bgMusic.play().catch(() => {});
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
      if (effectiveMusicVol === 0 || this.userPaused) {
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
        <input type="range" id="volume-slider" class="volume-slider" min="0" max="1" step="0.01" value="0.5" aria-label="Громкость">
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
 * Инициализация виджета фоновой музыки на вкладке «Хаб»:
 * play/pause, Prev/Next, Shuffle, Repeat (off/all/one), трек, громкость 1%.
 */
export function initHubMusicWidget() {
  const container = document.getElementById('hub-music-widget');
  if (!container) return;

  const toggleBtn = container.querySelector('#hub-music-toggle-btn');
  const icon = container.querySelector('#hub-music-icon');
  const trackEl = container.querySelector('#hub-music-track-label') || container.querySelector('#hub-music-track');
  const slider = container.querySelector('#hub-music-slider');
  const valLabel = container.querySelector('#hub-music-val');
  const prevBtn = container.querySelector('#hub-music-prev');
  const nextBtn = container.querySelector('#hub-music-next');
  const shuffleBtn = container.querySelector('#hub-music-shuffle');
  const repeatBtn = container.querySelector('#hub-music-repeat');

  function renderHub(engine) {
    const isPlaying = engine.isPlaying();
    const isMuted = engine.musicMuted || engine.musicVolume <= 0 || engine.muted;

    if (icon) {
      icon.textContent = isPlaying ? '🎵 ⏸' : '🎵 ▶';
    }
    if (toggleBtn) {
      toggleBtn.title = isPlaying ? 'Пауза' : 'Играть';
    }

    if (trackEl) {
      const title = trackTitle(engine.currentTrack());
      trackEl.textContent = title;
      trackEl.title = title;
    }

    if (slider) {
      slider.value = String(engine.musicVolume);
      const pct = Math.round(engine.musicVolume * 100);
      slider.style.setProperty('--vol-fill', `${pct}%`);
    }

    if (valLabel) {
      if (isMuted) {
        valLabel.textContent = 'ВЫКЛ';
      } else {
        valLabel.textContent = `${Math.round(engine.musicVolume * 100)}%`;
      }
    }

    if (shuffleBtn) {
      shuffleBtn.classList.toggle('active', engine.shuffle);
      shuffleBtn.title = engine.shuffle ? 'Перемешать: вкл' : 'Перемешать: выкл';
    }

    if (repeatBtn) {
      const active = engine.repeat !== 'off';
      repeatBtn.classList.toggle('active', active);
      repeatBtn.dataset.mode = engine.repeat;
      repeatBtn.textContent = engine.repeat === 'one' ? '🔂' : '🔁';
      const label = engine.repeat === 'off' ? 'выкл' : (engine.repeat === 'all' ? 'все' : 'один');
      repeatBtn.title = `Повтор: ${label}`;
    }
  }

  if (toggleBtn && !toggleBtn.dataset.wired) {
    toggleBtn.dataset.wired = '1';
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      soundEngine.togglePlay();
    });
  }

  if (prevBtn && !prevBtn.dataset.wired) {
    prevBtn.dataset.wired = '1';
    prevBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      soundEngine.prevTrack();
    });
  }

  if (nextBtn && !nextBtn.dataset.wired) {
    nextBtn.dataset.wired = '1';
    nextBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      soundEngine.nextTrack();
    });
  }

  if (shuffleBtn && !shuffleBtn.dataset.wired) {
    shuffleBtn.dataset.wired = '1';
    shuffleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      soundEngine.toggleShuffle();
    });
  }

  if (repeatBtn && !repeatBtn.dataset.wired) {
    repeatBtn.dataset.wired = '1';
    repeatBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      soundEngine.cycleRepeat();
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
