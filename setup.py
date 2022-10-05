#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# To rebuild just the cython modules, use these commands:
# sudo apt-get install python-setuptools cython
# python3 setup.py build_ext --inplace
from setuptools import setup, find_packages
from Cython.Build import cythonize # Warning: must be _after_ setup import
import glob
import os
import subprocess
import sys
import numpy

# To be updated to the current version
VERSION = "3.2.0"
# We cannot use the git version because it's not (always) available when building
# the debian package

# Trick from openshot
# Boolean: running as root in Linux?
ROOT = sys.platform.startswith("linux") and os.geteuid() == 0
# For Debian packaging it could be a fakeroot so reset flag to prevent execution of
# system update services for Mime and Desktop registrations.
# The debian/odemis.postinst script must do those.
if "FAKEROOTKEY" in os.environ:
    print("NOTICE: Detected execution in a FakeRoot so disabling system update services.")
    ROOT = False

# almost copy from odemis.__init__.py, but we cannot load it as it's not installed yet
def _get_version_git():
    """
    Get the version via git
    raises LookupError if no version info found
    """
    # change directory to root
    rootdir = os.path.dirname(__file__)

    try:
        out = subprocess.check_output(args=["git", "describe", "--tags", "--dirty", "--always"],
                                      cwd=rootdir)

        return out.strip().decode("utf-8")
    except EnvironmentError:
        raise LookupError("Unable to run git")

# Check version
try:
    gver = _get_version_git()
    if "-" in gver:
        sys.stderr.write("Warning: packaging a non-tagged version: %s\n" % gver)
    if VERSION != gver:
        sys.stderr.write("Warning: package version and git version don't match:"
                         " %s <> %s\n" % (VERSION, gver))
except LookupError:
    pass


if sys.platform.startswith('linux'):
    data_files = [('/etc/', ['install/linux/etc/odemis.conf']),
                  # Not copying sudoers file, as we are not sure there is a sudoers.d directory
                  # Not copying bash completion file, as we are not sure there is a directory
                  # TODO udev rules might actually be better off in /lib/udev/rules.d/
                  ('/lib/udev/rules.d', glob.glob('install/linux/lib/udev/rules.d/*.rules')),
                  ('share/odemis/', glob.glob('install/linux/usr/share/odemis/*.odm.yaml')),
                  ('share/odemis/sim', glob.glob('install/linux/usr/share/odemis/sim/*.odm.yaml')),
                  ('share/odemis/examples', glob.glob('install/linux/usr/share/odemis/examples/*.odm.yaml')),
                  ('share/odemis/hwtest', glob.glob('install/linux/usr/share/odemis/hwtest/*.odm.yaml')),
                  # The key(s) for the bug reporter
                  ('share/odemis/', glob.glob('install/linux/usr/share/odemis/*.key')),
                  # /usr/lib/odemis/plugins/ contains the plugins to be _loaded_,
                  # in /usr/share/, which put all the plugins which are available.
                  ('share/odemis/plugins/', glob.glob('plugins/*.py')),
                  # TODO: need to run desktop-file-install in addition to update-desktop-database?
                  ('share/applications/', glob.glob('install/linux/usr/share/applications/*.desktop')),
                  ('share/icons/hicolor/32x32/apps/', glob.glob('install/linux/usr/share/icons/hicolor/32x32/apps/odemis*.png')),
                  ('share/icons/hicolor/64x64/apps/', glob.glob('install/linux/usr/share/icons/hicolor/64x64/apps/odemis*.png')),
                  ('share/icons/hicolor/128x128/apps/', glob.glob('install/linux/usr/share/icons/hicolor/128x128/apps/odemis*.png')),
                  ('share/icons/hicolor/256x256/apps/', glob.glob('install/linux/usr/share/icons/hicolor/256x256/apps/odemis*.png')),
                  ('share/doc/odemis/', glob.glob('doc/*.txt')),
                  ('share/doc/odemis/scripts/', glob.glob('scripts/*.py') + glob.glob('scripts/*.m')),
                  ]
    # TODO: see if we could use entry_points instead
    scripts = ['install/linux/usr/bin/odemisd',
               'install/linux/usr/bin/odemis-cli',
               'install/linux/usr/bin/odemis-convert',
               'install/linux/usr/bin/odemis-gui',
               'install/linux/usr/bin/odemis-start',
               'install/linux/usr/bin/odemis-stop',
               'install/linux/usr/bin/odemis-cycle',
               'install/linux/usr/bin/odemis-relay',
               'install/linux/usr/bin/odemis-bug-report',
               'install/linux/usr/bin/odemis-sudo-gedit',
               'install/linux/usr/bin/odemis-edit-mic',
               'install/linux/usr/bin/odemis-hw-status',
               'install/linux/usr/bin/odemis-live-view',
               'install/linux/usr/bin/odemis-mic-selector',
               'util/piconfig',
               'util/pituner',
               'util/piterminal',
               'util/tmcmconfig',
               'util/shrkconfig',
               'util/saconfig',
               'util/pmconfig',
               'util/odemis-park-mirror',
               'util/check-mirror-ref',
               'util/axes-odometer',
               ]
else:
    data_files = []
    scripts = []
    sys.stderr.write("Warning: Platform %s not supported" % sys.platform)

dist = setup(name='Odemis',
             version=VERSION,
             description='Open Delmic Microscope Software',
             author=u'Éric Piel, Rinze de Laat, Kimon Tsitsikas, Philip Winkler, Anders Muskens, Sabrina Rossberger, Thera Pals, Victoria Mavrikopoulou, Kornee Kleijwegt, Bassim Lazem, Mahmoud Barazi, Arthur Helsloot',
             author_email='piel@delmic.com, laat@delmic.com, tsitsikas@delmic.com, winkler@delmic.com, muskens@delmic.com, rossberger@delmic.com, pals@delmic.com, mavrikopoulou@delmic.com, kleijwegt@delmic.com, lazem@delmic.com, barazi@delmic.com, helsloot@delmic.com',
             url='https://github.com/delmic/odemis',
             classifiers=["License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
                          "Operating System :: POSIX :: Linux",
                          "Programming Language :: Python",
                          "Intended Audience :: Science/Research",
                          "Topic :: Scientific/Engineering",
                          "Environment :: Console",
                          "Environment :: X11 Applications :: GTK",
                         ],
             package_dir={'': 'src'},
             packages=find_packages('src', exclude=["*.test"]),
             package_data={'odemis.gui.img': ["*.png", "icon/*.png", "menu/*.png", "button/*.png", "calibration/*.png"],
                           'odemis.gui': ["doc/*.html"],
                           'odemis.driver': ["*.tiff", "*.h5", "*.eds"],
                          },
             ext_modules=cythonize(glob.glob(os.path.join("src", "odemis", "util", "*.pyx")), language_level=3),
             scripts=scripts,
             data_files=data_files,  # not officially in setuptools, but works as for distutils
             include_dirs=[numpy.get_include()],
            )

if ROOT and dist is not None:
    # for mime file association, see openshot's setup.py
    # update the XDG .desktop file database
    try:
        print("Updating the .desktop file database.")
        subprocess.check_output(["update-desktop-database"])
    except Exception:
        sys.stderr.write("Failed to update.\n")
