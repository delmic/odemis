import os
import subprocess
import sys
import odemis
import traceback
from builtins import input

cpy_command = ["python", "setup.py", "build_ext", "--inplace"]
pyi_command = ["pyinstaller", "--clean", "-y", "viewer.spec"]
nsis_command = [
    r"C:\Program Files (x86)\NSIS\makensis",
    "/DPRODUCT_VERSION=" + '.'.join(odemis._get_version().split('-')[:2]),
    "setup.nsi"
]

# PyInstaller/tkinter might have problems finding init.tcl
if 'TCL_LIBRARY' not in os.environ or 'TK_LIBRARY' not in os.environ:
    print("\n* ATTENTION * You might need to set the 'TCL_LIBRARY' and 'TK_LIBRARY' env vars!\n")


def run_command(cmd, flavor=None):
    if flavor is not None:
        os.environ['FLAVOR'] = str(flavor)
    try:
        subprocess.check_call(cmd)
    except Exception as ex:
        # Don't close terminal after raising Exception
        traceback.print_exc(ex)
        input("Press any key to exit.")
        sys.exit(-1)


def add_size_to_version():
    with open('dist/version.txt', 'a') as f:
        version = '.'.join(odemis._get_version().split('-')[:2])
        f.write(str(os.path.getsize("dist\OdemisViewer-%s.exe" % version)) + '\n')


print("Build OdemisViewer " + '.'.join(odemis._get_version().split('-')[:2]))

os.chdir(os.path.dirname(__file__) or '.')


def build_odemisviewer_exe():
    run_command(cpy_command)
    run_command(pyi_command, "odemis")


def build_delphiviewer_exe():
    run_command(cpy_command)
    run_command(pyi_command, "delphi")


def build_odemisviewer_inst():
    info = [
        "/DPRODUCT_NAME=OdemisViewer",
        "/DPRODUCT_HNAME=Odemis Viewer",
        "/DIMAGE=install_odemis.bmp",
        "/DWEBSITE=http://www.delmic.com",
    ]
    nsis_cmd = nsis_command[:-1] + info + [nsis_command[-1]]
    return run_command(nsis_cmd, "odemis")


def build_delphiviewer_inst():
    info = [
        "/DPRODUCT_NAME=DelphiViewer",
        "/DPRODUCT_HNAME=Delphi Viewer",
        "/DIMAGE=install_delphi.bmp",
        "/DWEBSITE=http://www.delphimicroscope.com",
    ]
    nsis_cmd = nsis_command[:-1] + info + [nsis_command[-1]]
    run_command(nsis_cmd, "delphi")


while True:
    i = input("""
    [1] OdemisViewer Executable
    [2] OdemisViewer Installer

    [3] DelphiViewer Executable
    [4] DelphiViewer Installer

    [5] Both Executables
    [6] Both Installers

    [7] OdemisViewer Executable and Installer
    [8] DelphiViewer Executable and Installer

    [9] Build everything

> """)

    try:
        i = int(i)
    except:
        break

    if i == 1:
        build_odemisviewer_exe()
    elif i == 2:
        build_odemisviewer_inst()
        add_size_to_version()
    elif i == 3:
        build_delphiviewer_exe()
    elif i == 4:
        build_delphiviewer_inst()
        add_size_to_version()
    elif i == 5:
        build_odemisviewer_exe()
        build_delphiviewer_exe()
    elif i == 6:
        build_odemisviewer_inst()
        build_delphiviewer_inst()
        add_size_to_version()
    elif i == 7:
        build_odemisviewer_exe()
        build_odemisviewer_inst()
        add_size_to_version()
    elif i == 8:
        build_delphiviewer_exe()
        build_delphiviewer_inst()
        add_size_to_version()
    elif i == 9:
        build_odemisviewer_exe()
        build_delphiviewer_exe()
        build_odemisviewer_inst()
        build_delphiviewer_inst()
        add_size_to_version()
    else:
        break
    print("\n\nBuild Done.")
sys.exit(0)

