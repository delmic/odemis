# -*- coding: utf-8 -*-
"""
Created on 26 Sep 2012

@author: Éric Piel

Copyright © 2012-2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
from __future__ import division
from collections import OrderedDict
import collections

import logging
import numpy
import wx
from wx.lib.pubsub import pub

import odemis.acq.stream as acqstream
from odemis.acq.stream import OpticalStream, CameraStream
from odemis.gui import FG_COLOUR_DIS, FG_COLOUR_WARNING, FG_COLOUR_ERROR
from odemis.gui.comp.combo import ComboBox
from odemis.gui.comp.stream import StreamPanel
from odemis.gui.conf.data import HW_SETTINGS_CONFIG
import odemis.gui.model as guimodel
from odemis import model
from odemis.util import fluo
from odemis.gui.model import dye
from odemis.gui.util.widgets import VigilantAttributeConnector


# Stream scheduling policies: decides which streams which are with .should_update get .is_active
from odemis.util.conversion import wave2rgb
from odemis.util.fluo import to_readable_band, get_one_center


SCHED_LAST_ONE = 1  # Last stream which got added to the should_update set
SCHED_ALL = 2  # All the streams which are in the should_update stream
# Note: it seems users don't like ideas like round-robin, where the hardware
# keeps turn on and off, (and with fluorescence fine control must be done, to
# avoid bleaching).
# TODO: SCHED_ALL_INDIE -> Schedule at the same time all the streams which
# are independent (no emitter from a stream will affect any detector of another
# stream).


class SettingEntry(VigilantAttributeConnector):
    """ An Entry linked to a Vigilant Attribute """

    def __init__(self, name, va=None, stream=None, lbl_ctrl=None, value_ctrl=None,
                 va_2_ctrl=None, ctrl_2_va=None, events=None):
        """ See the super classes for parameter descriptions

        :param name: (str): The name of the setting
        :param va: (VigilantAttribute): The VA containing the setting value
        :param stream: (Stream): The possible data stream associated with this entry
        :param lbl_ctrl: (wx.StaticText): The setting label
        :param value_ctrl: (wx.Window): The widget containing the current value

        """

        self.name = name
        self.stream = stream
        self.lbl_ctrl = lbl_ctrl
        self.value_ctrl = value_ctrl

        VigilantAttributeConnector.__init__(self, va, value_ctrl, va_2_ctrl, ctrl_2_va, events)

    def pause(self):
        if self.stream_va:
            super(SettingEntry, self).pause()

    def resume(self):
        if self.stream_va:
            super(SettingEntry, self).resume()


class StreamController(object):
    """ Manage a stream and it's accompanying stream panel """

    def __init__(self, stream_bar, stream, tab_data_model, show=True):

        self.stream = stream
        self.stream_bar = stream_bar

        label_edit = False

        # Make the name label non-static if dye names are involved.
        # TODO: Make this more generic/clean
        if (
                hasattr(stream, "excitation") and hasattr(stream, "emission") and
                not (self.stream.excitation.readonly or self.stream.emission.readonly)
        ):
            label_edit = True

        self.stream_panel = StreamPanel(stream_bar, stream, label_edit)
        self.tab_data_model = tab_data_model

        # Peak excitation/emission wavelength of the selected dye, to be used for peak text and
        # wavelength colour
        # FIXME: Move all dye related code to a separate subclass of StreamController?
        self._dye_xwl = None
        self._dye_ewl = None
        self._dye_prev_ewl_center = None  # ewl when tint was last changed

        self._btn_excitation = None
        self._btn_emission = None

        self._lbl_exc_peak = None
        self._lbl_em_peak = None

        self.entries = OrderedDict()

        # Check if light and exposure controls are necessary
        if isinstance(stream, OpticalStream):
            if hasattr(stream, 'exposureTime'):
                self._add_exposure_time_ctrl()
            if hasattr(stream, 'lightPower'):
                self._add_light_power_ctrl()

        # Check if dye control is needed
        if hasattr(stream, "excitation") and hasattr(stream, "emission"):
            self._add_dye_ctrl()

        if hasattr(stream, "auto_bc") and hasattr(stream, "intensityRange"):
            self._add_brightnesscontrast_ctrls()
            self._add_outliers_ctrls()

        # if self._has_wl(self.stream):
        #     self._add_wl_controls()

        # Set the visibility button on the stream panel
        vis = stream in tab_data_model.focussedView.value.getStreams()
        self.stream_panel.set_visible(vis)

        stream_bar.add_stream_panel(self.stream_panel, show)

    def _on_new_dye_name(self, dye_name):
        """ Assign excitation and emission wavelengths if the given name matches a known dye """
        # update the name of the stream
        self.stream.name.value = dye_name

        # update the excitation and emission wavelength
        if dye_name in dye.DyeDatabase:
            xwl, ewl = dye.DyeDatabase[dye_name]
            self._dye_xwl = xwl
            self._dye_ewl = ewl

            self.stream.excitation.value = fluo.find_best_band_for_dye(
                xwl, self.stream.excitation.choices)
            self.stream.emission.value = fluo.find_best_band_for_dye(
                ewl, self.stream.emission.choices)

            # use peak values to pick the best tint and set the wavelength colour
            xcol = wave2rgb(xwl)
            self._btn_excitation.set_colour(xcol)
            ecol = wave2rgb(ewl)
            self._btn_emission.set_colour(ecol)
            self.stream.tint.value = ecol
        else:
            self._dye_xwl = None
            self._dye_ewl = None

        # Either update the peak info, or clean up if nothing to display
        self.stream_panel.update_peak_label_fit(self._lbl_exc_peak, self._btn_excitation,
                                                self._dye_xwl, self.stream.excitation.value)
        self.stream_panel.update_peak_label_fit(self._lbl_em_peak, self._btn_emission,
                                                self._dye_ewl, self.stream.emission.value)

    # Panel state methods

    def to_locked_mode(self):
        self.stream_panel.to_locked_mode()

    def to_static_mode(self):
        self.stream_panel.to_static_mode()

    # END Panel state methods

    def sync_tint_on_emission(self, emission_wl, exitation_wl):
        """
        Set the tint to the same colour as emission, if no dye has been
         selected. If a dye is selected, it's dependent on the dye information.
        ewl ((tuple of) tuple of floats): emission wavelength
        wwl ((tuple of) tuple of floats): excitation wavelength
        """
        if self._dye_ewl is None:  # if dye is used, keep the peak wavelength
            ewl_center = fluo.get_one_center_em(emission_wl, exitation_wl)
            if self._dye_prev_ewl_center == ewl_center:
                return
            self._dye_prev_ewl_center = ewl_center
            colour = wave2rgb(ewl_center)
            logging.debug("Synchronising tint to %s", colour)
            self.stream.tint.value = colour

    # Control addition

    def _add_exposure_time_ctrl(self):
        """ Add exposute time controls to the stream panel"""

        # Assertion mainly needed for dynamic attribute recognition (i.e. exposureTime)
        assert(isinstance(self.stream, CameraStream))
        et_config = HW_SETTINGS_CONFIG['ccd']['exposureTime']

        conf = {
            'min_val': et_config["range"][0],
            'max_val': et_config["range"][1],
            'unit': self.stream.exposureTime.unit,
            'scale': et_config["scale"],
            'accuracy': et_config["accuracy"],
            }

        lbl_ctrl, value_ctrl = self.stream_panel.add_exposure_time_ctrl(
            self.stream.exposureTime.value, conf
        )

        se = SettingEntry(name="exposureTime", va=self.stream.exposureTime, stream=self.stream,
                          lbl_ctrl=lbl_ctrl, value_ctrl=value_ctrl, events=wx.EVT_SLIDER)
        self.entries[se.name] = se

    def _add_light_power_ctrl(self):
        """ Add light power controls to the stream panel """

        # Assertion mainly needed for dynamic attribute recognition (i.e. lightPower)
        assert(isinstance(self.stream, CameraStream))
        et_config = HW_SETTINGS_CONFIG['light']['power']

        conf = {
            'min_val': self.stream.lightPower.range[0],
            'max_val': self.stream.lightPower.range[1],
            'unit': self.stream.lightPower.unit,
            'scale': et_config["scale"],
            'accuracy': 4
        }

        lbl_ctrl, value_ctrl = self.stream_panel.add_light_power_ctrl(
            self.stream.lightPower.value, conf
        )

        se = SettingEntry(name="lightPower", va=self.stream.lightPower, stream=self.stream,
                          lbl_ctrl=lbl_ctrl, value_ctrl=value_ctrl, events=wx.EVT_SLIDER)
        self.entries[se.name] = se

    def _add_dye_ctrl(self):
        """ Add controls to the stream panel needed for dye emission and exitation """

        if not self.stream.excitation.readonly:
            # TODO: mark dye incompatible with the hardware with a "disabled"
            # colour in the list. (Need a special version of the combobox?)
            self.stream_panel.set_header_choices(dye.DyeDatabase.keys())
            self.stream_panel.header_change_callback = self._on_new_dye_name

        center_wl = 0
        center_wl_color = wave2rgb(center_wl)

        band = to_readable_band(self.stream.excitation.value)
        readonly = self.stream.excitation.readonly or len(self.stream.excitation.choices) <= 1

        r = self.stream_panel.add_dye_excitation_ctrl(band, readonly, center_wl_color)
        lbl_ctrl, value_ctrl, self._lbl_exc_peak, self._btn_excitation = r
        self.update_peak_label_fit(self._lbl_exc_peak, self._btn_excitation, None, band)

        if not readonly:

            choices = sorted(self.stream.excitation.choices, key=get_one_center)
            for b in choices:
                value_ctrl.Append(to_readable_band(b), b)

            def _excitation_2_va(value_ctrl=value_ctrl):
                """
                Called when the text is changed (by the user).
                returns a value to set for the VA
                """
                excitation_wavelength = value_ctrl.GetClientData(value_ctrl.GetSelection())
                self.sync_tint_on_emission(self.stream.emission.value, excitation_wavelength)
                return excitation_wavelength

            def _excitation_2_ctrl(value, value_ctrl=value_ctrl):
                """
                Called to update the widgets (text + colour display) when the VA changes.
                returns nothing
                """
                # The control can be a label or a combo-box, but we are connected only
                # when it's a combo-box
                for i in range(value_ctrl.Count):
                    if value_ctrl.GetClientData(i) == value:
                        value_ctrl.SetSelection(i)
                        break
                else:
                    logging.error("No existing label found for value %s", value)

                if self._dye_xwl is None and self._btn_excitation:
                    # no dye info? use hardware settings
                    colour = wave2rgb(fluo.get_one_center_ex(value, self.stream.emission.value))
                    self._btn_excitation.set_colour(colour)
                else:
                    self.stream_panel.update_peak_label_fit(self._lbl_exc_peak,
                                                            self._btn_excitation,
                                                            self._dye_xwl, value)

                # also update emission colour as it's dependent on excitation when multi-band
                if self._dye_ewl is None and self._btn_emission:
                    colour = wave2rgb(fluo.get_one_center_em(self.stream.emission.value, value))
                    self._btn_emission.set_colour(colour)

            se = SettingEntry(name="excitation", va=self.stream.excitation, stream=self.stream,
                              lbl_ctrl=lbl_ctrl, value_ctrl=value_ctrl, events=wx.EVT_COMBOBOX,
                              va_2_ctrl=_excitation_2_ctrl, ctrl_2_va=_excitation_2_va)

            self.entries[se.name] = se

        band = to_readable_band(self.stream.emission.value)
        readonly = self.stream.emission.readonly or len(self.stream.emission.choices) <= 1

        r = self.stream_panel.add_dye_emission_ctrl(band, readonly, center_wl_color)
        lbl_ctrl, value_ctrl, self._lbl_em_peak, self._btn_emission = r
        self.update_peak_label_fit(self._lbl_em_peak, self._btn_emission, None, band)

        if not readonly:

            choices = sorted(self.stream.emission.choices, key=get_one_center)
            for b in choices:
                value_ctrl.Append(to_readable_band(b), b)

            def _emission_2_va(value_ctrl=value_ctrl):
                """ Called when the text is changed (by the user)
                Also updates the tint as a side-effect.

                """
                emission_wavelength = value_ctrl.GetClientData(value_ctrl.GetSelection())
                self.sync_tint_on_emission(emission_wavelength, self.stream.excitation.value)
                return emission_wavelength

            def _emission_2_ctrl(value, value_ctrl=value_ctrl):
                """
                Called to update the widgets (text + colour display) when the VA changes.
                returns nothing
                """
                for i in range(value_ctrl.Count):
                    if value_ctrl.GetClientData(i) == value:
                        value_ctrl.SetSelection(i)
                        break
                else:
                    logging.error("No existing label found for value %s", value)

                if self._dye_ewl is None:  # no dye info? use hardware settings
                    colour = wave2rgb(fluo.get_one_center_em(value, self.stream.excitation.value))
                    self._btn_emission.set_colour(colour)
                else:
                    self.stream_panel.update_peak_label_fit(self._lbl_em_peak,
                                                            self._btn_emission,
                                                            self._dye_ewl, value)
                # also update excitation colour as it's dependent on emission when multiband
                if self._dye_xwl is None:
                    colour = wave2rgb(fluo.get_one_center_ex(self.stream.excitation.value, value))
                    self._btn_excitation.set_colour(colour)

            se = SettingEntry(name="emission", va=self.stream.emission, stream=self.stream,
                              lbl_ctrl=lbl_ctrl, value_ctrl=value_ctrl, events=wx.EVT_COMBOBOX,
                              va_2_ctrl=_emission_2_ctrl, ctrl_2_va=_emission_2_va)

            self.entries[se.name] = se

    def _add_brightnesscontrast_ctrls(self):
        """ Add controls for manipulating the (auto) contrast of the stream image data """

        btn_autobc, sld_outliers = self.stream_panel.add_autobc_ctrls()

        # The following closures are used to link the state of the button to the availability of
        # the slider
        def _autobc_to_va():
            enabled = btn_autobc.GetToggle()
            sld_outliers.Enable(enabled)
            return enabled

        def _va_to_autobc(enabled):
            btn_autobc.SetToggle(enabled)
            sld_outliers.Enable(enabled)

        # Store a setting entry for the auto brightness/contrast button
        se = SettingEntry(name="autobc", va=self.stream.auto_bc, stream=self.stream,
                          value_ctrl=btn_autobc, events=wx.EVT_BUTTON,
                          va_2_ctrl=_va_to_autobc, ctrl_2_va=_autobc_to_va)
        self.entries[se.name] = se

        # Store a setting entry for the outliers slider
        se = SettingEntry(name="outliers", va=self.stream.auto_bc_outliers, stream=self.stream,
                          value_ctrl=sld_outliers, events=wx.EVT_SLIDER)
        self.entries[se.name] = se

    def _add_outliers_ctrls(self):
        """ Add the controls for manipulation the outliers """

        sld_hist, txt_low, txt_high = self.stream_panel.add_outliers_ctrls()

        se = SettingEntry(name="intensity_range", va=self.stream.intensityRange, stream=self.stream,
                          value_ctrl=sld_hist, events=wx.EVT_SLIDER)
        self.entries[se.name] = se

        if hasattr(self.stream, "auto_bc"):
            # The outlier controls need to be disabled when auto brightness/contrast is active
            def _enable_outliers(autobc_enabled):
                """ En/disable the controls when the auto brightness and contrast are toggled """
                sld_hist.Enable(not autobc_enabled)
                txt_low.Enable(not autobc_enabled)
                txt_high.Enable(not autobc_enabled)

            # ctrl_2_va gets passed an identify function, to prevent the VA connector from looking
            # for a linked value control (Which we don't really need in this case. This setting
            # entry is only here so that a reference to `_enable_outliers` will be preserved).
            se = SettingEntry("_auto_bc_switch", va=self.stream.auto_bc, stream=self.stream,
                              va_2_ctrl=_enable_outliers, ctrl_2_va=lambda x: x)
            self.entries[se.name] = se

        def _get_lowi():
            intensity_rng_va = self.stream.intensityRange
            req_lv = txt_low.GetValue()
            hiv = intensity_rng_va.value[1]
            # clamp low range to max high range
            lov = max(intensity_rng_va.range[0][0], min(req_lv, hiv, intensity_rng_va.range[1][0]))
            if lov != req_lv:
                txt_low.SetValue(lov)
            return lov, hiv

        se = SettingEntry(name="low_intensity", va=self.stream.intensityRange, stream=self.stream,
                          value_ctrl=txt_low, events=wx.EVT_COMMAND_ENTER,
                          va_2_ctrl=lambda r: txt_low.SetValue(r[0]), ctrl_2_va=_get_lowi)
        self.entries[se.name] = se

        def _get_highi():
            intensity_rng_va = self.stream.intensityRange
            lov = intensity_rng_va.value[0]
            req_hv = txt_high.GetValue()
            # clamp high range to at least low range
            hiv = max(lov, intensity_rng_va.range[0][1], min(req_hv, intensity_rng_va.range[1][1]))
            if hiv != req_hv:
                txt_high.SetValue(hiv)
            return lov, hiv

        se = SettingEntry(name="high_intensity", va=self.stream.intensityRange, stream=self.stream,
                          value_ctrl=txt_high, events=wx.EVT_COMMAND_ENTER,
                          va_2_ctrl=lambda r: txt_high.SetValue(r[1]), ctrl_2_va=_get_highi)
        self.entries[se.name] = se

        def _on_histogram(hist):
            """ Display the new histogram data in the histogram slider

            This closure has an attribute assigned to it (`prev_drange`), to keep track of dynamic
            range changes. This solution was chosen, because in this manner we can avoid adding an
            extra (possibly unused) attribute to the stream controller. Since every instance of this
            function belongs to at most 1 stream (panel), we don't need to worried about conflicts.

            :param hist: ndArray of integers, the contents is a list a values in [0.0..1.0]

            TODO: don't update when folded: it's useless => unsubscribe

            """

            intensity_rng_va = self.stream.intensityRange

            if len(hist):
                # a logarithmic histogram is easier to read
                lhist = numpy.log1p(hist)
                norm_hist = lhist / float(lhist.max())
                # ndarrays work too, but slower to display
                norm_hist = norm_hist.tolist()
            else:
                norm_hist = []

            drange = (intensity_rng_va.range[0][0], intensity_rng_va.range[1][1])

            if drange != _on_histogram.prev_drange:
                _on_histogram.prev_drange = drange

                sld_hist.SetRange(drange[0], drange[1])
                # Setting the values should not be necessary as the value should have
                # already been updated via the VA update
                txt_low.SetValueRange(drange[0], drange[1])
                txt_high.SetValueRange(drange[0], drange[1])

            sld_hist.SetContent(norm_hist)
        _on_histogram.prev_drange = (self.stream.intensityRange.range[0][0],
                                     self.stream.intensityRange.range[1][1])

        # Again, we use an entry to keep a reference of the closure around
        se = SettingEntry("_histogram_switch", va=self.stream.histogram, stream=self.stream,
                          va_2_ctrl=_on_histogram, ctrl_2_va=lambda x: x)
        self.entries[se.name] = se

    # END Control addition

    @staticmethod
    def update_peak_label_fit(lbl_ctrl, col_ctrl, wl, band):
        """ Changes the colour & tooltip of the peak label based on how well it fits to the given
        band setting.

        :param lbl_ctrl: (wx.StaticText) control to update the foreground colour
        :param col_ctrl: (wx.ButtonColour) just to update the tooltip
        :param wl: (None or float) the wavelength of peak of the dye or None if no dye
        :param band: ((list of) tuple of 2 or 5 floats) the band of the hw setting

        """

        if None in (lbl_ctrl, col_ctrl):
            return

        if wl is None:
            # No dye known => no peak information
            lbl_ctrl.LabelText = u""
            lbl_ctrl.SetToolTip(None)
            col_ctrl.SetToolTipString(u"Centre wavelength colour")
        else:
            wl_nm = int(round(wl * 1e9))
            lbl_ctrl.LabelText = u"Peak at %d nm" % wl_nm
            col_ctrl.SetToolTipString(u"Peak wavelength colour")

            fit = fluo.estimate_fit_to_dye(wl, band)
            # Update colour
            colour = {fluo.FIT_GOOD: FG_COLOUR_DIS,
                      fluo.FIT_BAD: FG_COLOUR_WARNING,
                      fluo.FIT_IMPOSSIBLE: FG_COLOUR_ERROR}[fit]
            lbl_ctrl.SetForegroundColour(colour)

            # Update tooltip string
            tooltip = {
                fluo.FIT_GOOD: u"The peak is inside the band %d→%d nm",
                fluo.FIT_BAD: u"Some light might pass through the band %d→%d nm",
                fluo.FIT_IMPOSSIBLE: u"The peak is too far from the band %d→%d nm"
            }[fit]

            if isinstance(band[0], collections.Iterable):  # multi-band
                band = fluo.find_best_band_for_dye(wl, band)
            low, high = [int(round(b * 1e9)) for b in (band[0], band[-1])]
            lbl_ctrl.SetToolTipString(tooltip % (low, high))


