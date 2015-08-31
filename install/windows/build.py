import os
import subprocess
import sys
import odemis

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

os.chdir(os.path.dirname(__file__))

i = raw_input("""
[1] - Build MS Windows executable
[2] - Build MS Windows installer
[3] - Build both executable and installer

> """)

if i == '1':
    run_command(pyi_command)
elif i == '2':
    run_command(nsis_command)
elif i == '3':
    ret_code = run_command(pyi_command)
    if ret_code == 0:
        run_command(nsis_command)

print "\n\nBuild Done."
sys.exit(0)

