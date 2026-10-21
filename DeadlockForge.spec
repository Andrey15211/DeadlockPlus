# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-спека DeadlockForge (single-file, windowed).

Ключевые решения:
- иконка exe берётся из web/assets/icons/deadlockforge.ico (иначе Windows
  подставляет стандартную «дискету»);
- api.txt с ключом LLM НИКОГДА не упаковывается: файл попадал в дистрибутив
  (deadlockforge/ai/api.txt) и утекал заказчику. Фильтр _strip_secrets снимает
  его и любые другие *.txt с ключом из datas;
- web/ и data/ пакуются целиком, остальное тянет collect_all.
"""
from PyInstaller.utils.hooks import collect_all

ROOT = 'C:/Projects/DeadlockForge'

# Имена, которые не должны попадать в сборку ни при каких условиях.
BLOCKED_DATA_SUFFIXES = ('api.txt', 'secrets.txt', '.env')

# Промежуточные файлы сборки контрпиков: сырой дамп сайта, английский источник,
# чанки перевода и кэш нужны только на машине разработчика. В дистрибутив
# уходит единственный data/counterpicks.json.
BLOCKED_DATA_PARTS = ('counterpicks/', '\\counterpicks\\')


def _strip_secrets(datas):
    """Убрать из datas секреты, пользовательские БД и промежуточные данные контрпиков."""
    keep = []
    for entry in datas:
        if len(entry) == 3:
            dest, src, type_ = entry
        else:
            src, dest = entry
        if src.lower().endswith(BLOCKED_DATA_SUFFIXES):
            continue
        if src.lower().endswith('.db'):  # SQLite создаётся на первом запуске
            continue
        if any(part in src for part in BLOCKED_DATA_PARTS):
            continue
        keep.append(entry)
    return keep


datas = [
    (f'{ROOT}/web', 'web'),
    (f'{ROOT}/data', 'data'),
]
binaries = []
hiddenimports = []

for pkg in ('deadlockforge', 'pywebview'):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

datas = _strip_secrets(datas)

a = Analysis(
    [f'{ROOT}/run_desktop.py'],
    pathex=[f'{ROOT}/src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
a.datas = _strip_secrets(a.datas)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DeadlockForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=f'{ROOT}/web/assets/icons/deadlockforge.ico',
)