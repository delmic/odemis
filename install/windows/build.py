#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import odemis
import logging
from builtins import input

cpy_command = ["python", "setup.py", "build_ext", "--inplace"]
pyi_command = ["pyinstaller", "--clean", "-y", "viewer.spec"]
nsis_command = [
    r"C:\Program Files (x86)\NSIS\makensis",
    "/DPRODUCT_VERSION=" + odemis.get_version_simplified(),
    "setup.nsi"
]
sign_command = ["signtool", "sign", "/fd", "SHA256", "/t", "http://timestamp.digicert.com"]

def run_command(cmd, flavor=None):
    if flavor is not None:
        os.environ['FLAVOR'] = str(flavor)
    try:
        subprocess.check_call(cmd)
    except Exception as ex:
        # Don't close terminal after raising Exception
        logging.exception("Failed to call %s", cmd)
        input("Press any key to return to menu.")


def add_size_to_version():
    with open('dist/version.txt', 'w') as f:
        version = odemis.get_version_simplified()
        f.write(version + '\n')
        f.write(str(os.path.getsize("dist\OdemisViewer-%s.exe" % version)) + '\n')


print("Build OdemisViewer " + odemis.get_version_simplified())

os.chdir(os.path.dirname(__file__) or '.')


def build_odemisviewer_exe():
    run_command(cpy_command)
    run_command(pyi_command, "odemis")


def build_odemisviewer_inst():
    info = [
        "/DPRODUCT_NAME=OdemisViewer",
        "/DPRODUCT_HNAME=Odemis Viewer",
        "/DIMAGE=install_odemis.bmp",
        "/DWEBSITE=http://www.delmic.com",
    ]
    nsis_cmd = nsis_command[:-1] + info + [nsis_command[-1]]
    return run_command(nsis_cmd, "odemis")


def sign_odemisviewer_inst():
    version = odemis.get_version_simplified()
    fn_exe = "dist\OdemisViewer-%s.exe" % version
    return run_command(sign_command + [fn_exe])

# In case the Delphi Viewer should be built, use these functions.
# def build_delphiviewer_exe():
#     run_command(cpy_command)
#     run_command(pyi_command, "delphi")

# def build_delphiviewer_inst():
#     info = [
#         "/DPRODUCT_NAME=DelphiViewer",
#         "/DPRODUCT_HNAME=Delphi Viewer",
#         "/DIMAGE=install_delphi.bmp",
#         "/DWEBSITE=http://www.delphimicroscope.com",
#     ]
#     nsis_cmd = nsis_command[:-1] + info + [nsis_command[-1]]
#     run_command(nsis_cmd, "delphi")


while True:
    i = input("""
    [1] OdemisViewer Executable
    [2] OdemisViewer Installer
    [3] Sign Installer

    [9] Build everything

    [Q] Quit

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
        sign_odemisviewer_inst()
        add_size_to_version()
    elif i == 9:
        build_odemisviewer_exe()
        # TODO: also sign the view exe?
#         build_delphiviewer_exe()
        build_odemisviewer_inst()
#         build_delphiviewer_inst()
        sign_odemisviewer_inst()
        add_size_to_version()
        print("\n\nBuild Done.")
    else:
        break

sys.exit(0)
