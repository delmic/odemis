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

 * `Ubuntu <http://www.ubuntu.com>`_ 12.04 (x86 32 bits, preferably with kernel 3.5 aka linux-generic-lts-quantal)
 * Eclipse + PyDev plug-in + Pylint
 * XRCed (package python-wxtools) for GUI edition

The source code available via a public git repository: https://github.com/delmic/odemis.
To *clone* it, type::

   git clone git@github.com:delmic/odemis.git

Note that Odemis can run in fully simulated mode, where no actual hardware is
needed. In this case, it can run in a virtual machine.

Detailed instructions
---------------------

Download Ubuntu 12.04 at this address:
http://www.ubuntu.com/download/desktop/thank-you?release=lts&bits=32&distro=desktop&status=zeroc

Install it by which ever way you prefer, following these instructions:
http://www.ubuntu.com/download/desktop/install-desktop-long-term-support

Once logged into your newly installed Ubuntu system, do the following steps.

Install the additional required packages
""""""""""""""""""""""""""""""""""""""""
Start a terminal (with Ctrl+Alt+T) and type::
 
    sudo add-apt-repository ppa:delmic-soft/odemis
    sudo apt-get update
    sudo apt-get dist-upgrade
    sudo apt-get install git imagej vim hdfview meld libtiff-tools gimp libhdf5-serial-1.8.4 python-pyro4-delmic odemis fluodb python-wxtools python-setuptools python-sphinx inkscape dia-gnome texlive
    sudo adduser $(whoami) odemis
    mkdir development
    cd development
    git clone git@github.com:delmic/odemis.git

Configure Odemis for development
""""""""""""""""""""""""""""""""
Edit /etc/odemis.conf with::

    sudo gedit /etc/odemis.conf

Modify the first lines so they read like this::

    DEVPATH="$HOME/development"
    PYTHONPATH="$DEVPATH/odemis/src/:$PYTHONPATH"

And edit the MODEL line for the model you want (probably a simulated microscope
like sparc-sim or secom-sim). For example::

    MODEL="$CONFIGPATH/sparc-sim.odm.yaml"

Install Eclipse and the plugins
"""""""""""""""""""""""""""""""
Type the folowing commands::

    sudo easy_install pylint
    cd
    mkdir usr
    cd usr
    wget http://www.eclipse.org/downloads/download.php?file=/technology/epp/downloads/release/kepler/SR1/eclipse-standard-kepler-SR1-linux-gtk.tar.gz
    tar xf eclipse-standard-kepler-SR1-linux-gtk.tar.gz
    ~/usr/eclipse/eclipse
 
Go to *Help/Marketplace...*. Search for PyDev, and install it.
Optionally, you can also install *Eclipse Color Theme* and *hunspell4eclipse*.
 
In the Eclipse preference window, go to PyDev/PyLint and as location of the 
pylint executable, indicate your lint.py, which is approximately at this place:
``/usr/local/lib/python2.7/dist-packages/pylint-1.0.0-py2.7.egg/pylint/lint.py``

Edit Odemis with Eclipse
"""""""""""""""""""""""""

1. Click on *File/New/PyDev Project*.

2. Enter "odemis" as project name

3. Select a directory for project contents: the place where Odemis was downloaded (i.e., ``/home/.../development/odemis``)
 
4. Select "Create 'src' folder and add it to the PYTHONPATH"

5. Click on Finish

Using Git
"""""""""

Source code version control is managed with git. If you are not familliar with 
this tool, it is better to first learning its basics before going further. Refer
to tutorials such as ``Pro Git <http://git-scm.com/book>``_ or
``Easy Version Control with Git <http://net.tutsplus.com/tutorials/other/easy-version-control-with-git/>``_.

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
information, add ``--log-level=2`` as a start-up parameter of any of the odemis 
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

For the easiest tasks, a shell script calling the cli program might be the
most appropriate way. See the CLI help command for a list of all possible
commands ("odemis-cli --help").

For more complex tasks, it might be easier to write a specialised python program.
In this case, the program directly access the backend. A good way to start is to
look at the source code of the CLI. It shows examples of the most common tasks.

Alternatively you may want to add the automated task as one option to the GUI.
See later section about extending the GUI.

Supporting new hardware
=======================

Add a module to the drivers/ directory following the interface for the specific
type of component (see the back-end specification).

Add a test class to the test directory which instantiates the component and at
least detects whether the component is connected or not (scan() and selfTest()
methods) and does basic tasks (e.g., acquiring an image or moving an actuator).

Update the configuration file for instantiating the microscope with the
parameters for your new driver.

Commit your code using "git add ..." and "git commit -a".

Optionally, send your extension to Delmic as a git patch or fork.

Adding a feature to the Graphical User Interface
================================================

To edit the interface, you should use XRCed, by typing this (with the right paths):
PYTHONPATH=./src/:../Pyro4/src/:/usr/local/lib/python2.7/dist-packages/wx-2.9.4-gtk2/wx/tools python src/odemis/gui/launch_xrced.py

If you add/modify an image in src/odemis/gui/img, you need to regenerate the data.py file:
sudo apt-get install pngcrush # on the first use
cd src/odemis/gui/img
./images2python


Improving the speed
===================
First, you need to profile the code to see where is the bottleneck.
PYTHONPATH=./src/:../Pyro4/src/ python -m cProfile -o odemis.profile src/odemis/gui/main.py
# run the typical usage you want to measure

python -m pstats odemis.profile
> sort time
> stats

