# PyInstaller spec for the Sprite Sheet Maker.
#
# One file, one console window. The page and its assets are bundled as data and
# unpacked beside the temporary bundle at run time; app/main.py looks for them
# through sys._MEIPASS. ffmpeg is deliberately NOT bundled - it is large, its
# licensing depends on the build, and the app degrades to GIF/APNG/still work
# without it and says so in a banner.
#
#   pyinstaller spritesheet-maker.spec
#
# Windows gets spritesheet-maker.exe; the same spec builds a native binary on
# macOS and Linux.
import sys

block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=[('static', 'static')],
    hiddenimports=[
        # uvicorn resolves these by name at run time, so the analysis misses them
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        'app.main',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'PIL.ImageQt', 'PySide6', 'PyQt5'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='spritesheet-maker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,            # the console is how you read the URL and stop the server
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
