******************************************
Coding Guidelines
******************************************

This will help you to write code with the correct format. In general, we follow the
guidelines stated in `PEP 8 <https://peps.python.org/pep-0008/>`_. Here we will list the most common practices used by a software
developer for odemis.

Naming Conventions
==================

* Class names should be in CamelCase in which multiple words are connected by capitalizing the first letter of the word. For e.g. ``MyClass()``.

* Function name should be in snake_case in which the multiple words in small capitalization are joined with underscore where the first word is verb. For e.g. ``get_image()``

* Import modules should not be renamed. Use the original import name. For e.g. ``import numpy`` (and not ``import numpy as np``)

 * Exception: ``import matplotlib.pyplot as plt``.

* VAs in odemis should be in mixedCase which is different from PEP 8.


Exception: If dependent on outer dependencies which follow different
conventions. For instance, code inheriting from wxPython classes must follow
the wxPython style, which follows the wxwidget style from
typical C++ conventions.


For e.g. 1.1 the VAs in odemis have the following structure

.. code-block:: python

 self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)
 self.horizontalFoV = model.FloatContinuous(hfv, range=[10e-9, 10e-3],
                                               unit="m")
 self.magnification = model.VigilantAttribute(self._hfw_nomag / hfv,
                                                 unit="", readonly=True)

* Naming of test files follow the below ways to provide consistency for files in test folders (no _init_.py file), which are included in each sub-directories of src folder.

 * A unittest convention is used to name the test class for a module as ``TestModuleName(unittest.Testcase)``.

 * Class methods are named as ``test_method_name()``.

 * In a base class test which is used for repeating test cases with different input settings, we name the base class as ``TestModuleName``.

For e.g. 1.2 the naming of a base class test is as follows

.. code-block:: python

    class LakeshoreBaseTest:
        """Base class for testing different models of LakeShore temperature controller."""

        @classmethod
        def tearDownClass(cls):
            cls.dev.terminate()  # free up socket.

In class, mark the attributes as private with a first letter as _. Although technically python still
allows to access them from other classes, by convention, such attributes should not be read or
modified externally.
The private attributes can be changed or removed later, without having to look at
the code outside of the class in which they belong.

The usage of public attributes is useful when their access is required by a third
party or another class. In this scenario, public attributes can be used.

For e.g. 1.2 the implementation of public and private attributes

.. code-block:: python

    class Pokemon:
        """
        Links special powers to the given pokemon type
        """

       def __init__(self, name, alias):
          self.name = name       # public
          self._alias = alias   # private nomenclature

       def print_who(self):
          print('name  : ', self.name)
          print('alias : ', self._alias)


Docstrings
==================

Use three double quotes for the docstring. The docstring consists of function definition, description of input and return arguments. We will follow the reStructuredText style to write the docstring.
For e.g. 2.1 when type is declared in the docstring

.. code-block:: python

    def get_equality(arg1, arg2):
        """
        Summary line.

        Extended description of function.

        :param arg1: (int) Description of arg1.
        :param arg2: (str) Description of arg2.
        :raise: ValueError if arg1 is equal to arg2
        :return: (bool) Description of return value
        """
        if arg1 == arg2:
            raise ValueError('arg1 must not be equal to arg2')

        return True


Either declare the type in the function or in the docstring.
For e.g. 2.2 when type is not declared in the docstring, it is declared in the function as seen below

.. code-block:: python

    def calculate_mean_and_std(values: List[float]) -> Tuple[float, float]:
        """
        Calculates the mean and standard deviation of a list of values

        :param values: list of values.
        :return: mean and standard deviation
        """
        mean = sum(values) / len(values)
        std = (sum((values - mean) ** 2) / len(values)) ** 0.5
        return mean, std


In test classes, it is recommended to have one-line docstring for each test method. For very short and obvious
tests, where the function name explain what all it does, then in such cases, the docstring can be omitted.

For e.g. 2.3 the test class should be defined as

