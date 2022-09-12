# -*- coding: utf-8 -*-

"""
:author: Rinze de Laat
:copyright: © 2012-2015 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
    the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
    Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the settings controls in the right setting column of the
user interface.

"""

from past.builtins import long
from future.utils import with_metaclass
from abc import ABCMeta
from collections.abc import Iterable
import locale
import logging
from odemis import model, util
from odemis.acq import calibration
import odemis.dataio
from odemis.gui import img
from odemis.gui.comp import hist
from odemis.gui.comp.buttons import ImageTextToggleButton
from odemis.gui.comp.file import EVT_FILE_SELECT
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.conf.data import HIDDEN_VAS, get_hw_config
from odemis.gui.conf.util import bind_setting_context_menu, create_setting_entry, SettingEntry, \
    create_axis_entry
from odemis.gui.cont.streams import StreamController
from odemis.gui.model import CHAMBER_UNKNOWN, CHAMBER_VACUUM
from odemis.gui.util import call_in_wx_main, formats_to_wildcards, get_picture_folder
from odemis.model import getVAs, VigilantAttributeBase
from odemis.util.units import readable_str
import os
import time
import wx

import odemis.gui.conf as guiconf


class SettingsController(with_metaclass(ABCMeta, object)):
    """ Settings base class which describes an indirect wrapper for FoldPanelItems

    :param fold_panel_item: (FoldPanelItem) Parent window
    :param default_msg: (str) Text message which will be shown if the SettingPanel does not
        contain any child windows.
    :param highlight_change: (bool) If set to True, the values will be highlighted when they
        match the cached values.

    """

    def __init__(self, fold_panel_item, default_msg, highlight_change=False, tab_data=None):

        self.panel = SettingsPanel(fold_panel_item, default_msg=default_msg)
        fold_panel_item.add_item(self.panel)

        self.highlight_change = highlight_change
        self.tab_data = tab_data

        self.num_entries = 0
        self.entries = []  # list of SettingEntry
        self._disabled_entries = set()  # set of SettingEntry objects

        self._subscriptions = []

    def hide_panel(self):
        self.show_panel(False)

    def show_panel(self, show=True):
        self.panel.Show(show)

    def pause(self):
        """ Pause SettingEntry related control updates """
        for entry in self.entries:
            entry.pause()
            if entry.value_ctrl and entry.value_ctrl.IsEnabled():
                entry.value_ctrl.Enable(False)
                self._disabled_entries.add(entry)

    def resume(self):
        """ Pause SettingEntry related control updates """
        for entry in self.entries:
            entry.resume()
            if entry in self._disabled_entries:
                entry.value_ctrl.Enable(True)
                self._disabled_entries.remove(entry)

    def enable(self, enabled):
        """ Enable or disable all SettingEntries """
        for entry in self.entries:
            if entry.value_ctrl:
                entry.value_ctrl.Enable(enabled)

    def add_file_button(self, label, value=None, tooltip=None, clearlabel=None, dialog_style=wx.FD_OPEN, wildcard=None):
        config = guiconf.get_acqui_conf()
        lbl_ctrl, value_ctrl = self.panel.add_file_button(label,
                                                          value or config.last_path,
                                                          clearlabel,
                                                          dialog_style,
                                                          wildcard)

        if tooltip is not None:
            lbl_ctrl.SetToolTip(tooltip)
            value_ctrl.SetToolTip(tooltip)

        # Add the corresponding setting entry
        ne = SettingEntry(name=label, lbl_ctrl=lbl_ctrl, value_ctrl=value_ctrl)
        self.entries.append(ne)
        return ne

    def add_setting_entry(self, name, va, hw_comp, conf=None):
        """ Add a name/value pair to the settings panel.

        :param name: (string): name of the value
        :param va: (VigilantAttribute)
        :param hw_comp: (Component): the component that contains this VigilantAttribute
        :param conf: ({}): Configuration items that may override default settings
        :return SettingEntry or None: the entry created, or None, if no entry was
          created (eg, because the conf indicates CONTROL_NONE).
        """

        assert isinstance(va, VigilantAttributeBase)

        # Remove any 'empty panel' warning
        self.panel.clear_default_message()

        ne = create_setting_entry(self.panel, name, va, hw_comp, conf, self.on_setting_changed)
        if ne is None:
            return None

        self.entries.append(ne)

        if self.highlight_change:
            bind_setting_context_menu(ne)

        self.panel.Parent.Parent.Layout()

        return ne

    def add_axis(self, name, comp, conf=None):
        """ Add a widget to the setting panel to control an axis

        :param name: (string): name of the axis
        :param comp: (Component): the component that contains this axis
        :param conf: ({}): Configuration items that may override default settings

        """

        ne = create_axis_entry(self.panel, name, comp, conf)
        self.entries.append(ne)

        # TODO: uncomment this once bind_setting_context_meny supports AxisSettingEntry
