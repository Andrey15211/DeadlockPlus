/**
 * Deadlock Plus — In-App Updater (Morphe Style)
 * Handles GitHub Releases check, live download progress, changelog rendering, and seamless restart.
 */

(function () {
  'use strict';

  let currentUpdateData = null;
  let pollInterval = null;
  let isChecking = false;

  function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return '0 МБ';
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(1)} МБ`;
  }

  function formatSpeed(bps) {
    if (!bps || bps <= 0) return '0 МБ/с';
    const mbps = bps / (1024 * 1024);
    if (mbps >= 1.0) {
      return `${mbps.toFixed(1)} МБ/с`;
    }
    const kbps = bps / 1024;
    return `${kbps.toFixed(0)} КБ/с`;
  }

  /**
   * Safe mini-markdown parser for GitHub release notes.
   * Renders headers, lists, code, bold, and links without innerHTML vulnerability.
   */
  function renderMarkdownToHtml(mdText) {
    if (!mdText) return '<p class="changelog-empty">Список изменений отсутствует.</p>';

    const lines = mdText.split(/\r?\n/);
    let html = '';
    let inList = false;

    for (let rawLine of lines) {
      let line = rawLine.trim();

      // Escape HTML entities first
      line = line
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

      // Headers
      if (line.startsWith('### ')) {
        if (inList) { html += '</ul>'; inList = false; }
        html += `<h4 class="cl-h4">${line.slice(4)}</h4>`;
        continue;
      }
      if (line.startsWith('## ')) {
        if (inList) { html += '</ul>'; inList = false; }
        html += `<h3 class="cl-h3">${line.slice(3)}</h3>`;
        continue;
      }
      if (line.startsWith('# ')) {
        if (inList) { html += '</ul>'; inList = false; }
        html += `<h2 class="cl-h2">${line.slice(2)}</h2>`;
        continue;
      }

      // Unordered list items
      if (line.startsWith('- ') || line.startsWith('* ')) {
        if (!inList) { html += '<ul class="cl-ul">'; inList = true; }
        let content = line.slice(2);
        content = parseInlineStyles(content);
        html += `<li class="cl-li">${content}</li>`;
        continue;
      }

      if (inList) {
        html += '</ul>';
        inList = false;
      }

      // Empty lines
      if (!line) {
        continue;
      }

      // Normal paragraph
      html += `<p class="cl-p">${parseInlineStyles(line)}</p>`;
    }

    if (inList) html += '</ul>';
    return html;
  }

  function parseInlineStyles(str) {
    return str
      // Bold **text**
      .replace(/\*\*(.+?)\*\*/g, '<strong class="cl-bold">$1</strong>')
      // Inline `code`
      .replace(/`([^`]+)`/g, '<code class="cl-code">$1</code>')
      // [Links](url)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" class="cl-link">$1</a>');
  }

  function showModal() {
    const modal = document.getElementById('update-modal');
    if (!modal) return;
    modal.classList.remove('hidden');
    document.body.classList.add('modal-open');
  }

  function closeModal() {
    const modal = document.getElementById('update-modal');
    if (!modal) return;
    modal.classList.add('hidden');
    document.body.classList.remove('modal-open');
  }

  function updateStatusCard(data, isError = false) {
    const dot = document.getElementById('update-status-dot');
    const text = document.getElementById('update-status-text');
    const sub = document.getElementById('update-status-sub');
    const headerBadge = document.getElementById('btn-update-badge');
    const verBadge = document.getElementById('profile-version-badge');

    if (verBadge && data.current_version) {
      verBadge.textContent = `v${data.current_version}`;
    }

    if (isError) {
      if (dot) dot.className = 'update-status-indicator is-error';
      if (text) text.textContent = 'Ошибка проверки обновлений';
      if (sub) sub.textContent = data.error || 'Проверьте подключение к сети';
      return;
    }

    if (data.update_available) {
      if (dot) dot.className = 'update-status-indicator is-update';
      if (text) text.innerHTML = `Доступно обновление <strong>v${data.latest_version}</strong>`;
      if (sub) sub.textContent = `Выпуск: ${data.release_name || 'Свежий релиз'}`;
      if (headerBadge) {
        headerBadge.classList.remove('hidden');
        const badgeText = headerBadge.querySelector('.hud-update-text');
        if (badgeText) badgeText.textContent = `v${data.latest_version}`;
      }
    } else {
      if (dot) dot.className = 'update-status-indicator is-ok';
      if (text) text.textContent = 'У вас установлена последняя версия';
      const checkDate = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      if (sub) sub.textContent = `Проверено: сегодня в ${checkDate} · GitHub Releases`;
      if (headerBadge) headerBadge.classList.add('hidden');
    }
  }

  function populateModal(data) {
    currentUpdateData = data;
    const curEl = document.getElementById('modal-current-ver');
    const latEl = document.getElementById('modal-latest-ver');
    const metaEl = document.getElementById('modal-release-meta');
    const clEl = document.getElementById('modal-changelog-content');
    const progBlock = document.getElementById('modal-progress-block');
    const msgEl = document.getElementById('modal-update-message');
    const btnAction = document.getElementById('btn-start-update');
    const actionText = document.getElementById('btn-update-text');

    if (curEl) curEl.textContent = `v${data.current_version}`;
    if (latEl) latEl.textContent = `v${data.latest_version}`;

    const sizeStr = data.asset_size ? ` · Размер: ${formatBytes(data.asset_size)}` : '';
    let dateStr = '';
    if (data.published_at) {
      const d = new Date(data.published_at);
      dateStr = d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
    }
    if (metaEl) metaEl.textContent = `${dateStr}${sizeStr}`;

    if (clEl) {
      clEl.innerHTML = renderMarkdownToHtml(data.release_notes);
    }

    if (progBlock) progBlock.classList.add('hidden');
    if (msgEl) {
      msgEl.classList.add('hidden');
      msgEl.textContent = '';
    }

    if (btnAction && actionText) {
      btnAction.disabled = false;
      btnAction.classList.remove('btn-ready-restart');
      actionText.textContent = 'Обновить сейчас';
    }
  }

  async function checkForUpdates(force = false, openModalIfAvailable = false) {
    if (isChecking) return;
    isChecking = true;

    const btn = document.getElementById('btn-check-updates');
    const spinner = document.getElementById('btn-check-spinner');
    const label = document.getElementById('btn-check-label');

    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.remove('hidden');
    if (label) label.textContent = 'Проверка...';

    try {
      const resp = await fetch(`/api/updater/check?force=${force ? 'true' : 'false'}`);
      const data = await resp.json();

      if (data.error && !data.update_available) {
        updateStatusCard(data, true);
        if (window.dfToast) window.dfToast(data.error);
      } else {
        updateStatusCard(data, false);
        if (data.update_available) {
          populateModal(data);
          if (openModalIfAvailable) {
            showModal();
          }
        } else if (force) {
          if (window.dfToast) window.dfToast('У вас установлена самая актуальная версия!');
        }
      }
    } catch (err) {
      console.warn('Update check failed:', err);
      updateStatusCard({ error: 'Не удалось связаться с сервером обновлений' }, true);
    } finally {
      isChecking = false;
      if (btn) btn.disabled = false;
      if (spinner) spinner.classList.add('hidden');
      if (label) label.textContent = 'Проверить обновления';
    }
  }

  async function startDownload() {
    if (!currentUpdateData || !currentUpdateData.asset_url) {
      // Fallback: open release page if direct asset is not attached
      if (currentUpdateData && currentUpdateData.html_url) {
        window.open(currentUpdateData.html_url, '_blank');
      }
      return;
    }

    const progBlock = document.getElementById('modal-progress-block');
    const progBar = document.getElementById('modal-progress-bar');
    const progLabel = document.getElementById('update-progress-label');
    const progStats = document.getElementById('update-progress-stats');
    const progDetails = document.getElementById('update-progress-details');
    const btnAction = document.getElementById('btn-start-update');
    const actionText = document.getElementById('btn-update-text');

    if (progBlock) progBlock.classList.remove('hidden');
    if (btnAction) btnAction.disabled = true;
    if (actionText) actionText.textContent = 'Загрузка...';

    try {
      const resp = await fetch('/api/updater/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          asset_url: currentUpdateData.asset_url,
          asset_name: currentUpdateData.asset_name,
        }),
      });

      if (!resp.ok) {
        throw new Error(`HTTP error ${resp.status}`);
      }

      // Start polling status
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(pollDownloadProgress, 350);
    } catch (err) {
      console.error('Download trigger failed:', err);
      if (progLabel) progLabel.textContent = 'Ошибка начала загрузки';
      if (btnAction) btnAction.disabled = false;
      if (actionText) actionText.textContent = 'Попробовать снова';
    }
  }

  async function pollDownloadProgress() {
    try {
      const resp = await fetch('/api/updater/status');
      const st = await resp.json();

      const progBar = document.getElementById('modal-progress-bar');
      const progLabel = document.getElementById('update-progress-label');
      const progStats = document.getElementById('update-progress-stats');
      const progDetails = document.getElementById('update-progress-details');
      const btnAction = document.getElementById('btn-start-update');
      const actionText = document.getElementById('btn-update-text');
      const msgEl = document.getElementById('modal-update-message');

      if (st.status === 'downloading') {
        const pct = st.progress_pct || 0;
        if (progBar) progBar.style.width = `${pct}%`;
        if (progStats) progStats.textContent = `${pct}%`;
        if (progDetails) {
          const down = formatBytes(st.downloaded_bytes);
          const total = formatBytes(st.total_bytes);
          const speed = formatSpeed(st.speed_bps);
          progDetails.textContent = `${down} / ${total} · ${speed}`;
        }
      } else if (st.status === 'ready') {
        clearInterval(pollInterval);
        pollInterval = null;

        if (progBar) progBar.style.width = '100%';
        if (progStats) progStats.textContent = '100%';
        if (progLabel) progLabel.textContent = 'Обновление загружено и проверено!';
        if (progDetails) progDetails.textContent = 'Готово к установке';

        if (btnAction && actionText) {
          btnAction.disabled = false;
          btnAction.classList.add('btn-ready-restart');
          actionText.textContent = 'Перезапустить и применить';
        }
      } else if (st.status === 'error') {
        clearInterval(pollInterval);
        pollInterval = null;

        if (progLabel) progLabel.textContent = 'Ошибка загрузки';
        if (msgEl) {
          msgEl.classList.remove('hidden');
          msgEl.className = 'update-modal-msg is-error';
          msgEl.textContent = `Не удалось скачать обновление: ${st.error || 'Сбой сети'}`;
        }
        if (btnAction && actionText) {
          btnAction.disabled = false;
          actionText.textContent = 'Повторить загрузку';
        }
      }
    } catch (err) {
      console.warn('Status poll error:', err);
    }
  }

  async function applyUpdate() {
    const btnAction = document.getElementById('btn-start-update');
    const actionText = document.getElementById('btn-update-text');
    const msgEl = document.getElementById('modal-update-message');

    if (btnAction) btnAction.disabled = true;
    if (actionText) actionText.textContent = 'Применение...';

    try {
      const resp = await fetch('/api/updater/apply', { method: 'POST' });
      const data = await resp.json();

      if (msgEl) {
        msgEl.classList.remove('hidden');
        msgEl.className = data.success ? 'update-modal-msg is-success' : 'update-modal-msg is-error';
        msgEl.textContent = data.message || 'Перезапуск программы...';
      }

      if (data.dev_mode) {
        if (actionText) actionText.textContent = 'Готово (Dev Mode)';
      }
    } catch (err) {
      console.error('Apply error:', err);
      if (btnAction) btnAction.disabled = false;
      if (actionText) actionText.textContent = 'Попробовать снова';
    }
  }

  function handleActionClick() {
    const btnAction = document.getElementById('btn-start-update');
    if (btnAction && btnAction.classList.contains('btn-ready-restart')) {
      applyUpdate();
    } else {
      startDownload();
    }
  }

  function init() {
    // 1. Hook check button in Profile
    const btnCheck = document.getElementById('btn-check-updates');
    if (btnCheck) {
      btnCheck.addEventListener('click', () => checkForUpdates(true, true));
    }

    // 2. Hook header update badge
    const headerBadge = document.getElementById('btn-update-badge');
    if (headerBadge) {
      headerBadge.addEventListener('click', () => {
        if (currentUpdateData && currentUpdateData.update_available) {
          showModal();
        } else {
          checkForUpdates(true, true);
        }
      });
    }

    // 3. Modal close hooks
    const btnClose = document.getElementById('btn-close-update-modal');
    if (btnClose) btnClose.addEventListener('click', closeModal);

    const btnCancel = document.getElementById('btn-cancel-update');
    if (btnCancel) btnCancel.addEventListener('click', closeModal);

    const modal = document.getElementById('update-modal');
    if (modal) {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
      });
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
        closeModal();
      }
    });

    // 4. Action button hook
    const btnAction = document.getElementById('btn-start-update');
    if (btnAction) {
      btnAction.addEventListener('click', handleActionClick);
    }

    // 5. Silent check on launch (delayed slightly so main UI boots instantaneously)
    setTimeout(() => {
      checkForUpdates(false, false);
    }, 1500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
