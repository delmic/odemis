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

 * `Ubuntu <http://www.ubuntu.com>`_ 18.04 (x86 64 bits)
 * `PyCharm <https://www.jetbrains.com/pycharm/>`_

The source code available via a public git repository: https://github.com/delmic/odemis.
To *clone* it, type::

    git clone https://github.com/delmic/odemis.git

Note that Odemis can run in fully simulated mode, where no actual hardware is
needed. In this case, it can run in a virtual machine.

Odemis can also run partially (ie, the data manipulation part) on Windows. See
the next section to install Odemis on this operating system.

Detailed instructions
---------------------

Download Ubuntu 18.04 at this address:
https://ubuntu.com/download/desktop/thank-you?version=18.04.6&architecture=amd64

Install it by which ever way you prefer, following these instructions:
http://www.ubuntu.com/download/desktop/install-ubuntu-desktop

Once logged into your newly installed Ubuntu system, do the following steps.

Install the additional required packages
""""""""""""""""""""""""""""""""""""""""
Start a terminal (with Ctrl+Alt+T) and type::
 
    sudo add-apt-repository ppa:delmic-soft/odemis
    sudo apt-get update
    sudo apt-get dist-upgrade
    sudo apt-get install odemis git vim meld libtiff-tools gimp \
     fluodb imagej inkscape pngcrush
    sudo apt-get build-dep odemis
    sudo adduser $(whoami) odemis
    mkdir development
    cd development
    git clone https://github.com/delmic/odemis.git
    cd odemis
    python3 setup.py build_ext --inplace

Note that all dependencies should have been picked-up. Afterwards, it should
normally not be needed to install any package via PIP.

Configure Odemis for development
""""""""""""""""""""""""""""""""
Edit /etc/odemis.conf with::

    sudo gedit /etc/odemis.conf

Modify the first lines so they read like this::

    DEVPATH="$HOME/development"
    PYTHONPATH="$DEVPATH/odemis/src/:$PYTHONPATH"

And edit the MODEL line for the model you want (probably a simulated microscope
like ``sparc2-sim`` or ``secom-sim``). For example::

    MODEL="$CONFIGPATH/sim/sparc2-sim.odm.yaml"
    
For some simulated microscopes, you need to set-up the simulated
acquisition board of the SEM. To automate it at computer start-up, create a
``/etc/rc.local`` (if not already existing) using ``sudo gedit /etc/rc.local``
and type the following::

    #!/bin/sh

    modprobe comedi comedi_num_legacy_minors=4
    modprobe comedi_test
    comedi_config /dev/comedi0 comedi_test 1000000,1000000
    comedi_config /dev/comedi1 comedi_test 100000,100000

Finally, make it executable with ``sudo chmod a+x /etc/rc.local``. You can run
it immediately by typing ``sudo /etc/rc.local``.

Install PyCharm
"""""""""""""""
`PyCharm <https://www.jetbrains.com/pycharm/>`_ is a good editor for Python code.
Install it with::

   sudo snap install pycharm-community --classic

In PyCharm, open the ``odemis`` directory.
In the project settings, change the Python interpreter to the
*system* interpreter (select Python 3.6).

On Ubuntu 18.04, PyCharm will report that some dependencies are not satisfied (eg, wxPython),
however, if you have already installed Odemis from the Ubuntu packages, this is
fine, and you should not install new version via PyCharm.

Install Eclipse and the plugins
"""""""""""""""""""""""""""""""
Alternatively, instead of PyCharm, you may prefer Eclipse.
It has some more advanced debugging functionalities, but everything else is a
little less polished than PyCharm.

Go to the
`Eclipse website <https://www.eclipse.org/downloads/>`_ to download the installer, uncompress it and run it.

Go to *Help/Eclipse Marketplace...*. Search for *PyDev*, and install it.
Optionally, you can also install the *ReST Editor*.

Optionally, if you want to edit the microscope configuration files (``*.odm.yaml``),
add a file association with the Python editor. For this, in the preference 
window, go to *General/Editors/File Association* and add a file type "``*.yaml``". As
default editor, add the Python editor.

Edit Odemis with Eclipse
""""""""""""""""""""""""