#         if self.highlight_change:
#             bind_setting_context_menu(ne)

        self.panel.Parent.Parent.Layout()

    def add_widgets(self, *wdg):
        """ Adds a widget at the end of the panel, on the whole width

        :param wdg: (wxWindow) the widgets to add (max 2)

        """

        # if only one widget: span over all the panel width
        if len(wdg) == 1:
            span = (1, 2)
        else:
            span = wx.DefaultSpan

        for i, w in enumerate(wdg):
            self.panel.gb_sizer.Add(w, (self.panel.num_rows, i), span=span,
                                     flag=wx.ALL | wx.EXPAND, border=5)
        self.panel.num_rows += 1

    def add_metadata(self, key, value):
        """ Adds an entry representing specific metadata

        According to the metadata key, the right representation is used for the value.

        :param key: (model.MD_*) the metadata key
        :param value: (depends on the metadata) the value to display

        """

        # By default the key is a nice user-readable string
        label = str(key)

        # Convert value to a nice string according to the metadata type
        try:
            if key == model.MD_ACQ_DATE:
                # convert to a date using the user's preferences
                nice_str = time.strftime("%c", time.localtime(value))
                # In Python 2, we still need to convert it to unicode
                if isinstance(nice_str, bytes):
                    nice_str = nice_str.decode(locale.getpreferredencoding())
            else:
                # Still try to beautify a bit if it's a number
                if (
                    isinstance(value, (int, long, float)) or
                    (
                        isinstance(value, Iterable) and
                        len(value) > 0 and
                        isinstance(value[0], (int, long, float))
                    )
                ):
                    nice_str = readable_str(value, sig=3)
                else:
                    nice_str = str(value)
        except Exception:
            logging.exception("Trying to convert metadata %s", key)
            nice_str = "N/A"

        self.panel.add_readonly_field(label, nice_str)

    def add_bc_control(self, detector):
        """ Add Hw brightness/contrast control """

        self.panel.add_divider()

        # Create extra gird bag sizer
        gb_sizer = wx.GridBagSizer()
        gb_sizer.SetEmptyCellSize((0, 0))

        # Create the widgets

        btn_autoadjust = ImageTextToggleButton(self.panel, height=24, label="Auto adjust",
                                               icon=img.getBitmap("icon/ico_contrast.png"))
        btn_autoadjust.SetToolTip("Adjust detector brightness/contrast")

        gb_sizer.Add(btn_autoadjust, (0, 0), (2, 1), border=10,
                     flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT)

        sld_conf = {
            "accuracy": 2,
            "event": wx.EVT_SCROLL_CHANGED,
            "control_type": odemis.gui.CONTROL_SLIDER,
            "type": "slider",
        }

        num_rows = 0

        if model.hasVA(detector, "brightness"):
            brightness_entry = self.add_setting_entry("brightness", detector.brightness, detector,
                                                      sld_conf)
            # TODO: 'Ugly' detaching somewhat nullifies the cleanliness created by using
            # 'add_setting_entry'. 'add_setting_entry' Needs some more refactoring anyway.
            self.panel.gb_sizer.Detach(brightness_entry.value_ctrl)
            self.panel.gb_sizer.Detach(brightness_entry.lbl_ctrl)

            gb_sizer.Add(brightness_entry.lbl_ctrl, (num_rows, 1))
            gb_sizer.Add(brightness_entry.value_ctrl, (num_rows, 2), flag=wx.EXPAND)
            num_rows += 1

        if model.hasVA(detector, "contrast"):
            contrast_entry = self.add_setting_entry("contrast", detector.contrast, detector,
                                                    sld_conf)

            self.panel.gb_sizer.Detach(contrast_entry.value_ctrl)
            self.panel.gb_sizer.Detach(contrast_entry.lbl_ctrl)

            gb_sizer.Add(contrast_entry.lbl_ctrl, (num_rows, 1))
            gb_sizer.Add(contrast_entry.value_ctrl, (num_rows, 2), flag=wx.EXPAND)
            num_rows += 1

        if num_rows:
            gb_sizer.AddGrowableCol(2)

        # Add the extra sizer to the main sizer
        self.panel.gb_sizer.Add(gb_sizer, (self.panel.num_rows, 0), span=(1, 2),
                                border=5, flag=wx.ALL | wx.EXPAND)
        self.panel.num_rows += 1

        # Connect various events to the auto adjust button

        def on_chamber_state(state, btn=btn_autoadjust):
            wx.CallAfter(btn.Enable, state in (CHAMBER_UNKNOWN, CHAMBER_VACUUM))

        # We keep a reference to keep the subscription active.
        self._subscriptions.append(on_chamber_state)
        self.tab_data.main.chamberState.subscribe(on_chamber_state, init=True)

        @call_in_wx_main
        def adjust_done(_):
            """ Callback that enables and untoggles the 'auto adjust' contrast button """
            btn_autoadjust.SetToggle(False)
            btn_autoadjust.SetLabel("Auto adjust")
            btn_autoadjust.Enable()
            brightness_entry.value_ctrl.Enable()
            contrast_entry.value_ctrl.Enable()

        def auto_adjust(_):
            """ Call the auto contrast method on the detector if it's not already running """
            if not btn_autoadjust.up:
                f = detector.applyAutoContrast()
                btn_autoadjust.SetLabel("Adjusting...")
                btn_autoadjust.Disable()
                brightness_entry.value_ctrl.Disable()
                contrast_entry.value_ctrl.Disable()
                f.add_done_callback(adjust_done)

        btn_autoadjust.Bind(wx.EVT_BUTTON, auto_adjust)

    def on_setting_changed(self, evt):
        logging.debug("Setting has changed")
        evt.Skip()

    def Refresh(self):
        """ TODO: check if this is still necessary after the foldpanel update """
        self.panel.Layout()

        p = self.panel.Parent
        while p:
            if isinstance(p, wx.ScrolledWindow):
                p.FitInside()
                p = None
            else:
                p = p.Parent


