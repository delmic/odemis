Running Odemis on Windows
=========================

This document describes how to get the development version Odemis GUI working on
MS Windows, so it can be used as an image viewer. It will also explain how to
create an installer for easy distribution.

Also, see https://delmicbv.box.com/s/3renoh6qw32kmzpduyssupnlro3sawn2 for binary packages

Creating the Odemis environment
-------------------------------

First we create a base installation of Python 2.7:

1.  Install the latest Python 2.7 32-bit release
2.  Install setuptools using instructions from
    https://pypi.python.org/pypi/setuptools
3.  Download and run https://raw.github.com/pypa/pip/master/contrib/get-pip.py
    to install pip
4.  Use pip to install Virtualenv: `pip install virtualenv`.

Additionally, virutalenv wrappers might be installed, which will make it a bit
easier to work with in Windows Powershell or the regular command prompt.

The next step is to create a virtualenv for Odemis and start installing the
required packages into it.

..Note: Pip cannot always be used, because some packages need to compile (parts
of) themselves. In this case we need to download the relevant Windows installer
and use `easy_install` (which is part of `setuptools`) to install the package.

#.  Git clone https://github.com/delmic/odemis.git into the project directory of
    the Odemis virtualenv.
#.  Install wheel, so we can install binary packages using pip:
    `pip install wheel`.
#.  Install futures using `pip install futures`
#.  Install Yaml using `pip install pyaml`
#.  Install 0MQ using `pip install pyzmq`
#.  Install the decorator module using `pip install decorator`

#.  Install Delmic's special version of Pyro4:
    `pip install git+https://github.com/delmic/Pyro4.git`
#.  Install Numpy using `pip install "numpy-1.9.2+mkl-cp27-none-win32.whl"`,
    downloaded from http://www.lfd.uci.edu/~gohlke/pythonlibs/#numpy
#.  Install wxPython3.0 using
    `pip install wxPython_common-3.0.2.0-py2-none-any.whl` followed by
    `pip install wxPython-3.0.2.0-cp27-none-win32.whl`, downloaded from
    http://www.lfd.uci.edu/~gohlke/pythonlibs/#wxpython
#.  Install using `pip install libtiff-0.4.0-cp27-none-win32.whl`, downloaded
    from http://www.lfd.uci.edu/~gohlke/pythonlibs/#pylibtiff
#.  `pip install scipy-0.15.1-cp27-none-win32.whl`, downloaded from
    http://www.lfd.uci.edu/~gohlke/pythonlibs/#scipy
#.  Install OpenCV using `pip install opencv_python-2.4.11-cp27-none-win32.whl`,
    downloaded from http://www.lfd.uci.edu/~gohlke/pythonlibs/#opencv
#.  Install H5py using `pip install h5py-2.5.0-cp27-none-win32.whl`, downloaded
    from http://www.lfd.uci.edu/~gohlke/pythonlibs/#h5py
#.  Install Matplotlib using `pip install matplotlib-1.4.3-cp27-none-win32.whl`,
    downloaded from http://www.lfd.uci.edu/~gohlke/pythonlibs/#matplotlib
#.  Download PyCairo from http://wxpython.org/cairo/ (The Wheel packages are not
    suitable for use with wxPython). We also need `libcairo-2.dll`,
    `freetype6.dll`, `libexpat-1.dll`, `libfontconfig-1.dll`, `libpng14-14.dll`
    and `zlib1.dll` from this location.
#.  Install PyCairo using `easy_install -Z py2cairo-1.10.0.win32-py2.7.exe` and
    copy all DLL files to %Windows%/SysWOW64
#.  Install Pillow, a repackaged version of PIL: `pip install Pillow`

Installing PyInstaller
----------------------

#. Add the Odemis root path to the `virtualenv_path_extensions.pth` file in the virtualenv:
   `<path to Odemis>\src`
#. Install PyWin32: `easy_install pywin32-219.win32-py2.7.exe`
#. Install PyInstaller: `pip install pyinstaller` or
   `pip install git+git://github.com/pyinstaller/pyinstaller.git@develop` if pymzq is causing is
   causing problems.
#. Install MSVCP90.dll redistribution by running `vcredist_x86.exe`, otherwise Pyinstaller won't be
   able to find and package it.

Building the stand-alone Odemis viewer
--------------------------------------

`pyinstaller -y viewer.spec`

Building Windows installer
--------------------------

Install Nsis and runL

`"C:\Program Files (x86)\NSIS\makensis" setup.nsi`