class StreamBarController(object):
    """  Manages the streams and their corresponding stream panels in the stream bar """

    def __init__(self, tab_data, stream_bar, static=False, locked=False):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope Model
        stream_bar (StreamBar): an empty stream panel
        static (Boolean): Treat streams as static
        locked (Boolean): Don't allow to add/remove/hide/show streams
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main

        self._stream_bar = stream_bar

        self._scheduler_subscriptions = {}  # stream -> callable
        self._sched_policy = SCHED_LAST_ONE  # works well in most cases

        if stream_bar.btn_add_stream:
            self._createAddStreamActions()

        self._tab_data_model.focussedView.subscribe(self._onView, init=True)
        pub.subscribe(self.removeStream, 'stream.remove')

        # TODO: uncomment if needed
        # if hasattr(tab_data, 'opticalState'):
        # tab_data.opticalState.subscribe(self.onOpticalState, init=True)
        #
        # if hasattr(tab_data, 'emState'):
        #     tab_data.emState.subscribe(self.onEMState, init=True)

        # This attribute indicates whether live data is processed by the streams
        # in the controller, or that they just display static data.
        self.static_mode = static
        # Disable all controls
        self.locked_mode = locked

    @classmethod
    def data_to_static_streams(cls, data):
        """ Split the given data into static streams
        :param data: (list of DataArrays) Data to be split
        :return: (list) A list of Stream instances

        """

        result_streams = []

        # AR data is special => all merged in one big stream
        ar_data = []

        # Add each data as a stream of the correct type
        for d in data:
            # Hack for not displaying Anchor region data
            # TODO: store and use acquisition type with MD_ACQ_TYPE?
            if d.metadata.get(model.MD_DESCRIPTION) == "Anchor region":
                continue

            # Streams only support 2D data (e.g., no multiple channels like RGB)
            # except for spectra which have a 3rd dimensions on dim 5.
            # So if that's the case => separate into one stream per channel
            channels_data = cls._split_channels(d)

            for channel_data in channels_data:
                # TODO: be more clever to detect the type of stream
                if (
                                    model.MD_WL_LIST in channel_data.metadata or
                                    model.MD_WL_POLYNOMIAL in channel_data.metadata or
                            (len(channel_data.shape) >= 5 and channel_data.shape[-5] > 1)
                ):
                    name = channel_data.metadata.get(model.MD_DESCRIPTION, "Spectrum")
                    klass = acqstream.StaticSpectrumStream
                elif model.MD_AR_POLE in channel_data.metadata:
                    # AR data
                    ar_data.append(channel_data)
                    continue
                elif (
                            (model.MD_IN_WL in channel_data.metadata and
                                     model.MD_OUT_WL in channel_data.metadata) or
                                model.MD_USER_TINT in channel_data.metadata
                ):
                    # No explicit way to distinguish between Brightfield and Fluo,
                    # so guess it's Brightfield iif:
                    # * No tint
                    # * (and) Large band for excitation wl (> 100 nm)
                    in_wl = d.metadata[model.MD_IN_WL]
                    if (
                                    model.MD_USER_TINT in channel_data.metadata or
                                        in_wl[1] - in_wl[0] < 100e-9
                    ):
                        # Fluo
                        name = channel_data.metadata.get(model.MD_DESCRIPTION, "Filtered colour")
                        klass = acqstream.StaticFluoStream
                    else:
                        # Brightfield
                        name = channel_data.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                        klass = acqstream.StaticBrightfieldStream
                elif model.MD_IN_WL in channel_data.metadata:  # no MD_OUT_WL
                    name = channel_data.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                    klass = acqstream.StaticBrightfieldStream
                else:
                    name = channel_data.metadata.get(model.MD_DESCRIPTION, "Secondary electrons")
                    klass = acqstream.StaticSEMStream

                result_streams.append(klass(name, channel_data))

        # Add one global AR stream
        if ar_data:
            result_streams.append(acqstream.StaticARStream("Angular", ar_data))

        return result_streams

    @classmethod
    def _split_channels(cls, data):
        """ Separate a DataArray into multiple DataArrays along the 3rd dimension (channel)

        :param data: (DataArray) can be any shape
        :return: (list of DataArrays) a list of one DataArray (if no splitting is needed) or more
            (if splitting happened). The metadata is the same (object) for all the DataArrays.

        """

        # Anything to split?
        if len(data.shape) >= 3 and data.shape[-3] > 1:
            # multiple channels => split
            das = []
            for c in range(data.shape[-3]):
                das.append(data[..., c, :, :])  # metadata ref is copied
            return das
        else:
            # return just one DA
            return [data]

    def to_static_mode(self):
        self.static_mode = True

    def to_locked_mode(self):
        self.locked_mode = True

    def setSchedPolicy(self, policy):
        """
        Change the stream scheduling policy
        policy (SCHED_*): the new policy
        """
        assert policy in [SCHED_LAST_ONE, SCHED_ALL]
        self._sched_policy = policy

    def _createAddStreamActions(self):
        """ Create the compatible "add stream" actions according to the current
        microscope.
        To be executed only once, at initialisation.
        """
        # Basically one action per type of stream

        # TODO: always display the action (if it's compatible), but update
        # the disable/enable depending on the state of the chamber (iow if SEM
        # or optical button is enabled)

        # First: Fluorescent stream (for dyes)
        if (
                self._main_data_model.light and
                self._main_data_model.light_filter and
                self._main_data_model.ccd
        ):
            def fluor_capable():
                # TODO: need better way to check, maybe opticalState == STATE_DISABLED?
                enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                       guimodel.CHAMBER_UNKNOWN}
                view = self._tab_data_model.focussedView.value
                compatible = view.is_compatible(acqstream.FluoStream)
                return enabled and compatible

            # TODO: how to know it's _fluorescent_ microscope?
            # => multiple source? filter?
            self._stream_bar.add_action("Filtered colour", self._userAddFluo, fluor_capable)

        # Bright-field
        if self._main_data_model.brightlight and self._main_data_model.ccd:
            def brightfield_capable():
                enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                       guimodel.CHAMBER_UNKNOWN}
                view = self._tab_data_model.focussedView.value
                compatible = view.is_compatible(acqstream.BrightfieldStream)
                return enabled and compatible

            self._stream_bar.add_action("Bright-field", self.addBrightfield, brightfield_capable)

        def sem_capable():
            enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                   guimodel.CHAMBER_UNKNOWN}
            view = self._tab_data_model.focussedView.value
            compatible = view.is_compatible(acqstream.SEMStream)
            return enabled and compatible

        # SED
        if self._main_data_model.ebeam and self._main_data_model.sed:
            self._stream_bar.add_action("Secondary electrons", self.addSEMSED, sem_capable)
        # BSED
        if self._main_data_model.ebeam and self._main_data_model.bsd:
            self._stream_bar.add_action("Backscattered electrons", self.addSEMBSD, sem_capable)

    def _userAddFluo(self, **kwargs):
        """ Called when the user request adding a Fluo stream
        Same as addFluo, but also changes the focus to the name text field
        """
        se = self.addFluo(**kwargs)
        se.stream_panel.set_focus_on_label()

    def addFluo(self, **kwargs):
        """
        Creates a new fluorescence stream and a stream panel in the stream bar
        returns (StreamPanel): the panel created
        """
        # Find a name not already taken
        names = [s.name.value for s in self._tab_data_model.streams.value]
        for i in range(1, 1000):
            name = "Filtered colour %d" % i
            if name not in names:
                break
        else:
            logging.error("Failed to find a new unique name for stream")
            name = "Filtered colour"

        s = acqstream.FluoStream(
            name,
            self._main_data_model.ccd,
            self._main_data_model.ccd.data,
            self._main_data_model.light,
            self._main_data_model.light_filter
        )

        # TODO: automatically pick a good set of excitation/emission which is
        # not yet used by any FluoStream (or the values from the last stream
        # deleted?) Or is it better to just use the values fitting the current
        # hardware settings as it is now?

        return self._addStream(s, **kwargs)

    def addBrightfield(self, **kwargs):
        """
        Creates a new brightfield stream and panel in the stream bar
        returns (StreamPanel): the stream panel created
        """
        s = acqstream.BrightfieldStream(
            "Bright-field",
            self._main_data_model.ccd,
            self._main_data_model.ccd.data,
            self._main_data_model.brightlight
        )
        return self._addStream(s, **kwargs)

    def addSEMSED(self, **kwargs):
        """
        Creates a new SED stream and panel in the stream bar
        returns (StreamPanel): the panel created
        """
        if self._main_data_model.role == "delphi":
            # For the Delphi, the SEM stream needs to be more "clever" because
            # it needs to run a simple spot alignment every time the stage has
            # moved before starting to acquire.
            s = acqstream.AlignedSEMStream(
                "Secondary electrons",
                self._main_data_model.sed,
                self._main_data_model.sed.data,
                self._main_data_model.ebeam,
                self._main_data_model.ccd,
                self._main_data_model.stage,
                shiftebeam=acqstream.MTD_EBEAM_SHIFT
            )
            # Select between "Metadata update" and "Stage move"
            # TODO: use shiftebeam once the phenom driver supports it
        else:
            s = acqstream.SEMStream(
                "Secondary electrons",
                self._main_data_model.sed,
                self._main_data_model.sed.data,
                self._main_data_model.ebeam
            )
        return self._addStream(s, **kwargs)

    def addSEMBSD(self, **kwargs):
        """
        Creates a new backscattered electron stream and panel in the stream bar
        returns (StreamPanel): the panel created
        """
        if self._main_data_model.role == "delphi":
            # For the Delphi, the SEM stream needs to be more "clever" because
            # it needs to run a simple spot alignment every time the stage has
            # moved before starting to acquire.
            s = acqstream.AlignedSEMStream(
                "Backscattered electrons",
                self._main_data_model.bsd,
                self._main_data_model.bsd.data,
                self._main_data_model.ebeam,
                self._main_data_model.ccd,
                self._main_data_model.stage,
                shiftebeam=acqstream.MTD_EBEAM_SHIFT
            )
            # Select between "Metadata update" and "Stage move"
            # TODO: use shiftebeam once the phenom driver supports it
        else:
            s = acqstream.SEMStream(
                "Backscattered electrons",
                self._main_data_model.bsd,
                self._main_data_model.bsd.data,
                self._main_data_model.ebeam
            )
        return self._addStream(s, **kwargs)

    def addStatic(self, name, image, cls=acqstream.StaticStream, **kwargs):
        """ Creates a new static stream and stream controller

        :param name: (string)
        :param image: (DataArray)
        :param cls: (class of Stream)
        :param returns: (StreamController): the controller created

        """

        s = cls(name, image)
        return self.addStream(s, **kwargs)

    def addStream(self, stream, **kwargs):
        """ Create a stream entry for the given existing stream

        :return StreamPanel: the panel created for the stream
        """
        return self._addStream(stream, **kwargs)

    def _addStream(self, stream, add_to_all_views=False, visible=True, play=None):
        """ Add the given stream to the tab data model and appropriate views

        stream (stream.Stream): the new stream to add
        add_to_all_views (boolean): if True, add the stream to all the
            compatible views, otherwise add only to the current view.
        visible (boolean): If True, create a stream entry, otherwise adds the
            stream but do not create any entry.
        play (None or boolean): If True, immediately start it, if False, let it
            stopped, and if None, only play if already a stream is playing
        returns (StreamController or Stream): stream controller or stream (if visible
            is False) that was created

        """

        if stream not in self._tab_data_model.streams.value:
            # Insert it as first, so it's considered the latest stream used
            self._tab_data_model.streams.value.insert(0, stream)

        if add_to_all_views:
            for v in self._tab_data_model.views.value:
                if hasattr(v, "stream_classes") and isinstance(stream, v.stream_classes):
                    v.addStream(stream)
        else:
            v = self._tab_data_model.focussedView.value
            if hasattr(v, "stream_classes") and not isinstance(stream, v.stream_classes):
                warn = "Adding %s stream incompatible with the current view"
                logging.warning(warn, stream.__class__.__name__)
            v.addStream(stream)

        # TODO: create a StreamScheduler call it like self._scheduler.addStream(stream)
        # ... or simplify to only support a stream at a time
        self._scheduleStream(stream)

        # start the stream right now (if requested)
        if play is None:
            if not visible:
                play = False
            else:
                play = any(s.should_update.value for s in self._tab_data_model.streams.value)
        stream.should_update.value = play

        if visible:
            show = isinstance(stream, self._tab_data_model.focussedView.value.stream_classes)
            stream_cont = self._add_stream_cont(stream, show, static=False)

            # TODO: make StreamTree a VA-like and remove this
            logging.debug("Sending stream.ctrl.added message")
            pub.sendMessage('stream.ctrl.added',
                            streams_present=True,
                            streams_visible=self._has_visible_streams(),
                            tab=self._tab_data_model)

            return stream_cont
        else:
            return stream

    def add_acquisition_stream_cont(self, stream):
        """ Create a stream controller for the given existing stream, adapted to acquisition

        :return: StreamController

        """

        return self._add_stream_cont(stream, show=True, static=True)

    def _add_stream_cont(self, stream, show=True, locked=False, static=False):
        """ Create and add a stream controller for the given stream

        :return: (StreamController)

        """

        stream_cont = StreamController(self._stream_bar, stream, self._tab_data_model, show)

        if locked:
            stream_cont.to_locked_mode()
        elif static:
            stream_cont.to_static_mode()

        return stream_cont

    # === VA handlers

    def _onView(self, view):
        """
        Called when the current view changes
        """

        if not view:
            return

        # import sys
        # print sys.getrefcount(self)

        # hide/show the stream panels which are compatible with the view
        allowed_classes = view.stream_classes
        for e in self._stream_bar.stream_panels:
            e.Show(isinstance(e.stream, allowed_classes))
        # self.Refresh()
        self._stream_bar.fit_streams()

        # update the "visible" icon of each stream panel to match the list
        # of streams in the view
        visible_streams = view.getStreams()

        for e in self._stream_bar.stream_panels:
            e.set_visible(e.stream in visible_streams)

        logging.debug("Sending stream.ctrl message")
        pub.sendMessage('stream.ctrl',
                        streams_present=True,
                        streams_visible=self._has_visible_streams(),
                        tab=self._tab_data_model)

    def _onStreamUpdate(self, stream, updated):
        """
        Called when a stream "updated" state changes
        """
        # Ensure it's visible in the current view (if feasible)
        if updated:
            fv = self._tab_data_model.focussedView.value
            if (isinstance(stream, fv.stream_classes) and  # view is compatible
                    not stream in fv.getStreams()):
                # Add to the view
                fv.addStream(stream)
                # Update the graphical display
                for e in self._stream_bar.stream_panels:
                    if e.stream is stream:
                        e.set_visible(True)

        # This is a stream scheduler:
        # * "should_update" streams are the streams to be scheduled
        # * a stream becomes "active" when it's currently acquiring
        # * when a stream is just set to be "should_update" (by the user) it
        # should be scheduled as soon as possible

        # Note we ensure that .streams is sorted with the new playing stream as
        # the first one in the list. This means that .streams is LRU sorted,
        # which can be used for various stream information.
        # TODO: that works nicely for live tabs, but in analysis tab, this
        # never happens so the latest stream is always the same one.
        # => need more ways to change current stream (at least pick one from the
        # current view?)

        if self._sched_policy == SCHED_LAST_ONE:
            # Only last stream with should_update is active
            if not updated:
                stream.is_active.value = False
                # the other streams might or might not be updated, we don't care
            else:
                # Make sure that other streams are not updated (and it also
                # provides feedback to the user about which stream is active)
                for s, cb in self._scheduler_subscriptions.items():
                    if s != stream:
                        s.is_active.value = False
                        s.should_update.unsubscribe(cb)  # don't inform us of that change
                        s.should_update.value = False
                        s.should_update.subscribe(cb)

                # activate this stream
                # It's important it's last, to ensure hardware settings don't
                # mess up with each other.
                stream.is_active.value = True
        elif self._sched_policy == SCHED_ALL:
            # All streams with should_update are active
            stream.is_active.value = updated
        else:
            raise NotImplementedError("Unknown scheduling policy %s" % self._sched_policy)

        if updated:
            # put it back to the beginning of the list to indicate it's the
            # latest stream used
            l = self._tab_data_model.streams.value
            try:
                i = l.index(stream)
            except ValueError:
                logging.info("Stream %s is not in the stream list", stream.name)
                return
            if i == 0:
                return  # fast path
            l = [stream] + l[:i] + l[i + 1:]  # new list reordered
            self._tab_data_model.streams.value = l

    def _scheduleStream(self, stream):
        """ Add a stream to be managed by the update scheduler.
        stream (Stream): the stream to add. If it's already scheduled, it's fine.
        """
        # create an adapted subscriber for the scheduler
        def detectUpdate(updated, stream=stream):
            self._onStreamUpdate(stream, updated)
            self._updateMicroscopeStates()

        self._scheduler_subscriptions[stream] = detectUpdate
        stream.should_update.subscribe(detectUpdate)

    def _unscheduleStream(self, stream):
        """
        Remove a stream from being managed by the scheduler. It will also be
        stopped from updating.
        stream (Stream): the stream to remove. If it's not currently scheduled,
          it's fine.
        """
        stream.is_active.value = False
        stream.should_update.value = False
        if stream in self._scheduler_subscriptions:
            callback = self._scheduler_subscriptions.pop(stream)
            stream.should_update.unsubscribe(callback)

    def onOpticalState(self, state):
        # TODO: disable/enable add stream actions
        if state == guimodel.STATE_OFF:
            pass
        elif state == guimodel.STATE_ON:
            pass

    def onEMState(self, state):
        # TODO: disable/enable add stream actions
        if state == guimodel.STATE_OFF:
            pass
        elif state == guimodel.STATE_ON:
            pass

    def _updateMicroscopeStates(self):
        """
        Update the SEM/optical states based on the stream currently playing
        """
        streams = set()  # streams currently playing
        for s in self._tab_data_model.streams.value:
            if s.should_update.value:
                streams.add(s)

        # optical state = at least one stream playing is optical
        if hasattr(self._tab_data_model, 'opticalState'):
            if any(isinstance(s, acqstream.OpticalStream) for s in streams):
                self._tab_data_model.opticalState.value = guimodel.STATE_ON
            else:
                self._tab_data_model.opticalState.value = guimodel.STATE_OFF

        # sem state = at least one stream playing is sem
        if hasattr(self._tab_data_model, 'emState'):
            if any(isinstance(s, acqstream.EMStream) for s in streams):
                self._tab_data_model.emState.value = guimodel.STATE_ON
            else:
                self._tab_data_model.emState.value = guimodel.STATE_OFF

    # TODO: shall we also have a suspend/resume streams that directly changes
    # is_active, and used when the tab/window is hidden?

    def enableStreams(self, enabled, classes=acqstream.Stream):
        """
        Enable/disable the play/pause button of all the streams of the given class

        enabled (boolean): True if the buttons should be enabled, False to
         disable them.
        classes (class or list of class): classes of streams that should be
          disabled.

        Returns (set of Stream): streams which were actually enabled/disabled
        """
        streams = set()  # stream changed
        for e in self._stream_bar.stream_panels:
            s = e.stream
            if isinstance(s, classes):
                streams.add(s)
                e.enable_updated_btn(enabled)

        return streams

    def pauseStreams(self, classes=acqstream.Stream):
        """
        Pause (deactivate and stop updating) all the streams of the given class
        classes (class or list of class): classes of streams that should be
        disabled.

        Returns (set of Stream): streams which were actually paused
        """
        streams = set()  # stream paused
        for s in self._tab_data_model.streams.value:
            if isinstance(s, classes):
                if s.should_update.value:
                    streams.add(s)
                    s.is_active.value = False
                    s.should_update.value = False
                    # TODO also disable stream panel "update" button?

        return streams

    def resumeStreams(self, streams):
        """
        (Re)start (activate) streams
        streams (set of streams): Streams that will be resumed
        """
        for s in streams:
            s.should_update.value = True
            # it will be activated by the stream scheduler

    def removeStream(self, stream):
        """
        Removes the given stream.
        stream (Stream): the stream to remove
        Note: the stream panel is to be destroyed separately via the stream_bar
        It's ok to call if the stream has already been removed
        """
        # don't schedule any more
        self._unscheduleStream(stream)

        # Remove from the views
        for v in self._tab_data_model.views.value:
            if hasattr(v, "removeStream"):
                v.removeStream(stream)

        try:
            self._tab_data_model.streams.value.remove(stream)
        except ValueError:
            logging.warn("Stream not found, so not removed")

        logging.debug("Sending stream.ctrl.removed message")
        pub.sendMessage('stream.ctrl.removed',
                        streams_present=self._has_streams(),
                        streams_visible=self._has_visible_streams(),
                        tab=self._tab_data_model)

    def clear(self):
        """
        Remove all the streams (from the model and the GUI)
        """
        # We could go for each stream panel, and call removeStream(), but it's
        # as simple to reset all the lists

        # clear the graphical part
        while self._stream_bar.stream_panels:
            spanel = self._stream_bar.stream_panels[0]
            self._stream_bar.remove_stream_panel(spanel)

        # clear the interface model
        # (should handle cases where a new stream is added simultaneously)
        while self._tab_data_model.streams.value:
            stream = self._tab_data_model.streams.value.pop()
            self._unscheduleStream(stream)

            # Remove from the views
            for v in self._tab_data_model.views.value:
                if hasattr(v, "removeStream"):
                    v.removeStream(stream)

        if self._has_streams() or self._has_visible_streams():
            logging.warning("Failed to remove all streams")

        logging.debug("Sending stream.ctrl.removed message")
        pub.sendMessage('stream.ctrl.removed',
                        streams_present=False,
                        streams_visible=False,
                        tab=self._tab_data_model)

    def _has_streams(self):
        return len(self._stream_bar.stream_panels) > 0

    def _has_visible_streams(self):
        return any(s.IsShown() for s in self._stream_bar.stream_panels)