class SemSettingsController(SettingsController):
    pass


class OpticalSettingsController(SettingsController):
    pass


class AngularSettingsController(SettingsController):
    pass


class SpectrumSettingsController(SettingsController):
    pass


class FileInfoSettingsController(SettingsController):
    pass


class SettingsBarController(object):
    """ The main controller class for the settings panel in the live view and acquisition frame

    This class can be used to set, get and otherwise manipulate the content of the setting panel.

    """

    def __init__(self, tab_data):
        self._tab_data_model = tab_data
        self.setting_controllers = []

        # TODO: see if we need to listen to main.is_acquiring, and automatically
        # pause + enable. For now, it's done by the acquisition controllers,
        # and it avoids pausing the settings controllers from other tabs.

        # build the default config value based on the global one + the role
        self._hw_settings_config = tab_data.main.hw_settings_config

        # disable settings while there is a preparation process running
        self._tab_data_model.main.is_preparing.subscribe(self.on_preparation)

    @call_in_wx_main
    def on_preparation(self, is_preparing):
        # Make sure nothing can be modified during preparation
        self.enable(not is_preparing)

    def pause(self):
        """ Pause SettingEntry related control updates """
        for setting_conroller in self.setting_controllers:
            setting_conroller.pause()

    def resume(self):
        """ Resume SettingEntry related control updates """
        for setting_conroller in self.setting_controllers:
            setting_conroller.resume()

    @property
    def entries(self):
        """ Return a list of all the setting entries of all the panels """
        entries = []
        for setting_controller in self.setting_controllers:
            ets = setting_controller.entries
            entries.extend(ets)
        return entries

    def enable(self, enabled):
        for setting_controller in self.setting_controllers:
            setting_controller.enable(enabled)

    def add_hw_component(self, hw_comp, setting_controller, hidden=None):
        """ Add setting entries for the given hardware component

        hidden (None or set of str): name of VAs to not show

        """

        hidden = HIDDEN_VAS | (hidden or set())
        self.setting_controllers.append(setting_controller)

        vas_comp = getVAs(hw_comp)
        vas_config = get_hw_config(hw_comp, self._hw_settings_config)  # OrderedDict or dict

        # Re-order the VAs of the component in the same order as in the config
        vas_names = util.sorted_according_to(list(vas_comp.keys()), list(vas_config.keys()))

        for name in vas_names:
            try:
                if name in hidden:
                    continue
                elif name in vas_config:
                    va_conf = vas_config[name]
                else:
                    logging.debug("No config found for %s: %s", hw_comp.role, name)
                    va_conf = None
                va = vas_comp[name]
                setting_controller.add_setting_entry(name, va, hw_comp, va_conf)
            except TypeError:
                msg = "Error adding %s setting for: %s"
                logging.exception(msg, hw_comp.name, name)

    def add_spec_chronograph(self, setting_cont, ftsize=None):
        """

        :param setting_cont: (SettingsController)
        :param ftsize: (int or None) font size for the value

        """

        # Add a intensity/time graph
        self.spec_graph = hist.Histogram(setting_cont.panel, size=(-1, 40))
        self.spec_graph.SetBackgroundColour("#000000")
        setting_cont.add_widgets(self.spec_graph)
        # the "Mean" value bellow the graph
        lbl_mean = wx.StaticText(setting_cont.panel, label="Mean")
        tooltip_txt = "Average intensity value of the last image"
        lbl_mean.SetToolTip(tooltip_txt)
        self.txt_mean = wx.TextCtrl(setting_cont.panel,
                                    style=wx.BORDER_NONE | wx.TE_READONLY)
        if ftsize is not None:
            f = self.txt_mean.GetFont()
            f.PointSize = ftsize
            self.txt_mean.SetFont(f)
        self.txt_mean.SetForegroundColour(odemis.gui.FG_COLOUR_MAIN)
        self.txt_mean.SetBackgroundColour(odemis.gui.BG_COLOUR_MAIN)
        self.txt_mean.SetToolTip(tooltip_txt)
        setting_cont.add_widgets(lbl_mean, self.txt_mean)


