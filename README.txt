Delmic Acquisition Software for Physik Instrumente C-170 piezo-motor controllers

= Requirements = 
Linux or Windows
Python (v2.7) with pySerial

= Basic usage =
dacontrol.py is the command line interface to the PIRedStone python class.

Run as "./dacontrol.py ...", with ... replaced by the correct arguments.
See "./dacontrol.py --help" for information.

Basically there are 2 modes:
* command mode: move the stage
* test: tests the connection to the local Quanta client


= License =
GPLv2

= Testing =
To test the software, connect the controller(s) and run pi_test.py. It is not necessary
to have the motors connected to the controllers, but if they are, they will _move_ (a lot).
