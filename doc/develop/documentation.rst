********************
Documenting Odemis
********************

Odemis' documentation is fractioned into several manuals:

* **User manual** (Using Odemis and the microscope): The target audience of this manual is the user. It is also useful to the technician in an imaging facility who has to install, maintain and troubleshoot the Delmic microscopes. It should also be usable as a memory freshener for the Delmic employees who do support on the microscope. All the tips and tricks on the using software, including the back-end should be described. A large section on calibrating the microscope and modifying the microscope configuration file. Also how to update the software, report bugs, and ask for help.
* **Developer manual** (Programming with Odemis and programming Odemis): *This document* Target audience of this manual is the user/programmer who wants to extend the Odemis software suite with his own advanced (software) features. It describes all the aspects related to software development and Odemis. Its source are stored in the ``doc/develop/`` directory.

Updating the documentation
==========================

The documentation is developed with `Sphinx <http://sphinx-doc.org/tutorial.html>`_, 
and saved with the source code in the ``doc/`` directory.

For more information on using Sphinx, refer to:

* The tutorial: http://sphinx-doc.org/tutorial.html
* An overview on the reStructuredText syntax: http://sphinx-doc.org/rest.html
* On the usage of git, see either `Pro Git <http://git-scm.com/book>`_ or 
  `Easy Version Control with Git <http://net.tutsplus.com/tutorials/other/easy-version-control-with-git/>`_.

Note, in case you don't have it yet, install sphinx (and other needed software)
with::

    sudo apt-get install python-sphinx inkscape dia-gnome texlive

To build a manual, go to its root directory (e.g., ``doc/develop/``) and depending
on the format you want to obtain either run ``make html`` (for HTML) or 
``make latexpdf`` (for a PDF). This command transforms the .rst files and images
present into the final format. The final document files are stored in ``/doc/code/_build``.

Generating the docstring documentation
--------------------------------------

Sphinx allows to automatically parse the source code and generate some kind of
documentation from the docstrings. Note that this documentation is currently not in use as
it's fairly hard to provide good documentation and clean code simultaneously from
one file.
Make sure Odemis is present in the Python path::
    
    export PYTHONPATH=./src/

Run sphinx::

    sphinx-apidoc -f -o <path to odemis>/doc/code/_gen <path to odemis>/src/

This will extract all doc strings from the source code and store them in .rst
files located at ``/doc/code/_gen``. Finally, build the documentation in the format
you want, with ``make html`` or ``make latexpdf``.

