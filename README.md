<p align="right">
  <img  src="./src/odemis/gui/img/logo_h30.png">
</p>

# Odemis
Odemis (_Open Delmic Microscope Software_) is the open-source microscopy software of [Delmic B.V.](https://www.delmic.com). Odemis is used for controlling microscopes of Delmic and the Odemis viewer allows to load previous experimental data for visualization, analysis and export.
Delmic’s mission is to empower companies and researchers by helping them achieve results that can be trusted implicitly with powerful and user-friendly solutions.

## Requirements
* Linux (tested on Ubuntu 18.04 and 20.04 x86)
* Python (v3.6+)
* Special (forked) version of Pyro4 from Delmic

Note: the viewer part is also tested to run on Windows (10+).

For the complete list of dependencies, see the file `requirements.txt`.

## Installation
See the `doc/INSTALL.txt` document for the complete installation procedure.

## Basic usage
Launch the "Odemis" program, or type on a terminal:
`odemis-start`
Eventually the GUI (Graphical User Interface) will appear.
As an argument it can take the name of the microscope file corresponding to the back-end.

It is not usually necessary, but if you want, to fully stop odemis (GUI and back-end), type:
`odemis-stop`


To run just the viewer, you can type:
`odemis-gui --standalone`

## Advanced usage
`odemisd` is the command line interface to start and manage the Odemis backend. It should be started first.

Run as `odemisd ...`, with ... replaced by the correct arguments. For all the
possible commands see:
`odemisd --help`

For example:
`odemisd --daemonize --log-level=2 src/odemis/odemisd/test/optical-sim.odm.yaml`



To use the command line interface use:
`odemis-cli --help`

To see the list of components:
`odemis-cli --list`

For example, to turn on the forth source of the "light" component, type:
`odemis-cli --set-attr light power "0.0, 0.0, 0.0, 0.2"`

For example, to move the Y axis of the "stage" component by 100µm, type:
`odemis-cli --move stage y 100`


## License
GPLv2, see the `LICENSE.txt` file for the complete license.

## Extending
For information on how to extend the software, see the developer documentation.
It must be first compiled, with:
```
cd doc/develop/
make html
# or
make latexpdf
```
Then it can be opened with: 
`firefox _build/html/index.html`
or
`evince _build/latex/odemis-develop.pdf`

## Testing
To test the software, there are several unit-test classes in each directory (in their `test/` sub-directory). There are also a few example microscope configuration file in the `install/linux/usr/share/odemis/`.

To run all the tests, you can call `util/runtests.sh`.
