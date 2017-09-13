Odemis, Open Delmic Microscope Software

= Requirements = 
Linux (tested on Ubuntu 12.04 and 16.04 x86 32-bits and 64-bits)
Python (v2.7)
Special (forked) version of Pyro4 from Delmic


Note: the viewer part is also tested to run on Windows (7 and 10).

= Installation =
See the doc/INSTALL.txt document for the complete installation procedure.

= Basic usage =
Launch the "Odemis" program, or type on a terminal:
odemis-start
Eventually the GUI (Graphical User Interface) will appear.

It is not usually necessary, but if you want, to fully stop odemis (GUI and back-end), type:
odemis-stop


To run just the viewer, you can type:
odemis-gui --standalone

= Advanced usage =
odemisd is the command line interface to start and manage the Odemis backend. It
should be started first.

Run as "odemisd ...", with ... replaced by the correct arguments. For all the 
possible commands see:
odemisd --help

For example:
odemisd --daemonize --log-level=2 src/odemis/odemisd/test/optical-sim.odm.yaml



To use the command line interface use:
odemis-cli --help

To see the list of components:
odemis-cli --list

For example, to set the emission values for the light engine "Spectra", type:
odemis-cli --set-attr Spectra emissions "0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0"


= License =
GPLv2, see the LICENSE.txt file for the complete license.

= Extending =
For information on how to extend the software, see the developer documentation.
It must be first compiled, with: 
cd doc/develop/
make html
# or
make latexpdf

Then it can be opened with:
firefox _build/html/index.html
or
evince _build/latex/odemis-develop.pdf

= Testing =
To test the software, there are several unit-test classes in each directory (in 
their test/ sub-directory). There are also a few example microscope 
configuration file in the install/linux/usr/share/odemis/.

To run all the tests, you can call util/runtests.sh