#. Click on *File/New/PyDev Project*.
#. Enter "odemis" as project name
#. Select a directory for project contents: the place where Odemis was downloaded (i.e., ``/home/.../development/odemis``)
#. Select "Create 'src' folder and add it to the PYTHONPATH"
#. Click on Finish

Learning Python
"""""""""""""""
Almost all Odemis is written in Python. If you are not familiar with this
programming language, it is recommended you first have a look at a tutorial.
For instance, read 
`A Crash Course in Python for Scientists <https://nbviewer.jupyter.org/gist/rpmuller/5920182>`_.

Using Git
"""""""""

Source code version control is managed with git. If you are not familiar with 
this tool, it is better to first learning its basics before going further. Refer
to tutorials such as `Pro Git <http://git-scm.com/book>`_ or
`Easy Version Control with Git <http://code.tutsplus.com/tutorials/easy-version-control-with-git--net-7449>`_.


Setting up the development environment on Windows
=================================================

This section describes how to get the development version Odemis GUI working on
Windows, so it can be used as an image viewer. It will also explain how to
create an installer for easy distribution.

Getting the Odemis source code
------------------------------
Install `git for windows <https://gitforwindows.org/>`_.
The source code is available via a public git repository: https://github.com/delmic/odemis.
Open the folder where you want to download the source code (eg, Documents),
right-click and select *Git Bash here*. Then type::

    git clone https://github.com/delmic/odemis.git

Creating the Odemis environment
-------------------------------

Install `Miniconda <https://docs.conda.io/en/latest/miniconda.html>`_ and
pick the right architecture for your computer (most likely 64-bit).
Odemis is validated on Python 3.6, but Anaconda typically comes with a newer version of Python (eg, 3.9)
The following instructions use the fact that Anaconda supports multiple versions of Python 
simultaneously to install Python 3.6.

Setup
"""""
Open the *Anaconda prompt* and type::

   cd Documents\odemis
   conda create -y --name odemisdev python==3.6.15
   conda activate odemisdev
   conda config --append channels conda-forge
   conda install --name odemisdev --file requirements-conda.txt
   echo %cd%\src\ > %userprofile%\miniconda3\envs\odemisdev\lib\site-packages\odemis.pth
   pip install https://github.com/delmic/Pyro4/archive/master.zip

..
   Note: `conda develop src` is nicer than `echo ...`, but it needs conda-build to
   be installed, which requires a lot more dependencies. On miniconda, it's not
   installed by default.

Download, install `Build Tools for Visual Studio 2019 <https://www.visualstudio.com/downloads/#build-tools-for-visual-studio-2019>`_,
and pick the "Visual C++ build tools". Install also the pre-selected options.

..
   Note that previously, one had to install pylibtiff via pip: `pip install libtiff`

Final steps
"""""""""""
Some parts of Odemis are written with Cython, for optimization reasons. This step
is optional. To build these modules on Windows run::

   python setup.py build_ext --inplace

