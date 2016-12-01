****************
Extending Odemis
****************

Depending on how you want to extend Odemis, there are different ways to modify
it. In this chapter we first see how to set up a computer in order to develop 
easily Odemis, and then have a quick look at the various ways of extending 
Odemis.

Please note that almost all source code is released under the `GPLv2 license 
<http://gnu.org/licenses/old-licenses/gpl-2.0.html>`_.
This means in very broad terms that any modification or extension you make to it
will have to either be kept internal or also be made freely available to anyone.
See the file ``LICENSE.txt`` in the source code for the complete and official text 
of the license.

Moreover, if you have a microscope from Delmic, the maintenance contract only
supports the original version of the Odemis software. Modified versions of the
software are not covered by the maintenance contract (unless explicitly stated).
If you share your extension with Delmic and we decide to integrate them into a
later version of Odemis, then the contract will also cover the extension.

Setting up the development environment
======================================

Odemis is written almost entirely in Python language. So in theory, a simple
text editor could be enough to edit Odemis. However in order to execute, debug,
test, and edit efficiently Odemis, we recommend the following environment:

 * `Ubuntu <http://www.ubuntu.com>`_ 12.04 or 16.04 (x86 32 or 64 bits)
 * Eclipse + PyDev plug-in + Pylint
 * XRCed (package python-wxtools) for GUI edition

The source code available via a public git repository: https://github.com/delmic/odemis.
To *clone* it, type::

   git clone https://github.com/delmic/odemis.git

Note that Odemis can run in fully simulated mode, where no actual hardware is
needed. In this case, it can run in a virtual machine.

Odemis can also run partially (ie, the data manipulation part) on Windows. See
the next section to install Odemis on this operating system.

Detailed instructions
---------------------

Download Ubuntu 16.04 at this address:
http://www.ubuntu.com/download/desktop/contribute?version=16.04.1&architecture=amd64

Install it by which ever way you prefer, following these instructions:
http://www.ubuntu.com/download/desktop/install-ubuntu-desktop

Once logged into your newly installed Ubuntu system, do the following steps.

