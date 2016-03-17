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

from __future__ import division

from abc import ABCMeta
import collections
import logging
from odemis import model, util
import odemis.dataio
from odemis.gui.comp.buttons import ImageTextToggleButton
from odemis.gui.comp.file import EVT_FILE_SELECT
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.conf.data import get_hw_settings_config, HIDDEN_VAS
from odemis.gui.conf.util import bind_setting_context_menu, create_setting_entry, SettingEntry, \
    create_axis_entry
from odemis.gui.model import CHAMBER_UNKNOWN, CHAMBER_VACUUM
import odemis.gui.util
from odemis.model import getVAs, VigilantAttributeBase
from odemis.util.units import readable_str
import time
import wx
from wx.lib.pubsub import pub

import odemis.gui.comp.hist as hist
import odemis.gui.conf as guiconf
from odemis.gui import img


class SettingsController(object):
    """ Settings base class which describes an indirect wrapper for FoldPanelItems

    :param fold_panel_item: (FoldPanelItem) Parent window
    :param default_msg: (str) Text message which will be shown if the SettingPanel does not
        contain any child windows.
    :param highlight_change: (bool) If set to True, the values will be highlighted when they
        match the cached values.

    """

    __metaclass__ = ABCMeta

    def __init__(self, fold_panel_item, default_msg, highlight_change=False, tab_data=None):

        self.panel = SettingsPanel(fold_panel_item, default_msg=default_msg)
        fold_panel_item.add_item(self.panel)

        self.highlight_change = highlight_change
        self.tab_data = tab_data

        self.num_entries = 0
        self.entries = []  # list of SettingEntry

    def hide_panel(self):
        self.show_panel(False)

    def show_panel(self, show=True):
        self.panel.Show(show)

    def pause(self):
        """ Pause SettingEntry related control updates """
        for entry in self.entries:
            entry.pause()

    def resume(self):
        """ Pause SettingEntry related control updates """
        for entry in self.entries:
            entry.resume()

    def enable(self, enabled):
        """ Enable or disable all SettingEntries """
        for entry in [e for e in self.entries if e.value_ctrl]:
            entry.value_ctrl.Enable(enabled)

    def add_browse_button(self, label, label_tl=None, clearlabel=None):
        config = guiconf.get_acqui_conf()
        lbl_ctrl, value_ctrl = self.panel.add_file_button(label, config.last_path, clearlabel)

        lbl_ctrl.SetToolTipString(label_tl)
        value_ctrl.SetToolTipString(label_tl)

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

        """

        assert isinstance(va, VigilantAttributeBase)

        # Remove any 'empty panel' warning
        self.panel.clear_default_message()

        ne = create_setting_entry(self.panel, name, va, hw_comp, conf, self.on_setting_changed)
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

        if self.highlight_change:
            bind_setting_context_menu(ne)

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
        label = unicode(key)

        # Convert value to a nice string according to the metadata type
        try:
            if key == model.MD_ACQ_DATE:
                # convert to a date using the user's preferences
                nice_str = time.strftime(u"%c", time.localtime(value))
            else:
                # Still try to beautify a bit if it's a number
                if (
                    isinstance(value, (int, long, float)) or
                    (
                        isinstance(value, collections.Iterable) and
                        len(value) > 0 and
                        isinstance(value[0], (int, long, float))
                    )
                ):
                    nice_str = readable_str(value, sig=3)
                else:
                    nice_str = unicode(value)
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
                                                icon=img.getBitmap("icon/ico_contrast.pnt"))

        gb_sizer.Add(btn_autoadjust, (0, 0), (2, 1), border=10,
                     flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT)

        sld_conf = {
            "accuracy": 2,
            "event": wx.EVT_SCROLL_CHANGED,
            "control_type": odemis.gui.CONTROL_SLIDER,
            "type": "slider",
        }

        num_rows = 0

        if isinstance(detector.brightness, VigilantAttributeBase):
            brightness_entry = self.add_setting_entry("brightness", detector.brightness, detector,
                                                      sld_conf)
            # TODO: 'Ugly' detaching somewhat nullifies the cleanliness created by using
            # 'add_setting_entry'. 'add_setting_entry' Needs some more refactoring anyway.
            self.panel.gb_sizer.Detach(brightness_entry.value_ctrl)
            self.panel.gb_sizer.Detach(brightness_entry.lbl_ctrl)

            gb_sizer.Add(brightness_entry.lbl_ctrl, (num_rows, 1))
            gb_sizer.Add(brightness_entry.value_ctrl, (num_rows, 2), flag=wx.EXPAND)
            num_rows += 1

        if isinstance(detector.contrast, VigilantAttributeBase):
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
            btn.Enable(state in (CHAMBER_UNKNOWN, CHAMBER_VACUUM))
        # We keep a reference to keep the subscription active.
        self._on_chamber_state = on_chamber_state
        self.tab_data.main.chamberState.subscribe(self._on_chamber_state, init=True)

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
        # Make sure the message is sent form the main thread
        wx.CallAfter(pub.sendMessage, 'setting.changed')
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
        self._va_config = get_hw_settings_config(tab_data.main.role)

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
            entries.extend(setting_controller.entries)
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
        vas_config = self._va_config.get(hw_comp.role, {}) # OrderedDict or dict

        # Re-order the VAs of the component in the same order as in the config
        vas_names = util.sorted_according_to(vas_comp.keys(), vas_config.keys())

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

    def add_stream(self, stream):
        pass

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
        lbl_mean.SetToolTipString(tooltip_txt)
        self.txt_mean = wx.TextCtrl(setting_cont.panel,
                                    style=wx.BORDER_NONE | wx.TE_READONLY)
        if ftsize is not None:
            f = self.txt_mean.GetFont()
            f.PointSize = ftsize
            self.txt_mean.SetFont(f)
        self.txt_mean.SetForegroundColour(odemis.gui.FG_COLOUR_MAIN)
        self.txt_mean.SetBackgroundColour(odemis.gui.BG_COLOUR_MAIN)
        self.txt_mean.SetToolTipString(tooltip_txt)
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

            # TODO once Power (light) is now by each stream individually. The
            # following code block can be disabled.
            #
            # if main_data.light:
            #     self._optical_panel.panel.add_divider()
            #
            #     self._optical_panel.add_setting_entry("power", main_data.light.power,
            #                                           main_data.light,
            #                                           self._va_config["light"]["power"])

        if main_data.ebeam:
            self.add_hw_component(main_data.ebeam, self._sem_panel)

            # If can do AutoContrast, display the button
            # TODO: check if detector has a .applyAutoContrast() method, instead
            # of detecting indirectly via the presence of .bpp.
            det = main_data.sed or main_data.bsd
            if det and hasattr(det, "bpp") and isinstance(det.bpp, VigilantAttributeBase):
                self._sem_panel.add_bc_control(det)


class LensAlignSettingsController(SettingsBarController):

    def __init__(self, tab_panel, tab_data, highlight_change=False):
        super(LensAlignSettingsController, self).__init__(tab_data)
        main_data = tab_data.main

        self._sem_panel = SemSettingsController(tab_panel.fp_lens_sem_settings,
                                                "No SEM found",
                                                highlight_change,
                                                tab_data)

        self._optical_panel = OpticalSettingsController(tab_panel.fp_lens_opt_settings,
                                                        "No optical microscope found",
                                                        highlight_change)

        # Add the components based on what is available
        if main_data.ccd:
            self.add_hw_component(main_data.ccd, self._optical_panel)

        if main_data.ebeam:
            self.add_hw_component(main_data.ebeam, self._sem_panel)


class AnalysisSettingsController(SettingsBarController):
    """ Control the widgets/settings in the right column of the analysis tab """

    def __init__(self, tab_panel, tab_data):
        super(AnalysisSettingsController, self).__init__(tab_data)

        self.tab_panel = tab_panel
        # Gui data model
        self.tab_data = tab_data

        # We add 3 different panels so, they can each be hidden/shown individually
        self._pnl_acqfile = None
        self._pnl_arfile = None
        self._pnl_specfile = None

        self._arfile_ctrl = None
        self._spec_bckfile_ctrl = None
        self._specfile_ctrl = None

        self._create_controls()

        # Subscribe to the VAs that influence how the settings look.
        # All these VAs contain FileInfo object
        tab_data.acq_fileinfo.subscribe(self.on_acqfile_change)

        # The following three can be replaced by callables taking a unicode and
        # returning a unicode (or raising a ValueError exception). They are
        # "filters" on what value can be accepted when changing the calibration
        # files. (Typically, the tab controller will put some of its functions)
        self.setter_ar_file = None
        self.setter_spec_bck_file = None
        self.setter_spec_file = None

    def _create_controls(self):
        """ Create the default controls

        We create a Panel for each group of controls that we need to be able
        to show and hide separately.

        ** AR background and Spectrum efficiency compensation **

        These two controls are linked using VAs in the tab_data model.

        The controls are also linked to the VAs using event handlers, so that
        they can pass on their changing data.
        """

        # Panel containing information about the acquisition file
        self._pnl_acqfile = FileInfoSettingsController(self.tab_panel.fp_fileinfo, "No file loaded")

        # Panel with AR background file information
        # It's displayed only if there are AR streams (handled by the tab cont)
        self._pnl_arfile = FileInfoSettingsController(self.tab_panel.fp_fileinfo, "")
        self._arfile_ctrl = self._pnl_arfile.add_browse_button(
            "AR background",
            "Angle-resolved background acquisition file",
            "None").value_ctrl
        wildcards, _ = odemis.gui.util.formats_to_wildcards(odemis.dataio.get_available_formats(),
                                                            include_all=True)
        self._arfile_ctrl.SetWildcard(wildcards)
        self._pnl_arfile.hide_panel()
        self._arfile_ctrl.Bind(EVT_FILE_SELECT, self._on_ar_file_select)
        self.tab_data.ar_cal.subscribe(self._on_ar_cal, init=True)

        # Panel with spectrum background + efficiency compensation file information
        # They are displayed only if there are Spectrum streams
        self._pnl_specfile = FileInfoSettingsController(self.tab_panel.fp_fileinfo, "")
        self._spec_bckfile_ctrl = self._pnl_specfile.add_browse_button(
            "Spec. background",
            "Spectrum background correction file",
            "None").value_ctrl
        self._spec_bckfile_ctrl.SetWildcard(wildcards)
        self._spec_bckfile_ctrl.Bind(EVT_FILE_SELECT, self._on_spec_bck_file_select)
        self.tab_data.spec_bck_cal.subscribe(self._on_spec_bck_cal, init=True)

        self._specfile_ctrl = self._pnl_specfile.add_browse_button(
            "Spec. correction",
            "Spectrum efficiency correction file",
            "None").value_ctrl
        self._specfile_ctrl.SetWildcard(wildcards)
        self._pnl_specfile.hide_panel()
        self._specfile_ctrl.Bind(EVT_FILE_SELECT, self._on_spec_file_select)
        self.tab_data.spec_cal.subscribe(self._on_spec_cal, init=True)

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
                self._arfile_ctrl.SetValue(self.tab_data.ar_cal.value)
                return
            except Exception:
                self._arfile_ctrl.SetValue(self.tab_data.ar_cal.value)
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
                self._spec_bckfile_ctrl.SetValue(self.tab_data.spec_bck_cal.value)
                return
            except Exception:
                self._spec_bckfile_ctrl.SetValue(self.tab_data.spec_bck_cal.value)
                raise

        self.tab_data.spec_bck_cal.value = fn

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
                self._specfile_ctrl.SetValue(self.tab_data.spec_cal.value)
                return
            except Exception:
                self._specfile_ctrl.SetValue(self.tab_data.spec_cal.value)
                raise

        self.tab_data.spec_cal.value = fn

    def _on_ar_cal(self, val):
        self._arfile_ctrl.SetValue(val)

    def _on_spec_bck_cal(self, val):
        self._spec_bckfile_ctrl.SetValue(val)

    def _on_spec_cal(self, val):
        self._specfile_ctrl.SetValue(val)

    def show_calibration_panel(self, ar=None, spec=None):
        """ Show/hide the the ar/spec panels

        ar (boolean or None): show, hide or don't change AR calib panel
        spec (boolean or None): show, hide or don't change spec calib panel
        """

        if ar is not None:
            self._pnl_arfile.show_panel(ar)
        if spec is not None:
            self._pnl_specfile.show_panel(spec)

        self.tab_panel.Layout()


class SparcAlignSettingsController(SettingsBarController):

    def __init__(self, tab_panel, tab_data):
        super(SparcAlignSettingsController, self).__init__(tab_data)
        main_data = tab_data.main
        self.hw_settings_config = get_hw_settings_config(main_data.role)

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
            for a in ("wavelength", "grating", "slit-in"):
                if a not in comp.axes:
                    logging.debug("Skipping non existent axis %s on component %s",
                                  a, comp.name)
                    continue
                conf = self.hw_settings_config[comp.role].get(a)
                self._spect_setting_cont.add_axis(a, comp, conf)
