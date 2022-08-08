# -*- coding: utf-8 -*-
"""
Created on 08 Aug 2022

@author: Kornee Kleijwegt

Copyright © 2013-2014 Kornee Kleijwegt, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import copy
import logging

from odemis import model
from odemis.util import almost_equal


class NoApertureError(IOError):
    """
    Exception used to indicate that no apertures of a certain size are available anymore.
    """
    pass

class HwError(IOError):
    """
    Exception used to indicate a problem coming from the hardware.
    """
    pass

class HighLevelAperture():
    """
    Represents the Aperture
    """
    def __init__(self, low_lvl_aptr_comp, scanner):
        self._low_lvl_aptr_comp = low_lvl_aptr_comp
        self.scanner = scanner

        self._low_level_aptr_dict = low_lvl_aptr_comp._apertureDict
        self._high_level_aptr_data = {}
        for aprtr_nmbr in self._low_level_aptr_dict.keys():
            self._high_level_aptr_data[aprtr_nmbr] = {
                                                      "Nominal probe-current": None,
                                                      "Last measured current": None,
                                                      "Worn-out": None
                                                     }
        self._updateHighLevelApertureData(self._loadPersistentMetadata())

        self.available_apertures = {}
        self._updateAvailableApertures()
        # TODO K.K. add check on all
        self.replacement_needed = model.BooleanVA(False)

        self.scanner.probe_current.subscribe(self.listener_probe_current)
        self.scanner.probe_current.subscribe(self._checkAperturePresets)

    def listener_probe_current(self, probe_current):
        aperture_number = self.scanner.presetData[probe_current]['aperture_number']
        state = self.getApertureStatus(aperture_number)
        # TODO K.K. Implement some waiting before doing anything to drastic, maybe try if the aperture/voltage is already set.

        if not state:
            self._setApertureWornOut(aperture_number)
            new_aperture = self._suggestReplacementAperture()
            logging.error(f"Aperture {aperture_number} is not working correctly any more, it is set to worn-out.\n"
                          f"A good replacement candidate is aperture number {new_aperture}.")

    def getApertureStatus(self, aperture_number, allowed_deviation=0.2):
        # TODO K.K. either the naming is bad or the input aperture_number should be used instead.

        nominal_probe_current = self.scanner.probe_current.value
        # Reset the probe current to be sure the correct settings are already set on the microscope before the measurement
        self.scanner.probe_current._setter(nominal_probe_current)


        measured_probe_current = self.scanner.performFaradayCupMeasurement().result()
        if almost_equal(measured_probe_current, nominal_probe_current, rtol=allowed_deviation):
            return True
        else:
            # TODO K.K. add units
            logging.error(f"The nominal probe current is {nominal_probe_current} however the measured probe current "
                          f"was only {measured_probe_current}.")
            return False

    def _checkAperturePresets(self, *args, **kwargs):
        # TODO K.K. why not use values instead of items?
        for probe_current, aperture_data in self.scanner.presetData.items():
            expected_aperture = aperture_data["aperture_number"]
            found_aperture = self.scanner.getApertureNmbrFromPreset(self.parent.preset_manager.GetPreset(aperture_data["name"]))
            if expected_aperture != found_aperture:
                raise HwError(f"The preset {aperture_data['name']} aperture number has been changed."
                              f"Preset number {expected_aperture} was expected but the current setting contains: {found_aperture}\n"
                              f"This may affect the probe current, aperture mangement and other parts of the microscope functionality.")


    def setNewAperture(self, probe_current_preset, new_aperture):
        # TODO K.K. deal with  _checkAperturePresets
        # TODO K.K. deal with setting up a new apertures nominal current
        # TODO K.K. deal with the GUI
        # TODO K.K. deal with it when an aperture is already worn out
        pass

    def _setApertureWornOut(self, aperture_number):
        self._high_level_aptr_data[aperture_number]["Worn-out"] = True
        self._updateAvailableApertures()

    def _suggestReplacementAperture(self, aperture_size):
        if len(self.available_apertures[aperture_size]) == 0:
            raise NoApertureError(f"No apertures of {aperture_size} micrometer available any more. Please use another size.")
        elif len(self.available_apertures[aperture_size]) == 1:
            logging.error(f"Last aperture of size {aperture_size} suggested to be used.\n"
                          f"Order a replacement aperture plate to prevent downtime of the system.")
        return min(self.available_apertures[aperture_size])

    def checkAperturePlateState(self, min_available=1):
        self._updateAvailableApertures()
        critical_aprtrs = {}
        for aprtr_size, available_aprtrs in self.available_apertures.items():
            if len(available_aprtrs) == 0:  # An extra warning for when an aperture size is worn out
                logging.error(f"All apertures of size {aprtr_size} are worn out."
                              f"Install a replacement aperture plate.")
            if len(available_aprtrs) <= min_available:
                critical_aprtrs.update({aprtr_size: len(available_aprtrs)})
        aperture_message = [f"Aperture size: {aprtr_size} has left {left}" for aptr_size, left in critical_aprtrs.items()]
        if len(critical_aprtrs) > 0:
            logging.error(f"The following apertures are at or below the set minimal availability:\n"
                          f"{aperture_message}\n"
                          f"Consider ordering a new aperture plate to prevent downtime of the system.")
        return critical_aprtrs


    def _updateAvailableApertures(self):
        """
        A separated function to update the available apertures.
        """
        for aprtr_nmbr, aprtr_data in self.getCombinedApertureData().items():
            if not aprtr_data["Worn-out"]:
                if not aprtr_data["Size"] in self.available_apertures:
                    self.available_apertures.update({aprtr_data["Size"]: {aprtr_nmbr}})
                else:
                    self.available_apertures[aprtr_data["Size"]].add(aprtr_nmbr)

        for aprtr_size, available_aprtrs in self.available_apertures.items():
            if len(available_aprtrs) <= 1:
                # If any of the aperture sizes is (almost) running out, replacement is needed
                self.replacement_needed.value = True


    def getCombinedApertureData(self):
        """
        Combines the high and low level aperture data
        :return (dict): Dict with both the high and low level aperture data
        """
        complete_dict = copy.deepcopy(self._low_level_aptr_dict)
        for aprtr_nmbr in self._low_level_aptr_dict.keys():
            complete_dict[aprtr_nmbr].update(self._high_level_aptr_data[aprtr_nmbr])

        return complete_dict

    def _updateHighLevelApertureData(self, new_data):
        for aprtr_nmbr, value in new_data.items():
            self._high_level_aptr_data[aprtr_nmbr].update(value)

    def _loadPersistentMetadata(self):
        """
        Update the dict _high_level_aptr_data with the data from last time.
        :return (dict): Dict from the persistent data containing the attribute _high_level_aptr_data
        """
        # TODO K.K. add importing of persistent metadata
        logging.error("Returns an empty dict for now")
        return {}