Delmic Acquisition Software for Andor Neo camera with SDK v2 and v3

= Requirements = 
Linux or Windows
Andor SDK v3.3+ -- on Linux, the bitflow driver has to be reinstalled (recompiled) each time the kernel is updated.
alternatively: Andor SDK v2.97 -- on Linux/Ubuntu you need to install libusb-dev (so that whereis reports libusb.so). Also the the permissions of all the files must be allowed for normal user: everything is installed by default as root only, so nothing can work from the normal user account. 
Python (v2.7)

= Basic usage =
dacontrol.py is the command line interface to acquire pictures from the Andor
Neo camera (SDK v3) or Clara camera (SDK v2).

Run as "./dacontrol.py ...", with ... replaced by the correct arguments.
See "./dacontrol.py --help" for information.

Basically there are 3 modes:
* command mode: acquire an image (or a series of images)
* list mode: find all compatible cameras connected to the computer
* test: tests the connection to the camera

ex: ./dacontrol.py --device 20 --test

= License =
GPLv2

= Testing =
To test the software, run andorcam_test.py. If you have both SDK v2 and SDK v3,
you probably have to disable one of them in the program as it appears to cause
crashes to have them simultaneously.  It is not necessary
to have the camera connected and turned on if you have the SDK v3 simulation
library installed. 