class SecomSettingsController(SettingsBarController):

    def __init__(self, tab_panel, tab_data, highlight_change=False):
        super(SecomSettingsController, self).__init__(tab_data)
        main_data = tab_data.main

        self._sem_panel = SemSettingsController(tab_panel.fp_settings_secom_sem,
                                                "No SEM found",
                                                highlight_change,
                                                tab_data)

        self._optical_panel = OpticalSettingsController(tab_panel.fp_settings_secom_optical,
                                                        "No optical microscope found",
                                                        highlight_change,
                                                        tab_data)

        # Add the components based on what is available
        # TODO: move it to a separate thread to save time at init?
        if main_data.ccd:
            # Hide exposureTime as it's in local settings of the stream
            self.add_hw_component(main_data.ccd, self._optical_panel, hidden={"exposureTime"})

        if hasattr(tab_data, "confocal_set_stream"):
            conf_set_e = StreamController(tab_panel.pnl_opt_streams, tab_data.confocal_set_stream, tab_data)
            conf_set_e.stream_panel.flatten()  # removes the expander header
            # StreamController looks pretty much the same as SettingController
            self.setting_controllers.append(conf_set_e)
        else:
            tab_panel.pnl_opt_streams.Hide()  # Not needed

        # For now, we assume that the pinhole (axis) is global: valid for all
        # the confocal streams and FLIM stream. That's partly because most likely
        # the user wouldn't want to have separate values... and also because
        # anyway we don't currently support local stream axes.
        if main_data.pinhole:
            conf = get_hw_config(main_data.pinhole, self._hw_settings_config)
            for a in ("d",):
                if a not in main_data.pinhole.axes:
                    continue
                self._optical_panel.add_axis(a, main_data.pinhole, conf.get(a))

        if main_data.ebeam:
            self.add_hw_component(main_data.ebeam, self._sem_panel)

            # If can do AutoContrast, display the button
            # TODO: check if detector has a .applyAutoContrast() method, instead
            # of detecting indirectly via the presence of .bpp.
            det = main_data.sed or main_data.bsd
            if det and model.hasVA(det, "bpp"):
                self._sem_panel.add_bc_control(det)


class LocalizationSettingsController(SettingsBarController):

    def __init__(self, tab_panel, tab_data, highlight_change=False):
        super(LocalizationSettingsController, self).__init__(tab_data)
        main_data = tab_data.main

        self._optical_panel = OpticalSettingsController(tab_panel.fp_settings_secom_optical,
                                                        "No optical microscope found",
                                                        highlight_change,
                                                        tab_data)

        # Add the components based on what is available
        # TODO: move it to a separate thread to save time at init?
        if main_data.ccd:
            # Hide exposureTime as it's in local settings of the stream
            self.add_hw_component(main_data.ccd, self._optical_panel, hidden={"exposureTime"})


