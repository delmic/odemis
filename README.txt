Delmic Acquisition Software for Andor Neo camera with SDK v3

= Requirements = 
Linux or Windows
Andor SDK v3.3 (or later) -- on Linux, the bitflow driver has to be reinstalled (recompiled) each time the kernel is updated.
Python (v2.7)

= Basic usage =
dacontrol.py is the command line interface to acquire pictures from the Andor
neo camera.

Run as "./dacontrol.py ...", with ... replaced by the correct arguments.
See "./dacontrol.py --help" for information.

Basically there are 3 modes:
* command mode: acquire an image
* list mode: find all compatible cameras connected to the computer
* test: tests the connection to the camera

= License =
GPLv2

= Testing =
To test the software, run andorcam_test.py. It is not necessary
to have the camera connected and turned on if you have the simulation library installed.


= Note about SDK v2 =
Linux:
For the USB support, you need to install libusb-dev (so that whereis reports libusb.so)
Check the permissions of all the files: everything is installed by default as root only,
so nothing can work from the normal user account. 
