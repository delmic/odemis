Delmic Acquisition Software for FEI Quanta microscope

= Requirements = 
Windows
FEI xTLib DCOM Client v4 (on Windows 7, it should be older than april 2011)
Python (v2.7 32 bits) with comtypes, PIL, Pyro (v3)

After the first run, the comtypes generated layer must be modified for GetImages, pbImageData should be converted from 'out' to 'in'. See C:\Python27\Lib\site-packages\comtypes\gen\_8B506272_8A1D_11D4_A5EE_00B0D03B7B0E_0_1_0.py


= Basic usage =
dacontrol.py is the command line interface to the Quanta python class.

Run as "python dacontrol.py ...", with ... replaced by the correct arguments.
See "python dacontrol.py --help" for information.

Basically there are 4 modes:
* direct access: connects directly to the Quanta DCOM client on the machine
* daemon: connects to the Quanta DCOM client on the machine and listen to remote client
* remote client: connects to the Quanta client indirectly by connecting to a daemon
* test: tests the connection to the local Quanta client

To get it working in daemon/remote:
1. Run the daemon on a Windows machine:
python dacontrol.py --host=127.0.0.1 --daemon

-> read the address of the daemon

2. Run the remote client from anywhere and acquire an image (into the file called image.tiff):
python dacontrol.py --remote=145.94.169.158:7766 --output=image.tiff

= License =
GPLv2

= Emulation mode selection =
To be able to change the emulation mode, you need to have write permission rights on HKEY_LOCAL_MACHINE\SOFTWARE\XTLib . To make sure this is good, use regedit and edit this key (or HKEY_LOCAL_MACHINE\SOFTWARE\Wow6432Node\XTLib on 64bit Windows). Right-click on it, select "Permissions..." and add write permissions to your user account (you need to add it to the list).

= Testing =
To test the software, just run quanta_test.py.
