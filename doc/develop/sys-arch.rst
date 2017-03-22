*********************
Architecture Overview
*********************

Odemis is made of two main parts:
  * The back-end, which is made of the drivers and a manager.
  * The front-end, which can be any user interface such as a command line 
    interface or the graphical user interface.

The figure below represents a typical Odemis instance run.

.. image:: sys-arch.*
    :width: 100 %
    :alt: Odemis architecture overview

The following describes the various parts of Odemis in more details.

Drivers
-------
Many independent modules which are each an adapter between a
hardware component and the rest of Odemis, following a programming
interface (with specificities for each type of the hardware).
The code is found in the ``src/odemis/drivers/`` directory.

For stability purpose, each adapter run in a separate process 
(called a :py:class:`model.Container`).


Back-end manager
----------------
It is the core of Odemis and is in charge of connecting the various drivers
together according to a configuration file. It provides a uniform view of the
microscope (independently of the actual hardware components) to the user 
interface. The code is found in the ``src/odemis/odemisd/`` directory.
Example of configuration files for various types of microscopes can be found in
``/usr/share/odemis/*.odm.yaml``

Command-line interface
----------------------
The command-line interface (CLI) allows basic manipulation of the microscope via
a terminal, or in a script. The code is found in the ``src/odmemis/cli/``
directory.

Graphical user interface
------------------------
The Graphical User Interface (GUI) allows the user to manipulate the microscope
and displays the acquired data. This usual user interface for the target user.
The code can be found in the ``src/odemis/gui/`` directory.

File import/export
------------------
Each file format supported to save or open acquired data is handled by a separate
Python module.
The code of the modules can be found in the ``src/odemis/dataio`` directory.


Code Structure
==============

* src/odemis/: to import to know the version of odemis

* src/odemis/model/: the default classes for the microscope model (including properties) import odemis.model as model imports everything. (same behaviour as wx or numpy).

* src/odemis/driver/: contains the device drivers (or a wrapper to the actual driver). One python module per hardware type whenever possible.

* src/odemis/odemisd/odemisd.py : backend, in charge of instantiating the microscope model and running it.

* src/odemis/gui/main.py: main for the graphical user interface

* src/odemis/cli/main.py : main for a basic command line interface to the backend (retrieve status of microscope, acquire image)

* src/odemis/util/: some helper functions

* scripts/

* doc/


Additionally, every directory contains a test/ directory which contains various
Python test classes used to validate the behaviour of the program.

