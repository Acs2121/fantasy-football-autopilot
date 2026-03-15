# PyInstaller spec for Fantasy Football Autopilot
# Build command: pyinstaller build.spec -y

from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# Collect all submodules + data for packages PyInstaller commonly misses
nfl_datas,    nfl_bins,  nfl_hidden    = collect_all('nfl_data_py')
pd_datas,     pd_bins,   pd_hidden     = collect_all('pandas')
pa_datas,     pa_bins,   pa_hidden     = collect_all('pyarrow')
req_datas,    req_bins,  req_hidden    = collect_all('requests')

a = Analysis(
    ['AutoPicker_Desktop.py'],
    pathex=['.'],
    binaries=[
        *nfl_bins,
        *pd_bins,
        *pa_bins,
        *req_bins,
    ],
    datas=[
        ('templates',  'templates'),
        ('static',     'static'),
        ('data',       'data'),
        ('config.py',  '.'),
        ('engine',     'engine'),
        *nfl_datas,
        *pd_datas,
        *pa_datas,
        *req_datas,
    ],
    hiddenimports=[
        # Flask stack
        'flaskwebgui',
        'psutil',
        'flask',
        'werkzeug',
        'jinja2',
        'engineio.async_drivers.threading',
        # New data pipeline
        'nfl_data_py',
        'appdirs',
        'pandas',
        'pyarrow',
        'pyarrow.pandas_compat',
        # Common pandas internals PyInstaller misses
        'pandas._libs.tslibs.np_datetime',
        'pandas._libs.tslibs.nattype',
        'pandas._libs.tslibs.timedeltas',
        'pandas._libs.tslibs.timestamps',
        'pandas._libs.tslibs.offsets',
        'pandas._libs.tslibs.period',
        'pandas._libs.tslibs.parsing',
        'pandas._libs.parsers',
        'pandas._libs.reduction',
        'pandas._libs.ops',
        'pandas._libs.skiplist',
        # Spread entries from collect_all
        *nfl_hidden,
        *pd_hidden,
        *pa_hidden,
        *req_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook.py'],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FantasyFootballAutopilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FantasyFootballAutopilot',
)
