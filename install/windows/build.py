import os
import subprocess
import sys
import odemis

cpy_command = ["python", "setup.py", "build_ext", "--inplace"]
pyi_command = ["pyinstaller", "-y", "viewer.spec"]
nsis_command = [r"C:\Program Files (x86)\NSIS\makensis",
                "/DPRODUCT_VERSION=" + '.'.join(odemis._get_version().split('-')[:2]),
                "setup.nsi"]


def run_command(cmd, flavor=None):

    if flavor is not None:
        os.environ['FLAVOR'] = str(flavor)

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print "\n\n"
    for line in iter(p.stdout.readline, b''):
        print line.rstrip()
    return p.wait()


def add_size_to_version():
    with open('dist/version.txt', 'a') as f:
        version = '.'.join(odemis._get_version().split('-')[:2])
        f.write(str(os.path.getsize("dist\OdemisViewer-%s.exe" % version)) + '\n')

print "Build Odemis viewers", '.'.join(odemis._get_version().split('-')[:2])

os.chdir(os.path.dirname(__file__) or '.')

while True:
    i = raw_input("""
    [1] - Executable build
    [2] - Installer build

    > """)

    if i == '1':
        run_command(cpy_command)
        run_command(pyi_command, "odemis")
        run_command(pyi_command, "delphi")
    elif i == '2':
        info = [
            "/DPRODUCT_NAME=OdemisViewer",
            "/DPRODUCT_HNAME=Odemis Viewer",
            "/DIMAGE=install_odemis.bmp",
            "/DWEBSITE=http://www.delmic.com",
        ]
        nsis_cmd = nsis_command[:-1] + info + [nsis_command[-1]]
        run_command(nsis_cmd, "odemis")

        info = [
            "/DPRODUCT_NAME=DelphiViewer",
            "/DPRODUCT_HNAME=Delphi Viewer",
            "/DIMAGE=install_delphi.bmp",
            "/DWEBSITE=http://www.delphimicroscope.com",
        ]
        nsis_cmd = nsis_command[:-1] + info + [nsis_command[-1]]
        run_command(nsis_cmd, "delphi")

        add_size_to_version()
    else:
        break
    print "\n\nBuild Done."
sys.exit(0)

