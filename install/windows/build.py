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

print "Building OdemisViewer", '.'.join(odemis._get_version().split('-')[:2])

os.chdir(os.path.dirname(__file__) or '.')

while True:
    i = raw_input("""
    [1] - Compile CPython modules
    [2] - Build Odemis Viewer executable
    [3] - Build Odemis Viewer installer
    [4] - Build Delphi Viewer executable
    [5] - Build Delphi Viewer installer
    [6] - Run all

    > """)

    if i == '1':
        run_command(cpy_command)
    elif i == '2':
        run_command(pyi_command, "odemis")
    elif i == '3':
        run_command(nsis_command, "odemis")
        add_size_to_version()
    elif i == '4':
        run_command(pyi_command, "delphi")
    elif i == '5':
        run_command(nsis_command, "delphi")
        add_size_to_version()
    elif i == '6':
        ret_code = run_command(cpy_command)
        if ret_code == 0:
            ret_code = run_command(pyi_command)
            if ret_code == 0:
                ret_code = run_command(nsis_command)
                add_size_to_version()
    else:
        break
    print "\n\nBuild Done."
sys.exit(0)