Install the additional required packages
""""""""""""""""""""""""""""""""""""""""
Start a terminal (with Ctrl+Alt+T) and type::
 
    sudo add-apt-repository ppa:delmic-soft/odemis
    sudo apt-get update
    sudo apt-get dist-upgrade
    sudo apt-get install git imagej vim hdfview meld libtiff-tools gimp \
     libhdf5-serial-1.8.4 python-pyro4-delmic odemis fluodb python-wxtools \
     python-setuptools python-sphinx inkscape dia-gnome texlive pngcrush cython
    sudo apt-get build-dep odemis
    sudo adduser $(whoami) odemis
    mkdir development
    cd development
    git clone https://github.com/delmic/odemis.git
    cd odemis
    python setup.py build_ext --inplace

Configure Odemis for development
""""""""""""""""""""""""""""""""
Edit /etc/odemis.conf with::

    sudo gedit /etc/odemis.conf

Modify the first lines so they read like this::

    DEVPATH="$HOME/development"
    PYTHONPATH="$DEVPATH/odemis/src/:$PYTHONPATH"

And edit the MODEL line for the model you want (probably a simulated microscope
like ``sparc-sim`` or ``secom-sim``). For example::

    MODEL="$CONFIGPATH/sparc-sim.odm.yaml"
    
For some simulated microscopes, you need to set-up the simulated
acquisition board of the SEM with the following commands::

    sudo modprobe comedi comedi_num_legacy_minors=4
    sudo modprobe comedi_test
    sudo chmod a+rw /dev/comedi0
    sudo comedi_config /dev/comedi0 comedi_test 1000000,1000000

To automatically set-up the simulated board at computer start-up, you can copy
the 4 lines to ``/etc/rc.local``, without the ``sudo`` part.

Install Eclipse and the plugins
"""""""""""""""""""""""""""""""
Type the following commands::

    sudo add-apt-repository ppa:webupd8team/java
    sudo apt-get update
    sudo apt-get install oracle-java7-installer
    sudo easy_install --upgrade pylint
    cd ~
    mkdir usr
    cd usr
    wget http://www.eclipse.org/downloads/download.php?file=/technology/epp/downloads/release/neon/R/eclipse-java-neon-R-linux-gtk-x86_64.tar.gz&mirror_id=1099
    tar xf eclipse-java-neon-R-linux-gtk-x86_64.tar.gz
    ~/usr/eclipse/eclipse

Go to *Help/Marketplace...*. Search for PyDev, and install it.
Optionally, you can also install *Eclipse Color Theme*, *hunspell4eclipse*, and *ReST Editor*.

In the Eclipse preference window, go to *PyDev/PyLint* and as location of the 
pylint executable, indicate your lint.py, which is approximately at this place:
``/usr/bin/pylint``

Optionally, if you want to edit the microscope configuration files (``*.odm.yaml``),
add a file association with the Python editor. For this, in the preference 
window, go to *General/Editors/File Association* and add a file type "``*.yaml``". As
default editor, add the Python editor.

Edit Odemis with Eclipse
""""""""""""""""""""""""

1. Click on *File/New/PyDev Project*.

2. Enter "odemis" as project name

3. Select a directory for project contents: the place where Odemis was downloaded (i.e., ``/home/.../development/odemis``)

4. Select "Create 'src' folder and add it to the PYTHONPATH"

5. Click on Finish

Learning Python
"""""""""""""""
Almost all Odemis is written in Python. If you are not familiar with this
programming language, it is recommended you first have a look at a tutorial.
For instance, read 
`A Crash Course in Python for Scientists <http://nbviewer.ipython.org/gist/rpmuller/5920182>`_.

