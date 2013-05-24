#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import glob
import sys
import os
import subprocess

# Trick from openshot
# Boolean: running as root?
ROOT = os.geteuid() == 0
# For Debian packaging it could be a fakeroot so reset flag to prevent execution of
# system update services for Mime and Desktop registrations.
# The debian/odemis.postinst script must do those.
if not os.getenv("FAKEROOTKEY") == None:
    print "NOTICE: Detected execution in a FakeRoot so disabling calls to system update services."
    ROOT = False

dist = setup(name='Odemis',
      version='1.1', # TODO: get from git? see http://dcreager.net/2010/02/10/setuptools-git-version-numbers/
      description='Open Delmic Microscope Software',
      author='Éric Piel, Rinze de Laat',
      author_email='piel@delmic.com, laat@delmic.com',
      url='https://github.com/delmic/odemis',
      classifiers=["License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
                   "Operating System :: POSIX :: Linux",
                   "Programming Language :: Python",
                   "Intended Audience :: Science/Research",
                   "Topic :: Scientific/Engineering",
                   "Environment :: Console",
                   "Environment :: X11 Applications :: GTK",
                  ],
      package_dir = {'': 'src'},
      packages=find_packages('src', exclude=["*.test"]),
      # TODO should be dependent on os
      data_files=[('/etc/', ['install/linux/etc/odemis.conf']),
                  # TODO udev rules might actually be better off in /lib/udev/rules.d/
                  ('/etc/udev/rules.d', glob.glob('install/linux/etc/udev/rules.d/*.rules')), # TODO: use os.path.join for /
                  ('share/odemis/', glob.glob('install/linux/usr/share/odemis/*.odm.yaml')),
                  # TODO: need to run desktop-file-install in addition to update-desktop-database?
                  ('share/applications/', ['install/linux/usr/share/applications/odemis.desktop']),
                  ('share/icons/hicolor/32x32/apps/', ['install/linux/usr/share/icons/hicolor/32x32/apps/odemis.png']),
                  ('share/icons/hicolor/64x64/apps/', ['install/linux/usr/share/icons/hicolor/64x64/apps/odemis.png']),
                  ('share/icons/hicolor/128x128/apps/', ['install/linux/usr/share/icons/hicolor/128x128/apps/odemis.png']),
                  ('bin', ['install/linux/usr/local/bin/odemisd',
                                'install/linux/usr/local/bin/odemis-cli',
                                'install/linux/usr/local/bin/odemis-gui',
                                'install/linux/usr/local/bin/odemis-start',
                                'install/linux/usr/local/bin/odemis-stop'
                                ]),
                  ]
     )

if ROOT and dist != None:
    # for mime file association, see openshot's setup.py
    # update the XDG .desktop file database
    try:
        sys.stdout.write('Updating the .desktop file database.\n')
        subprocess.check_output(["update-desktop-database"])
    except Exception:
        sys.stderr.write("Failed to update.\n")
