Odemis, Open Delmic Microscope Software

= Requirements = 
Linux (tested on Ubuntu 11.10 32-bits) or Windows (7)
Python (v2.7) with python-wxgtk2.8 (wxPython), python-imaging (PIL), 
python-yaml (PyYaml), python-gdal (GDAL), python-serial (pyserial)

For Andor Neo camera support:
Andor SDK v3.3+ -- on Linux, the bitflow driver has to be reinstalled (recompiled) each time the kernel is updated.
For Andor Clara camera support:
Andor SDK v2.97 -- on Linux/Ubuntu you need to install libusb-dev (so that whereis reports libusb.so). Also the the permissions of all the files must be allowed for normal user: everything is installed by default as root only, so nothing can work from the normal user account. 


= Basic usage =
odemisd is the command line interface to start and manage the Odemis backend. It
should be started first.

Run as "odemisd ...", with ... replaced by the correct arguments.
See "odemisd --help" for information.


= License =
GPLv2

= Testing =
To test the software, there are several unit-test classes in each directory (in 
their test/ sub-directory). There are also a few example microscope 
configuration file in the odemisd/test.
