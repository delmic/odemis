# -*- coding: utf-8 -*-
"""
Created on 14 Jan 2013

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

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

CONF_PATH = os.path.join(get_home_folder(), u".config/odemis")
ACQUI_PATH = get_picture_folder()

CONF_ACQUI = None
CONF_GENERAL = None
CONF_CALIBRATION = None

def get_general_conf():
    global CONF_GENERAL

    if not CONF_GENERAL:
        CONF_GENERAL = GeneralConfig()

    return CONF_GENERAL

def get_acqui_conf():
    """ Return the Acquisition config object and create/read it first if it does
        not yet exist.
    """
    global CONF_ACQUI

    if not CONF_ACQUI:
        CONF_ACQUI = AcquisitionConfig()

    return CONF_ACQUI


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
        self.file_path = os.path.abspath(
                                    os.path.join(CONF_PATH, self.file_name))
        # print self.file_path
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
            logging.warn(u"Using default %s configuration",
                         self.__class__.__name__)
            self.use_default()

    def write(self):
        """ Write the configuration to the given file if it exists or raise
        ``IOError`` otherwise
        """
        if self._exists():
            logging.debug(u"Writing configuration file '%s'", self.file_path)
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
            logging.debug(u"Creating path '%s'", CONF_PATH)
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
        if not self.config.has_section(section):
            logging.warn("Section %s not found, creating...", section)
            self.config.add_section(section)
        self.config.set(section, option, value)

    def get(self, section, option):
        """ Get the value of an option """
        try:
            return self.config.get(section, option)
        except (ConfigParser.NoOptionError, ConfigParser.NoSectionError):
            return self.default.get(section, option)

class GeneralConfig(Config):
    """ General configuration values """

    def __init__(self):
        file_name = "odemis.config"

        super(GeneralConfig, self).__init__(file_name)

        # Define the default settings
        self.default.add_section("help")

        self.default.set("help",
                         "manual_base_name",
                         u"user-guide.pdf"
                        )

        self.default.set("help",
                         "manual_path",
                         u"/usr/share/doc/odemis/"
                        )

        # For the calibration files (used in analysis tab)
        self.default.add_section("calibration")
        self.default.set("calibration", "ar_file", u"")
        self.default.set("calibration", "spec_file", u"")

    def get_manual(self, role=None):
        """ This method returns the path to the user manual

        First, it will look for a specific manual if a role is defined. If no
        role is defined or it does not exists, it will try and find the general
        user manual and return its path. If that also fails, None is returned.
        """
        manual_path = self.get("help", "manual_path")
        manual_base_name = self.get("help", "manual_base_name")

        if role:
            full_path = os.path.join(
                                manual_path,
                                u"%s-%s" % (role, manual_base_name)
                        )
            if os.path.exists(full_path):
                return full_path
            else:
                logging.info("%s manual not found, will use default one.", role)

        full_path = os.path.join(manual_path, manual_base_name)
        if os.path.exists(full_path):
            return full_path
        else:
            return None

    def get_dev_manual(self):
        """
        Returns (unicode): the path to the developer manual (or None)
        """
        manual_path = self.get("help", "manual_path")
        full_path = os.path.join(manual_path, u"odemis-develop.pdf")
        if os.path.exists(full_path):
            return full_path
        return None

class AcquisitionConfig(Config):

    def __init__(self):
        file_name = "acquisition.config"

        super(AcquisitionConfig, self).__init__(file_name)

        # Define the default settings
        self.default.add_section("acquisition")
        self.default.set("acquisition", "last_path", ACQUI_PATH)
        self.default.set("acquisition", "last_format", tiff.FORMAT)
        self.default.set("acquisition", "last_extension", tiff.EXTENSIONS[0])

    @property
    def last_path(self):
        lp = self.get("acquisition", "last_path")
        # Check that it (still) exists, and if not, fallback to the default
        if not os.path.isdir(lp):
            lp = ACQUI_PATH
        return lp

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

class CalibrationConfig(Config):

    def __init__(self):
        file_name = "calibration.config"

        super(CalibrationConfig, self).__init__(file_name)

        # Define the default settings
        self.default.add_section("calibration")
        self.default.set("calibration", "last_path", ACQUI_PATH)



