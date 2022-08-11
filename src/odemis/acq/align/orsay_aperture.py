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

APERTURE_ALREADY_WORN_OUT = "Aperture is already worn-out, bad replacement candidate"
NON_MATCHING_APERTURE_SIZE = "Aperture size doesn't match the excisting aperture size, bad replacement candidate"


class HighLevelAperture():
    """
    Represents the aperture plate from a high level perspectiveand monitors the available apertures left.
    """
    def __init__(self, low_lvl_aptr_comp, scanner, fib_beam):
        self._low_lvl_aptr_comp = low_lvl_aptr_comp
        self.scanner = scanner
        self._fib_beam = fib_beam
        # TODO K.K. This reference this doesn't work on a real system with an actual back-end
        self.preset_manager = self.scanner.parent.preset_manager

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
        self.replacement_needed = model.BooleanVA(False)

        self.scanner.probeCurrent.subscribe(self.listener_probe_current)
        self.scanner.probeCurrent.subscribe(self._checkAperturePresets)

    def getCombinedApertureData(self):
        """
        Combines the high and low level aperture data
        :return (dict): Dict with both the high and low level aperture data
        """
        complete_dict = copy.deepcopy(self._low_level_aptr_dict)
        for aprtr_nmbr in self._low_level_aptr_dict.keys():
            complete_dict[aprtr_nmbr].update(self._high_level_aptr_data[aprtr_nmbr])

        return complete_dict

    def getCurrentApertureStatus(self, allowed_deviation=0.2):
        """
        Checks the state of the aperture matching the currently selected probe current. Returns True when the
        aperture status is good and False when it is bad. If an aperture is worn-out it will set it to worn-out. If a
        worn-out aperture works again as expected it is set back to not worn-out.

        :param allowed_deviation (float): relative factor by which the faraday cup measurement is allowed to deviate
        from the nominal probe current.
        :return (bool): True when the status is good, False when the current aperture is worn-out.
        """
        nominal_probe_current = self.scanner.probeCurrent.value
        probe_current_settings = self.scanner.presetData[nominal_probe_current]
        aperture_number = probe_current_settings['aperture_number']
        if self._low_lvl_aptr_comp.selectedAperture.value != aperture_number or \
           not almost_equal(self._fib_beam.condenserVoltage.value, probe_current_settings['condenser_voltage'], atol=10):
                # Recall the setter of the probe current to make sure the correct preset is loaded.
                self.scanner.probeCurrent._setter(nominal_probe_current)

        measured_probe_current = self.scanner.performFaradayCupMeasurement().result()
        self._high_level_aptr_data[aperture_number]["Last measured current"] = measured_probe_current
        if almost_equal(measured_probe_current, nominal_probe_current, rtol=allowed_deviation):
            if self._high_level_aptr_data[aperture_number]["Worn-out"]:
                logging.warning(f"Aperture {aperture_number} was Worn-out, however it now seems to work again and "
                                f"fall within the allowed limits. The aperture is set to be fully working again.")
                self._high_level_aptr_data[aperture_number]["Worn-out"] = False
            self._updatePersistentMetadata()
            return True
        else:
            logging.error(f"The nominal probe current is {nominal_probe_current}A however the measured probe current "
                          f"was only {measured_probe_current}. The aperture is worn-out.")
            self._setApertureWornOut(probe_current_settings['aperture_number'])
            return False

    def checkAperturePlateState(self, min_available=1):
        """
        Checks the amount of non worn-out apertures for all sizes on the aperture place. Returns a dict with the number of
        non worn-out apertures for each size which has less non worn-out aperture than min_available.

        :param min_available (int): minimal number of non worn-out apertures
        :return (Dict/None): Dict with the sizes which have a limited number of not worn-out apertures, None if there are none.
        """
        self._updateAvailableApertures()
        critical_aprtrs = {}
        for aprtr_size, available_aprtrs in self.available_apertures.items():
            if len(available_aprtrs) == 0:  # An extra warning for when an aperture size is worn-out
                logging.error(f"All apertures of size {aprtr_size} are worn-out."
                              f"Install a replacement aperture plate.")
            if len(available_aprtrs) <= min_available:
                critical_aprtrs.update({aprtr_size: len(available_aprtrs)})
        aperture_message = [f"Aperture size: {aprtr_size} has left {left}" for aptr_size, left in critical_aprtrs.items()]
        if len(critical_aprtrs) > 0:
            logging.error(f"The following apertures are at or below the set minimal availability:\n"
                          f"{aperture_message}\n"
                          f"Consider ordering a new aperture plate to prevent downtime of the system.")
        return critical_aprtrs if critical_aprtrs else None  # Return None if the dict is empty

    def setNewAperture(self, probe_current, new_aperture_nmbr):
        """
        Sets up a new aperture

        :param probe_current (int): Key of the presetData key for which the aperture should be set
        :param new_aperture_nmbr (int): The aperture number which should be set
        """
        self.scanner.presetData[probe_current]['aperture_number'] = new_aperture_nmbr
        self._updatePersistentMetadata()
        self.scanner.probeCurrent.value = probe_current

        if self._low_lvl_aptr_comp.selectedAperture.value != new_aperture_nmbr or \
           not almost_equal(self._fib_beam.condenserVoltage.value, self.scanner.presetData[probe_current]['condenser_voltage'], atol=10):
                # Recall the setter of the probe current to make sure the new preset is loaded.
                self.scanner.probeCurrent._setter(probe_current)

        measured_probe_current = self.scanner.performFaradayCupMeasurement().result()
        self._high_level_aptr_data[new_aperture_nmbr]["Nominal probe-current"] = measured_probe_current
        self._high_level_aptr_data[new_aperture_nmbr]["Last measured current"] = measured_probe_current

    def validateNewAperture(self, probe_current, new_aperture_nmbr):
        """
        Validate if there are any problems for an aperture to replace an existing aperture from a preset.
        Note: this method does not perform an extra faraday cup measurement.

        :param probe_current (int): Key of the presetData key for which the aperture should be validated
        :param new_aperture_nmbr (int): The aperture number which should be validated

        :return (None/str): None if there are no problems and the new aperture is a match if there is a problem
                            the strings APERTURE_ALREADY_WORN_OUT or NON_MATCHING_APERTURE_SIZE are returned.
        """
        full_preset = self.preset_manager.GetPreset(self.scanner.presetData[probe_current]["name"])
        current_aperture_nbr = self.scanner.getApertureNmbrFromPreset(full_preset)
        current_aperture_size = self.getCombinedApertureData()[current_aperture_nbr]["Size"]
        new_aperture_size = self.getCombinedApertureData()[new_aperture_nmbr]["Size"]

        if self._high_level_aptr_data[new_aperture_nmbr]["Worn-out"]:
            return APERTURE_ALREADY_WORN_OUT
        elif new_aperture_size != current_aperture_size:
            return NON_MATCHING_APERTURE_SIZE
        else:
            return None  # If nothing else is wrong

    def listener_probe_current(self, probe_current):
        """
        Listener for the probe current, checks if the aperture for that current is still in good condition.

        :param probe_current (int):
        """
        aperture_number = self.scanner.presetData[probe_current]['aperture_number']
        state = self.getCurrentApertureStatus(aperture_number)

        if not state:
            self._setApertureWornOut(aperture_number)
            new_aperture = self._suggestReplacementAperture()
            logging.error(f"Aperture {aperture_number} is not working correctly any more, it is set to worn-out.\n"
                          f"A good replacement candidate is aperture number {new_aperture}.")

    def _setApertureWornOut(self, aperture_number):
        """
        Sets an aperture to worn-out in the _high_level_aptr_data and updates the available apertures and persistent
        metadata.

        :param aperture_number (int): aperture number of the aperture to be set worn-out
        """
        logging.warning(f"Aperture {aperture_number} is set to worn-out.")
        self._high_level_aptr_data[aperture_number]["Worn-out"] = True
        self._updateAvailableApertures()
        self._updatePersistentMetadata()

    def _suggestReplacementAperture(self, aperture_size):
        """
        Finds the next aperture of a size which is not worn-out.

        :param aperture_number (float): aperture size in meters
        """
        if len(self.available_apertures[aperture_size]) == 0:
            raise NoApertureError(f"No apertures of {aperture_size} micrometer available any more. Please use another size.")
        elif len(self.available_apertures[aperture_size]) == 1:
            logging.error(f"Last aperture of size {aperture_size} suggested to be used.\n"
                          f"Order a replacement aperture plate to prevent downtime of the system.")
        return min(self.available_apertures[aperture_size])  # return the lowest number of the replacement candidates.

    def _checkAperturePresets(self, *args, **kwargs):
        """
        Checks if the known preset settings still match those on the Orsay system. This guarantees that the correct
        probe current can be set by Odemis.

        :raises: an exception if the presets are changed without updating the Odemis aperture data.
        """
        for probe_current, aperture_data in self.scanner.presetData.items():
            expected_aperture = aperture_data["aperture_number"]
            # TODO K.K. don't call the preset manager/back-end here, that doesn't work, maybe change getApertureNmbrFromPreset
            found_aperture = self.scanner.getApertureNmbrFromPreset(self.preset_manager.GetPreset(aperture_data["name"]))
            if expected_aperture != found_aperture:
                logging.error(f"For the preset {aperture_data['name']}, which sets a probe current of {probe_current}pA,"
                              f" the aperture number has been changed. Preset number {expected_aperture} was expected "
                              f" but the current setting contains: {found_aperture}\n "
                              f"This may affect the probe current, aperture mangement and other parts of the microscope functionality.")

    def _updateHighLevelApertureData(self, new_data):
        """
        Updates the _high_level_aptr_data dict with another dict as input. It does not delete data in sub dicts but
        does overwrite them. Afterwards the persistent metadata is updated. Note the main advantage of the method
        over the default {}.update method is that it doesn't overwrite sub dicts.

        :param new_data (dict): dict with the same keys as _high_level_aptr_data with data to be update on _high_level_aptr_data.
                                the dict should look like this:
                                {aperture_nmbr1 : {"Nominal probe-current": float,"Last measured current": float, "Worn-out": bool},
                                 aperture_nmbr2 : {"Nominal probe-current": float,"Last measured current": float, etc.....
        """
        for aprtr_nmbr, value in new_data.items():
            self._high_level_aptr_data[aprtr_nmbr].update(value)
        self._updatePersistentMetadata()

    def _updateAvailableApertures(self):
        """
        A separated function to update the available apertures.
        """
        self.available_apertures = {}  # Start with an empty dict.
        for aprtr_nmbr, aprtr_data in self.getCombinedApertureData().items():
                if not aprtr_data["Size"] in self.available_apertures:
                    self.available_apertures.update({aprtr_data["Size"]: set()})
                if not aprtr_data["Worn-out"]:
                    self.available_apertures[aprtr_data["Size"]].add(aprtr_nmbr)

        for aprtr_size, available_aprtrs in self.available_apertures.items():
            if len(available_aprtrs) <= 1:
                # If any of the aperture sizes is (almost) running out, replacement is needed
                self.replacement_needed.value = True
                break # No need to continue, the plate needs to be replaced anyhow

    def _loadPersistentMetadata(self):
        """
        Load the dict self._high_level_aptr_data with the data from last time.
        :return (dict): Dict from the persistent data containing the attribute _high_level_aptr_data
        """
        # TODO K.K. add importing of persistent metadata and test case for this method.
        logging.error("Returns an empty dict for now")
        return {}

    def _updatePersistentMetadata(self):
        """
        Update the data from self._high_level_aptr_data to be persistent
        :raises: an exception if it failed to upste
        """
        # TODO K.K. add updating of persistent metadata and test case for this method.
        logging.error("Doesn't update the persistent data for now")