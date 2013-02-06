# -*- coding: utf-8 -*-
"""
Created on 14 Jan 2013

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains all code needed for the access to and management of GUI
related configuration files.

"""

import ConfigParser
import logging
import os.path

from odemis import dataio
from odemis.dataio import tiff
from odemis.gui.util import get_picture_folder, get_home_folder


CONF_PATH = os.path.join(get_home_folder(), r".config/odemis")
ACQUI_PATH = get_picture_folder()

CONF_ACQUI = None
CONF_GENERAL = None

def get_acqui_conf():
    """ Return the Acquisition config object and create/read it first if it does
        not yet exist.
    """
    global CONF_ACQUI

    if not CONF_ACQUI:
        CONF_ACQUI = AcquisitionConfig()

    return CONF_ACQUI

def get_general_conf():
    global CONF_GENERAL

    if not CONF_GENERAL:
        CONF_GENERAL = GeneralConfig()

    return CONF_GENERAL

class Config(object):
    """ Configuration super class

        Configurations are built around the
        :py:class:`ConfigParser.SafeConfigParser` class.
    """
    def __init__(self, file_name, read=True):
        """ If no path is provided, the default path will be loaded using.
            :py:func:`elit.util.get_config_dir` function.

            :param string file_name:    Name of the configuration file
            :param read: Try and read the config file on creation
            :type path: string or None:
        """

        self.file_name = file_name

        # Absolute path to the configuration file
        self.file_path = os.path.abspath(os.path.join(CONF_PATH, self.file_name))

        # Attribute that contains the actual configuration
        self.config = ConfigParser.SafeConfigParser()
        # Default configuration used to check for completeness
        self.default = ConfigParser.SafeConfigParser()

        if read:
            self.read()

    def read(self):
        """ Will try to read the configuration file and will use the default.
            values when it fails.
        """
        if self._exists():
            self.config.read(self.file_path)
        else:
            logging.warn("Using default %s configuration", self.__class__.__name__)
            self.use_default()

    def write(self):
        """ Write the configuration to the given file if it exists or raise ``IOError``
            otherwise
        """
        if self._exists():
            logging.debug("Writing configuration file '%s'", self.file_path)
            f = open(self.file_path, "w")
            self.config.write(f)
            f.close()
        else:
            self.create()
            self.write()

    def use_default(self):
        """ Assign the default configuration to the main one """
        self.config = self.default

    def create(self):
        """ Create the configuration file if it does not exist """
        # Create directory structure if it doesn't exist.
        if not os.path.exists(CONF_PATH):
            logging.debug("Creating path '%s'", CONF_PATH)
            os.makedirs(CONF_PATH)

        # Create file if it doesn't exist.
        if not os.path.exists(self.file_path):
            open(self.file_path, 'w').close()

    def _exists(self):
        """ Check whether the stored configuration file path exists.
        """
        return os.path.exists(self.file_path)

    def set(self, section, option, value):
        """ Set the value of an option """
        self.config.set(section, option, value)

    def get(self, section, option):
        """ Get the value of an option """
        return self.config.get(section, option)

class GeneralConfig(Config):
    """ General configuration values """

    def __init__(self):
        file_name = "odemis.config"

        super(GeneralConfig, self).__init__(file_name)

        # Define the default settings
        self.default.add_section("help")

        self.default.set("help",
                         "html_dev_doc",
                         os.path.abspath(
                            os.path.join(
                                __file__,
                                "../../../../../doc/code/_build/html/index.html")
                            )
                        )

    @property
    def html_dev_doc(self):
        return self.get("help", "html_dev_doc")


class AcquisitionConfig(Config):

    def __init__(self):
        file_name = "acquisition.config"

        super(AcquisitionConfig, self).__init__(file_name)

        # Define the default settings
        self.default.add_section("acquisition")

        self.default.set("acquisition",
                         "last_path",
                         ACQUI_PATH)

        self.default.set("acquisition",
                         "last_format",
                         tiff.FORMAT)

        self.default.set("acquisition",
                         "last_extension",
                         tiff.EXTENSIONS[0])

    @property
    def last_path(self):
        return self.get("acquisition", "last_path")

    @last_path.setter
    def last_path(self, last_path):
        self.set("acquisition", "last_path", last_path)

    @property
    def last_format(self):
        return self.get("acquisition", "last_format")

    @last_format.setter
    def last_format(self, value):
        self.set("acquisition", "last_format", value)

    @property
    def last_extension(self):
        return self.get("acquisition", "last_extension")

    @last_extension.setter
    def last_extension(self, last_extension):
        self.set("acquisition", "last_extension", last_extension)

