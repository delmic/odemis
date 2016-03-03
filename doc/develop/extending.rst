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

 * `Ubuntu <http://www.ubuntu.com>`_ 12.04 (x86 32 or 64 bits)
 * Eclipse + PyDev plug-in + Pylint
 * XRCed (package python-wxtools) for GUI edition

The source code available via a public git repository: https://github.com/delmic/odemis.
To *clone* it, type::

   git clone https://github.com/delmic/odemis.git

Note that Odemis can run in fully simulated mode, where no actual hardware is
needed. In this case, it can run in a virtual machine.

Detailed instructions
---------------------

Download Ubuntu 12.04 at this address:
http://www.ubuntu.com/download/desktop/thank-you?release=lts&bits=64&distro=desktop&status=zeroc

Install it by which ever way you prefer, following these instructions:
http://www.ubuntu.com/download/desktop/install-desktop-long-term-support

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
    wget http://download.eclipse.org/technology/epp/downloads/release/luna/R/eclipse-standard-luna-R-linux-gtk-x86_64.tar.gz
    tar xf eclipse-standard-luna-R-linux-gtk-x86_64.tar.gz
    ~/usr/eclipse/eclipse

Go to *Help/Marketplace...*. Search for PyDev, and install it.
Optionally, you can also install *Eclipse Color Theme* and *hunspell4eclipse*.

In the Eclipse preference window, go to *PyDev/PyLint* and as location of the 
pylint executable, indicate your lint.py, which is approximately at this place:
``/usr/local/lib/python2.7/dist-packages/pylint-1.0.0-py2.7.egg/pylint/lint.py``

Optionally, if you want to edit the microscope configuration files (``*.odm.yaml``),
add a file association with the Python editor. For this, in the preference 
window, go to *General/Editors/File Association* and add a file type "``*.yaml``". As
default editor, add the Python editor.

Edit Odemis with Eclipse
"""""""""""""""""""""""""

1. Click on *File/New/PyDev Project*.

2. Enter "odemis" as project name

3. Select a directory for project contents: the place where Odemis was downloaded (i.e., ``/home/.../development/odemis``)

4. Select "Create 'src' folder and add it to the PYTHONPATH"

5. Click on Finish

Learning Python
"""""""""""""""
Allmost all Odemis is written in Python. If you are not familliar with this
programming language, it is recommended you first have a look at a tutorial.
For instance, read 
`A Crash Course in Python for Scientists <http://nbviewer.ipython.org/gist/rpmuller/5920182>`_.

Using Git
"""""""""

Source code version control is managed with git. If you are not familiar with 
this tool, it is better to first learning its basics before going further. Refer
to tutorials such as `Pro Git <http://git-scm.com/book>`_ or
`Easy Version Control with Git <http://code.tutsplus.com/tutorials/easy-version-control-with-git--net-7449>`_.

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

Supporting new hardware
=======================

In order to support a new hardware, you need to create a new device adapter (also
called *driver*). High chances is that your device directly falls into one of these
categories:

* Emitter: generates energy (to influence the sample)
* Detector: observes energy (from the sample)
* Actuator: moves physically something

To create a new device adapter, add a python module to the ``src/odemis/drivers/``
directory following the interface for the specific type of component (see the back-end API).

Add a test class to the test directory which instantiates the component and at
least detects whether the component is connected or not (``scan()`` and ``selfTest()``
methods) and does basic tasks (e.g., acquiring an image or moving an actuator).

Update the microscope configuration file for instantiating the microscope with the
parameters for your new driver.

Do not forget to commit your code using ``git add ...`` and ``git commit -a``.
Optionally, send your extension to Delmic as a git patch or a github merge request.

Adding a feature to the Graphical User Interface
================================================

Note that it's not recommended to modify the GUI before you are already quite
familiar with Odemis' code. In particular, there is no API for extending the
interface, and therefore you'll most likely need to modify the code in many
different files.

To edit the interface, you should use XRCed.
Launch it by typing this (from ``~/development/odemis``)::

    PYTHONPATH=./src/ ./util/launch_xrced.py src/odemis/gui/main.xrc

When saving the file, main_xrc.py will automatically be updated too.

If you add/modify an image (used as a GUI element, not a microscope acquisition), 
it should be done in ``src/odemis/gui/img``. After the modifications, you need to 
regenerate the ``data.py`` file::

    ./src/odemis/gui/img/img2python.py

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
===================
First, you need to profile the code to see where is the bottleneck. One option is 
to use the cProfile. This allows to run the cProfile on the GUI::

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
