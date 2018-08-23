# -*- coding: utf-8 -*-
"""
Created on 26 Sep 2012

@author: Éric Piel

Copyright © 2012-2015 Éric Piel, Delmic

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
import functools
import gc
import logging
import numpy
from odemis import model, util
from odemis.gui import FG_COLOUR_DIS, FG_COLOUR_WARNING, FG_COLOUR_ERROR, \
    CONTROL_COMBO, CONTROL_FLT
from odemis.gui.comp.overlay.world import RepetitionSelectOverlay
from odemis.gui.comp.stream import StreamPanel, EVT_STREAM_VISIBLE, \
    EVT_STREAM_PEAK, OPT_BTN_REMOVE, OPT_BTN_SHOW, OPT_BTN_UPDATE, OPT_BTN_TINT, \
    OPT_NAME_EDIT, OPT_BTN_PEAK
from odemis.gui.conf import data
from odemis.gui.conf.data import get_local_vas
from odemis.gui.conf.util import create_setting_entry, create_axis_entry, SettingEntry
from odemis.gui.model import dye, TOOL_SPOT, TOOL_NONE
from odemis.gui.util import call_in_wx_main, wxlimit_invocation, dead_object_wrapper
from odemis.util import fluo
from odemis.util.conversion import wave2rgb
from odemis.util.fluo import to_readable_band, get_one_center
from odemis.util.units import readable_str
import time
import wx
from wx.lib.pubsub import pub

import odemis.acq.stream as acqstream
import odemis.gui.model as guimodel

# There are two kinds of controllers:
# * Stream controller: links 1 stream <-> stream panel (cont/stream/StreamPanel)
# * StreamBar controller: links .streams VA <-> stream bar (cont/stream/StreamBar)
#   The StreamBar controller is also in charge of the scheduling of the streams.
# Stream scheduling policies: decides which streams which are with .should_update get .is_active
SCHED_LAST_ONE = 1  # Last stream which got added to the should_update set
SCHED_ALL = 2  # All the streams which are in the should_update stream
# Note: it seems users don't like ideas like round-robin, where the hardware
# keeps turn on and off, (and with fluorescence fine control must be done, to
# avoid bleaching).
# TODO: SCHED_ALL_INDIE -> Schedule at the same time all the streams which
# are independent (no emitter from a stream will affect any detector of another
# stream).

PEAK_METHOD_TO_STATE = {None: None, "gaussian": 0, "lorentzian": 1}


class StreamController(object):
    """ Manage a stream and its accompanying stream panel """

    def __init__(self, stream_bar, stream, tab_data_model, show_panel=True, view=None,
                 sb_ctrl=None):
        """
        view (MicroscopeView or None): Link stream to a view. If view is None, the stream
        will be linked to the focused view. Passing a view to the controller ensures 
        that the visibility button functions correctly when multiple views are present.
        sb_ctrl (StreamBarController or None): the StreamBarController which (typically)
          created this StreamController. Only needed for ROA repetition display.
        """

        self.stream = stream
        self.stream_bar = stream_bar
        self.view = view
        self._sb_ctrl = sb_ctrl

        self._stream_config = data.get_stream_settings_config()

        options = (OPT_BTN_REMOVE | OPT_BTN_SHOW | OPT_BTN_UPDATE)
        # Special display for dyes (aka FluoStreams)
        if isinstance(stream, (acqstream.FluoStream, acqstream.StaticFluoStream)):
            options |= OPT_BTN_TINT
            if not isinstance(stream, acqstream.StaticStream):
                options |= OPT_NAME_EDIT

        # Special display for spectrum (aka SpectrumStream)
        if isinstance(stream, acqstream.SpectrumStream) and hasattr(stream, "peak_method"):
            options |= OPT_BTN_PEAK

        self.stream_panel = StreamPanel(stream_bar, stream, options)
        # Detect when the panel is destroyed (but _not_ any of the children)
        # Make sure to Unbind ALL event bound to the stream panel!!
        self.stream_panel.Bind(wx.EVT_WINDOW_DESTROY, self._on_stream_panel_destroy,
                               source=self.stream_panel)

        self.tab_data_model = tab_data_model

        # To update the local resolution without hardware feedback
        self._resva = None
        self._resmx = None
        self._binva = None

        # Peak excitation/emission wavelength of the selected dye, to be used for peak text and
        # wavelength colour
        self._dye_xwl = None
        self._dye_ewl = None
        self._dye_prev_ewl_center = None  # ewl when tint was last changed

        self._btn_excitation = None
        self._btn_emission = None

        self._lbl_exc_peak = None
        self._lbl_em_peak = None

        # TODO: make it a list, as the dict keys are not used right now, and
        # they could be just looked up via the SettingEntry.name anyway
        self.entries = OrderedDict()  # name -> SettingEntry

        # Metadata display in analysis tab (static streams)
        if isinstance(self.stream, acqstream.StaticStream):
            self._display_metadata()

        # Add local hardware settings to the stream panel
        self._add_hw_setting_controls()

        if hasattr(stream, "emtResolution") or hasattr(stream, "detResolution"):
            self._link_resolution()
        # TODO: Add also a widget to change the "cropping" by selecting a ratio
        # (of the area of the detector), and update the ROI/resolution based on this.
        # In that case, we might be able to drop resolution from the local VA
        # completely, and only display as an information based on binning and ROI.

        self._add_stream_setting_controls()

        # Check if dye control is needed
        if hasattr(stream, "excitation") and hasattr(stream, "emission"):
            self._add_dye_ctrl()
        elif hasattr(stream, "excitation"):  # only excitation
            self._add_excitation_ctrl()
        elif hasattr(stream, "emission"):  # only emission
            self._add_emission_ctrl()

        # Add metadata button to show dialog with full list of metadata
        if isinstance(self.stream, acqstream.StaticStream):
            metadata_btn = self.stream_panel.add_metadata_button()
            metadata_btn.Bind(wx.EVT_BUTTON, self._on_metadata_btn)

        # TODO: Change the way in which BC controls are hidden (Use config in data.py)
        if hasattr(stream, "auto_bc") and hasattr(stream, "intensityRange"):
            self._add_brightnesscontrast_ctrls()
            self._add_outliers_ctrls()

        if hasattr(stream, "spectrumBandwidth"):
            self._add_wl_ctrls()
            if hasattr(self.stream, "selectionWidth"):
                self._add_selwidth_ctrl()
                
        if hasattr(self.tab_data_model, "roa"):
            self.tab_data_model.roa.subscribe(self._onROA)

            # for storing the ROI listeners of the repetition streams
            self.roi_listeners = {}  # RepetitionStream -> callable

            if hasattr(stream, "repetition"):
                # Repetition visualisation
                self.hover_stream = None  # stream for which the repetition must be displayed
                self.rep_listeners = {}  # RepetitionStream -> callable
                self.rep_ctrl = {}  # wx Control -> RepetitionStream

                # Repetition Combobox updater
                self.repct_listeners = {}  # RepetitionStream -> callable

        # Add entries from the stream config based on the dictionary
        stream_config = self._stream_config.get(type(self.stream), {})
        # present. (and get the controls via stream_cont.entries[vaname])
        # Add VAs (in same order as config)
        vas = stream_config.keys()
        vactrls = []

        if hasattr(stream, "zlevel") and hasattr(self.tab_data_model, "zpos"):
            self.tab_data_model.zpos.subscribe(self._on_z_pos)
        else:
            if "zlevel" in vas:
                vas.remove("zlevel")

        for vaname in vas:
            try:
                va = getattr(self.stream, vaname)
            except AttributeError:
                logging.debug("Skipping non existent VA %s on %s", vaname, self.stream)
                continue
            conf = stream_config.get(vaname)
            ent = self.add_setting_entry(vaname, va, hw_comp=None, conf=conf)
            vactrls.append(ent.value_ctrl)

            if vaname == "repetition":
                self.connectRepContent(self.stream, ent.value_ctrl)

        if hasattr(stream, "repetition"):
            self._add_repetition_ctrl()

        # Set the visibility button on the stream panel
        if view:
            vis = stream in view.stream_tree
        else:
            vis = stream in tab_data_model.focussedView.value.stream_tree
        self.stream_panel.set_visible(vis)
        self.stream_panel.Bind(EVT_STREAM_VISIBLE, self._on_stream_visible)

        if isinstance(stream, acqstream.SpectrumStream) and hasattr(stream, "peak_method"):
            # Set the peak button on the stream panel
            self.stream_panel.set_peak(PEAK_METHOD_TO_STATE[stream.peak_method.value])
            self.stream_panel.Bind(EVT_STREAM_PEAK, self._on_stream_peak)

        stream_bar.add_stream_panel(self.stream_panel, show_panel)

    def _display_metadata(self):
        """ 
        Display metadata for integration time, ebeam voltage, probe current and
        emission/excitation wavelength
        """
        if hasattr(self.stream, "_das"):
            # For tiled stream => use the DataArrayShadow (which is the only reliable one)
            md = self.stream._das.metadata
        elif self.stream.raw:
            md = self.stream.raw[0].metadata
        else:
            logging.warning("No raw data in stream")
            return

        # Use "integration time" instead of "exposure time" since, in some cases,
        # the dwell time is stored in MD_EXP_TIME.
        if model.MD_EXP_TIME in md:
            self.add_metadata("Integration time", md[model.MD_EXP_TIME], 's')
        elif model.MD_DWELL_TIME in md:
            self.add_metadata(model.MD_DWELL_TIME, md[model.MD_DWELL_TIME], 's')

        if model.MD_EBEAM_VOLTAGE in md:
            self.add_metadata("Acceleration voltage", md[model.MD_EBEAM_VOLTAGE], 'V')

        if model.MD_EBEAM_CURRENT in md:
            self.add_metadata("Emission current", md[model.MD_EBEAM_CURRENT], 'A')

    def pause(self):
        """ Pause (freeze) SettingEntry related control updates """
        # TODO: just call enable(False) from here? or is there any reason to
        # want to pause without showing it to the user?
        for entry in self.entries.values():
            entry.pause()

        self.stream_panel.enable(False)

    def resume(self):
        """ Resume SettingEntry related control updates """
        for entry in self.entries.values():
            entry.resume()

        self.stream_panel.enable(True)

    def enable(self, enabled):
        """ Enable or disable all SettingEntries
        """

        # FIXME: There is a possible problem that, for now, seems to work itself out: When related
        # controls dictate between themselves which ones are enabled (i.e. a toggle button,
        # dictating which slider is activated, as with auto brightness and contrast), enabling
        # all of them could/would be wrong.
        #
        # When all are enabled now, the position the toggle button is in, immediately causes
        # the right slider to be disabled again.

        for entry in (e for e in self.entries.values() if e.value_ctrl):
            entry.value_ctrl.Enable(enabled)

    def _add_hw_setting_controls(self):
        """ Add local version of linked hardware setting VAs """
        # Get the emitter and detector configurations if they exist
        hw_settings = self.tab_data_model.main.hw_settings_config
        if self.stream.emitter:
            emitter_conf = hw_settings.get(self.stream.emitter.role, {})
        else:
            emitter_conf = {}

        if self.stream.detector:
            detector_conf = hw_settings.get(self.stream.detector.role, {})
        else:
            detector_conf = {}

        add_divider = False

        # Process the hardware VAs first (emitter and detector hardware VAs are combined into one
        # attribute called 'hw_vas'
        vas_names = util.sorted_according_to(self.stream.hw_vas.keys(), emitter_conf.keys())

        for name in vas_names:
            va = self.stream.hw_vas[name]
            conf = emitter_conf.get(name, detector_conf.get(name, None))
            if conf is not None:
                logging.debug("%s hardware configuration found", name)

            self.add_setting_entry(name, va, self.stream.emitter, conf)
            add_divider = True

        # Process the emitter VAs first
        vas_names = util.sorted_according_to(self.stream.emt_vas.keys(), emitter_conf.keys())

        for name in vas_names:
            va = self.stream.emt_vas[name]
            conf = emitter_conf.get(name)
            if conf is not None:
                logging.debug("%s emitter configuration found for %s", name,
                              self.stream.emitter.role)

            self.add_setting_entry(name, va, self.stream.emitter, conf)
            add_divider = True

        # Then process the detector
        vas_names = util.sorted_according_to(self.stream.det_vas.keys(), detector_conf.keys())

        for name in vas_names:
            va = self.stream.det_vas[name]
            conf = detector_conf.get(name)
            if conf is not None:
                logging.debug("%s detector configuration found for %s", name,
                              self.stream.detector.role)

            self.add_setting_entry(name, va, self.stream.detector, conf)
            add_divider = True

        if add_divider:  # TODO: only do so, if some other controls are displayed
            self.stream_panel.add_divider()

    def _add_stream_setting_controls(self):
        """ Add control for the VAs of the stream
        Note: only the VAs which are defined in the stream_config are shown.
        """
        stream_config = self._stream_config.get(type(self.stream), {})
        for vaname, conf in stream_config.items():
            try:
                va = getattr(self.stream, vaname)
            except AttributeError:
                logging.debug("Skipping non existent VA %s on %s", vaname, self.stream)
                continue
            conf = stream_config.get(vaname)
            self.add_setting_entry(vaname, va, hw_comp=None, conf=conf)

    def add_setting_entry(self, name, va, hw_comp, conf=None):
        """ Add a name/value pair to the settings panel.

        :param name: (string): name of the value
        :param va: (VigilantAttribute)
        :param hw_comp: (Component): the component that contains this VigilantAttribute
        :param conf: ({}): Configuration items that may override default settings

        """

        se = create_setting_entry(self.stream_panel, name, va, hw_comp, conf)
        self.entries[se.name] = se

        return se

    def add_axis_entry(self, name, comp, conf=None):
        """ Add a widget to the setting panel to control an axis

        :param name: (string): name of the axis
        :param comp: (Component): the component that contains this axis
        :param conf: ({}): Configuration items that may override default settings

        """

        ae = create_axis_entry(self.stream_panel, name, comp, conf)
        self.entries[ae.name] = ae

        return ae

    def add_metadata(self, key, value, unit=None):
        """ Adds an entry representing specific metadata

        According to the metadata key, the right representation is used for the value.

        :param key: (model.MD_*) the metadata key
        :param value: (depends on the metadata) the value to display
        :param unit: (None or string) unit of the values. If necessary a SI prefix
        will be used to make the value more readable, unless None is given.
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
                    nice_str = readable_str(value, unit, 3)
                else:
                    nice_str = unicode(value)
            self.stream_panel.add_readonly_field(label, nice_str)
        except Exception:
            logging.exception("Trying to convert metadata %s", key)

    def _on_stream_panel_destroy(self, _):
        """ Remove all references to setting entries and the possible VAs they might contain
        """
        # TODO: Make stream panel creation and destruction cleaner by having the
        # StreamBarController being the main class responsible for it.

        logging.debug("Stream panel %s destroyed", self.stream.name.value)

        # Destroy references to this controller in even handlers
        # (More references are present, see getrefcount
        self.stream_panel.Unbind(wx.EVT_WINDOW_DESTROY)
        self.stream_panel.header_change_callback = None
        self.stream_panel.Unbind(EVT_STREAM_VISIBLE)
        self.stream_panel.Unbind(EVT_STREAM_PEAK)

        self._unlink_resolution()
        self._disconnectRepOverlay()
        if hasattr(self.stream, "repetition"):
            self.stream.repetition.unsubscribe(self._onStreamRep)

        self.entries = OrderedDict()

        gc.collect()

    def _on_stream_visible(self, evt):
        """ Show or hide a stream in the focussed view if the visibility button is clicked """
        if self.view:
            view = self.view
        else:
            view = self.tab_data_model.focussedView.value

        if not view:
            return

        if evt.visible:
            logging.debug("Showing stream '%s'", self.stream.name.value)
            view.addStream(self.stream)
        else:
            logging.debug("Hiding stream '%s'", self.stream.name.value)
            view.removeStream(self.stream)

    def _on_stream_peak(self, evt):
        """ Show or hide a stream in the focussed view if the peak button is clicked """
        for m, s in PEAK_METHOD_TO_STATE.items():
            if evt.state == s:
                self.stream.peak_method.value = m
                logging.debug("peak method set to %s", m)
                break
        else:
            logging.error("No peak method corresponding to state %s", evt.state)

    def _on_z_pos(self, zpos):
        self.stream.setZLevelPosition(zpos)

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
        self.update_peak_label_fit(self._lbl_exc_peak, self._btn_excitation,
                                   self._dye_xwl, self.stream.excitation.value)
        self.update_peak_label_fit(self._lbl_em_peak, self._btn_emission,
                                   self._dye_ewl, self.stream.emission.value)

    def _on_metadata_btn(self, evt):
        text = ""
        # Show empty window if no image present
        if hasattr(self.stream, '_das'):
            # For tiled streams
            r = self.stream._das
        else:
            if self.stream.raw:
                r = self.stream.raw[0]
            else:
                r = None

        if r is not None:
            shape = r.shape
            dtype = r.dtype
            md = r.metadata

            text += "Shape: %s\n" % (" x ".join(str(s) for s in shape),)
            text += "Data type: %s\n" % (dtype,)
            for key in sorted(md):
                if key == model.MD_ACQ_DATE:  # display date in readable format
                    text += "%s: %s\n" % (key, time.strftime(u"%c", time.localtime(md[key])))
                else:
                    text += "%s: %s\n" % (key, md[key])

        md_frame = self.stream_panel.create_text_frame("Metadata of %s" % self.stream.name.value, text)
        md_frame.ShowModal()

    # Panel state methods

    def to_locked_mode(self):
        self.stream_panel.to_locked_mode()

    def to_static_mode(self):
        self.stream_panel.to_static_mode()

    # END Panel state methods

    def _link_resolution(self):
        """
        Ensure that the resolution setting is recomputed when the binning/scale
          changes.
        """
        # shape and resolution.range[1] are almost always the same, but in some
        # cases like spectrometer, only the shape contains the info needed.
        if hasattr(self.stream, "emtResolution"):
            self._resva = self.stream.emtResolution
            self._resmx = self.stream.emitter.shape[:2]
            prefix = "emt"
        elif hasattr(self.stream, "detResolution"):
            self._resva = self.stream.detResolution
            self._resmx = self.stream.detector.shape[:2]
            prefix = "det"
        else:
            raise LookupError("No resolution VA found")

        if self._resva.readonly:
            logging.info("Will not update resolution, as it is readonly")

        # Find the binning/scale VA
        for n in ("Binning", "Scale"):
            fn = prefix + n
            if hasattr(self.stream, fn):
                self._binva = getattr(self.stream, fn)
                break
        else:
            logging.warning("Stream has resolution VA but no binning/scale, "
                            "so it will not be updated.")
            return

        self._binva.subscribe(self._update_resolution)
        self._resva.subscribe(self._on_resolution)

    def _unlink_resolution(self):
        if self._binva:
            self._binva.unsubscribe(self._update_resolution)
        if self._resva:
            self._resva.subscribe(self._on_resolution)

    def _update_resolution(self, scale, crop=1.0):
        """
        scale (2 ints or floats): new divisor of the resolution
        crop (0 < float <= 1): ratio of the FoV used
        """
        # if the stream is not playing, the hardware should take care of it
        if self.stream.is_active.value:
            return
        newres = (int((self._resmx[0] * crop) // scale[0]),
                  int((self._resmx[1] * crop) // scale[1]))
        newres = self._resva.clip(newres)
        logging.debug("Updated resolution to %s", newres)
        self._resva.value = newres

    def _on_resolution(self, res, crop=1.0):
        # if the stream is not playing, the hardware should take care of it
        if self.stream.is_active.value:
            return
        scale = self._binva.value
        maxres = (int((self._resmx[0] * crop) // scale[0]),
                  int((self._resmx[1] * crop) // scale[1]))
        maxres = self._resva.clip(maxres)
        newres = (min(res[0], maxres[0]), min(res[1], maxres[1]))
        if newres != res:
            logging.debug("Limiting resolution to %s", newres)
            self._resva.unsubscribe(self._on_resolution)  # to avoid infinite recursion
            self._resva.value = newres
            self._resva.subscribe(self._on_resolution)

    def sync_tint_on_emission(self, emission_wl, exitation_wl):
        """ Set the tint to the same colour as emission, if no dye has been selected. If a dye is
        selected, it's dependent on the dye information.

        :param emission_wl: ((tuple of) tuple of floats) emission wavelength
        :param exitation_wl: ((tuple of) tuple of floats) excitation wavelength

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

    def _add_selwidth_ctrl(self):
        lbl_selection_width, sld_selection_width = self.stream_panel.add_specselwidth_ctrl()

        se = SettingEntry(name="selectionwidth", va=self.stream.selectionWidth, stream=self.stream,
                          lbl_ctrl=lbl_selection_width, value_ctrl=sld_selection_width,
                          events=wx.EVT_SLIDER)
        self.entries[se.name] = se

    def _add_wl_ctrls(self):
        btn_rgbfit = self.stream_panel.add_rgbfit_ctrl()

        se = SettingEntry(name="rgbfit", va=self.stream.fitToRGB, stream=self.stream,
                          value_ctrl=btn_rgbfit, events=wx.EVT_BUTTON,
                          va_2_ctrl=btn_rgbfit.SetToggle, ctrl_2_va=btn_rgbfit.GetToggle)
        self.entries[se.name] = se

        self._sld_spec, txt_spec_center, txt_spec_bw = self.stream_panel.add_specbw_ctrls()

        se = SettingEntry(name="spectrum", va=self.stream.spectrumBandwidth, stream=self.stream,
                          value_ctrl=self._sld_spec, events=wx.EVT_SLIDER)
        self.entries[se.name] = se

        def _get_center():
            """ Return the low/high values for the bandwidth, from the requested center """

            va = self.stream.spectrumBandwidth
            ctrl = txt_spec_center

            # ensure the low/high values are always within the allowed range
            wl = va.value
            wl_rng = (va.range[0][0], va.range[1][1])

            width = wl[1] - wl[0]
            ctr_rng = wl_rng[0] + width / 2, wl_rng[1] - width / 2
            req_center = ctrl.GetValue()
            new_center = min(max(ctr_rng[0], req_center), ctr_rng[1])

            if req_center != new_center:
                # VA might not change => update value ourselves
                ctrl.SetValue(new_center)

            return new_center - width / 2, new_center + width / 2

        se = SettingEntry(name="spectrum_center", va=self.stream.spectrumBandwidth,
                          stream=self.stream, value_ctrl=txt_spec_center, events=wx.EVT_COMMAND_ENTER,
                          va_2_ctrl=lambda r: txt_spec_center.SetValue((r[0] + r[1]) / 2),
                          ctrl_2_va=_get_center)
        self.entries[se.name] = se

        def _get_bandwidth():
            """ Return the low/high values for the bandwidth, from the requested bandwidth """

            va = self.stream.spectrumBandwidth
            ctrl = txt_spec_bw

            # ensure the low/high values are always within the allowed range
            wl = va.value
            wl_rng = (va.range[0][0], va.range[1][1])

            center = (wl[0] + wl[1]) / 2
            max_width = max(center - wl_rng[0], wl_rng[1] - center) * 2
            req_width = ctrl.GetValue()
            new_width = max(0, min(req_width, max_width))

            if req_width != new_width:
                # VA might not change => update value ourselves
                ctrl.SetValue(new_width)

            return center - new_width / 2, center + new_width / 2

        se = SettingEntry(name="spectrum_bw", va=self.stream.spectrumBandwidth,
                          stream=self.stream, value_ctrl=txt_spec_bw, events=wx.EVT_COMMAND_ENTER,
                          va_2_ctrl=lambda r: txt_spec_bw.SetValue(r[1] - r[0]),
                          ctrl_2_va=_get_bandwidth)
        self.entries[se.name] = se

        # TODO: should the stream have a way to know when the raw data has changed? => just a
        # spectrum VA, like histogram VA
        self.stream.image.subscribe(self._on_new_spec_data, init=True)

    @wxlimit_invocation(0.2)
    def _on_new_spec_data(self, _):
        logging.debug("New spec data")
        # Display the global spectrum in the visual range slider
        gspec = self.stream.getMeanSpectrum()
        if len(gspec) <= 1:
            logging.warning("Strange spectrum of len %d", len(gspec))
            return

        # make it fit between 0 and 1
        if len(gspec) >= 5:
            # skip the 2 biggest peaks
            s_values = numpy.sort(gspec)
            mins, maxs = s_values[0], s_values[-3]
        else:
            mins, maxs = gspec.min(), gspec.max()

        # for spectrum, 0 has little sense, just care of the min
        if mins < maxs:
            coef = 1 / (maxs - mins)
        else:  # division by 0
            coef = 1

        gspec = (gspec - mins) * coef
        # TODO: use decorator for this (call_in_wx_main_wrapper), once this code is stable
        wx.CallAfter(dead_object_wrapper(self._sld_spec.SetContent), gspec.tolist())

    def _add_dye_ctrl(self):
        """
        Add controls to the stream panel needed for dye emission and excitation
        Specifically used when both emission and excitation are present (because
         together, more information can be extracted/presented).
        """
        # Excitation
        if not self.stream.excitation.readonly:
            # TODO: mark dye incompatible with the hardware with a "disabled"
            # colour in the list. (Need a special version of the combobox?)
            self.stream_panel.set_header_choices(dye.DyeDatabase.keys())
            self.stream_panel.header_change_callback = self._on_new_dye_name

        center_wl = fluo.get_one_center_ex(self.stream.excitation.value, self.stream.emission.value)
        self._add_excitation_ctrl(wave2rgb(center_wl))

        # Emission
        center_wl = fluo.get_one_center_em(self.stream.emission.value, self.stream.excitation.value)
        self._add_emission_ctrl(wave2rgb(center_wl))

    def _add_excitation_ctrl(self, center_wl_color=None):
        """
        Add excitation ctrl
        center_wl_color (None or 3 0<= int <= 255): RGB colour. If None, it
          will be guessed.
        """
        if center_wl_color is None:
            center_wl = fluo.get_one_center(self.stream.excitation.value)
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
                for i in range(value_ctrl.GetCount()):
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
                    self.update_peak_label_fit(self._lbl_exc_peak,
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

    def _add_emission_ctrl(self, center_wl_color=None):
        """
        Add emission ctrl
        center_wl_color (None or 3 0<= int <= 255): RGB colour. If None, it
          will be guessed.
        """
        em = self.stream.emission.value
        band = to_readable_band(em)
        readonly = self.stream.emission.readonly or len(self.stream.emission.choices) <= 1

        if center_wl_color is None:
            if isinstance(em, basestring):
                # Unknown colour or non-meaningful
                center_wl_color = None
            else:
                center_wl = fluo.get_one_center(self.stream.emission.value)
                center_wl_color = wave2rgb(center_wl)

        r = self.stream_panel.add_dye_emission_ctrl(band, readonly, center_wl_color)
        lbl_ctrl, value_ctrl, self._lbl_em_peak, self._btn_emission = r

        if isinstance(em, basestring):
            if not readonly:
                logging.error("Emission band is a string, but not readonly")
            return

        self.update_peak_label_fit(self._lbl_em_peak, self._btn_emission, None, band)

        if not readonly:

            choices = sorted(self.stream.emission.choices, key=fluo.get_one_center)
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
                for i in range(value_ctrl.GetCount()):
                    if value_ctrl.GetClientData(i) == value:
                        value_ctrl.SetSelection(i)
                        break
                else:
                    logging.error("No existing label found for value %s", value)

                if self._dye_ewl is None:  # no dye info? use hardware settings
                    colour = wave2rgb(fluo.get_one_center_em(value, self.stream.excitation.value))
                    self._btn_emission.set_colour(colour)
                else:
                    self.update_peak_label_fit(self._lbl_em_peak,
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

        btn_autobc, lbl_bc_outliers, sld_outliers = self.stream_panel.add_autobc_ctrls()

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
                          value_ctrl=sld_outliers, lbl_ctrl=lbl_bc_outliers, events=wx.EVT_SLIDER)
        self.entries[se.name] = se

    def _add_outliers_ctrls(self):
        """ Add the controls for manipulation the outliers """

        sld_hist, txt_low, txt_high = self.stream_panel.add_outliers_ctrls()

        self._prev_drange = (self.stream.intensityRange.range[0][0],
                             self.stream.intensityRange.range[1][1])

        # The standard va_2_ctrl could almost work, but in some cases, if the
        # range and value are completely changed, they need to be set in the
        # right order. The slider expects to have first the range updated, then
        # the new value. So always try to do the fast version, and if it failed,
        # use a slower version which uses the latest known values of everything.
        def _on_irange(val):
            intensity_rng_va = self.stream.intensityRange
            drange = (intensity_rng_va.range[0][0], intensity_rng_va.range[1][1])
            if drange != self._prev_drange:
                self._prev_drange = drange

                sld_hist.SetRange(drange[0], drange[1])
                # Setting the values should not be necessary as the value should have
                # already been updated via the VA update
                txt_low.SetValueRange(drange[0], drange[1])
                txt_high.SetValueRange(drange[0], drange[1])

            if not all(drange[0] <= v <= drange[1] for v in val):
                # Value received is not fitting the current range, which is a
                # sign that it's too old. Getting the latest one should fix it.
                cval = intensity_rng_va.value
                logging.debug("Updating latest irange %s to %s", val, cval)
                val = cval

            sld_hist.SetValue(val)
            # TODO: also do the txt_low & txt_high .SetValue?

        se = SettingEntry(name="intensity_range", va=self.stream.intensityRange, stream=self.stream,
                          value_ctrl=sld_hist, events=wx.EVT_SLIDER, va_2_ctrl=_on_irange)
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
            hist (nd.array of N values): the content of the histogram, ordered
              by bins.
            """
            # TODO: don't update when folded: it's useless => unsubscribe

            if len(hist):
                # a logarithmic histogram is easier to read
                lhist = numpy.log1p(hist)
                lhistmx = lhist.max()
                if lhistmx == 0:  # avoid dividing by 0
                    lhistmx = 1
                norm_hist = lhist / lhistmx
                # ndarrays work too, but slower to display
                norm_hist = norm_hist.tolist()
            else:
                norm_hist = []

            sld_hist.SetContent(norm_hist)

        # Again, we use an entry to keep a reference of the closure around
        se = SettingEntry("_histogram", va=self.stream.histogram, stream=self.stream,
                          va_2_ctrl=_on_histogram, ctrl_2_va=lambda x: x)
        self.entries[se.name] = se

    def _add_repetition_ctrl(self):
        """
        Add the repetition/pixelSize/fuzzing settings for the RepetitionStreams
        """
        va_config = OrderedDict((
            ("repetition", {
                "control_type": CONTROL_COMBO,
                "choices": set((1, 1)),  # Actually, it's immediately replaced by _onStreamRep()
                "accuracy": None,  # never simplify the numbers
            }),
            ("pixelSize", {
                "control_type": CONTROL_FLT,
            }),
            ("fuzzing", {
                "tooltip": u"Scans each pixel over their complete area, instead of only scanning the center the pixel area.",
            }),
        ))

        roa_ctrls = []
        for vaname, conf in va_config.items():
            try:
                va = getattr(self.stream, vaname)
            except AttributeError:
                logging.debug("Skipping non existent VA %s on %s", vaname, self.stream)
                continue
            ent = self.add_setting_entry(vaname, va, hw_comp=None, conf=conf)

            if vaname == "repetition":
                self._rep_ctrl = ent.value_ctrl
                # Update the combo box choices based on the current repetition value
                self.stream.repetition.subscribe(self._onStreamRep, init=True)

            if vaname in ("repetition", "pixelSize"):
                roa_ctrls.append(ent.value_ctrl)

        if hasattr(self.tab_data_model, "roa") and self._sb_ctrl:
            self._connectRepOverlay(roa_ctrls)

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
            col_ctrl.SetToolTip(u"Centre wavelength colour")
        else:
            wl_nm = int(round(wl * 1e9))
            lbl_ctrl.LabelText = u"Peak at %d nm" % wl_nm
            col_ctrl.SetToolTip(u"Peak wavelength colour")

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
            lbl_ctrl.SetToolTip(tooltip % (low, high))

    # Repetition visualisation on focus/hover methods
    # The global rule (in order):
    # * if mouse is hovering an entry (repetition or pixel size) => display
    #   repetition for this stream
    # * if an entry of stream has focus => display repetition for this stream
    # * don't display repetition

    def _connectRepOverlay(self, controls):
        """
        Connects the stream VAs and controls to display the repetition overlay
          when needed.
        Warning: must be called from the main GUI thread.
        stream (RepetitionStream)
        controls (list of wx.Controls): controls that are used to change the
          repetition/pixel size info
        """
        self._roa_ctrls = set()  # all wx Controls which should activate visualisation
        self._hover_rep = False  # True if current mouse is hovering a control

        # repetition VA not needed: if it changes, either roi or pxs also change
        self.stream.roi.subscribe(self._onRepStreamVA)
        self.stream.pixelSize.subscribe(self._onRepStreamVA)

        for c in controls:
            self._roa_ctrls.add(c)
            c.Bind(wx.EVT_SET_FOCUS, self._onRepFocus)
            c.Bind(wx.EVT_KILL_FOCUS, self._onRepFocus)
            c.Bind(wx.EVT_ENTER_WINDOW, self._onRepHover)
            c.Bind(wx.EVT_LEAVE_WINDOW, self._onRepHover)
            # To handle the combobox, which send leave window events when the
            # mouse goes into the text ctrl child of the combobox.
            if hasattr(c, "TextCtrl"):
                tc = c.TextCtrl
                self._roa_ctrls.add(tc)
                tc.Bind(wx.EVT_ENTER_WINDOW, self._onRepHover)

    def _disconnectRepOverlay(self):
        if hasattr(self.stream, "roi"):
            self.stream.roi.unsubscribe(self._onRepStreamVA)
        if hasattr(self.stream, "pixelSize"):
            self.stream.pixelSize.unsubscribe(self._onRepStreamVA)

    @wxlimit_invocation(0.1)
    def _updateRepOverlay(self):
        """
        Ensure the repetition overlay is displaying the right thing
        """
        # Show iff: the mouse is hovering a roa_ctrls, or one roa_ctrl has the focus
        focused = wx.Window.FindFocus()
        show_rep = self._hover_rep or (focused in self._roa_ctrls)
        if show_rep:
            rep = self.stream.repetition.value
            if isinstance(self.stream, acqstream.ARStream):
                style = RepetitionSelectOverlay.FILL_POINT
            else:
                style = RepetitionSelectOverlay.FILL_GRID
            self._sb_ctrl.show_roa_repetition(self.stream, rep, style)
        else:
            self._sb_ctrl.show_roa_repetition(self.stream, None)

    def _onRepStreamVA(self, _):
        """
        Called when one of the repetition VAs of a RepetitionStream is modified
        stream (RepetitionStream)
        val (value): new VA value, unused
        """
        self._updateRepOverlay()

    def _onRepFocus(self, evt):
        """
        Called when any control related to the repetition get/loose focus
        """
        self._updateRepOverlay()
        evt.Skip()

    def _onRepHover(self, evt):
        if evt.Entering():
            self._hover_rep = True
        elif evt.Leaving():
            self._hover_rep = False
        else:
            logging.warning("neither leaving nor entering")
        self._updateRepOverlay()
        evt.Skip()

    # Repetition combobox content updater

    def _onStreamRep(self, rep):
        """
        Called when the repetition VAs of a RepetitionStream is modified.
        Recalculate the repetition presets according to the repetition ratio
        """
        ratio = rep[1] / rep[0]

        # Create the entries:
        choices = [(1, 1)]  # 1 x 1 should always be there

        # Add a couple values below/above the current repetition
        for m in (1 / 4, 1 / 2, 1, 2, 4, 10):
            x = int(round(rep[0] * m))
            y = int(round(x * ratio))
            choices.append((x, y))

        # remove non-possible ones
        rng = self.stream.repetition.range
        def is_compatible(c):
            # TODO: it's actually further restricted by the current size of
            # the ROI (and the minimum size of the pixelSize), so some of the
            # big repetitions might actually not be valid. It's not a big
            # problem as the VA setter will silently limit the repetition
            return (rng[0][0] <= c[0] <= rng[1][0] and
                    rng[0][1] <= c[1] <= rng[1][1])

        choices = set(choice for choice in choices if is_compatible(choice))
        choices = sorted(choices)

        # replace the old list with this new version
        self._rep_ctrl.Clear()
        for choice in choices:
            self._rep_ctrl.Append(u"%s x %s px" % choice, choice)


class StreamBarController(object):
    """
    Manages the streams and their corresponding stream panels in the stream bar.
    In particular it takes care of:
      * Defining the menu entries for adding streams
      * Play/pause the streams, via a "scheduler"
      * Play/pause the spot stream in spot mode
      * Connects the ROA to the .roi of RepetitionStream
      * Shows the repetition overlay when the repetition setting is focused
    """

    def __init__(self, tab_data, stream_bar, static=False, locked=False, ignore_view=False,
                 view_ctrl=None):
        """
        :param tab_data: (MicroscopyGUIData) the representation of the microscope Model
        :param stream_bar: (StreamBar) an empty stream bar
        :param static: (bool) Treat streams as static (can't play/pause)
        :param locked: (bool) Don't allow to add/remove/hide/show streams
        :param ignore_view: (bool) don't change the visible panels on focussed
           view change. If False and not locked, it will show the panels
           compatible with the focussed view. If False and locked, it will show
           the panels which are seen in the focussed view.
        :param view_ctrl (ViewPortController or None): Only required to show
           repetition and on the SPARC ensure the right view is shown.
        """

        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._stream_bar = stream_bar

        # Never allow SEM and CLi Stream to play with spot mode (because they are
        # spatial, so it doesn't make sense to see just one point), and force
        # AR, Spectrum, and Monochromator streams with spot mode (because the
        # first two otherwise could be playing with beam "blanked", which shows
        # weird signal, and the last one would be very slow to update).
        # TODO: it could make sense to allow AR and Spectrum stream to play
        # while doing a normal scan, but the scheduler would need to allow playing
        # on spatial stream simultaneously (just the SE?) and force to play it
        # when not in spot mode. (for now, we keep it simple)
        self._spot_incompatible = (acqstream.SEMStream, acqstream.CLStream, acqstream.OpticalStream)
        self._spot_required = (acqstream.ARStream, acqstream.SpectrumStream,
                               acqstream.MonochromatorSettingsStream, acqstream.ScannedTCSettingsStream)
        tab_data.tool.subscribe(self.on_tool_change)

        self._view_controller = view_ctrl

        self.stream_controllers = []
        self._roi_listeners = {}  # (Repetition)Stream -> callable
        self._show_reps = {}  # Stream -> (rep, style)

        # This attribute indicates whether live data is processed by the streams
        # in the controller, or that they just display static data.
        self.static_mode = static
        # Disable all controls
        self.locked_mode = locked

        self.menu_actions = collections.OrderedDict()  # title => callback

        self._scheduler_subscriptions = {}  # stream -> callable
        self._sched_policy = SCHED_LAST_ONE  # works well in most cases

        self._createAddStreamActions()

        # Don't hide or show stream panel when the focused view changes
        self.ignore_view = ignore_view
        self._prev_view = None

        self._tab_data_model.focussedView.subscribe(self._onView, init=True)
        # FIXME: don't use pubsub events, but either wxEVT or VAs. For now every
        # stream controller is going to try to remove the stream.
        pub.subscribe(self.removeStream, 'stream.remove')

        # Stream preparation future
        self.preparation_future = model.InstantaneousFuture()

        # If any stream already present: listen to them in the scheduler (but
        # don't display)
        for s in tab_data.streams.value:
            logging.debug("Adding stream present at init to scheduler: %s", s)
            self._scheduleStream(s)

        # TODO: use the same behaviour on the SPARC
        self._spot_stream = None
        if hasattr(tab_data, "spotStream") and tab_data.spotStream:
            self._spot_stream = tab_data.spotStream
            self._scheduleStream(self._spot_stream)

        self._stream_config = data.get_stream_settings_config()

    def pause(self):
        """ Pause (=freeze) SettingEntry related control updates """
        for stream_controller in self.stream_controllers:
            stream_controller.pause()

    def resume(self):
        """ Resume SettingEntry related control updates """
        for stream_controller in self.stream_controllers:
            stream_controller.resume()

    def enable(self, enabled):
        """ Enable or disable all the streambar controls """
        for stream_controller in self.stream_controllers:
            stream_controller.enable(enabled)

        self._stream_bar.btn_add_stream.Enable(enabled)

    # unused (but in test case)
    def get_actions(self):
        return self.menu_actions

    # TODO need to have actions enabled/disabled depending on the context:
    #  * if microscope is off/pause => disabled
    #  * if focused view is not about this type of stream => disabled
    #  * if there can be only one stream of this type, and it's already present
    #    => disabled
    def add_action(self, title, callback, check_enabled=None):
        """ Add an action to the stream menu

        It's added at the end of the list. If an action with the same title exists, it is replaced.

        :param title: (string) Text displayed in the menu
        :param callback: (callable) function to call when the action is selected

        """

        if self._stream_bar.btn_add_stream is None:
            logging.error("No add button present!")
        else:
            logging.debug("Adding %s action to stream panel", title)
            self.menu_actions[title] = callback
            self._stream_bar.btn_add_stream.add_choice(title, callback, check_enabled)

    def remove_action(self, title):
        """
        Remove the given action, if it exists. Otherwise does nothing
        title (string): name of the action to remove
        """
        if title in self.menu_actions:
            logging.debug("Removing %s action from stream panel", title)
            del self.menu_actions[title]
            self._stream_bar.btn_add_stream.set_choices(self.menu_actions)

    def to_static_mode(self):
        self.static_mode = True

    def to_locked_mode(self):
        self.locked_mode = True

    def setSchedPolicy(self, policy):
        """
        Change the stream scheduling policy
        policy (SCHED_*): the new policy
        """
        assert policy in (SCHED_LAST_ONE, SCHED_ALL)
        self._sched_policy = policy

    def _createAddStreamActions(self):
        """
        Create the compatible "add stream" actions according to the current
        microscope.
        To be executed only once, at initialisation.
        """
        pass

    def _userAddFluo(self, **kwargs):
        """ Called when the user request adding a Fluo stream
        Same as addFluo, but also changes the focus to the name text field
        """
        se = self.addFluo(**kwargs)
        se.stream_panel.set_focus_on_label()

    def _ensure_power_non_null(self, stream):
        """
        Ensure the emtPower VA of a stream is not 0. The goal is to make sure
        that when the stream will start playing, directly some data will be
        obtained (to avoid confusing the user). In practice, if it is 0, a small
        value (10%) will be set.
        stream (Stream): the stream with a emtPower VA
        """
        if stream.emtPower.value > 0:
            return

        # Automatically picks some power if it was at 0 W (due to the stream
        # defaulting to the current hardware settings), so that the user is not
        # confused when playing the stream and nothing happens.
        if hasattr(stream.emtPower, "range"):
            stream.emtPower.value = stream.emtPower.range[1] * 0.1
        elif hasattr(stream.emtPower, "choices"):
            stream.emtPower.value = sorted(stream.emtPower.choices)[1]
        else:
            logging.info("Stream emtPower has no info about min/max")

    def addFluo(self, **kwargs):
        """
        Creates a new fluorescence stream and a stream panel in the stream bar
        returns (StreamController): the panel created
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
            self._main_data_model.light_filter,
            focuser=self._main_data_model.focus,
            opm=self._main_data_model.opm,
            detvas={"exposureTime"},
            emtvas={"power"}
        )
        self._ensure_power_non_null(s)

        # TODO: automatically pick a good set of excitation/emission which is
        # not yet used by any FluoStream (or the values from the last stream
        # deleted?) Or is it better to just use the values fitting the current
        # hardware settings as it is now?

        return self._add_stream(s, **kwargs)

    def addBrightfield(self, **kwargs):
        """
        Creates a new brightfield stream and panel in the stream bar
        returns (StreamController): the stream panel created
        """
        s = acqstream.BrightfieldStream(
            "Bright-field",
            self._main_data_model.ccd,
            self._main_data_model.ccd.data,
            self._main_data_model.brightlight,
            focuser=self._main_data_model.focus,
            opm=self._main_data_model.opm,
            detvas={"exposureTime"},
            emtvas={"power"}
        )
        self._ensure_power_non_null(s)

        return self._add_stream(s, **kwargs)

    def addDarkfield(self, **kwargs):
        """
        Creates a new darkfield stream and panel in the stream bar
        returns (StreamController): the stream panel created
        """
        # Note: it's also displayed as 'brightfield' stream
        s = acqstream.BrightfieldStream(
            "Dark-field",
            self._main_data_model.ccd,
            self._main_data_model.ccd.data,
            self._main_data_model.backlight,
            focuser=self._main_data_model.focus,
            opm=self._main_data_model.opm,
            detvas={"exposureTime"},
            emtvas={"power"}
        )
        self._ensure_power_non_null(s)

        return self._add_stream(s, **kwargs)

    def addConfocal(self, detector, **kwargs):
        """
        Creates a new confocal stream and panel in the stream bar
        detector (Detector): the photo-detector to use
        returns (StreamController): the stream panel created
        """
        # As there is only one stream per detector, we can put its VAs directly
        # instead of them being a local copy. This also happens to work around
        # an issue with detecting IntContinuous in local VAs.
        # set_stream contains the shared settings for the laser_mirror and light
        s = acqstream.ScannedFluoStream(
            "Confocal %s" % (detector.name,),
            detector,
            detector.data,
            self._main_data_model.light,
            self._main_data_model.laser_mirror,
            self._main_data_model.light_filter,
            focuser=self._main_data_model.focus,
            opm=self._main_data_model.opm,
            hwdetvas=get_local_vas(detector, self._main_data_model.hw_settings_config),
            setting_stream=self._tab_data_model.confocal_set_stream,
        )

        return self._add_stream(s, **kwargs)

    def addSEMSED(self, **kwargs):
        """ Creates a new SED stream and panel in the stream bar
        return (StreamController) The controller created for the SED stream
        """
        return self._add_sem_stream("Secondary electrons",
                                    self._main_data_model.sed, **kwargs)

    def addSEMBSD(self, **kwargs):
        """
        Creates a new backscattered electron stream and panel in the stream bar
        returns (StreamPanel): the panel created
        """
        return self._add_sem_stream("Backscattered electrons",
                                    self._main_data_model.bsd, **kwargs)

    def addEBIC(self, **kwargs):
        """
        Creates a new EBIC stream and panel in the stream bar
        returns (StreamPanel): the panel created
        """
        return self._add_sem_stream("EBIC", self._main_data_model.ebic, **kwargs)

    def addScannedTCSettings(self, **kwargs):
        """
        Creates a new ScannedTCSettingStream and panel in the stream bar.
        returns (StreamPanel): the panel created
        """
        s = acqstream.ScannedTCSettingsStream(
            "FLIM",
            self._main_data_model.tc_detector,
            self._main_data_model.light,
            self._main_data_model.laser_mirror,
            self._main_data_model.time_correlator,
            scanner_extra=self._main_data_model.tc_scanner,
            tc_detector_live=self._main_data_model.tc_detector_live,
            opm=self._main_data_model.opm,
            emtvas=get_local_vas(self._main_data_model.light, self._main_data_model.hw_settings_config),
        )

        stream_cont = self._add_stream(s, add_to_view=True, **kwargs)

        # TODO: should we really not show this visible button? Left-over from SPARC.
        # Currently, as it can only be seen on its own view, it actually makes sense.
        stream_cont.stream_panel.show_visible_btn(False)

        return stream_cont

    def _add_sem_stream(self, name, detector, **kwargs):
        if self._main_data_model.role == "delphi":
            # For the Delphi, the SEM stream needs to be more "clever" because
            # it needs to run a simple spot alignment every time the stage has
            # moved before starting to acquire.
            s = acqstream.AlignedSEMStream(
                name,
                detector,
                detector.data,
                self._main_data_model.ebeam,
                self._main_data_model.ccd,
                self._main_data_model.stage,
                self._main_data_model.focus,
                focuser=self._main_data_model.ebeam_focus,
                opm=self._main_data_model.opm,
                shiftebeam=acqstream.MTD_EBEAM_SHIFT
            )
        else:
            s = acqstream.SEMStream(
                name,
                detector,
                detector.data,
                self._main_data_model.ebeam,
                focuser=self._main_data_model.ebeam_focus,
                opm=self._main_data_model.opm
            )

        # If the detector already handles brightness and contrast, don't do it by default
        # TODO: check if it has .applyAutoContrast() instead (once it's possible)
        if (s.intensityRange.range == ((0, 0), (255, 255)) and
            model.hasVA(detector, "contrast") and
            model.hasVA(detector, "brightness")):
            s.auto_bc.value = False
            s.intensityRange.value = (0, 255)

        return self._add_stream(s, **kwargs)

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
        Must be run in the main GUI thread.

        :return StreamPanel: the panel created for the stream
        """
        return self._add_stream(stream, **kwargs)

    def _add_stream(self, stream, add_to_view=False, visible=True, play=None):
        """ Add the given stream to the tab data model and appropriate views

        Args:
            stream (Stream): the new stream to add

        Kwargs:
            add_to_view (boolean or View): if True, add the stream to all the compatible views,
                if False add to the current view, otherwise, add to the given view.
            visible (boolean): If True, create a stream entry, otherwise adds the stream but do not
                create any entry.
            play (None or boolean): If True, immediately start it, if False, let it stopped, and if
                None, only play if already a stream is playing.

        Returns:
            (StreamController or Stream): the stream controller or stream (if visible is False) that
                was created

        """

        if stream not in self._tab_data_model.streams.value:
            # Insert it as first, so it's considered the latest stream used
            self._tab_data_model.streams.value.insert(0, stream)

        fview = self._tab_data_model.focussedView.value

        if add_to_view is True:
            for v in self._tab_data_model.views.value:
                if hasattr(v, "stream_classes") and isinstance(stream, v.stream_classes):
                    v.addStream(stream)
        else:
            if add_to_view is False:
                v = fview
            else:
                v = add_to_view
            if hasattr(v, "stream_classes") and not isinstance(stream, v.stream_classes):
                warn = "Adding %s stream incompatible with the view %s"
                logging.warning(warn, stream.__class__.__name__, v.name.value)
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
            linked_view = None
            if self.ignore_view:  # Always show the stream panel
                show_panel = True
                if not isinstance(add_to_view, bool):
                    linked_view = v
            elif self.locked_mode:  # (and don't ignore_view)
                # Show the stream panel iif the view is showing the stream
                show_panel = stream in fview.getStreams()
            else:  # (standard = not locked and don't ignore_view)
                # Show the stream panel iif the view could display the stream
                show_panel = isinstance(stream, fview.stream_classes)

            stream_cont = self._add_stream_cont(stream,
                                                show_panel,
                                                locked=self.locked_mode,
                                                static=self.static_mode,
                                                view=linked_view,
                                                )
            return stream_cont
        else:
            return stream

    def _add_stream_cont(self, stream, show_panel=True, locked=False, static=False,
                         view=None):
        """ Create and add a stream controller for the given stream

        :return: (StreamController)

        """

        stream_cont = StreamController(self._stream_bar, stream, self._tab_data_model,
                                       show_panel, view, sb_ctrl=self)

        if locked:
            stream_cont.to_locked_mode()
        elif static:
            stream_cont.to_static_mode()

        self.stream_controllers.append(stream_cont)

        # Only connect the .roi of RepetitionStreams (ie, has .repetition)
        if hasattr(stream, "repetition") and hasattr(self._tab_data_model, "roa"):
            self._connectROI(stream)

        return stream_cont

    # === VA handlers

    def _onView(self, view):
        """ Handle the changing of the focused view """

        if not view or self.ignore_view:
            return

        if self.locked_mode:
            # hide/show the stream panels of the streams visible in the view
            allowed_streams = view.getStreams()
            for e in self._stream_bar.stream_panels:
                e.Show(e.stream in allowed_streams)
        else:
            # hide/show the stream panels which are compatible with the view
            allowed_classes = view.stream_classes
            for e in self._stream_bar.stream_panels:
                e.Show(isinstance(e.stream, allowed_classes))

        self._stream_bar.fit_streams()

        # update the "visible" icon of each stream panel to match the list
        # of streams in the view
        if self._prev_view is not None:
            self._prev_view.stream_tree.flat.unsubscribe(self._on_visible_streams)
        view.stream_tree.flat.subscribe(self._on_visible_streams, init=True)
        self._prev_view = view

    def _on_visible_streams(self, flat):
        # Convert the DataProjections into Stream
        visible_streams = [s if isinstance(s, acqstream.Stream) else s.stream for s in flat]

        for e in self._stream_bar.stream_panels:
            e.set_visible(e.stream in visible_streams)

    def _onStreamUpdate(self, stream, updated):
        """
        Called when a stream "updated" state changes
        """
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

        # Don't mess too much with the spot stream => just copy "should_update"
        if hasattr(self._tab_data_model, "spotStream"):
            if stream is self._tab_data_model.spotStream:
                stream.is_active.value = updated
                return

        if self._sched_policy == SCHED_LAST_ONE:
            # Only last stream with should_update is active
            if not updated:
                self._prepareAndActivate(stream, False)
                # the other streams might or might not be updated, we don't care
            else:
                # FIXME: hack to not stop the spot stream => different scheduling policy?
                spots = getattr(self._tab_data_model, "spotStream", None)
                # Make sure that other streams are not updated (and it also
                # provides feedback to the user about which stream is active)
                for s, cb in self._scheduler_subscriptions.items():
                    if s != stream and s is not spots:
                        self._prepareAndActivate(s, False)
                        s.should_update.unsubscribe(cb)  # don't inform us of that change
                        s.should_update.value = False
                        s.should_update.subscribe(cb)

                # prepare and activate this stream
                # It's important it's last, to ensure hardware settings don't
                # mess up with each other.
                self._prepareAndActivate(stream, True)
        elif self._sched_policy == SCHED_ALL:
            # All streams with should_update are active
            # TODO: there is probably no way it works as-is (and it's never used anyway)
            self._prepareAndActivate(stream, updated)
        else:
            raise NotImplementedError("Unknown scheduling policy %s" % self._sched_policy)

        if updated:
            # Activate or deactivate spot mode based on what the stream needs
            # Note: changing tool is fine, because it will only _pause_ the
            # other streams, and we will not come here again.
            if isinstance(stream, self._spot_incompatible):
                logging.info("Stopping spot mode because %s starts", stream)
                if self._tab_data_model.tool.value == TOOL_SPOT:
                    self._tab_data_model.tool.value = TOOL_NONE
                    spots = self._tab_data_model.spotStream
                    spots.is_active.value = False

            elif isinstance(stream, self._spot_required):
                logging.info("Starting spot mode because %s starts", stream)
                spots = self._tab_data_model.spotStream
                was_active = spots.is_active.value
                self._tab_data_model.tool.value = TOOL_SPOT

                # Hack: to be sure the settings of the spot streams are correct
                # (because the concurrent stream might have changed them, cf
                # Monochromator), we stop/start it each time a stream plays
                if was_active:
                    # FIXME: when switching from one Monochromator stream to
                    # another one, it seems to mess up the resolution on the
                    # first time => needs to be done after the old stream is paused
                    # and before the new one plays
                    logging.debug("Resetting spot mode")
                    spots.is_active.value = False
                    spots.is_active.value = True

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

    def on_tool_change(self, tool):
        """ Pause the SE and CLI streams when the Spot mode tool is activated """
        if hasattr(self._tab_data_model, 'spotStream'):
            spots = self._tab_data_model.spotStream
            if tool == TOOL_SPOT:
                # Make sure the streams non compatible are not playing
                paused_st = self.pauseStreams(self._spot_incompatible)
                spots.should_update.value = True
            else:
                # Make sure that the streams requiring the spot are not playing
                paused_st = self.pauseStreams(self._spot_required)
                spots.should_update.value = False

    def _prepareAndActivate(self, stream, updated):
        """
        Prepare and activate the given stream.
        stream (Stream): the stream to prepare and activate.
        """
        # Cancel the previous preparation in case it's still trying
        self.preparation_future.cancel()
        if updated:
            self._main_data_model.is_preparing.value = True
            self.preparation_future = stream.prepare()
            cb_on_prepare = functools.partial(self._canActivate, stream)
            self.preparation_future.add_done_callback(cb_on_prepare)
        else:
            stream.is_active.value = False

    @call_in_wx_main
    def _canActivate(self, stream, future):
        self._main_data_model.is_preparing.value = False
        if future.cancelled():
            logging.debug("Not activating %s as its preparation was cancelled", stream.name.value)
        elif not stream.should_update.value:
            logging.debug("Not activating %s as it is now paused", stream.name.value)
        else:
            try:
                future.result()
            except Exception:
                logging.exception("Preparation of %s failed, but will activate the stream anyway",
                                  stream.name.value)
            else:
                logging.debug("Preparation of %s completed, will activate it", stream.name.value)
            stream.is_active.value = True

        # Mostly to avoid keeping ref to the stream (hold in the callback)
        self.preparation_future = model.InstantaneousFuture()

    def _scheduleStream(self, stream):
        """ Add a stream to be managed by the update scheduler.
        stream (Stream): the stream to add. If it's already scheduled, it's fine.
        """
        # create an adapted subscriber for the scheduler
        def detectUpdate(updated, stream=stream):
            self._onStreamUpdate(stream, updated)

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
        """ Removes the given stream

        Args:
            stream (Stream): the stream to remove

        Note:
            The stream panel is to be destroyed separately via the stream_bar.

        It's ok to call if the stream has already been removed.

        """

        # don't schedule any more
        self._unscheduleStream(stream)
        self._disconnectROI(stream)

        # Remove from the views
        for v in self._tab_data_model.views.value:
            if hasattr(v, "removeStream"):
                # logging.warn("> %s > %s", v, stream)
                v.removeStream(stream)

        # Remove from the list of streams
        try:
            self._tab_data_model.streams.value.remove(stream)
            logging.debug("%s removed", stream)
        except ValueError:
            # Can happen, as all the tabs receive this event
            logging.info("%s not found, so not removed", stream)

        # Remove the corresponding stream controller
        for sc in self.stream_controllers:
            if sc.stream is stream:
                self.stream_controllers.remove(sc)
                break
        else:
            logging.info("Stream controller of %s not found", stream)

        # Explicitly collect garbage, because for some reason not all stream controllers were
        # collected immediately, which would keep a reference to the Stream object, which in turn
        # would prevent the Stream render thread from terminating.
        gc.collect()

    def clear(self):
        """
        Remove all the streams (from the model and the GUI)
        """
        # We could go for each stream panel, and call removeStream(), but it's
        # as simple to reset all the lists

        # clear the graphical part
        self._stream_bar.clear()

        # clear the interface model
        # (should handle cases where a new stream is added simultaneously)
        while self._tab_data_model.streams.value:
            stream = self._tab_data_model.streams.value.pop()
            self._unscheduleStream(stream)
            self._disconnectROI(stream)

            # Remove from the views
            for v in self._tab_data_model.views.value:
                if hasattr(v, "removeStream"):
                    v.removeStream(stream)

        # Clear the stream controller
        self.stream_controllers = []

        gc.collect()

        if self._has_streams() or self._has_visible_streams():
            logging.warning("Failed to remove all streams")

    def _has_streams(self):
        return len(self._stream_bar.stream_panels) > 0

    def _has_visible_streams(self):
        return any(s.IsShown() for s in self._stream_bar.stream_panels)

    # ROA synchronisation methods
    # When the ROA is updated:
    # 1. The ROA is copied to the "main" stream
    # 2. The ROI of the main stream is copied back to the ROA (to give feedback)
    # 3. The ROA is copied to the other streams
    #
    # When a ROI of a stream is changed (but not due to ROA change):
    # 1. It's copied to the ROA
    # 2. It's copied to the other streams
    #
    # Updating the ROI requires a bit of care, because the streams might
    # update back their ROI with a modified value. To avoid loops, we disable
    # and re-enable before and after each (direct) change.

    def _connectROI(self, stream):
        """
        Connect the .roi of the (repetition) stream to the global ROA
        """
        # First, start with the same ROI as the global ROA
        stream.roi.value = self._tab_data_model.roa.value
        self._tab_data_model.roa.subscribe(self._onROA)

        listener = functools.partial(self._onStreamROI, stream)
        stream.roi.subscribe(listener)
        self._roi_listeners[stream] = listener


    def _disconnectROI(self, stream):
        """
        Remove ROI subscriptions for the stream.
        It's fine to call for a stream which is not connected to ROI.
        stream (Stream): the stream being removed
        """
        if stream in self._roi_listeners:
            logging.debug("Removing %s from ROI subscriptions", stream)
            # Removing the callable from the roi_listeners should be sufficient,
            # as the callable should be unreferenced and free'd, which should drop
            # it from the subscriber... but let's make everything explicit.
            stream.roi.unsubscribe(self._roi_listeners[stream])
            del self._roi_listeners[stream]

    def _disableROISub(self):
        self._tab_data_model.roa.unsubscribe(self._onROA)
        for s, listener in self._roi_listeners.items():
            s.roi.unsubscribe(listener)

    def _enableROISub(self):
        self._tab_data_model.roa.subscribe(self._onROA)
        for s, listener in self._roi_listeners.items():
            s.roi.subscribe(listener)

    def _onStreamROI(self, stream, roi):
        """
        Called when the ROI of a stream is changed.
        Used to update the global ROA.
        stream (Stream): the stream which is changed
        roi (4 floats): roi
        """
        self._disableROISub()
        try:
            # Set the global ROA to the new ROI (defined by the user)
            logging.debug("Setting ROA from %s to %s", stream.name.value, roi)
            self._tab_data_model.roa.value = roi

            # Update all the other streams to (almost) the same ROI too
            for s in self._roi_listeners:
                if s is not stream:
                    logging.debug("Setting ROI of %s to %s", s.name.value, roi)
                    s.roi.value = roi
        finally:
            self._enableROISub()

    def _onROA(self, roa):
        """
        Called when the ROA is changed
        To synchronise global ROA -> streams ROI
        """
        self._disableROISub()
        try:
            # ROI-related streams, in LRU order => the first one is the "main" stream
            roi_ss = [s for s in self._tab_data_model.streams.value
                      if s in self._roi_listeners]

            for i, s in enumerate(roi_ss):
                logging.debug("Setting ROI of %s to %s", s.name.value, roa)
                s.roi.value = roa
                if i == 0:
                    # Read back the ROA from the "main" stream (= latest played)
                    logging.debug("Setting ROA back from %s to %s",
                                  s.name.value, s.roi.value)
                    roa = s.roi.value
                    self._tab_data_model.roa.value = roa

        finally:
            self._enableROISub()

    def show_roa_repetition(self, stream, rep, style=None):
        """
        Request the views to show (or not) the repetition for the given stream
        Depending on requests from other streams, it might or not be actually done
        stream (Stream): stream for which the display is requested
        rep (None or tuple of 2 ints): if None, repetition is hidden
        style (overlay.FILL_*): type of repetition display
        """
        # Update the list of streams that want to show their repetition
        if rep:
            self._show_reps[stream] = (rep, style)
        else:
            self._show_reps.pop(stream, None)  # remove iff present

        # Pick the best repetition: use the latest stream which has something to show
        rep, style = None, None  # default is "no show"
        for s in self._tab_data_model.streams.value:
            if s in self._show_reps:
                rep, style = self._show_reps[s]
                break

        if not self._view_controller:
            # Too bad, but this can happen if the GUI has no viewport controller
            # (ie, the AcquisitionDialog
            logging.info("Can not show repetition, as view controller is unknown")
            return

        # Update all the views which care (ie, spatial/SEM/Optical view)
        # TODO: instead, look at each canvas which has a allowed_modes TOOL_ROA?
        views = self._tab_data_model.visible_views.value
        em_views = [v for v in views if (issubclass(acqstream.EMStream, v.stream_classes) or
                                         issubclass(acqstream.OpticalStream, v.stream_classes))]
        for em_view in em_views:
            vp = self._view_controller.get_viewport_by_view(em_view)
            vp.canvas.show_repetition(rep, style)


class SecomStreamsController(StreamBarController):
    """
    Controls the streams for the SECOM and DELPHI live view
    """

    def _createAddStreamActions(self):
        """ Create the compatible "add stream" actions according to the current microscope.

        To be executed only once, at initialisation.
        """

        # Basically one action per type of stream

        # TODO: always display the action (if it's compatible), but disable the
        # play/pause button if the microscope state doesn't allow it (IOW if SEM
        # or optical button is disabled)

        # First: Fluorescent stream (for dyes)
        if (
                self._main_data_model.light and
                self._main_data_model.light_filter and
                self._main_data_model.ccd
        ):
            def fluor_capable():
                enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                       guimodel.CHAMBER_UNKNOWN}
                view = self._tab_data_model.focussedView.value
                compatible = view.is_compatible(acqstream.FluoStream)
                return enabled and compatible

            # TODO: how to know it's _fluorescent_ microscope?
            # => multiple source? filter?
            self.add_action("Filtered colour", self._userAddFluo, fluor_capable)

        # Bright-field & Dark-field are almost identical but for the emitter
        def brightfield_capable():
            enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                   guimodel.CHAMBER_UNKNOWN}
            view = self._tab_data_model.focussedView.value
            compatible = view.is_compatible(acqstream.BrightfieldStream)
            return enabled and compatible

        if self._main_data_model.brightlight and self._main_data_model.ccd:
            self.add_action("Bright-field", self.addBrightfield, brightfield_capable)

        if self._main_data_model.backlight and self._main_data_model.ccd:
            self.add_action("Dark-field", self.addDarkfield, brightfield_capable)

        def confocal_capable(detector):
            enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                   guimodel.CHAMBER_UNKNOWN}
            # Only allow one stream with the detector at a time
            present = any(s.detector is detector for s in self._tab_data_model.streams.value)
            view = self._tab_data_model.focussedView.value
            compatible = view.is_compatible(acqstream.FluoStream)
            return enabled and compatible and not present

        if self._main_data_model.laser_mirror:
            ds = sorted(self._main_data_model.photo_ds, key=lambda d: d.name)
            for pd in ds:
                act = functools.partial(self.addConfocal, detector=pd)
                cap = functools.partial(confocal_capable, detector=pd)
                self.add_action("Confocal %s" % (pd.name,), act, cap)

        def sem_capable():
            """ Check if focussed view is compatible with a SEM stream """
            enabled = self._main_data_model.chamberState.value in {guimodel.CHAMBER_VACUUM,
                                                                   guimodel.CHAMBER_UNKNOWN}
            view = self._tab_data_model.focussedView.value
            compatible = view.is_compatible(acqstream.SEMStream)
            return enabled and compatible

        def flim_capable():
            enabled = (self._main_data_model.time_correlator is not None)
            view = self._tab_data_model.focussedView.value
            compatible = view.is_compatible(acqstream.ScannedTCSettingsStream)

            # Check if there is a FLIM stream already
            flim_already = any(isinstance(s, acqstream.ScannedTCSettingsStream)
                    for s in self._tab_data_model.streams.value)

            return enabled and compatible and not flim_already

        # SED
        if self._main_data_model.ebeam and self._main_data_model.sed:
            self.add_action("Secondary electrons", self.addSEMSED, sem_capable)
        # BSED
        if self._main_data_model.ebeam and self._main_data_model.bsd:
            self.add_action("Backscattered electrons", self.addSEMBSD, sem_capable)
        # EBIC
        if self._main_data_model.ebeam and self._main_data_model.ebic:
            self.add_action("EBIC", self.addEBIC, sem_capable)
        # FLIM
        if self._main_data_model.time_correlator is not None:
            self.add_action("FLIM", self.addScannedTCSettings, flim_capable)

    def _onStreamUpdate(self, stream, updated):
        # When a stream starts playing, ensure it's visible in at least one view
        if updated:
            fv = self._tab_data_model.focussedView.value
            if stream not in fv.stream_tree.flat.value and stream is not self._spot_stream:
                # if the stream is hidden in the current focused view, then unhide
                # it everywhere
                for v in self._tab_data_model.views.value:
                    if isinstance(stream, acqstream.SEMStream) and (not v.is_compatible(acqstream.SEMStream)):
                        continue
                    elif isinstance(stream, acqstream.FluoStream) and (not v.is_compatible(acqstream.FluoStream)):
                        continue
                    else:
                        if stream not in v.stream_tree.flat.value:
                            # make sure we don't display old data
                            str_img = stream.image.value
                            if ((str_img is not None) and
                                (str_img.metadata.get(model.MD_POS, (0, 0)) != self._main_data_model.stage)):
                                stream.image.value = None
                            v.addStream(stream)
        super(SecomStreamsController, self)._onStreamUpdate(stream, updated)
        self._updateMicroscopeStates()

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


class SparcStreamsController(StreamBarController):
    """
    Controls the streams for the SPARC acquisition tab
    In addition to the standard controller it:
     * Knows how to create the special RepeptionStreams
     * Updates the .acquisitionStreams when a stream is added/removed
     * Connects tab_data.useScanStage to the streams

    Note: tab_data.spotStream should be in tab_data.streams
    """

    def __init__(self, tab_data, *args, **kwargs):
        super(SparcStreamsController, self).__init__(tab_data, *args, **kwargs)

        # Each stream will be created both as a SettingsStream and a MDStream
        # When the SettingsStream is deleted, automatically remove the MDStream
        tab_data.streams.subscribe(self._on_streams)

        # Connect the global useScanStage VA to each RepStream
        tab_data.useScanStage.subscribe(self._updateScanStage)

    def _createAddStreamActions(self):
        """ Create the compatible "add stream" actions according to the current microscope.

        To be executed only once, at initialisation.
        """
        main_data = self._main_data_model

        # Basically one action per type of stream
        if main_data.ebic:
            self.add_action("EBIC", self.addEBIC)
        if main_data.cld:
            self.add_action("CL intensity", self.addCLIntensity)
        if main_data.ccd and main_data.lens and model.hasVA(main_data.lens, "polePosition"):
            # Some simple SPARC have a CCD which can only do rough chamber view,
            # but no actual AR acquisition. This is indicate by not having any
            # polePosition VA on the optical path.
            self.add_action("Angle-resolved", self.addAR)

        # On the SPARCv2, there is potentially 4 different ways to acquire a
        # spectrum: two spectrographs, with each two ports. In practice, there
        # are never more than 2 at the same time.
        sptms = [main_data.spectrometer, main_data.spectrometer_int]
        sptms = [s for s in sptms if s is not None]
        for sptm in sptms:
            if len(sptms) == 1:
                actname = "Spectrum"
            else:
                actname = "Spectrum with %s" % (sptm.name,)
            act = functools.partial(self.addSpectrum, name=actname, detector=sptm)
            self.add_action(actname, act)

        if main_data.monochromator:
            self.add_action("Monochromator", self.addMonochromator)

    def _on_streams(self, streams):
        """ Remove MD streams from the acquisition view that have one or more sub streams missing
        Also remove the ROI subscriptions and wx events.

        Args:
            streams (list of streams): The streams currently used in this tab
        """
        semcls = self._tab_data_model.semStream

        # Clean-up the acquisition streams
        for acqs in self._tab_data_model.acquisitionStreams.copy():
            if not isinstance(acqs, acqstream.MultipleDetectorStream):
                if acqs not in streams:
                    logging.debug("Removing stream %s from acquisition too",
                                  acqs.name.value)
                    self._tab_data_model.acquisitionStreams.discard(acqs)
            else:
                # Are all the sub streams of the MDStreams still there?
                for ss in acqs.streams:
                    # If not, remove the MD stream
                    if ss is not semcls and ss not in streams:
                        if isinstance(ss, acqstream.SEMStream):
                            logging.warning("Removing stream because %s is gone!", ss)
                        logging.debug("Removing acquisition stream %s because %s is gone",
                                      acqs.name.value, ss.name.value)
                        self._tab_data_model.acquisitionStreams.discard(acqs)
                        break

    def _getAffectingSpectrograph(self, comp):
        """
        Find which spectrograph matters for the given component (ex, spectrometer)
        comp (Component): the hardware which is affected by a spectrograph
        return (None or Component): the spectrograph affecting the component
        """
        cname = comp.name
        main_data = self._main_data_model
        for spg in (main_data.spectrograph, main_data.spectrograph_ded):
            if spg is not None and cname in spg.affects.value:
                return spg
        else:
            logging.warning("No spectrograph found affecting component %s", cname)
            # spg should be None, but in case it's an error in the microscope file
            # and actually, there is a spectrograph, then use that one
            return main_data.spectrograph

    def addEBIC(self, **kwargs):
        # Need to use add_to_view=True to force only showing on the right
        # view (and not onself.cnvs.update_drawing() the current view)
        # TODO: should it be handled the same way as CLIntensity? (ie, respects
        # the ROA)
        return super(SparcStreamsController, self).addEBIC(add_to_view=True, **kwargs)

    def _add_sem_stream(self, name, detector, **kwargs):

        # Only put some local VAs, the rest should be global on the SE stream
        emtvas = get_local_vas(self._main_data_model.ebeam, self._main_data_model.hw_settings_config)
        emtvas &= {"resolution", "dwellTime", "scale"}

        s = acqstream.SEMStream(
            name,
            detector,
            detector.data,
            self._main_data_model.ebeam,
            focuser=self._main_data_model.ebeam_focus,
            emtvas=emtvas,
            detvas=get_local_vas(detector, self._main_data_model.hw_settings_config),
        )

        # If the detector already handles brightness and contrast, don't do it by default
        # TODO: check if it has .applyAutoContrast() instead (once it's possible)
        if (s.intensityRange.range == ((0, 0), (255, 255)) and
            model.hasVA(detector, "contrast") and
            model.hasVA(detector, "brightness")):
            s.auto_bc.value = False
            s.intensityRange.value = (0, 255)

        # add the stream to the acquisition set
        self._tab_data_model.acquisitionStreams.add(s)

        return self._add_stream(s, **kwargs)

    def _addRepStream(self, stream, mdstream, axes, **kwargs):
        """
        Display and connect a new RepetitionStream to the GUI
        stream (RepetitionStream): freshly baked stream
        mdstream (MDStream): corresponding new stream for acquisition
        vas (list of str): name of VAs entries to create (in addition to standard one,
          such as local HW VAs and B/C control)
        axes (dict axis name -> Component): axis entries to create
        kwargs (dict): to be passed to _add_stream()
        return (StreamController): the new stream controller
        """
        if model.hasVA(stream, "useScanStage"):
            stream.useScanStage.value = self._tab_data_model.useScanStage.value

        stream_cont = self._add_stream(stream, add_to_view=True, **kwargs)
        stream_cont.stream_panel.show_visible_btn(False)

        # add the acquisition stream to the acquisition set
        self._tab_data_model.acquisitionStreams.add(mdstream)

        stream_config = self._stream_config.get(type(stream), {})

        # Add Axes (in same order as config)
        axes_names = util.sorted_according_to(axes.keys(), stream_config.keys())
        for axisname in axes_names:
            comp = axes[axisname]
            if comp is None:
                logging.debug("Skipping axis %s for non existent component",
                              axisname)
                continue
            if axisname not in comp.axes:
                logging.debug("Skipping non existent axis %s on component %s",
                              axisname, comp.name)
                continue
            conf = stream_config.get(axisname)
            stream_cont.add_axis_entry(axisname, comp, conf)

        return stream_cont

    def addAR(self):
        """ Create a camera stream and add to to all compatible viewports """

        main_data = self._main_data_model
        ar_stream = acqstream.ARSettingsStream(
            "Angle-resolved",
            main_data.ccd,
            main_data.ccd.data,
            main_data.ebeam,
            main_data.pol_analyzer,
            sstage=main_data.scan_stage,
            opm=self._main_data_model.opm,
            # TODO: add a focuser for the SPARCv2?
            detvas=get_local_vas(main_data.ccd, self._main_data_model.hw_settings_config),
        )
        # Make sure the binning is not crazy (especially can happen if CCD is shared for spectrometry)
        if model.hasVA(ar_stream, "detBinning"):
            b = ar_stream.detBinning.value
            if b[0] != b[1] or b[0] > 16:
                ar_stream.detBinning.value = ar_stream.detBinning.clip((1, 1))
                ar_stream.detResolution.value = ar_stream.detResolution.range[1]

        # Create the equivalent MDStream
        sem_stream = self._tab_data_model.semStream
        sem_ar_stream = acqstream.SEMARMDStream("SEM AR",
                                                [sem_stream, ar_stream])

        return self._addRepStream(ar_stream, sem_ar_stream,
                                  axes={"band": main_data.light_filter}
                                  )

    def addCLIntensity(self):
        """ Create a CLi stream and add to to all compatible viewports """

        main_data = self._main_data_model
        cli_stream = acqstream.CLSettingsStream(
            "CL intensity",
            main_data.cld,
            main_data.cld.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            focuser=self._main_data_model.ebeam_focus,
            opm=self._main_data_model.opm,
            emtvas={"dwellTime"},
            detvas=get_local_vas(main_data.cld, self._main_data_model.hw_settings_config),
        )

        # Special "safety" feature to avoid having a too high gain at start
        if hasattr(cli_stream, "detGain"):
            cli_stream.detGain.value = cli_stream.detGain.range[0]

        # Create the equivalent MDStream
        sem_stream = self._tab_data_model.semStream
        sem_cli_stream = acqstream.SEMMDStream("SEM CLi",
                                               [sem_stream, cli_stream])

        # Need to pick the right filter wheel (if there is one)
        axes = {}
        for fw in (main_data.cl_filter, main_data.light_filter):
            if fw is None:
                continue
            if main_data.cld.name in fw.affects.value:
                axes["band"] = fw

        ret = self._addRepStream(cli_stream, sem_cli_stream,
                                  axes=axes,
                                  play=False
                                  )

        # With CLi, often the user wants to get the whole area, same as the survey.
        # But it's not very easy to select all of it, so do it automatically.
        # (after the controller creation, to automatically set the ROA too)
        if cli_stream.roi.value == acqstream.UNDEFINED_ROI:
            cli_stream.roi.value = (0, 0, 1, 1)
        return ret

    def addSpectrum(self, name=None, detector=None):
        """
        Create a Spectrum stream and add to to all compatible viewports
        name (str or None): name of the stream to be created
        detector (Detector or None): the spectrometer to use. If None, it will
          use the one with "spectrometer" as role.
        """
        main_data = self._main_data_model

        if name is None:
            name = "Spectrum"

        if detector is None:
            detector = main_data.spectrometer
        logging.debug("Adding spectrum stream for %s", detector.name)

        spg = self._getAffectingSpectrograph(detector)
        spec_stream = acqstream.SpectrumSettingsStream(
            name,
            detector,
            detector.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            opm=self._main_data_model.opm,
            # emtvas=get_local_vas(main_data.ebeam, self._main_data_model.hw_settings_config), # no need
            detvas=get_local_vas(detector, self._main_data_model.hw_settings_config),
        )

        # Create the equivalent MDStream
        sem_stream = self._tab_data_model.semStream
        sem_spec_stream = acqstream.SEMSpectrumMDStream("SEM " + name,
                                                        [sem_stream, spec_stream])

        # TODO: all the axes, including the filter band should be local. The
        # band should be set to the pass-through by default
        axes = {"wavelength": spg,
                "grating": spg,
                "slit-in": spg,
               }

        # Also add light filter for the spectrum stream if it affects the detector
        for fw in (main_data.cl_filter, main_data.light_filter):
            if fw is None:
                continue
            if detector.name in fw.affects.value:
                axes["band"] = fw

        return self._addRepStream(spec_stream, sem_spec_stream,
                                  axes=axes,
                                  )

    def addMonochromator(self):
        """ Create a Monochromator stream and add to to all compatible viewports """

        main_data = self._main_data_model
        spg = self._getAffectingSpectrograph(main_data.spectrometer)
        monoch_stream = acqstream.MonochromatorSettingsStream(
            "Monochromator",
            main_data.monochromator,
            main_data.monochromator.data,
            main_data.ebeam,
            sstage=main_data.scan_stage,
            opm=self._main_data_model.opm,
            emtvas={"dwellTime"},
            detvas=get_local_vas(main_data.monochromator, self._main_data_model.hw_settings_config),
        )

        # Create the equivalent MDStream
        sem_stream = self._tab_data_model.semStream
        sem_monoch_stream = acqstream.SEMMDStream("SEM Monochromator",
                                                  [sem_stream, monoch_stream])

        # TODO: all the axes, including the filter band should be local. The
        # band should be set to the pass-through by default
        axes = {"wavelength": spg,
                "grating": spg,
                "slit-in": spg,
                "slit-monochromator": spg,
               }

        # Also add light filter if it affects the detector
        for fw in (main_data.cl_filter, main_data.light_filter):
            if fw is None:
                continue
            if main_data.monochromator.name in fw.affects.value:
                axes["band"] = fw

        return self._addRepStream(monoch_stream, sem_monoch_stream,
                                  axes=axes,
                                  play=False
                                  )

    def _onStreamUpdate(self, stream, updated):

        # Make sure that the stream is visible in every (compatible) view
        if updated:
            fv = self._tab_data_model.focussedView.value
            if (isinstance(stream, fv.stream_classes) and  # view is compatible
                stream not in fv.stream_tree):
                # Add to the view
                fv.addStream(stream)
                # Update the graphical display
                for e in self._stream_bar.stream_panels:
                    if e.stream is stream:
                        e.set_visible(True)

        super(SparcStreamsController, self)._onStreamUpdate(stream, updated)

        # Make sure the current view is compatible with the stream playing
        if updated:
            self._view_controller.focusViewWithStream(stream)

    def _updateScanStage(self, use):
        """
        Updates the useScanStage VAs of each RepStream based on the global
          useScanStage VA of the tab.
        """
        for s in self._tab_data_model.streams.value:
            if model.hasVA(s, "useScanStage"):
                s.useScanStage.value = use