Launching Odemis Viewer
"""""""""""""""""""""""
Run Odemis with::

   python src\odemis\gui\main.py --standalone --log-level 2
   # or
   python install\windows\odemis_viewer.py


Installing arpolarimetry
""""""""""""""""""""""""

The ``arpolarimetry`` library is internal to Delmic and provides some supplementary polarized AR projections. Everything else will work fine without it, so for a regular Windows installation which does not require this functionality, this is not necessary.
If you have access to the Delmic Bitbucket repository, do the following::

   cd ..
   git clone https://<YOUR_NAME>@bitbucket.org/delmic/arpolarimetry.git
   cd arpolarimetry
   python setup.py install


Building Odemis Viewer and the installer
----------------------------------------

Install `NSIS <https://nsis.sourceforge.io/Download>`_.

Download the latest version of the `KillProc NSIS plugin <http://nsis.sourceforge.net/KillProc_plug-in>`_.
Unzip it, and place the ``KillProc.dll`` in ``C:\\Program Files (x86)\\NSIS\\Plugins\\x86-unicode\``.

Open the *Anaconda prompt* and make sure you are in the Odemis folder,
with the *odemisdev* Python environment::

   cd Documents\odemis
   conda activate odemisdev

To build just the viewer executable::

   pyinstaller -y install\windows\viewer.spec

To build the installer::

   "C:\Program Files (x86)\NSIS\makensis" install\windows\setup.nsi

..
   Note: it actually needs extra /D options to work. see the build.py

As a shortcut to build everything::

   python install\windows\build.py

Note that this script also allows to sign the executable. For this, you'll need
a special signing dongle, with a signing certificate.


Setting up a data analysis environment on Windows
=================================================

For users which don't want to actually modify Odemis, but only rely on it as a
Python module for data analysis, it's possible to set-up an environment in a
relatively straight-forward way.


Installing Odemis Viewer
------------------------

This is an optional step, which allows you to open and analyze acquisitions files
straight into the same graphical interface as the acquisition software.

Download the Odemis viewer from https://www.delmic.com/en/products/clem-solutions/secom. 
In case your browser warns you about potential thread, confirm you are willing to download
the file. Then run the executable, and Odemis viewer will be available as a
standard software.


Installing Python environment
-----------------------------

This allows you to manipulate the data in Python, either by writing Python
scripts, or via a command-line interface.

Install `Anaconda <https://www.anaconda.com/distribution/>`_ with Python 3.x and pick the right
architecture for your computer (most likely 64-bit). Note that the following instructions are valid
for conda version 4.9.2. In case you have an older version make sure that you update the conda version
through the Anaconda prompt before proceeding to the next steps.

Anaconda typically comes with a newer version of Python (eg, 3.8). It's crucial to create a new environment
with the python version (3.6.15) on which Odemis has been well tested.

#. To create a new conda environment, named odemis, open the Anaconda command prompt and type:
   ``conda create -n odemis python=3.6.15``.
   
#. Note that the new 'odemis' environment should be activated in order to do the
   following steps. To activate the environment type ``conda activate odemis``.
   Note that you will have to re-execute this command every time the Anaconda command-line is started.
   Once it's activated, the environment name is shown in the command line of
   Anaconda command prompt. It looks like:
   ``(odemis) C:\Users\...``.

#. Download the ZIP file of the latest release of Odemis from:
   https://github.com/delmic/odemis/releases.
      
#. Extract the Odemis release into the folder Documents (or any folder of
   your preference). The folder is named with the release number of Odemis.
   You can rename the extracted folder 'odemis-release_version' to 'Odemis'.
    
#. Open the anaconda prompt and make sure you are in the Odemis folder, with the
   'odemis' python environment activated::
   
    cd Documents\Odemis
    conda activate odemis
    conda config --append channels conda-forge
    conda install --name odemis --file requirements-conda.txt
    conda develop src   
   
#. To use this new conda python environment follow the instructions of 
   http://medium.com/@apremgeorge/using-conda-python-environments-with-spyder-ide-and-jupyter-notebooks-in-windows-4e0a905aaac5.
   Note that:
      
   #. to set the conda environment for jupyter install the kernel inside the 'odemis"
      environment by typing ``conda install -c anaconda ipykernel``.
      To use jupyter notebook, first install it by typing ``pip install jupyter notebook``.

   #. to set the conda environment for Spyder install the spyder kernel inside the environment 
      and type ``conda install "spyder-kernels<1.10.0"``.
    
#. Install Delmic's special version of Pyro4, by typing in the Anaconda command
   prompt the following command:
   ``pip install https://github.com/delmic/Pyro4/archive/master.zip``.

#. Download bitarray‑1.6.1‑cp36‑cp36m‑win_amd64.whl (or ‑win32.whl) from http://www.lfd.uci.edu/~gohlke/pythonlibs/#bitarray ,
   and pylibtiff‑0.4.2‑cp36‑cp36m‑win_amd64.whl (or -win32.whl) from http://www.lfd.uci.edu/~gohlke/pythonlibs/#pylibtiff.
   and install them with a single command ``pip install bitarray‑1.6.1‑cp36‑cp36m‑win_amd64.whl pylibtiff‑0.4.2‑cp36‑cp36m‑win_amd64.whl``
   (or ``pip install bitarray‑1.6.1‑cp36‑cp36m‑win32.whl pylibtiff‑0.4.2‑cp36‑cp36m‑win32.whl`` based on your system type).
   
You can now use Python via the "Spyder" interface or the "Jupyter" notebook.
Note that you may need to navigate to the Documents/Odemis/src folder to be able to import from odemis.

To read an acquisition file you can use code such as:

.. code-block:: python

    from odemis.dataio import hdf5
    das = hdf5.read_data(u"C:\\Path\\to\\the\\acquistion.h5")
    print das
    print das[0].metadata


Starting odemis from the terminal/console
=========================================

After setting up the development environment it is possible to start odemis via the terminal.
It is also possible to specify a specific configuration (``*.yaml``) file used for staring odemis.


Starting Odemis
---------------

Odemis can be started from the terminal by typing the following command in the terminal::

    odemis-start

The default microscope file (``*.yaml``) is defined in the configuration file, which can be found and changed in
``/etc/odemis.conf``.

Starting Odemis with configuration file
---------------------------------------

Odemis can be started using different hardware microscope files (``*.yaml``).
There are various examples, hardware tests and simulators available in
``~/development/odemis/install/linux/usr/share/odemis/``.

Launch Odemis with a microscope file by typing the following command in the terminal::

    odemis-start ~/development/odemis/install/linux/usr/share/odemis/sim/sparc2-sim.odm.yaml


Starting Odemis with no GUI
---------------------------

The Odemis backend can be started without launching the GUI by using the following command::

    odemis-start --nogui


Starting the Odemis-Viewer
--------------------------

The Odemis Viewer runs without a microscope file specified and is a useful tool to load and perform some basic
analysis on previously acquired data sets. The Odemis viewer can be started by using the following command::

    odemis-gui --standalone


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
        grating (Vigilant Attribute)	 value: 2 (choices: 1: '300 g/mm BLZ=  345NM',
                                    2: '600 g/mm BLZ=   89NM', 3: '1200 g/mm BLZ= 700NM')

.. note:
    When the name of a component which contains spaces is given as a 
    parameter, it should be put into quotes, such as ``"EBeam ExtXY"``.

To acquire
5 images sequentially from the secondary electron detector at 5 different 
positions on the sample, you could write this in bash:

.. code-block:: bash

    for i in $(seq 5); do
        odemis-cli --acquire "SED ExtXY" --output etd-pos$i.h5
        odemis-cli --move OLStage y -100
    done


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
least detects whether the component is connected or not
and does basic tasks (e.g., acquiring an image or moving an actuator).

Update the microscope configuration file for instantiating the microscope with the
parameters for your new driver.

Do not forget to commit your code using ``git add ...`` and ``git commit -a``.
Optionally, send your extension to Delmic as a git patch or a github pull request.

Sometimes, on Linux, a driver needs to be associated to a udev rule. udev only
reloads the list of rules at boot time. So, when changing the rules, you can
force it to reload them with::

    sudo udevadm control --reload-rules

When developing a device adapter, it's normally possible to use the module independently
from the rest of Odemis to test and debug it. However, eventually the module will be used
by the back-end, as defined within a microscope file. If during this phase some
issue arises, it is possible to observe the entire back-end with a debugger, but
it is not straightforwards. Normally the back-end is run as root, and executes
every adapter in a separate process. This makes the debugging quite complicated.
If you need to execute the back-end in a debugger, you can disable the multi-process
safety, and run it as a normal user with a command looking like this::

    python3 src/odemis/odemisd/main.py --debug --log-level 2 path/to/microscope.odm.yaml




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

To edit the interface, you could use XRCed (but it only works with wxPython3).
Launch it by typing this (from ``~/development/odemis``)::

    PYTHONPATH=./src/ ./util/launch_xrced.py src/odemis/gui/main.xrc

When saving the file, main_xrc.py will automatically be updated too.
Alternatively, you can just regenerate the ``.py`` file from a ``.xrc`` file with
this command::

    ./util/generate_xrc.py src/odemis/gui/main.xrc

Note that on Ubuntu 18.04 this call does not work with python3, therefore you
will need to explicitly call this with python2::

    python2 ./util/generate_xrc.py src/odemis/gui/main.xrc


If you add/modify an image (used as a GUI element, not a microscope acquisition),
it should be done in ``src/odemis/gui/img``. After the modifications, you should
make sure the images are optimised, with the following script::

    ./util/groom-img.py

If you modify the application main icons in ``image/icon_gui*.png``, you need to call::

    ./util/generate_icons.sh

To start the GUI directly as a python module, for example to run it in a debugger,
you can run it this way::

    python3 -m odemis.gui.main --log-level 2 --log-target $HOME/odemis-gui.log

To start the GUI just in viewer mode::

    python3 -m odemis.gui.main --standalone --log-level 2 --log-target $HOME/odemis-gui.log


If you need to see more log messages of the GUI while it is running, it's possible
to increase the log level. To do so, select Help/Development/Inspect GUI.
In console panel (PyCrust) of the inspection window, type:

.. code-block:: python

    import logging
    logging.getLogger().setLevel(logging.DEBUG)

From now on, all log messages are displayed and recorded in the log file.

In the same way, if you need to test some python code inside the GUI, you can
access the main objects of the GUI via commands like this:

.. code-block:: python

    import wx
    app = wx.GetApp()
    main_data = app.main_data  # the main GUI data model
    ta = main_data.getTabByName("analysis")  # the tab controller
    ta.tab_data_model.streams.value  # the tab data model and the streams


An other important detail to take into account when modifying the GUI is that
the wxPython framework has a limitation: any change to the GUI widgets must
done from within the main thread. Not respecting this can result in some
random crashes of the GUI without any backtrace. This can happen for instance
in a callback for a VigilantAttribute or DataFlow. To avoid such issue, there
are two simple ways. The simplest way is to decorate the function with the special
``@call_in_wx_main`` decorator. This decorator ensures that the function is
always run from within the main GUI thread. Another way is to call every GUI
related function using the special ``wx.CallAfter()`` function.


Unit testing
============
The source code comes with a large set of unit tests and some integration tests.
They allow checking the behaviour of the different parts of Odemis.
After changes are made to the source, the tests should be rerun in order to validate
these changes.


Running test cases
------------------

To run the test cases, it is recommended to first create an
empty directory next to the odemis directory, and name it ``odemis-testing``.
Optionally, you may also have another directory ``mic-odm-yaml``, which contains
extra microscopes files to be used during integration testing (the file names
should end with ``-sim.odm.yaml``).
It is then possible to run all the test cases by running from the ``odemis-testing``
directory this command::

    ../odemis/util/runtests.sh 2>&1 | tee test-$(date +%Y%m%d).log

The summary of the test results will be stored in ``test-DATE.log``, and the
complete log will be stored in separate files.

Please note that before running the test cases, you might need to run once
``odemis-start`` in order to set-up some directories with the correct access
rights. Also, running all the test cases may take up to a couple of hours, during
which windows will pop-up and automatically close from time to time.

It is also possible to run a single test at a time, by executing the test file.
It's possible to indicate as argument the specific test case and even function to
run.
Note that by default the test cases for drivers attempt to use the real
hardware by default. To force the use of a simulator (if available), the
environment variable TEST_NOHW to 1.
The simplest to do all of it from the command line is to write such as::

    TEST_NOHW=1 python3 src/odemis/driver/test/static_test.py --verbose

Adding test cases
-----------------
Test cases go into separate files located in a subdirectory ``test``. Each
filename must end with ``_test.py``. They use the unittest Python framework.

In the test, assertion functions can be used. In addition to the
`standard ones <https://docs.python.org/3/library/unittest.html#test-cases>`_,
numpy provides some useful functions in
`numpy.testing <https://numpy.org/doc/stable/reference/routines.testing.html>`_,
and some extra functions are available in ``odemis.util.test``.


Speed optimization
==================
To speed up the code, first, you need to profile the code to see where is the 
bottleneck. One option is to use the cProfile.
This allows to run the cProfile on the GUI::

    PYTHONPATH=./src/ python3 -m cProfile -o odemis.profile src/odemis/gui/main.py
    
Then use the features you want to measure/optimize, and eventually close the GUI.

After the program is closed, you can read the profile with the following commands::

    python3 -m pstats odemis.profile
    > sort time
    > stats

Another option for line-by-line profiling is the line_profiler. To use it, you need 
to install the python package via pip::

    pip install line_profiler
    
Then you have to add the @profile decorator to the functions that you want to profile 
(importing the corresponding package is not needed). With the below line you will get
detailed profile statistics for the decorated functions within your module::

    kernprof.py -l -v your_module.py


It is also possible to write your own runtime tracker:

.. code-block:: python

    import time

    def timeit(method):

        def timed(*args, **kw):
            ts = time.time()
            result = method(*args, **kw)
            te = time.time()

            print '%r (%r, %r) %2.2f sec' % \
                  (method.__name__, args, kw, te-ts)
            return result

        return timed

    @timeit
    def yourFunctionToTrack():
        do something


Memory optimization
===================
The main thing to look at is memory leaks. That is to say, data which is not used
anymore but still hold in memory. In Python, there are mostly three reasons for
data to stay in memory while not used anymore:

* Some object still in use has a reference to the data. For example, if a
  temporary result is hold as an attribute ``self._temp``, that object will not be
  de-referenced until self is unreferenced, or ``self._temp`` is replaced.
* Some objects have cyclic dependencies, and one of them has a ``__del__`` method.
  Python 2 is not able to garbage collect any of these objects.
* A ``C`` library has not free'd some data.
 

Only a few memory profilers are able to detect ``C`` library memory leakage. One of
them is ``memory_profiler``. You can install it with::

    sudo easy_install -U memory_profiler

or if you have installed the pip package::

    pip install memory_profiler --user

In order to find the leaks, it's possible to add a decorator ``@profile``
to the suspect methods/functions, and then run::

    python3 -m memory_profiler program.py

It will list line-per-line the change of memory usage after closing the GUI.
.. TODO: the memory usage listed in terminal of viewer is not line-by-line and displays something weired..

It is also possible to add an import statement in the module where you want to track a function and decorate the
function with the decorator ``@profile``. The advantage is that the line-by-line memory usage is displayed in
the terminal of the Odemis GUI and you don't need to close the GUI. Thus, it is possible to check the same
function multiple times with different e.g. input images:

.. code-block:: python

    from memory_profiler import profile

    @profile
    def yourFunctionToTrack():
        do something

You may also want to combine tracking of memory and time. You can do this by combining the following two decorators
(be aware of the order of the decorators!):

.. code-block:: python

    from memory_profiler import profile
    import time

    def timeit(method):

        def timed(*args, **kw):
            ts = time.time()
            result = method(*args, **kw)
            te = time.time()

            print '%r (%r, %r) %2.2f sec' % \
                  (method.__name__, args, kw, te-ts)
            return result

        return timed

    @timeit
    @profile
    def yourFunctionToTrack():
        do something

Another option to track the memory usage is the cProfile package::

    python3 -m cProfile -s cumtime program.py

It will display the overall used memory per function, the number of calls per function and many more
quantities regarding memory usage. However, you need to close the GUI before the statistics are displayed
within the terminal. This tool might be useful to analyze the overall performance of the GUI.
.. TODO how use with import cProfile statement - did not find a decorator...

If you use the editor ``PyCharm`` you can pass the following arguments in the interpreter options
(depending on which profiler you may choose)::

    Run --> Edit Configurations --> Interpreter options : -m cProfile -s cumtime

or::

    Run --> Edit Configurations --> Interpreter options : -m memory_profiler

If you add the memory_profiler option, you don't need the import statement but the decorator as explained before.
Both options display the used memory after closing the GUI.


Another possibility is to use ``pympler``, which allows to list the biggest objects
that were recently created. You can add in your program, or in the Python console
of the Odemis GUI:

.. code-block:: python

    from pympler import tracker
    tr = tracker.SummaryTracker()

    # After every interesting call
    tr.print_diff()

As it will not detect ``C`` library memory allocations, if no new large object has
appeared and the Python process uses more memory, then it's likely a C library
memory leak.

To test numpy arrays for memory usage, it is possible to call::

    numpy.ndarray.nbytes

It displays the total bytes consumed by the elements of the array.
It does not include memory consumed by non-element attributes of the array object.

A similar and more generic way is to use the sys function to check on the memory allocated to your
object of interest::

    sys.getsizeof(yourObject)

It returns the size of an object in bytes. The object can be any type of object.
All built-in objects will return correct results, but this does not have to hold true for
third-party extensions as it is implementation specific.
Only the memory consumption directly attributed to the object is accounted for,
not the memory consumption of objects it refers to.
In other words, for objects created via a ``C`` library, the reported size might be correct,
or might be underestimated.