class AnalysisSettingsController(SettingsBarController):
    """ Control the widgets/settings in the right column of the analysis tab """

    def __init__(self, tab_panel, tab_data):
        super(AnalysisSettingsController, self).__init__(tab_data)

        self.tab_panel = tab_panel
        # Gui data model
        self.tab_data = tab_data

        self._pnl_acqfile = None  # panel containing info about file loaded
        self._pnl_calibration = None  # panel allowing to load background correction and calibration files

        self._create_controls()

        # Subscribe to the VAs that influence how the settings look.
        # All these VAs contain FileInfo object
        tab_data.acq_fileinfo.subscribe(self.on_acqfile_change)

        # The following can be replaced by callables taking a unicode and
        # returning a unicode (or raising a ValueError exception). They are
        # "filters" on what value can be accepted when changing the calibration
        # files. (Typically, the tab controller will put some of its functions)
        self.setter_ar_file = None
        self.setter_spec_bck_file = None
        self.setter_temporalspec_bck_file = None
        self.setter_angularspec_bck_file = None
        self.setter_spec_file = None

    def _create_controls(self):
        """ Create the default controls

        We create a Panel for each group of controls that we need to be able
        to show and hide separately.

        ** AR background and spectrum background, temporal spectrum background,
        angular spectrum background and spectrum efficiency compensation **

        These two controls are linked using VAs in the tab_data model.

        The controls are also linked to the VAs using event handlers, so that
        they can pass on their changing data.
        """

        # Panel containing information about the acquisition file
        self._pnl_acqfile = FileInfoSettingsController(self.tab_panel.fp_fileinfo, "No file loaded")
        wildcards, _ = formats_to_wildcards(odemis.dataio.get_available_formats(),
                                                            include_all=True)

        # Panel allowing to load bg correction or calibration files
        # Settings displayed are stream specific
        self._pnl_calibration = FileInfoSettingsController(self.tab_panel.fp_fileinfo, "")

        # Display with AR background file information
        # It's displayed only if there are AR streams (handled by the tab cont)
        self._ar_bckfile_entry = self._pnl_calibration.add_file_button(
            "AR background",
            tooltip="Angle-resolved background acquisition file",
            clearlabel="None", wildcard=wildcards)
        self._ar_bckfile_entry.lbl_ctrl.Show(False)
        self._ar_bckfile_entry.value_ctrl.Show(False)
        self._ar_bckfile_entry.value_ctrl.Bind(EVT_FILE_SELECT, self._on_ar_file_select)
        self.tab_data.ar_cal.subscribe(self._on_ar_cal, init=True)

        # Display for spectrum/temporal spectrum/angular spectrum background + efficiency compensation file information
        # They are displayed only if there are spectrum streams or temporal spectrum streams
        self._spec_bckfile_entry = self._pnl_calibration.add_file_button(
            "Spectrum background",
            tooltip="Spectrum background acquisition file",
            clearlabel="None", wildcard=wildcards)
        self._spec_bckfile_entry.lbl_ctrl.Show(False)
        self._spec_bckfile_entry.value_ctrl.Show(False)
        self._spec_bckfile_entry.value_ctrl.Bind(EVT_FILE_SELECT, self._on_spec_bck_file_select)
        self.tab_data.spec_bck_cal.subscribe(self._on_spec_bck_cal, init=True)

        self._temporalspec_bckfile_entry = self._pnl_calibration.add_file_button(
            "Temporal spectrum background",
            tooltip="Temporal spectrum background acquisition file",
            clearlabel="None", wildcard=wildcards)
        self._temporalspec_bckfile_entry.lbl_ctrl.Show(False)
        self._temporalspec_bckfile_entry.value_ctrl.Show(False)
        self._temporalspec_bckfile_entry.value_ctrl.Bind(EVT_FILE_SELECT, self._on_temporalspec_bck_file_select)
        self.tab_data.temporalspec_bck_cal.subscribe(self._on_temporalspec_bck_cal, init=True)

        self._angularspec_bckfile_entry = self._pnl_calibration.add_file_button(
            "Angular spectrum background",
            tooltip="Angular spectrum background acquisition file",
            clearlabel="None", wildcard=wildcards)
        self._angularspec_bckfile_entry.lbl_ctrl.Show(False)
        self._angularspec_bckfile_entry.value_ctrl.Show(False)
        self._angularspec_bckfile_entry.value_ctrl.Bind(EVT_FILE_SELECT, self._on_angularspec_bck_file_select)
        self.tab_data.angularspec_bck_cal.subscribe(self._on_angularspec_bck_cal, init=True)

        self._specfile_entry = self._pnl_calibration.add_file_button(
            "Spectrum correction",
            tooltip="Spectrum efficiency correction file",
            clearlabel="None", wildcard=wildcards)
        self._specfile_entry.lbl_ctrl.Show(False)
        self._specfile_entry.value_ctrl.Show(False)
        self._specfile_entry.value_ctrl.Bind(EVT_FILE_SELECT, self._on_spec_file_select)
        self.tab_data.spec_cal.subscribe(self._on_spec_cal, init=True)

        self._pnl_calibration.Refresh()
        self.tab_panel.Layout()
        self.tab_panel.fp_fileinfo.expand()

    def on_acqfile_change(self, file_info):
        """ Display the name and location of the file described by file_info

        The controls in the acquisition file panel can be destroyed and
        re-created each time, because it's one-way traffic between the VA and
        the controls.

        """

        # Remove the old controls
        self._pnl_acqfile.panel.clear_all()

        if file_info:
            lc, vc = self._pnl_acqfile.panel.add_readonly_field("File", file_info.file_basename)
            # Make sure the end is visible
            vc.SetInsertionPointEnd()

            lc, vc = self._pnl_acqfile.panel.add_readonly_field("Path", file_info.file_path)
            vc.SetInsertionPointEnd()

            # Add any meta data as labels
            for key, value in file_info.metadata.items():
                self._pnl_acqfile.add_metadata(key, value)

            # Change default dir for the calibration files
            for file_entry in (self._ar_bckfile_entry, self._spec_bckfile_entry,
                               self._temporalspec_bckfile_entry, self._angularspec_bckfile_entry,
                               self._specfile_entry):
                file_entry.value_ctrl.default_dir = file_info.file_path

        self._pnl_acqfile.Refresh()

    # TODO: refactor into widgets.FileConnector
    def _on_ar_file_select(self, evt):
        """ Pass the selected AR background file on to the VA """
        logging.debug("AR background selected by user")
        fn = evt.selected_file or u""  # selected_file is None if no file
        if self.setter_ar_file:
            try:
                fn = self.setter_ar_file(fn)
            except ValueError:
                logging.debug(u"Setter refused the file '%s'", fn)
                # Put back old file name
                self._ar_bckfile_entry.value_ctrl.SetValue(self.tab_data.ar_cal.value)
                return
            except Exception:
                self._ar_bckfile_entry.value_ctrl.SetValue(self.tab_data.ar_cal.value)
                raise

        self.tab_data.ar_cal.value = fn

    def _on_spec_bck_file_select(self, evt):
        """ Pass the selected spec background file on to the VA """
        logging.debug("Spectrum background file selected by user")
        fn = evt.selected_file or u""
        if self.setter_spec_bck_file:
            try:
                fn = self.setter_spec_bck_file(fn)
            except ValueError:
                logging.debug(u"Setter refused the file '%s'", fn)
                # Put back old file name
                self._spec_bckfile_entry.value_ctrl.SetValue(self.tab_data.spec_bck_cal.value)
                return
            except Exception:
                self._spec_bckfile_entry.value_ctrl.SetValue(self.tab_data.spec_bck_cal.value)
                raise

        self.tab_data.spec_bck_cal.value = fn

    def _on_temporalspec_bck_file_select(self, evt):
        """ Pass the selected spec background file on to the VA """
        logging.debug("Temporal spectrum background file selected by user")
        fn = evt.selected_file or u""
        if self.setter_temporalspec_bck_file:
            try:
                fn = self.setter_temporalspec_bck_file(fn)
            except ValueError:
                logging.debug(u"Setter refused the file '%s'", fn)
                # Put back old file name
                self._temporalspec_bckfile_entry.value_ctrl.SetValue(self.tab_data.temporalspec_bck_cal.value)
                return
            except Exception:
                self._temporalspec_bckfile_entry.value_ctrl.SetValue(self.tab_data.temoralspec_bck_cal.value)
                raise

        self.tab_data.temporalspec_bck_cal.value = fn

    def _on_angularspec_bck_file_select(self, evt):
        """ Pass the selected spec background file on to the VA """
        logging.debug("Angular spectrum background file selected by user")
        fn = evt.selected_file or u""
        if self.setter_angularspec_bck_file:
            try:
                fn = self.setter_angularspec_bck_file(fn)
            except ValueError:
                logging.debug("Setter refused the file '%s', reverting to previous %s",
                              fn, self.tab_data.angularspec_bck_cal.value)
                # Put back old file name
                self._angularspec_bckfile_entry.value_ctrl.SetValue(self.tab_data.angularspec_bck_cal.value)
                return
            except Exception:
                self._angularspec_bckfile_entry.value_ctrl.SetValue(self.tab_data.angularspec_bck_cal.value)
                raise

        self.tab_data.angularspec_bck_cal.value = fn

    def _on_spec_file_select(self, evt):
        """ Pass the selected efficiency compensation file on to the VA """
        logging.debug("Efficiency compensation file selected by user")
        fn = evt.selected_file or u""
        if self.setter_spec_file:
            try:
                fn = self.setter_spec_file(fn)
            except ValueError:
                logging.debug(u"Setter refused the file '%s'", fn)
                # Put back old file name
                self._specfile_entry.value_ctrl.SetValue(self.tab_data.spec_cal.value)
                return
            except Exception:
                self._specfile_entry.value_ctrl.SetValue(self.tab_data.spec_cal.value)
                raise

        self.tab_data.spec_cal.value = fn

    def _on_ar_cal(self, val):
        self._ar_bckfile_entry.value_ctrl.SetValue(val)

    def _on_spec_bck_cal(self, val):
        self._spec_bckfile_entry.value_ctrl.SetValue(val)

    def _on_temporalspec_bck_cal(self, val):
        self._temporalspec_bckfile_entry.value_ctrl.SetValue(val)

    def _on_angularspec_bck_cal(self, val):
        self._angularspec_bckfile_entry.value_ctrl.SetValue(val)

    def _on_spec_cal(self, val):
        self._specfile_entry.value_ctrl.SetValue(val)

    def show_calibration_panel(self, ar, spectrum, temporalspectrum, angularspectrum):
        """ Show/hide the the angle resolved/spectrum/temporal spectrum panel
        (background or efficiency corrections).
        :param ar: (boolean) Show, hide or don't change angle resolved calib panel.
        :param spectrum: (boolean) Show, hide or don't change spectrum calib panel.
        :param temporalspectrum: (boolean) Show, hide or don't change temporal spectrum calib panel.
        """

        self._ar_bckfile_entry.lbl_ctrl.Show(ar)
        self._ar_bckfile_entry.value_ctrl.Show(ar)
        self._spec_bckfile_entry.lbl_ctrl.Show(spectrum)
        self._spec_bckfile_entry.value_ctrl.Show(spectrum)
        self._temporalspec_bckfile_entry.lbl_ctrl.Show(temporalspectrum)
        self._angularspec_bckfile_entry.lbl_ctrl.Show(angularspectrum)
        self._angularspec_bckfile_entry.value_ctrl.Show(angularspectrum)
        self._temporalspec_bckfile_entry.value_ctrl.Show(temporalspectrum)
        self._specfile_entry.lbl_ctrl.Show(spectrum or temporalspectrum or angularspectrum)
        self._specfile_entry.value_ctrl.Show(spectrum or temporalspectrum or angularspectrum)

        self._pnl_calibration.Refresh()
        self.tab_panel.Layout()


