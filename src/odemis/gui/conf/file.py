# -*- coding: utf-8 -*-
"""
:author: Rinze de Laat <laat@delmic.com>
:copyright: © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms  of the GNU
    General Public License version 2 as published by the Free Software  Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;  without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR  PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

"""
from future.utils import with_metaclass
from abc import ABCMeta, abstractproperty
import configparser
from configparser import NoOptionError
import logging
import math
import os.path

from odemis.dataio import tiff
from odemis.acq.align import delphi
from odemis.gui.util import get_picture_folder, get_home_folder
import sys
from past.builtins import unicode

CONF_PATH = os.path.join(get_home_folder(), u".config/odemis")
ACQUI_PATH = get_picture_folder()


class Config(with_metaclass(ABCMeta, object)):
    """ Abstract configuration super class

    Configurations are built around the :py:class:`configparser.ConfigParser` class.

    The main difference is that the filename is fixed, and changes are automatically saved.

    """

    @abstractproperty
    def file_name(self):
        """Name of the configuration file"""
        pass

    def __init__(self):
        # Absolute path to the configuration file
        self.file_path = os.path.abspath(os.path.join(CONF_PATH, self.file_name))
        # Attribute that contains the actual configuration
        # Disable "interpolation" to support easily storing values containing "%"
        self.config = configparser.ConfigParser(interpolation=None)

        # Note: the defaults argument of ConfigParser doesn't do enough, because
        # it only allows to specify default options values, independent of the
        # section.

        # Default configuration used to check for completeness
        self.default = configparser.ConfigParser(interpolation=None)

        self.read()

    def read(self):
        """ Will try to read the configuration file and will use the default.
            values when it fails.
        """
        if os.path.exists(self.file_path):
            self.config.read(self.file_path)
        else:
            logging.info(u"Using default %s configuration",
                         self.__class__.__name__)
            self.config = self.default

            # Create the file and save the default configuration, so the user
            # will be able to see the option exists. The drawback is that if we
            # change the default settings later on, the old installs will not
            # catch them up automatically.
            # TODO: => save the default settings as comments?
            self.write()

    def write(self):
        """
        Write the configuration file
        """
        # Create directory structure if it doesn't exist.
        if not os.path.exists(CONF_PATH):
            logging.debug(u"Creating path '%s'", CONF_PATH)
            os.makedirs(CONF_PATH)

        logging.debug(u"Writing configuration file '%s'", self.file_path)
        f = open(self.file_path, "w")
        self.config.write(f)
        f.close()

    def set(self, section, option, value):
        """ Set the value of an option in the given section
        section (string)
        option (string)
        value (byte string or unicode): if unicode, it's encoded as UTF-8
        """
        if not self.config.has_section(section):
            logging.warning("Section %s not found, creating...", section)
            self.config.add_section(section)
        value = self._ensure_str_format(value)
        self.config.set(section, option, value)
        self.write()

    def set_many(self, section, option_value_list):
        """ Set the values of options in the given section
        section (string)
        option_value_list (dict string->byte string or unicode): option name -> value
          (if the value is unicode, it's encoded as UTF-8)
        """
        if not self.config.has_section(section):
            logging.warning("Section %s not found, creating...", section)
            self.config.add_section(section)
        for option, value in option_value_list:
            value = self._ensure_str_format(value)
            self.config.set(section, option, value)
        self.write()

    def get(self, section, option):
        """ Get the value of an option in the given section
        section (string)
        option (string)
        returns (unicode): the value is converted from UTF-8
        """
        try:
            # Try to convert back from UTF-8 (and if it's not working, don't fail
            # but replace it by U+FFFD)
            ret = self.config.get(section, option)
            if isinstance(ret, bytes):  # python2
                return ret.decode("utf-8", "replace")
            else:  # python3
                return ret
        except (configparser.NoOptionError, configparser.NoSectionError):
            return self.default.get(section, option)

    def _ensure_str_format(self, s):
        """
        The value argument of ConfigParser requires a unicode str in python3 and a byte str
        in python2. This function makes sure a string is in the right format.
        """
        if sys.version_info[0] >= 3 and isinstance(s, bytes):
            s = s.decode("utf-8")
        elif sys.version_info[0] < 3 and isinstance(s, unicode):
            s = s.encode("utf-8")
        return s


