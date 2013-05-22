#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import glob

setup(name='Odemis',
      version='1.1', # TODO: get from git? see http://dcreager.net/2010/02/10/setuptools-git-version-numbers/
      description='Open Delmic Microscope Software',
      author='Éric Piel, Rinze de Laat',
      author_email='piel@delmic.com, laat@delmic.com',
      url='https://github.com/delmic/odemis',
      classifiers=["License :: OSI Approved :: GNU General Public License v2 (GPLv2)"],
      package_dir = {'': 'src'},
      packages=find_packages('src', exclude=["*.test"]),
      # TODO should be dependent on os
      data_files=[('/etc/', ['install/linux/etc/odemis.conf']),
                  ('/etc/udev/rules.d', glob.glob('install/linux/etc/udev/rules.d/*.rules')), # TODO: use os.path.join for /
                  ('/usr/share/odemis/', glob.glob('install/linux/usr/share/odemis/*.*')),
                  # TODO: how to run desktop-file-install?
                  # see http://ubuntuforums.org/showthread.php?t=1121501
                  ('/usr/share/applications/', ['install/linux/usr/share/applications/odemis.desktop']),
                  ('/usr/share/icons/hicolor/128x128/apps/', ['install/linux/usr/share/icons/hicolor/128x128/apps/odemis.png']),
                  ]
     )
