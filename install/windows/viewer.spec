# -*- mode: python -*-

import os


def get_lib_tiff():
    """ Help PyInstaller find all the lib-tiff files it needs """
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
    """ Help PyInstaller find all the Cairo files it needs """
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

    raise ImportError("Could not find Cairo files!")


def get_version():
    """ Write the current version of Odemis to a txt file and tell PyInstaller where to find it """
    import odemis

    with open('dist/version.txt', 'w') as f:
        long_version = '.'.join(odemis._get_version().split('-')[:2])
        f.write(long_version + '\n')
    return [('version.txt', 'dist/version.txt', 'DATA')]


# Check what type of viewer we are building
if os.environ.get('FLAVOR') == "delphi":
    name = "DelphiViewer"
    script = "delphi_viewer.py"
else:
    name = "OdemisViewer"
    script = "odemis_viewer.py"


# The statements in a spec file create instances of four classes, Analysis, PYZ, EXE and COLLECT.
#
# * A new instance of class Analysis takes a list of script names as input. It analyzes all
#   imports and other dependencies. The resulting object (assigned to a) contains lists of
#   dependencies in class members named:
#
#   - scripts: the python scripts named on the command line;
#   - pure: pure python modules needed by the scripts;
#   - binaries: non-python modules needed by the scripts;
#   - datas: non-binary files included in the app.
# * An instance of class PYZ is a .pyz archive (described under Inspecting Archives below),
#   which contains all the Python modules from a.pure.
# * An instance of EXE is built from the analyzed scripts and the PYZ archive. This object creates
#   the executable file.
# * An instance of COLLECT creates the output folder from all the other parts.

block_cipher = None
use_upx = False

a = Analysis(
    [script],
    pathex=['.'],
    binaries=None,
    datas=None,
    hiddenimports=[
        'cairo',
        'odemis.dataio.*',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher
)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='%s.exe' % name,
    debug=False,
    strip=False,
    upx=use_upx,
    console=False,
    icon='odemis-viewer.ico'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    get_lib_tiff(),
    get_cairo_dlls(),
    get_version(),
    [('OdemisViewer.ico', 'odemis-viewer.ico', 'DATA')],
    strip=False,
    upx=use_upx,
    name=name
)