class GeneralConfig(Config):
    """ General configuration values """

    file_name = "odemis.config"

    def __init__(self):

        super(GeneralConfig, self).__init__()

        # Define the default settings
        self.default.add_section("help")

        self.default.set("help", "manual_base_name", u"user-guide.pdf")

        # TODO: handle windows OS
        self.default.set("help", "manual_path", u"/usr/share/doc/odemis/")

        # For the calibration files (used in analysis tab)
        self.default.add_section("calibration")
        self.default.set("calibration", "ar_file", u"")
        self.default.set("calibration", "spec_file", u"")
        self.default.set("calibration", "spec_bck_file", u"")
        self.default.set("calibration", "temporalspec_bck_file", u"")
        self.default.set("calibration", "angularspec_bck_file", "u")

        # Section for Odemis/Delphi viewer config
        self.default.add_section("viewer")
        self.default.set("viewer", "update", "yes")

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
        """ Return the full path to the developer manual

        :return: (unicode) the path to the developer manual (or None)

        """

        manual_path = self.get("help", "manual_path")
        full_path = os.path.join(manual_path, u"odemis-develop.pdf")
        if os.path.exists(full_path):
            return full_path
        return None

class AcquisitionConfig(Config):
    file_name = "acquisition.config"

    def __init__(self):
        super(AcquisitionConfig, self).__init__()

        # Define the default settings
        self.default.add_section("acquisition")
        self.default.set("acquisition", "last_path", ACQUI_PATH)
        self.default.set("acquisition", "last_format", tiff.FORMAT)
        self.default.set("acquisition", "last_extension", tiff.EXTENSIONS[0])

        # fn_ptn is a (unicode) string representing a filename pattern. It may contain placeholders
        # surrounded by curly braces such as {datelng}, {daterev}, {count}, ...
        # These placeholders are replaced by the actual date/time/count when a filename is
        # generated. The placeholder options and the related code can be found in
        # odemis.util.filename. fn_count saves the latest count corresponding to a
        # filename pattern as a string (possibly with leading zeros).
        # Example: If the user saves the name 'test-20180101-05', the pattern
        # 'test-{datelng}-{cnt}' will be generated and fn_count will be set to '05'. This
        # pattern is used to suggest a new filename after the acquisition
        # is completed.
        self.default.set("acquisition", "fn_ptn", u"{datelng}-{timelng}")
        self.default.set("acquisition", "fn_count", "0")
        self.default.set("acquisition", "overlap", "0.0")

        self.default.add_section("export")
        self.default.set("export", "last_path", ACQUI_PATH)
        # Cannot save the format, as it depends on the type, but at least remember
        # whether it was "raw" (= post-processing) or not.
        self.default.set("export", "raw", "False")

        # Define the default settings for the project parameters
        self.default.add_section("project")
        self.default.set("project", "pj_last_path", ACQUI_PATH + "/")
        self.default.set("project", "pj_ptn", u"{datelng}-{timelng}")
        self.default.set("project", "pj_count", "0")

    @property
    def last_path(self):
        lp = self.get("acquisition", "last_path")
        # Check that it (still) exists, and if not, fallback to the default
        if not os.path.isdir(lp):
            lp = ACQUI_PATH
        return lp

    @last_path.setter
    def last_path(self, last_path):
        # Note that paths (or filenames) which end with a space have their name
        # trimmed when read back, so they will not be recorded properly.
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

    @property
    def last_export_path(self):
        lp = self.get("export", "last_path")
        # Check that it (still) exists, and if not, fallback to the default
        if not os.path.isdir(lp):
            lp = ACQUI_PATH
        return lp

    @last_export_path.setter
    def last_export_path(self, last_path):
        self.set("export", "last_path", last_path)

    @property
    def export_raw(self):
        return self.get("export", "raw").lower() == "true"

    @export_raw.setter
    def export_raw(self, value):
        strval = "True" if value else "False"
        self.set("export", "raw", strval)

    @property
    def fn_ptn(self):
        return self.get("acquisition", "fn_ptn")

    @fn_ptn.setter
    def fn_ptn(self, ptn):
        self.set("acquisition", "fn_ptn", ptn)

    @property
    def fn_count(self):
        return self.get("acquisition", "fn_count")

    @fn_count.setter
    def fn_count(self, cnt):
        self.set("acquisition", "fn_count", cnt)

    @property
    def overlap(self):
        return float(self.get("acquisition", "overlap"))

    @overlap.setter
    def overlap(self, value):
        self.set("acquisition", "overlap", value)

    @property
    def pj_last_path(self):
        lp = self.get("project", "pj_last_path")
        # Check that it (still) exists, and if not, fallback to the default
        if not os.path.isdir(lp):
            lp = ACQUI_PATH + "/"
        return lp

    @pj_last_path.setter
    def pj_last_path(self, pj_last_path):
        # Note that paths (or filenames) which end with a space have their name
        # trimmed when read back, so they will not be recorded properly.
        self.set("project", "pj_last_path", pj_last_path)

    @property
    def pj_ptn(self):
        return self.get("project", "pj_ptn")

    @pj_ptn.setter
    def pj_ptn(self, ptn):
        self.set("project", "pj_ptn", ptn)

    @property
    def pj_count(self):
        return self.get("project", "pj_count")

    @pj_count.setter
    def pj_count(self, cnt):
        self.set("project", "pj_count", cnt)


