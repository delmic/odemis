Odemis, Open Delmic Microscope Software

= Requirements = 
Linux (tested on Ubuntu 12.04 32-bits) or Windows (7)
Python (v2.7)

= Installation =
See the doc/INSTALL.txt document for the complete installation procedure.

= Basic usage =
odemisd is the command line interface to start and manage the Odemis backend. It
should be started first.

Run as "odemisd ...", with ... replaced by the correct arguments.
See "odemisd --help" for information.
For example:
PYTHONPATH=./src/:../Pyro4/src/ ./src/odemis/odemisd/main.py --daemonize --log-level=2 src/odemis/odemisd/test/optical-sim.odm.yaml

To use the command line interface use:
PYTHONPATH=./:../Pyro4/src/ ./src/odemis/cli/main.py --help

= License =
GPLv2, see the LICENSE.txt file for the complete license.

= Extending =
For information on how to extend the software, see the doc/DEVELOP.txt document.

= Testing =
To test the software, there are several unit-test classes in each directory (in 
their test/ sub-directory). There are also a few example microscope 
configuration file in the odemisd/test.

