*****************************
Commands and utility scripts
*****************************

There are various system commands and utility scripts provided with Odemis.
Most of them are either stored in ``util/`` , or, for the linux-only ones, in 
``install/linux/usr/bin/``.


Main Odemis commands
====================
* ``odemisd``: runs the backend.
* ``odemis-gui``: runs the graphical user interface.
* ``odemis-cli`` (or just ``odemis``): command line client to interact with the back-end.


Managing Odemis execution
=========================
* ``odemis-start``: starts the backend, if not yet started, and the GUI, if not yet started. Odemis icon default function.
* ``odemis-stop``: stops the GUI and the backend. Odemis icon "Full stop".
* ``odemis-cycle``: calls odemis-stop and then odemis-start.
* ``odemis-mic-selector``: outputs a specified text based on the presence of USB devices and/or SPARC modules. Used by odemis-start to detect the right microscope file configuration. (mostly for SPARCs).
* ``odemis-select-mic-start``: shows a window to let the user select a microscope file to start.
* ``odemis-select-channel``: shows a window to let the user select an update channel for Odemis (stable, release candidate, development).
* ``odemis-hw-status``: list the status of every component and show it in a window. Odemis icon "Show hardware status".
* ``odemis-live-view``: shows a selection window for detectors and then shows the live view of the detector.


Side utilities for Odemis
=========================
* ``odemis-convert``: read a Odemis acquisition file, and output in a different format. Can also take a series of tiles to generate the tiled file.
* ``odemis-park-mirror``: Park the SPARC parabolic mirror, even if Odemis is not running. Some Odemis icons are extended with "Park parabolic mirror" function.
* ``check-mirror-ref``: (SPARC) extra check that the parabolic mirror reference sensors work (rarely used).


Hardware configuration utilities
================================
* ``ftdiprog``: (FASTEM) FTDI USB/serial adapter: flash a special "USB description" to be used to detect the right device. (Not currently used in Odemis)
* ``lksconfig``: (ENZEL) lakeshore temperature controller: change the PID values of the temperature control.
* ``nfterminal``: (SPARC) NewPort NewFocus actuator: Terminal (aka REPL) utility to send command to the device. Used for configuration of some hardware (light tunnel) at installation.
* ``piconfig``: (SECOM) Physics Instrument actuator: read/write configuration in flash (stored as ``.pi.tsv``).
* ``piterminal``: (SECOM) Physics Instrument actuator: Terminal (aka REPL) utility to send command to the device. Used for configuration of the IP address at installation.
* ``pituner``: (SECOM) Physics Instrument actuator: helps tune the PID values. Moves an axis with a given setting, and display the actual position over time (with minimal GUI). Adjust the PID values, and repeat. For installation.
* ``pmconfig``: (FASTEM) Piezomotor actuator. Execute special commands needed at installation (calibration...).
* ``saconfig``: (METEOR) SmarAct actuator. Execute special commands needed at installation (change actuator type, referencing mode...).
* ``shrkconfig``: (SPARC) Andor Shamrock/Kymera spectrographs. Runs calibration commands (used regularly on the SPARC).
* ``kymera-exchange-turret.sh``: (SPARC) Andor Kymera 328i: Turret exchange utility (with minimal GUI).
* ``tmcmconfig``: (SPARC/METEOR/ENZEL) Trinamic actuator: Read/write configuration in flash (stored as ``.tmcm.tsv``)
* ``odemis-relay``: (SPARC) PMT control unit: Turn off & on power for an external device.


Odemis configuration and log management
=======================================
* ``odemis-sudo-gedit``: run gedit with root access (ask for password).
* ``odemis-edit-mic``: find the current microscope file, open it as root, and validate it after closing.
* ``grupdate``: aka "grep-update", extend a log file with all new instances of a regex match not yet in the log file.
* ``axes-odometer``: compute the travel distance of axes, based on the backend log.


Development tools
=================
* ``generate_icons.sh``: generate windows icons from the PNG icons.
* ``getfluodb.py``: downloads the fluorophore database (SECOM/METEOR/ENZEL) – currently the website is broken so fails to download.
* ``groom-img.py``: recompresses all PNG files to makes them as small as possible (for XRC files).
* ``launch_xrced.py``: Starts XRCed with the extra Odemis XRC code. XRCed doesn’t work since around 2020.
* ``release-odemis``: Releases the current git HEAD as a new version (on github and Ubuntu).
* ``runtests.sh``: run all the test cases, and generate a report.
* ``pytest_log_filter.py``: used by runtest.sh to parse the log, and highlight the errors.
* ``run_intg_tests.py``: used by runtest.sh (but disabled) to run every simulator backend & GUI, and check they do start.

