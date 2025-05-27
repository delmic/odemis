# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel, Rinze de Laat, Philip Winkler

Copyright © 2012-2022 Éric Piel, Rinze de Laat, Delmic

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

This module contains classes to control the actions related to the acquisition
of microscope images.

"""

import logging

import wx

from odemis.gui.preset import preset_as_is, get_global_settings_entries, \
    get_local_settings_entries, apply_preset
from odemis.gui.win.acquisition import OverviewAcquisitionDialog, CorrelationDialog
from odemis.gui import model as guimod

class OverviewStreamAcquiController(object):
    """ controller to handle high-res image acquisition of the overview for the cryo-secom
    """

    def __init__(self, tab_data, tab, mode: guimod.AcquiMode = guimod.AcquiMode.FLM):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab: (Tab): the tab which should show the data
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab = tab
        self.acqui_mode = mode

    def open_acquisition_dialog(self):
        """
        return None or a list of DataArrays: the acquired images. None if it was
          cancelled.
        """
        # Indicate we are acquiring, especially important for the SEM which
        # need to get the external signal to not scan (cf MicroscopeController)
        self._main_data_model.is_acquiring.value = True

        # save the original settings
        settingsbar_controller = self._tab.settingsbar_controller
        orig_entries = get_global_settings_entries(settingsbar_controller)
        for sc in self._tab.streambar_controller.stream_controllers:
            orig_entries += get_local_settings_entries(sc)
        orig_settings = preset_as_is(orig_entries)
        settingsbar_controller.pause()
        settingsbar_controller.enable(False)

        # pause all the live acquisitions
        streambar_controller = self._tab.streambar_controller
        streambar_controller.pauseStreams()
        streambar_controller.pause()
        streambar_controller.enable(False)

        # create the dialog
        try:
            acq_dialog = OverviewAcquisitionDialog(
                self._tab.main_frame, self._tab_data_model,
                mode=self.acqui_mode)
            parent_size = [v * 0.77 for v in self._tab.main_frame.GetSize()]

            acq_dialog.SetSize(parent_size)
            acq_dialog.Center()
            action = acq_dialog.ShowModal()
        except Exception:
            logging.exception("Failed to create acquisition dialog")
            raise
        finally:
            apply_preset(orig_settings)

            settingsbar_controller.enable(True)
            settingsbar_controller.resume()

            streambar_controller.enable(True)
            streambar_controller.resume()

            self._main_data_model.is_acquiring.value = False
            acq_dialog.Destroy()

        if action == wx.ID_OPEN:
            return acq_dialog.data
        else:
            return None


class CorrelationDialogController:
    """Controller to handle the multipoint correlation (3DCT) iniMtialization"""

    def __init__(self, tab_data, tab):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab: (Tab): the tab which should show the data
        """
        self._tab_data_model = tab_data
        self._tab = tab
        self.cor_dialog = None

    def open_correlation_dialog(self):
        """
        Opens the multipoint correlation dialog
        """
        # save the original settings
        settingsbar_controller = self._tab.settingsbar_controller
        orig_entries = get_global_settings_entries(settingsbar_controller)
        for sc in self._tab.streambar_controller.stream_controllers:
            orig_entries += get_local_settings_entries(sc)
        orig_settings = preset_as_is(orig_entries)
        settingsbar_controller.pause()
        settingsbar_controller.enable(False)

        # pause all the live acquisitions
        streambar_controller = self._tab.streambar_controller
        streambar_controller.pauseStreams()
        streambar_controller.pause()
        streambar_controller.enable(False)

        # create the dialog
        try:
            self.cor_dialog = CorrelationDialog(
                self._tab.main_frame, self._tab_data_model)
            parent_size = [int(v * 0.9) for v in self._tab.main_frame.GetSize()]

            self.cor_dialog.SetSize(parent_size)
            self.cor_dialog.Center()
            self.cor_dialog.ShowModal()

        except Exception:
            logging.exception("Failed to create correlation dialog")
            raise
        finally:
            apply_preset(orig_settings)

            settingsbar_controller.enable(True)
            settingsbar_controller.resume()

            streambar_controller.enable(True)
            streambar_controller.resume()

            self.cor_dialog.Destroy()