Using Git
"""""""""

Source code version control is managed with git. If you are not familiar with 
this tool, it is better to first learning its basics before going further. Refer
to tutorials such as `Pro Git <http://git-scm.com/book>`_ or
`Easy Version Control with Git <http://code.tutsplus.com/tutorials/easy-version-control-with-git--net-7449>`_.


Setting up the development environment on Windows
=================================================

This section describes how to get the development version Odemis GUI working on
MS Windows, so it can be used as an image viewer. It will also explain how to
create an installer for easy distribution.

Creating the Odemis environment
-------------------------------

First we create a base installation of Python 2.7:

#.  Install the latest Python 2.7 32-bit release
#.  Install setuptools using instructions from
    https://pypi.python.org/pypi/setuptools
#.  Download and run https://raw.github.com/pypa/pip/master/contrib/get-pip.py
    to install pip
#.  Use pip to install Virtualenv: ``pip install virtualenv``.

Additionally, virtualenv wrappers might be installed, which will make it a bit
easier to work with in Windows Powershell or the regular command prompt.

The next step is to create a virtualenv for Odemis and start installing the
required packages into it.
``mkvirtualenv odemisdev``

Note: Pip cannot always be used, because some packages need to compile (parts
of) themselves. In this case we need to download the relevant Windows installer
and use `easy_install` (which is part of `setuptools`) to install the package.

#.  Git clone https://github.com/delmic/odemis.git into the project directory of
    the Odemis virtualenv.
#.  Install wheel, so we can install binary packages using pip:
    ``pip install wheel``.
#.  Install futures using ``pip install futures``
#.  Install Yaml using ``pip install pyaml``
#.  Install 0MQ using ``pip install pyzmq``
#.  Install the decorator module using ``pip install decorator``
#.  Install Delmic's special version of Pyro4:
    ``pip install git+https://github.com/delmic/Pyro4.git``
#.  Install Numpy using ``pip install "numpy-1.9.2+mkl-cp27-none-win32.whl"``,
    downloaded from http://www.lfd.uci.edu/~gohlke/pythonlibs/#numpy
#.  Install wxPython3.0 using
    ``pip install wxPython_common-3.0.2.0-py2-none-any.whl`` followed by
    ``pip install wxPython-3.0.2.0-cp27-none-win32.whl``, downloaded from
    http://www.lfd.uci.edu/~gohlke/pythonlibs/#wxpython
#.  Install using ``pip install libtiff-0.4.0-cp27-none-win32.whl``, downloaded
    from http://www.lfd.uci.edu/~gohlke/pythonlibs/#pylibtiff
#.  ``pip install scipy-0.15.1-cp27-none-win32.whl``, downloaded from
    http://www.lfd.uci.edu/~gohlke/pythonlibs/#scipy
#.  Install OpenCV using ``pip install opencv_python-2.4.11-cp27-none-win32.whl``,
    downloaded from http://www.lfd.uci.edu/~gohlke/pythonlibs/#opencv
#.  Install H5py using ``pip install h5py-2.5.0-cp27-none-win32.whl``, downloaded
    from http://www.lfd.uci.edu/~gohlke/pythonlibs/#h5py
#.  Install Matplotlib using ``pip install matplotlib-1.4.3-cp27-none-win32.whl``,
    downloaded from http://www.lfd.uci.edu/~gohlke/pythonlibs/#matplotlib
#.  Download PyCairo from http://wxpython.org/cairo/ (The Wheel packages are not
    suitable for use with wxPython). We also need ``libcairo-2.dll``,
    ``freetype6.dll``, ``libexpat-1.dll``, ``libfontconfig-1.dll``, ``libpng14-14.dll``
    and ``zlib1.dll`` from this location.
#.  Install PyCairo using ``easy_install -Z py2cairo-1.10.0.win32-py2.7.exe`` and
    copy all DLL files to ``%Windows%\SysWOW64``
#.  Install Pillow, a repackaged version of PIL: ``pip install Pillow``

Building Cython module(s)
-------------------------

Some parts of Odemis are written with Cython, for optimization reasons. To build these modules on
MS Windows, first install Visual Studio for Python 2.7, which can be found here:

https://www.microsoft.com/en-us/download/details.aspx?id=44266

This is a simple compiler distribution from Microsoft, specifically made for Python.
You also need to install Cython using ``pip install Cython-0.25.1-cp27-cp27m-win32.whl``,
downloaded from http://www.lfd.uci.edu/~gohlke/pythonlibs/#cython

After installation, use the `setup.py` file from the `install/windows` folder to
build the `*.pyd` files:

``python setup.py build_ext --inplace``

**IMPORTANT**: It will be necessary to update the `productdir` path in the `setup.py` file!

Installing PyInstaller
----------------------

#. Add the Odemis root path to the virtualenv:
   ``add2virtualenv <path to Odemis>\src``.
   Alternatively, you can modify the `virtualenv_path_extensions.pth` file.
#. Install PyWin32: ``easy_install pywin32-219.win32-py2.7.exe``
#. Install PyInstaller: `pip install pyinstaller` or
   ``pip install git+git://github.com/pyinstaller/pyinstaller.git@develop`` if pyzmq is causing is
   causing problems.
#. Install MSVCP90.dll redistribution by running `vcredist_x86.exe`, otherwise
   Pyinstaller won't be able to find and package it. It can be downloaded from
   https://www.microsoft.com/en-us/download/details.aspx?id=29 .

Building the stand-alone Odemis viewer
--------------------------------------

``pyinstaller -y viewer.spec``

Building Windows installer
--------------------------

Install Nsis and run:

``"C:\Program Files (x86)\NSIS\makensis" setup.nsi``


Setting up a data analysis environment on Windows
=================================================

For users which don't want to actually modify Odemis, but only rely on it as a
Python module for data analysis, it's possible to set-up an environment in a
relatively straight-forward way.


Installing Odemis Viewer
------------------------

This is an optional step, which allows you to open and analyse acquisitions files
straight into the same graphical interface as the acquisition software.

Download the Odemis viewer from http://www.delmic.com/odemis. In case your
browser warns you about potential thread, confirm you are willing to download
the file. Then run the executable, and Odemis viewer will be available as a
standard software.


Installing Python environment
-----------------------------

This allows you to manipulate the data in Python, either by writing Python
scripts, or via a command-line interface.

#. Install Anaconda from https://www.continuum.io/downloads. Pick the Python 2.7
   version, with the right architecture for your computer (most likely 64-bit).

#. Install Delmic's special version of Pyro4:
   `pip install git+https://github.com/delmic/Pyro4.git`

#. Install using `pip install libtiff-0.4.0-cp27-none-win64.whl` (or `-win32`),
   downloaded from http://www.lfd.uci.edu/~gohlke/pythonlibs/#pylibtiff

#. Download the ZIP file of the latest release of Odemis from:
   https://github.com/delmic/odemis/releases

#. Extract the Odemis release into `C:\\Program Files\\Odemis` (or any folder of
   your preference).

#. Create an empty text file `odemis.pth` in the Anaconda Python installation folder:
   `C:\\Users\\YOURUSERNAME\\Anaconda2\\Lib\\site-packages`. Make sure the file does
   *not* have a `.txt` extension. Edit that file and enter the full path to the
   Odemis source code, such as: `C:\\Program Files\\Odemis\\src\\`.

You can now use Python via the "Spyder" interface. To read an acquisition file
you can use code such as:

.. code-block:: python

    from odemis.dataio import hdf5
    das = hdf5.read_data("C:\\Path\\to\\the\\acquistion.h5")
    print das
    print das[0].metadata


Automating the acquisition of data
==================================

There are several ways to automate the data acquisition. There are mostly a
trade-off between simplicity of development and complexity of the task to
automate.

.. only:: html

    For the easiest tasks, a shell script calling the CLI might be the
    most appropriate way. See the CLI help command for a list of all possible
    commands (``odemis-cli --help``). For example, to list all the available hardware
    components::

        $ odemis-cli --list

        SimSPARC	role:sparc
          ↳ ARSimCam	role:ccd
          ↳ SED ExtXY	role:se-detector
          ↳ FakeSpec10	role:spectrometer
            ↳ FakeSP2300i	role:spectrograph
            ↳ SpecSimCam	role:sp-ccd
          ↳ EBeam ExtXY	role:e-beam
          ↳ MirrorMover	role:mirror
     
.. only:: pdf

    For the easiest tasks, a shell script calling the CLI might be the
    most appropriate way. See the CLI help command for a list of all possible
    commands (``odemis-cli --help``). For example, to list all the available hardware
    components::

        $ odemis-cli --list

        SimSPARC	role:sparc
          > ARSimCam	role:ccd
          > SED ExtXY	role:se-detector
          > FakeSpec10	role:spectrometer
            > FakeSP2300i	role:spectrograph
            > SpecSimCam	role:sp-ccd
          > EBeam ExtXY	role:e-beam
          > MirrorMover	role:mirror

To list all the properties of a component::

    $ odemis-cli --list-prop FakeSP2300i
     
    Component 'FakeSP2300i':
        role: spectrograph
        affects: 'SpecSimCam'
        axes (RO Attribute)	 value: frozenset(['wavelength'])
        swVersion (RO Attribute)	 value: v1.1-190-gb5c626b (serial driver: Unknown)
        ranges (RO Attribute)	 value: {'wavelength': (0, 2.4e-06)}
        hwVersion (RO Attribute)	 value: SP-FAKE (s/n: 12345)
        position (RO Vigilant Attribute)	 value: {'wavelength': 0.0} (unit: m)
        speed (RO Vigilant Attribute)	 value: 1e-07 (unit: m/s) (range: 1e-07 → 1e-07)
        grating (Vigilant Attribute)	 value: 2 (choices: 1: '300 g/mm BLZ=  345NM', 2: '600 g/mm BLZ=   89NM', 3: '1200 g/mm BLZ= 700NM')

.. note:
    When the name of a component which contains spaces is given as a 
    parameter, it should be put into quotes, such as ``"EBeam ExtXY"``.

To acquire
5 images sequentially from the secondary electron detector at 5 different 
positions on the sample, you could write this in bash::

    for i in $(seq 5); do odemis-cli --acquire "SED ExtXY" --output etd-pos$i.h5; odemis-cli --move OLStage y -100; done

For more complex tasks, it might be easier to write a specialised python script.
In this case, the program directly accesses the back-end. In addition to reading
this documentation, a good way to start is to look at the source code of the CLI
in ``src/odemis/cli/main.py`` and the python
scripts in ``scripts`` (and ``/usr/share/doc/odemis/scripts``). The most common 
tasks can be found there. For example the following script acquires 10 SEM images
at 10 different dwell times, and save them in one HDF5 file.

.. code-block:: python

    from odemis import model, dataio
    import sys

    filename = sys.argv[1]
    exporter = dataio.find_fittest_converter(filename)

    # find components by their role
    escan = model.getComponent(role="e-beam")
    sed = model.getComponent(role="se-detector")

    data = []
    for i in range(1, 11): # 10 acquisitions
        escan.dwellTime.value = i * 1e-6 # i µs
        img = sed.data.get()
        data.append(img)
        
    exporter.export(filename, data)

Alternatively you may want to add the automated task as one option to the GUI.
See later section about extending the GUI.


Fixing a bug
============

Like every complex piece of software, Odemis contains bugs, even though we do
our best to minimize their amount. In the event you are facing a bug, we advise
you first to report it to us (bugreport@delmic.com). We might have already solved it
or might be able to fix it for you. If neither of these two options work out,
you can try to fix it yourself. When reporting a bug, please include a
description of what is happening compared to what you expect to happen, the log
files and screen-shots if relevant.

If you try to solve a bug by yourself, the first step is to locate the bug. 
Have a look at the log files:

* ``/var/log/odemis.log`` contains the logs of the back-end (odemisd)
* ``~/odemis-gui.log`` contains the logs of the GUI (odemis-gui)

It is also possible to run each part of Odemis independently. To get the maximum
information, add ``--log-level=2`` as a start-up parameter of any of the Odemis 
parts. By running a part from Eclipse, it's possible to use the visual debugger
to observe the internal state of the python processes and place breakpoints.
In order to avoid the container separation in the back-end, which prevents 
debugging of the drivers, launch with the ``--debug`` parameter.

Once the bug fixed, commit your code using ``git add ...`` and ``git commit -a``.
Export the patch with ``git format-patch -1`` and send it to us 
(bugreport@delmic.com) for inclusion in the next version of Odemis.


Supporting new hardware
=======================

In order to support a new hardware, you need to create a new device adapter (also
called *driver*). High chances is that your device directly falls into one of these
categories:

* Emitter: generates energy (to influence the sample)
* Detector: observes energy (from the sample)
* Actuator: moves physically something

To create a new device adapter, add a python module to the ``src/odemis/drivers/``
directory following the interface for the specific type of component (see the
back-end API in chapter _`Back-end Application Programming Interface`).

Add a test class to the test directory which instantiates the component and at
least detects whether the component is connected or not (``scan()`` and ``selfTest()``
methods) and does basic tasks (e.g., acquiring an image or moving an actuator).

Update the microscope configuration file for instantiating the microscope with the
parameters for your new driver.

Do not forget to commit your code using ``git add ...`` and ``git commit -a``.
Optionally, send your extension to Delmic as a git patch or a github merge request.

Adding a feature to the Graphical User Interface
================================================

There are two ways to extend the Graphical User Interface (GUI). The first and
easiest way is to develop a 'plugin'. 
See the chapter _`Graphical User Interface Plugins` for a detailed description.
At start-up, Odemis GUI will load all the plugins available on the computer.
The main drawbacks is that for very
advanced or integrated functionality, it might be harder to develop and debug
the code than modifying directly the GUI code. Plugins are also not distributed
in standard, so it's not the right way to improve the default Odemis. 

The second way to extend the GUI, is to modify the original code in ``src/odemis/gui``.
Note that it is recommended to be quite familiar with Odemis' code and concepts
before tackling such a task. In particular, there is no API for extending the
interface, and therefore you'll most likely need to modify the code in many
different files. Also, as the GUI relies on the wxPython and cairo libraries to
display widgets, it is also recommended to have a basic knowledge of these
libraries.

To edit the interface, you should use XRCed.
Launch it by typing this (from ``~/development/odemis``)::

    PYTHONPATH=./src/ ./util/launch_xrced.py src/odemis/gui/main.xrc

When saving the file, main_xrc.py will automatically be updated too.

If you add/modify an image (used as a GUI element, not a microscope acquisition), 
it should be done in ``src/odemis/gui/img``. After the modifications, you should
make sure the images are optimised, with the following script::

    ./util/groom-img.py

If you modify the application main icons in ``image/icon_gui*.png``, you need to call::

    ./util/generate_icons.sh

If you need to see more log messages of the GUI while it is running, it's possible
to increase the log level. To do so, select Help/Development/Inspect GUI.
In console panel (PyCrust) of the inspection window, type:

.. code-block:: python

    import logging
    logging.getLogger()
    l.setLevel(logging.DEBUG)

From now on, all log messages are displayed and recorded in the log file.

Speed optimization
==================
To speed up the code, first, you need to profile the code to see where is the 
bottleneck. One option is to use the cProfile.
This allows to run the cProfile on the GUI::

    PYTHONPATH=./src/ python -m cProfile -o odemis.profile src/odemis/gui/main.py
    
Then use the features you want to measure/optimize, and eventually close the GUI.

After the program is closed, you can read the profile with the following commands::

    python -m pstats odemis.profile
    > sort time
    > stats

Another option for line-by-line profiling is the line_profiler. To use it, you need 
to install the python package via pip::

    pip install line_profiler
    
Then you have to add the @profile decorator to the functions that you want to profile 
(importing the corresponding package is not needed). With the below line you will get
detailed profile statistics for the decorated functions within your module::

    kernprof.py -l -v your_module.py


Memory optimization
===================
The main thing to look at is memory leaks. That is to say, data which is not used
anymore but still hold in memory. In Python, there are mostly three reasons for
data to stay in memory while not used anymore:

* Some object still in use has a reference to the data. For example, if a
  temporary result is hold as an attribute ``self._temp``, that object will not be
  dereferenced until self is unreferenced, or ``self._temp`` is replaced.
* Some objects have cyclic dependencies, and one of them has a ``__del__`` method.
  Python 2 is not able to garbage collect any of these objects.
* A C library has not free'd some data.
 

Only a few memory profilers are able to detect C library memory leakage. One of
them is ``memory_profile``. You can install with::

   sudo easy_install -U memory_profiler

In order to find the leaks, it's possible to then add a decorator ``@profile`` 
to the suspect methods, and then run::
 
   python -m memory_profiler program.py

It will list line-per-line the change of memory usage.

Another possibility is to use ``pympler``, which allows to list the biggest objects
that were recently created. You can add in your program, or in the Python console
of the Odemis GUI:

.. code-block:: python

   from pympler import tracker
   tr = tracker.SummaryTracker()

   # After every interesting call
   tr.print_diff()

As it will not detect C library memory allocations, if no new large object has
appeared and the Python process uses more memory, then it's likely a C library
memory leak.

