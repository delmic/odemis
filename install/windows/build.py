import os
import subprocess
import sys
import odemis

cpy_command = ["python", "setup.py", "build_ext", "--inplace"]
pyi_command = ["pyinstaller", "-y", "viewer.spec"]
nsis_command = [r"C:\Program Files (x86)\NSIS\makensis",
                "/DPRODUCT_VERSION=" + odemis._get_version(),
                "setup.nsi"]


def run_command(cmd):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print "\n\n"
    for line in iter(p.stdout.readline, b''):
        print line.rstrip()
    return p.wait()

print "Building OdemisViewer", odemis._get_version()

os.chdir(os.path.dirname(__file__) or '.')

while True:
    i = raw_input("""
    [1] - Compile CPython modules
    [2] - Build MS Windows executable
    [3] - Build MS Windows installer
    [4] - Run all

    > """)

    if i == '1':
        run_command(cpy_command)
    elif i == '2':
        run_command(pyi_command)
    elif i == '3':
        run_command(nsis_command)
    elif i == '4':
        ret_code = run_command(cpy_command)
        if ret_code == 0:
            ret_code = run_command(pyi_command)
            if ret_code == 0:
                ret_code = run_command(nsis_command)
    else:
        break
    print "\n\nBuild Done."
sys.exit(0)