class SparcAlignSettingsController(SettingsBarController):

    def __init__(self, tab_panel, tab_data):
        super(SparcAlignSettingsController, self).__init__(tab_data)
        main_data = tab_data.main
        self._ar_setting_cont = AngularSettingsController(tab_panel.fp_ma_settings_ar,
                                                          "No angle-resolved camera found")
        self._spect_setting_cont = SpectrumSettingsController(tab_panel.fp_ma_settings_spectrum,
                                                              "No spectrometer found")

        if main_data.ccd:
            self.add_hw_component(main_data.ccd, self._ar_setting_cont)

        if main_data.spectrometer:
            self.add_hw_component(main_data.spectrometer, self._spect_setting_cont)
            # increase a bit the font size for easy reading from far
            self.add_spec_chronograph(self._spect_setting_cont, 12)

        if main_data.spectrograph:
            comp = main_data.spectrograph
            conf = get_hw_config(comp, self._hw_settings_config)
            for a in ("wavelength", "grating", "slit-in"):
                if a not in comp.axes:
                    logging.debug("Skipping non existent axis %s on component %s",
                                  a, comp.name)
                    continue
                self._spect_setting_cont.add_axis(a, comp, conf.get(a))


class MirrorSettingsController(SettingsBarController):
    """
    Controller, which provides the user with the option to
    select among different configurations regarding the mirror position. For example, the user can select the
    configuration with the flipped mirror which is under the sample and placed upside-down.
    """
    def __init__(self, tab_panel, tab_data):
        super(MirrorSettingsController, self).__init__(tab_data)
        self.panel = tab_panel
        mirror_lens = tab_data.main.lens

        self.panel_center = SettingsPanel(self.panel.pnl_mode_btns)
        self.panel_center.SetBackgroundColour(odemis.gui.BG_COLOUR_PANEL)
        self.panel.pnl_mode_btns.GetSizer().Add(self.panel_center, 1, border=5,
                                            flag=wx.LEFT | wx.RIGHT | wx.EXPAND)

        entry_mirrorPosition = create_setting_entry(self.panel_center, "Mirror type",
                                                    mirror_lens.configuration,
                                                    mirror_lens,
                                                    conf={"control_type": odemis.gui.CONTROL_COMBO,
                                                          "label": "Mirror type",
                                                          "tooltip": "Change the type of the mirror"})

        entry_mirrorPosition.value_ctrl.SetBackgroundColour(odemis.gui.BG_COLOUR_PANEL)
        # remove border
        self.panel_center.GetSizer().GetItem(0).SetBorder(0)
        self.panel_center.Layout()

    @call_in_wx_main
    def on_preparation(self, is_preparing):
        # Don't change enable based on the preparation
        pass

    def enable(self, enabled):
        self.panel_center.Enable(enabled)