class CalibrationConfig(Config):
    """ For saving/restoring sample holder calibration data in the Delphi """

    file_name = "calibration.config"

    @staticmethod
    def _get_section_name(shid):
        return "delphi-%x" % shid

    def set_sh_calib(self, shid, htop, hbot, hfoc, ofoc, strans, sscale, srot,
                     iscale, irot, iscale_xy, ishear, resa, resb, hfwa, scaleshift):
        """ Store the calibration data for a given sample holder

        shid (int): the sample holder ID
        htop (2 floats): position of the top hole
        hbot (2 floats): position of the bottom hole
        hfoc (float): focus used for hole detection
        ofoc (float): focus used for the optical image
        strans (2 floats): stage translation
        sscale (2 floats > 0): stage scaling
        srot (float): stage rotation (rad)
        iscale (2 floats > 0): image scaling applied to CCD
        irot (float): image rotation (rad)
        iscale_xy (2 floats > 0)): image scaling applied to SEM
        ishear (float): image shear
        resa (2 floats): resolution related SEM image shift, slope of linear fit
        resb (2 floats): resolution related SEM image shift, intercept of linear fit
        hfwa (2 floats): hfw related SEM image shift, slope of linear fit
        scaleshift (2 floats): SEM shift when scale is set to 0 as a ratio of HFW

        """

        sec = self._get_section_name(shid)
        if self.config.has_section(sec):
            logging.info("ID %s already exists, overwriting...", sec)
        else:
            self.config.add_section(sec)

        self.set_many(sec, [
            ("top_hole_x", "%.15f" % htop[0]),
            ("top_hole_y", "%.15f" % htop[1]),
            ("bottom_hole_x", "%.15f" % hbot[0]),
            ("bottom_hole_y", "%.15f" % hbot[1]),
            ("hole_focus", "%.15f" % hfoc),
            ("optical_focus", "%.15f" % ofoc),
            ("stage_trans_x", "%.15f" % strans[0]),
            ("stage_trans_y", "%.15f" % strans[1]),
            ("stage_scaling_x", "%.15f" % sscale[0]),
            ("stage_scaling_y", "%.15f" % sscale[1]),
            ("stage_rotation", "%.15f" % srot),
            ("image_scaling_x", "%.15f" % iscale[0]),
            ("image_scaling_y", "%.15f" % iscale[1]),
            ("image_rotation", "%.15f" % irot),
            ("image_scaling_scan_x", "%.15f" % iscale_xy[0]),
            ("image_scaling_scan_y", "%.15f" % iscale_xy[1]),
            ("image_shear", "%.15f" % ishear),
            ("resolution_a_x", "%.15f" % resa[0]),
            ("resolution_a_y", "%.15f" % resa[1]),
            ("resolution_b_x", "%.15f" % resb[0]),
            ("resolution_b_y", "%.15f" % resb[1]),
            ("hfw_a_x", "%.15f" % hfwa[0]),
            ("hfw_a_y", "%.15f" % hfwa[1]),
            ("scale_shift_x", "%.15f" % scaleshift[0]),
            ("scale_shift_y", "%.15f" % scaleshift[1]),
        ])

    def _get_tuple(self, section, option):
        """ Read a tuple of float with the option name + _x and _y

        return (2 floats)

        :raises:
            ValueError: if the config file doesn't contain floats
            NoOptionError: if not all the options are present

        """

        x = self.config.getfloat(section, option + "_x")
        y = self.config.getfloat(section, option + "_y")
        return x, y

    def get_sh_calib(self, shid):
        """ Read the calibration of a given sample holder

        shid (int): the sample holder ID
        returns None (if no calibration data available), or :
            htop (2 floats): position of the top hole
            hbot (2 floats): position of the bottom hole
            hfoc (float): focus used for hole detection
            ofoc (float): focus used for the optical image
            strans (2 floats): stage translation
            sscale (2 floats > 0): stage scaling
            srot (float): stage rotation
            iscale (2 floats > 0): image scaling applied to CCD
            irot (float): image rotation
            iscale_xy (2 floats > 0)): image scaling applied to SEM
            ishear (float): image shear
            resa (2 floats): resolution related SEM image shift, slope of linear fit
            resb (2 floats): resolution related SEM image shift, intercept of linear fit
            hfwa (2 floats): hfw related SEM image shift, slope of linear fit
            scaleshift (2 floats): SEM shift when scale is set to 0 as a ratio of HFW

        """

        sec = self._get_section_name(shid)
        if self.config.has_section(sec):
            try:
                htop = self._get_tuple(sec, "top_hole")
                hbot = self._get_tuple(sec, "bottom_hole")
                try:
                    hfoc = self.config.getfloat(sec, "hole_focus")
                except (ValueError, NoOptionError):
                    logging.info("No SEM focus calibration found. A re-calibration should be performed.")
                    hfoc = delphi.SEM_KNOWN_FOCUS

                try:
                    ofoc = self.config.getfloat(sec, "optical_focus")
                except (ValueError, NoOptionError):
                    logging.info("No optical focus calibration found. A re-calibration should be performed.")
                    ofoc = delphi.OPTICAL_KNOWN_FOCUS

                strans = self._get_tuple(sec, "stage_trans")

                sscale = self._get_tuple(sec, "stage_scaling")
                if not (sscale[0] > 0 and sscale[1] > 0):
                    raise ValueError("stage_scaling %s must be > 0" % str(sscale))

                srot = self.config.getfloat(sec, "stage_rotation")
                if not 0 <= srot <= (2 * math.pi):
                    raise ValueError("stage_rotation %f out of range" % srot)

                iscale = self._get_tuple(sec, "image_scaling")
                if not (iscale[0] > 0 and iscale[1] > 0):
                    raise ValueError("image_scaling %s must be > 0" % str(iscale))

                irot = self.config.getfloat(sec, "image_rotation")
                if not 0 <= irot <= (2 * math.pi):
                    raise ValueError("image_rotation %f out of range" % irot)

                # Take care of missing skew values
                try:
                    iscale_xy = self._get_tuple(sec, "image_scaling_scan")
                    if not (iscale_xy[0] > 0 and iscale_xy[1] > 0):
                        raise ValueError("image_scaling_scan %s must be > 0" % str(iscale_xy))
                    ishear = self.config.getfloat(sec, "image_shear")
                except (ValueError, NoOptionError):
                    iscale_xy = (1, 1)
                    ishear = 0

                # Take care of old calibration files
                try:
                    resa = self._get_tuple(sec, "resolution_a")
                    resb = self._get_tuple(sec, "resolution_b")
                    hfwa = self._get_tuple(sec, "hfw_a")
                except (ValueError, NoOptionError):
                    logging.warning("No SEM image calibration found. Using default values. A re-calibration should be performed.")
                    resa = (0, 0)
                    resb = (0, 0)
                    hfwa = delphi.HFW_SHIFT_KNOWN

                # Until Odemis v2.5, it was called "spot_shift", and the value
                # was computed including res* and hfwa, for a resolution of 456 px.
                # From Odemis v2.6, this calibration value is _in addition_ to
                # res* and hfwa, for a resolution of 256 px.
                # => The metadata is still named MD_SPOT_SHIFT in the driver,
                # but it's a different name in the calibration file to avoid
                # using old incorrect value.
                try:
                    scaleshift = self._get_tuple(sec, "scale_shift")
                except (ValueError, NoOptionError):
                    logging.warning("No SEM spot shift calibration found. Using default values. A re-calibration should be performed.")
                    scaleshift = delphi.SPOT_SHIFT_KNOWN

                return (htop, hbot, hfoc, ofoc, strans, sscale, srot, iscale, irot,
                        iscale_xy, ishear, resa, resb, hfwa, scaleshift)
            except (ValueError, NoOptionError):
                logging.info("Not all calibration data readable, new calibration is required",
                             exc_info=True)
            except Exception:
                logging.exception("Failed to read calibration data")

        return None
