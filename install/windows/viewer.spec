# -*- mode: python -*-


def get_lib_tiff():
    import site
    import os

    for path in site.getsitepackages():
        tiff_path = os.path.join(path, 'libtiff')
        if os.path.exists(tiff_path):
            return [
                ('libtiff\\libtiff.dll', os.path.join(tiff_path, 'libtiff.dll'), 'DATA'),
                ('libtiff\\tiff.h', os.path.join(tiff_path, 'tiff.h'), 'DATA')
            ]

    raise ImportError("Could not find Libtiff files!")


def get_cairo_dlls():
    import os

    dlls = [
        "freetype6.dll",
        "libcairo-2.dll",
        "libexpat-1.dll",
        "libfontconfig-1.dll",
        "libpng14-14.dll"
    ]

    dll_path = '.\\bin\\dll'

    if all(os.path.exists(os.path.join(dll_path, dll)) for dll in dlls):
        return [(dll, os.path.join(dll_path, dll), 'DATA') for dll in dlls]

    dll_path = os.environ['WINDIR'] + "\\SysWOW64"

    if all(os.path.exists(os.path.join(dll_path, dll)) for dll in dlls):
        return [(dll, os.path.join(dll_path, dll), 'DATA') for dll in dlls]

    dll_path = os.environ['WINDIR'] + "\\System32"

    if all(os.path.exists(os.path.join(dll_path, dll)) for dll in dlls):
        return [(dll, os.path.join(dll_path, dll), 'DATA') for dll in dlls]

    raise ImportError("Could not find Libtiff files!")

def get_version():
    import odemis
    with open('version.txt', 'w') as f:
        f.write(odemis._get_version())
    return [('version.txt', 'version.txt', 'DATA')]

a = Analysis(['viewer.py'],
             pathex=['.'],
             hiddenimports=[
                 'cairo',
                 'odemis.dataio.*',
             ],
             hookspath=None,
             runtime_hooks=None)

pyz = PYZ(a.pure)

exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='OdemisViewer.exe',
          debug=False,
          strip=None,
          upx=False,
          console=False,
          icon='odemis-viewer.ico'
       )

coll = COLLECT(exe,
               a.binaries,
               get_lib_tiff(),
               get_cairo_dlls(),
               get_version(),
               [('OdemisViewer.ico', 'odemis-viewer.ico', 'DATA')],
               a.zipfiles,
               a.datas,
               strip=None,
               upx=False,
               name='OdemisViewer'
       )