class StreakCamAlignSettingsController(SettingsBarController):
    """
    Controller, which creates the streak panel in the alignment tab and
    provides the necessary settings to align and calibrate a streak camera.
    """
    def __init__(self, tab_panel, tab_data):
        super(StreakCamAlignSettingsController, self).__init__(tab_data)
        self.panel = tab_panel
        main_data = tab_data.main
        self.streak_ccd = main_data.streak_ccd
        self.streak_delay = main_data.streak_delay
        self.streak_unit = main_data.streak_unit
        self.streak_lens = main_data.streak_lens

        self._calib_path = get_picture_folder()  # path to the trigger delay calibration folder

        self.panel_streak = SettingsPanel(self.panel.pnl_streak)
        self.panel_streak.SetBackgroundColour(odemis.gui.BG_COLOUR_PANEL)
        self.panel.pnl_streak.GetSizer().Add(self.panel_streak, 1, border=5,
                                             flag=wx.BOTTOM | wx.EXPAND)

        entry_timeRange = create_setting_entry(self.panel_streak, "Time range",
                                               self.streak_unit.timeRange,
                                               self.streak_unit,
                                               conf={"control_type": odemis.gui.CONTROL_COMBO,
                                                     "label": "Time range",
                                                     "tooltip": "Time needed by the streak unit for one sweep "
                                                                "from top to bottom of the readout camera chip."}
                                               )
        entry_timeRange.value_ctrl.SetBackgroundColour(odemis.gui.BG_COLOUR_PANEL)
        self.ctrl_timeRange = entry_timeRange.value_ctrl

        entry_triggerDelay = create_setting_entry(self.panel_streak, "Trigger delay",
                                                  self.streak_delay.triggerDelay,
                                                  self.streak_delay,
                                                  conf={"control_type": odemis.gui.CONTROL_FLT,
                                                        "label": "Trigger delay",
                                                        "tooltip": "Change the trigger delay value to "
                                                                   "center the image."},
                                                  change_callback=self._onUpdateTriggerDelayMD)

        entry_triggerDelay.value_ctrl.SetBackgroundColour(odemis.gui.BG_COLOUR_PANEL)
        self.ctrl_triggerDelay = entry_triggerDelay.value_ctrl

        entry_magnification = create_setting_entry(self.panel_streak, "Magnification",
                                                   self.streak_lens.magnification,
                                                   self.streak_lens,
                                                   conf={"control_type": odemis.gui.CONTROL_COMBO,
                                                         "label": "Magnification",
                                                         "tooltip": "Change the magnification of the input"
                                                                    "optics for the streak camera system. \n"
                                                                    "Values < 1: De-magnifying \n"
                                                                    "Values > 1: Magnifying"})

        entry_magnification.value_ctrl.SetBackgroundColour(odemis.gui.BG_COLOUR_PANEL)
        self.combo_magnification = entry_magnification.value_ctrl

        # remove border
        self.panel_streak.GetSizer().GetItem(0).SetBorder(0)
        self.panel_streak.Layout()

        self.panel.btn_open_streak_calib_file.Bind(wx.EVT_BUTTON, self._onOpenCalibFile)
        self.panel.btn_save_streak_calib_file.Bind(wx.EVT_BUTTON, self._onSaveCalibFile)

    def _onUpdateTriggerDelayMD(self, evt):
        """
        Callback method for trigger delay ctrl GUI element.
        Overwrites the triggerDelay value in the MD after a new value was requested via the GUI.
        """
        evt.Skip()
        cur_timeRange = self.streak_unit.timeRange.value
        requested_triggerDelay = self.ctrl_triggerDelay.GetValue()
        # get a copy of  MD
        trigger2delay_MD = self.streak_delay.getMetadata()[model.MD_TIME_RANGE_TO_DELAY]

        # check if key already exists (prevent creating new key due to floating point issues)
        key = util.find_closest(cur_timeRange, trigger2delay_MD.keys())
        if util.almost_equal(key, cur_timeRange):
            # Replace the current delay value with the requested for an already existing timeRange in the dict.
            # This avoid duplication of keys, which are only different because of floating point issues.
            trigger2delay_MD[key] = requested_triggerDelay
        else:
            trigger2delay_MD[cur_timeRange] = requested_triggerDelay
            logging.warning("A new entry %s was added to MD_TIME_RANGE_TO_DELAY, "
                            "which is not in the device .timeRange choices.", cur_timeRange)

        # check the number of keys in the dict is same as choices for VA
        if len(trigger2delay_MD.keys()) != len(self.streak_unit.timeRange.choices):
            logging.warning("MD_TIME_RANGE_TO_DELAY has %d entries, while the device .timeRange has %d choices.",
                            len(trigger2delay_MD.keys()), len(self.streak_unit.timeRange.choices))

        self.streak_delay.updateMetadata({model.MD_TIME_RANGE_TO_DELAY: trigger2delay_MD})
        # Note: updateMetadata should here never raise an exception as the UnitFloatCtrl already
        # catches errors regarding type and out-of-range inputs

        # update txt displayed in GUI
        self._onUpdateTriggerDelayGUI("Calibration not saved yet", odemis.gui.FG_COLOUR_WARNING)

    def _onUpdateTriggerDelayGUI(self, text, colour=odemis.gui.FG_COLOUR_EDIT):
        """
        Updates the GUI elements regarding the new trigger delay value.
        :parameter text (str): the text to show
        :parameter colour (wx.Colour): the colour to use
        """
        self.panel.txt_StreakCalibFilename.Value = text
        self.panel.txt_StreakCalibFilename.SetForegroundColour(colour)

    def _onOpenCalibFile(self, event):
        """
        Loads a calibration file (*csv) containing the time range and the corresponding trigger delay
        for streak camera calibration.
        """
        logging.debug("Open trigger delay calibration file for temporal acquisition.")

        dialog = wx.FileDialog(self.panel,
                               message="Choose a calibration file to load",
                               defaultDir=self._calib_path,
                               defaultFile="",
                               style=wx.FD_OPEN,
                               wildcard="csv files (*.csv)|*.csv")

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return

        # get selected path + filename and update default directory
        self._calib_path = dialog.GetDirectory()
        path = dialog.GetPath()
        filename = dialog.GetFilename()

        # read file
        try:
            tr2d = calibration.read_trigger_delay_csv(path,
                                                      self.streak_unit.timeRange.choices,
                                                      self.streak_delay.triggerDelay.range)
        except ValueError as error:
            self._onUpdateTriggerDelayGUI("Error while loading file!", odemis.gui.FG_COLOUR_HIGHLIGHT)
            logging.error("Failed loading %s: %s", filename, error)
            return

        # update the MD: overwrite the complete dict
        self.streak_delay.updateMetadata({model.MD_TIME_RANGE_TO_DELAY: tr2d})

        # update triggerDelay shown in GUI
        cur_timeRange = self.streak_unit.timeRange.value
        # find the corresponding trigger delay
        key = util.find_closest(cur_timeRange, tr2d.keys())
        # Note: no need to check almost_equal again as we do that already when loading the file
        self.streak_delay.triggerDelay.value = tr2d[key]  # set the new value

        self._onUpdateTriggerDelayGUI(filename)  # update txt displayed in GUI

    def _onSaveCalibFile(self, event):
        """
        Saves a calibration file (*csv) containing the time range and the corresponding trigger delay
        for streak camera calibration.
        """
        logging.debug("Save trigger delay calibration file for temporal acquisition.")

        dialog = wx.FileDialog(self.panel,
                               message="Choose a filename and destination to save the calibration file. "
                                       "It is advisory to include the SEM voltage into the filename.",
                               defaultDir=self._calib_path,
                               defaultFile="",
                               style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                               wildcard="csv files (*.csv)|*.csv")

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return

        # get selected path + filename and update default directory
        self._calib_path = dialog.GetDirectory()
        path = dialog.GetPath()
        filename = dialog.GetFilename()

        # check if filename is provided with the correct extension
        if os.path.splitext(filename)[1] != ".csv":
            filename += ".csv"
            path += ".csv"

        # get a copy of the triggerDelay dict from MD
        tr2d = self.streak_delay.getMetadata()[model.MD_TIME_RANGE_TO_DELAY]
        calibration.write_trigger_delay_csv(path, tr2d)

        # update txt displayed in GUI
        self._onUpdateTriggerDelayGUI(filename)