.. code-block:: python

    class TestOrsayStatic(unittest.TestCase):
        """
        Tests which don't need an Orsay component ready
        """

        def test_creation(self):
            """
            Test to create an Orsay component
            """
            if TEST_NOHW == True:
                self.skipTest(NO_SERVER_MSG)
            try:
                oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
            except Exception as e:
                self.fail(e)
            self.assertEqual(len(oserver.children.value), len(CONFIG_ORSAY["children"].keys()))

            oserver.terminate()

        def test_wrong_ip(self):
            """
            Tests that an HwError is raised when an empty ip address is entered
            """
            with self.assertRaises(HwError):
                orsay.OrsayComponent(name="Orsay", role="orsay", host="", children="")



General Guidelines

* For modules, add a license docstring (i.e. within three double quotes) at the top.

  * Exception: It is not necessary for empty '__init__.py' to carry a license.

* Add the description of the script as a comment below the license.

* Add a docstring to define the class. See example 1.1.

Code Cleaning up
==================

* If you clean the code for the current feature, make a separate commit for cleaning. It is recommended to place this commit as the first commit of a pull-request.

* Create a separate pull request if you would like to clean an unrelated code w.r.t current working branch in the repository.


Import Order
==================

Use the following order while importing python modules

* Standard library: See: https://docs.python.org/3/library/

* External: (aka 3rd party) library: e.g. numpy, pandas etc.

* Internal: For e.g. odemis packages.

Within each of the three sub categories follow the alphabetical order. In each block, all the ``import`` statements are first, then come all the ``from`` statements.

When importing many functions or variables from a single module, the length of the import line may not fit within the maximum line length. In that case, use parentheses to place the import over multiple lines.

For e.g. 4.1 standard and internal imports

.. code-block:: python


      import logging
      from collections.abc import Iterable, Mapping

      import cairo
      import numpy

      import odemis
      import odemis.util.units as units
      from odemis.gui import FG_COLOUR_DIS
      from odemis.gui.comp.buttons import ImageToggleButton
      from odemis.model import (MD_AT_AR, MD_AT_CL, MD_AT_EK, MD_AT_EM, MD_AT_FLUO,
                                MD_AT_ALIGN_OVERLAY, MD_AT_SPECTRUM, MD_AT_TEMPORAL,
                                MD_AT_TEMPSPECTRUM, MD_AT_FIB)


Naming Convention for Pull Requests, Branches and Commits
==========================================================

* The pull request title and the branch name must be the same.

    * The words in a pull request title must be separated by spaces, for the branch name they must be separated by dashes.
      The branch name should be all lower case.
    * Recommended naming convention: *[Issue-ID] title*.
      Where the *Issue-ID* is the Jira task short name, in upper-case, just as `PROJ-123`.
    * It is recommended to use the *[]* only for the pull request name.

* A pull request must contain a minimal description of the changes and what problem they solve.

    * One can include images, links, and tables to help convey this information.
    * If a pull request contains a single commit it is recommended to use the commit message as the pull request description.
    * A good to follow template for pull request description:

        .. code-block:: text

            ## Describe your changes

            ## Task number or link

* A commit message must contain a title and body.

    * Recommended convention:
    * for the commit title: *[label] title*. It must be lowercase and written in an imperative mood. See Note for suggested labels.
    * for the commit body: *try to explain what and why, not how (motivation)*.
    * Please do leave a blank line between the title and body.
    * In case the commit is for a fix, please do add the error messages as such in the commit body.

.. note::
    Possible *[label]*

        **fix**: A bug fix. Correlates with PATCH in SemVer

        **feat**: A new feature. Correlates with MINOR in SemVer

        **docs**: Documentation only changes

        **style**: Changes that do not affect the meaning of the code (white-space, formatting, missing semi-colons, etc)

        **refactor**: A code change that neither fixes a bug nor adds a feature

        **perf**: A code change that improves performance

        **test**: Adding missing or correcting existing tests

        **build**: Changes that affect the build system or external dependencies (example scopes: pip, docker, npm)

        **ci**: Changes to our CI configuration files and scripts (example scopes: GitHub CI)

        **config**: Changes to simulator or microscope configuration files
