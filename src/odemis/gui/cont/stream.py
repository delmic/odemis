# -*- coding: utf-8 -*-
"""
Created on 26 Sep 2012

@author: Éric Piel, Philip Winkler

Copyright © 2012-2022 Éric Piel, Delmic

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

import gc
import locale
import logging
import time
from builtins import str
from collections import OrderedDict
from collections.abc import Iterable

import numpy
import odemis.acq.stream as acqstream
import wx
from odemis import model, util
from odemis.acq.stream import MeanSpectrumProjection
from odemis.gui import (CONTROL_COMBO, CONTROL_FLT, FG_COLOUR_DIS,
                        FG_COLOUR_ERROR, FG_COLOUR_WARNING)
from odemis.gui.comp.buttons import ImageTextButton
from odemis.gui.comp.foldpanelbar import FoldPanelBar
from odemis.gui.comp.overlay.repetition_select import RepetitionSelectOverlay
from odemis.gui.comp.toggle import GraphicalToggleButtonControl
from odemis.gui.comp.stream_panel import (OPT_BTN_PEAK, OPT_BTN_REMOVE, OPT_BTN_SHOW,
                                          OPT_BTN_TINT, OPT_BTN_UPDATE, OPT_FIT_RGB,
                                          OPT_NAME_EDIT, OPT_NO_COLORMAPS,
                                          StreamPanel)
from odemis.gui.conf import data
from odemis.gui.conf.data import get_hw_config
from odemis.gui.conf.util import (SettingEntry, create_axis_entry,
                                  create_setting_entry)
from odemis.gui.evt import EVT_STREAM_PEAK, EVT_STREAM_VISIBLE
from odemis.gui.model import dye
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.util import fluo
from odemis.util.conversion import wavelength2rgb
from odemis.util.fluo import get_one_center, to_readable_band
from odemis.util.units import readable_str

# There are two kinds of controllers:
# * Stream controller: links 1 stream <-> stream panel (cont/stream/StreamPanel)
# * StreamBar controller: links .streams VA <-> stream bar (cont/stream/StreamBar)

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

        self._stream_config = data.get_stream_settings_config().get(type(stream), {})

        options = (OPT_BTN_REMOVE | OPT_BTN_SHOW | OPT_BTN_UPDATE)
        # Add tint/colormap option if there is a tint VA and adjust based on the stream type
        if hasattr(stream, "tint"):
            options |= OPT_BTN_TINT
            if isinstance(stream, acqstream.RGBStream):
                options |= OPT_NO_COLORMAPS
            # (Temporal)SpectrumStreams *with spectrum data* accept the FIT_TO_RGB option
            if isinstance(stream, acqstream.SpectrumStream) and stream.raw[0].shape[0] > 1:
                options |= OPT_FIT_RGB

        # Allow changing the name of dyes (aka FluoStreams)
        if isinstance(stream, acqstream.FluoStream):
            options |= OPT_NAME_EDIT

        # Special display for spectrum (aka SpectrumStream)
        if isinstance(stream, acqstream.SpectrumStream) and hasattr(stream, "peak_method"):
            options |= OPT_BTN_PEAK

        self.stream_panel = StreamPanel(stream_bar, stream, options)
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

        self.entries = []  # SettingEntry
        self._disabled_entries = set()  # set of SettingEntry objects

        # Metadata display in analysis tab (static streams)
        if isinstance(self.stream, acqstream.StaticStream):
            self._display_metadata()

        # Add current power VA setting entry (instead of using emtVas)
        if hasattr(stream, "power"):
            va = self.stream.power
            name = "power"
            if hasattr(self.stream, "light"):
                comp = self.stream.light
            else:
                comp = self.stream.emitter

            if comp is not None:
                hw_settings = self.tab_data_model.main.hw_settings_config
                emitter_conf = get_hw_config(comp, hw_settings)
            else:
                emitter_conf = {}

            conf = emitter_conf.get(name)
            if conf is not None:
                logging.debug("%s emitter configuration found for %s", name,
                              comp.role)

            self.add_setting_entry(name, va, comp, conf)

        # Add local hardware settings to the stream panel
        self._add_hw_setting_controls()

        if hasattr(stream, "emtResolution") or hasattr(stream, "detResolution"):
            self._link_resolution()
        # TODO: Add also a widget to change the "cropping" by selecting a ratio
        # (of the area of the detector), and update the ROI/resolution based on this.
        # In that case, we might be able to drop resolution from the local VA
        # completely, and only display as an information based on binning and ROI.

        self._add_stream_setting_controls()

        if len(self.entries) > 0:  # TODO: only do so, if some other controls are displayed after
            self.stream_panel.add_divider()

        num_settings_entries = len(self.entries)
        if hasattr(self.stream, "axis_vas"):
            self._add_axis_controls()

        # Check if dye control is needed
        if hasattr(stream, "excitation") and hasattr(stream, "emission"):
            self._add_dye_ctrl()
        elif hasattr(stream, "excitation"):  # only excitation
            self._add_excitation_ctrl()
        elif hasattr(stream, "emission"):  # only emission
            self._add_emission_ctrl()

        if len(self.entries) > num_settings_entries:  # TODO: also only if other controls after
            self.stream_panel.add_divider()

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
            self.mean_spec_proj = MeanSpectrumProjection(self.stream)
            self.mean_spec_proj.image.subscribe(self._on_new_spec_data, init=True)
            if hasattr(self.stream, "selectionWidth"):
                self._add_selwidth_ctrl()

        if hasattr(stream, "zIndex") and hasattr(self.tab_data_model, "zPos"):
            self.stream.zIndex.subscribe(self._on_z_index)
            self.tab_data_model.zPos.subscribe(self._on_z_pos, init=True)

        if hasattr(stream, "zIndex") and hasattr(stream, "max_projection"):
            self.zindex_se = None
            for se in self.entries:
                if se.vigilattr is self.stream.zIndex:
                    self._zindex_se = se
                    break

            if self.zindex_se is None:
                logging.warning("Stream has zIndex but no corresponding stream entry found.")
            self.stream.max_projection.subscribe(self._on_max_projection)

        if hasattr(stream, "repetition"):
            self._add_repetition_ctrl()

        # Set the visibility button on the stream panel
        vis = False
        if view:
            vis = stream in view.stream_tree
        elif tab_data_model.focussedView.value:
            vis = stream in tab_data_model.focussedView.value.stream_tree
        self.stream_panel.set_visible(vis)
        self.stream_panel.Bind(EVT_STREAM_VISIBLE, self._on_stream_visible)

        if isinstance(stream, acqstream.SpectrumStream) and hasattr(stream, "peak_method"):
            # Set the peak button on the stream panel
            self.stream_panel.set_peak(PEAK_METHOD_TO_STATE[stream.peak_method.value])
            self.stream_panel.Bind(EVT_STREAM_PEAK, self._on_stream_peak)

        stream_bar.add_stream_panel(self.stream_panel, show_panel,
                                    on_destroy=self._on_stream_panel_destroy)

    def _on_stream_panel_destroy(self):
        """ Remove all references to setting entries and the possible VAs they might contain
        """
        logging.debug("Stream panel %s destroyed", self.stream.name.value)

        # Destroy references to this controller in even handlers
        # (More references are present, see getrefcount
        self.stream_panel.header_change_callback = None
        self.stream_panel.Unbind(EVT_STREAM_VISIBLE)
        self.stream_panel.Unbind(EVT_STREAM_PEAK)

        self._unlink_resolution()
        self._disconnectRepOverlay()
        if hasattr(self.stream, "repetition"):
            self.stream.repetition.unsubscribe(self._onStreamRep)

        # Unsubscribe from all the VAs
        # TODO: it seems that in some cases we still receive a call after destruction
        for entry in self.entries:
            entry.disconnect()

        self.entries = []

        if self._sb_ctrl:
            self._sb_ctrl.removeStream(self.stream)

        gc.collect()

    def _display_metadata(self):
        """
        Display metadata for integration time, ebeam voltage, probe current and
        emission/excitation wavelength
        """
        mds = self.stream.getRawMetadata()
        if not mds:
            logging.warning("No raw data in stream")
            return

        md = mds[0]

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
        for entry in self.entries:
            entry.pause()
            if entry.value_ctrl and entry.value_ctrl.IsEnabled():
                entry.value_ctrl.Enable(False)
                self._disabled_entries.add(entry)

        self.stream_panel.enable(False)

    def resume(self):
        """ Resume SettingEntry related control updates """
        for entry in self.entries:
            entry.resume()
            if entry in self._disabled_entries:
                entry.value_ctrl.Enable(True)
                self._disabled_entries.remove(entry)

        self.stream_panel.enable(True)

    def pauseStream(self):
        """ Pause (deactivate and stop updating) the stream """
        if self.stream.should_update.value:
            self.stream.is_active.value = False
            self.stream.should_update.value = False

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

        for entry in self.entries:
            if entry.value_ctrl:
                entry.value_ctrl.Enable(enabled)

    def _add_hw_setting_controls(self):
        """ Add local version of linked hardware setting VAs """
        # Get the emitter and detector configurations if they exist
        hw_settings = self.tab_data_model.main.hw_settings_config
        if self.stream.emitter:
            emitter_conf = get_hw_config(self.stream.emitter, hw_settings)
        else:
            emitter_conf = {}

        if self.stream.detector:
            detector_conf = get_hw_config(self.stream.detector, hw_settings)
        else:
            detector_conf = {}

        # TODO "integrationTime" not part of detector VAs now, as stream VA
        #  -> should be handled as detector VA as it replaces exposureTime VA

        # Process the hardware VAs first (emitter and detector hardware VAs are combined into one
        # attribute called 'hw_vas'
        # sort the list of hardware VAs first in case it contains items that are not in the
        # emitter and detector conf, they will be appended at the end
        vas_names = util.sorted_according_to(sorted(list(self.stream.hw_vas.keys())),
                                             list(emitter_conf.keys()) + list(detector_conf.keys()))

        for name in vas_names:
            va = self.stream.hw_vas[name]
            conf = emitter_conf.get(name, detector_conf.get(name, None))
            if conf is not None:
                logging.debug("%s hardware configuration found", name)

            self.add_setting_entry(name, va, self.stream.emitter, conf)

        # Process the emitter VAs first
        vas_names = util.sorted_according_to(list(self.stream.emt_vas.keys()), list(emitter_conf.keys()))

        for name in vas_names:
            va = self.stream.emt_vas[name]
            conf = emitter_conf.get(name)
            if conf is not None:
                logging.debug("%s emitter configuration found for %s", name,
                              self.stream.emitter.role)

            self.add_setting_entry(name, va, self.stream.emitter, conf)

        # Then process the detector
        vas_names = util.sorted_according_to(list(self.stream.det_vas.keys()), list(detector_conf.keys()))

        for name in vas_names:
            va = self.stream.det_vas[name]
            conf = detector_conf.get(name)
            if conf is not None:
                logging.debug("%s detector configuration found for %s", name,
                              self.stream.detector.role)

            self.add_setting_entry(name, va, self.stream.detector, conf)

    def _add_stream_setting_controls(self):
        """ Add control for the VAs of the stream
        Note: only the VAs which are defined in the stream_config are shown.
        """
        for vaname, conf in self._stream_config.items():
            try:
                va = getattr(self.stream, vaname)
            except AttributeError:
                logging.debug("Skipping non existent VA %s on %s", vaname, self.stream)
                continue
            conf = self._stream_config.get(vaname)
            self.add_setting_entry(vaname, va, hw_comp=None, conf=conf)

    def _add_axis_controls(self):
        """
        Add controls for the axes that are connected to the stream
        """
        # Add Axes (in same order as config)
        axes_names = util.sorted_according_to(list(self.stream.axis_vas.keys()), list(self._stream_config.keys()))

        for axisname in axes_names:
            conf = self._stream_config.get(axisname)
            self.add_setting_entry(axisname, self.stream.axis_vas[axisname], None, conf)

    def add_setting_entry(self, name, va, hw_comp, conf=None):
        """ Add a name/value pair to the settings panel.

        :param name: (string): name of the value
        :param va: (VigilantAttribute)
        :param hw_comp: (Component): the component that contains this VigilantAttribute
        :param conf: ({}): Configuration items that may override default settings
        :return SettingEntry or None: the entry created, or None, if no entry was
          created (eg, because the conf indicates CONTROL_NONE).
        """

        se = create_setting_entry(self.stream_panel, name, va, hw_comp, conf)
        if se is not None:
            self.entries.append(se)

        return se

    def add_axis_entry(self, name, comp, conf=None):
        """ Add a widget to the setting panel to control an axis

        :param name: (string): name of the axis
        :param comp: (Component): the component that contains this axis
        :param conf: ({}): Configuration items that may override default settings

        """
        ae = create_axis_entry(self.stream_panel, name, comp, conf)
        if ae is not None:
            self.entries.append(ae)

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
                    isinstance(value, (int, float)) or
                    (
                        isinstance(value, Iterable) and
                        len(value) > 0 and
                        isinstance(value[0], (int, float))
                    )
                ):
                    nice_str = readable_str(value, unit, 3)
                else:
                    nice_str = str(value)
            self.stream_panel.add_readonly_field(label, nice_str)
        except Exception:
            logging.exception("Trying to convert metadata %s", key)

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

    def _on_z_index(self, zIndex):

        self.tab_data_model.zPos.unsubscribe(self._on_z_pos)

        metadata = self.stream.getRawMetadata()[0]  # take the first only
        zcentre = metadata[model.MD_POS][2]
        zstep = metadata[model.MD_PIXEL_SIZE][2]
        # The number of zIndexes is zIndex.range[1] + 1 (as it starts at 0).
        # zstart is the *center* position of the first pixel, so we need
        # len(zIndexes) - 1 ==  zIndex.range[1]
        zstart = zcentre - self.stream.zIndex.range[1] * zstep / 2
        self.tab_data_model.zPos.value = self.tab_data_model.zPos.clip(zstart + zstep * zIndex)

        self.tab_data_model.zPos.subscribe(self._on_z_pos)

    def _on_z_pos(self, zPos):
        # Given an absolute physical position in z pos, set the z index for a stream
        # based on physical parameters

        self.stream.zIndex.unsubscribe(self._on_z_index)

        metadata = self.stream.getRawMetadata()[0]  # take the first only
        zcentre = metadata[model.MD_POS][2]
        zstep = metadata[model.MD_PIXEL_SIZE][2]
        zstart = zcentre - self.stream.zIndex.range[1] * zstep / 2
        val = int(round((zPos - zstart) / zstep))
        self.stream.zIndex.value = self.stream.zIndex.clip(val)

        self.stream.zIndex.subscribe(self._on_z_index)

    @call_in_wx_main
    def _on_max_projection(self, val):
        """Disable/enable the z-index control based on the max_projection setting"""
        if self._zindex_se is not None:
            self._zindex_se.value_ctrl.Enable(not val)

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
            xcol = wavelength2rgb(xwl)
            self._btn_excitation.set_colour(xcol)
            ecol = wavelength2rgb(ewl)
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
        text = u""
        raw = [r for r in self.stream.raw if r is not None]
        text += u"======================================\nGeneral\n"
        text += u"======================================\n"
        for i, r in enumerate(raw):
            if len(raw) > 1:
                text += u"========= Array %d =========\n" % (i + 1,)
            shape = r.shape
            dtype = r.dtype
            md = r.metadata

            text += u"Shape: %s\n" % (u" x ".join(str(s) for s in shape),)
            text += u"Data type: %s\n" % (dtype,)
            for key in sorted(md):
                if key == model.MD_EXTRA_SETTINGS:
                    # show extra settings last
                    continue
                v = md[key]
                if key == model.MD_ACQ_DATE:  # display date in readable format
                    nice_str = time.strftime("%c", time.localtime(v))
                    # In Python 2, we still need to convert it to unicode
                    if isinstance(nice_str, bytes):
                        nice_str = nice_str.decode(locale.getpreferredencoding())
                    text += u"%s: %s\n" % (key, nice_str)
                else:
                    if isinstance(v, numpy.ndarray):
                        # Avoid ellipses (eg, [1, ..., 100 ])as we want _all_
                        # the data (unless it'd get really crazy).
                        # TODO: from numpy v1.14, the "threshold" argument can
                        # be directly used in array2string().
                        numpy.set_printoptions(threshold=2500)
                        v = numpy.array2string(v, max_line_width=100, separator=u", ")
                        numpy.set_printoptions(threshold=1000)
                    elif isinstance(v, list) and len(v) > 2500:
                        v = u"[%s … %s]" % (u", ".join(str(a) for a in v[:20]), u", ".join(str(a) for a in v[-20:]))
                    text += u"%s: %s\n" % (key, v)

        # only display extra settings once
        if model.MD_EXTRA_SETTINGS in raw[0].metadata:
            text += u"\n======================================\nHardware Settings\n"
            text += u"======================================\n"
            for comp, vas in md[model.MD_EXTRA_SETTINGS].items():
                try:
                    if vas:
                        text += u"Component %s:\n" % comp
                    for name, (value, unit) in vas.items():
                        unit = unit or ""  # don't display 'None'
                        unit = unit if value is not None else ""  # don't display unit if data is None (None Hz doesn't make sense)
                        if isinstance(value, dict):
                            if value:
                                text += u"\t%s:\n" % name
                                for key, val in value.items():
                                    text += u"\t\t%s: %s %s\n" % (key, val, unit)
                            else:
                                # still display the VA, might be interesting (e.g. that no axis was referenced)
                                text += u"\t%s: {}\n" % name
                        else:
                            text += u"\t%s: %s %s\n" % (name, value, unit)
                except Exception as ex:
                    logging.warning("Couldn't display metadata for component %s: %s" % (comp, ex))
                    continue

        # Note: we show empty window even if no data present, to let the user know
        # that there is no data, but the button worked fine.
        md_frame = self.stream_panel.create_text_frame(u"Metadata of %s" % self.stream.name.value, text)
        md_frame.ShowModal()
        md_frame.Destroy()

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
            if hasattr(self.stream, "spectrum_binning") and hasattr(self.stream, "angular_binning"):
                # Check if angular(vertical) and spectrum(horizontal) are present.
                # Update the default binning by assigning the angular binning to it and subscribe to the spectrum
                # binning to update the resolution. The opposite would also work.
                self._binva = self.stream.angular_binning
                self.stream.spectrum_binning.subscribe(self._update_resolution)
            else:
                logging.warning("Stream has resolution VA but no binning/scale, "
                                "so it will not be updated.")
                return

        self._binva.subscribe(self._update_resolution)
        self._resva.subscribe(self._on_resolution)

    def _unlink_resolution(self):
        if self._binva:
            self._binva.unsubscribe(self._update_resolution)
            if hasattr(self.stream, "spectrum_binning"):
                self.stream.spectrum_binning.unsubscribe(self._update_resolution)
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
        if hasattr(self.stream, "spectrum_binning"):
            scale = (self.stream.spectrum_binning.value, self.stream.angular_binning.value)
        newres = (int((self._resmx[0] * crop) // scale[0]),
                  int((self._resmx[1] * crop) // scale[1]))
        newres = self._resva.clip(newres)
        logging.debug("Updated resolution to %s", newres)
        self._resva.value = newres

    def _on_resolution(self, res, crop=1.0):
        # if the stream is not playing, the hardware should take care of it
        if self.stream.is_active.value:
            return
        if hasattr(self.stream, "spectrum_binning") and hasattr(self.stream, "angular_binning"):
            scale = (self.stream.spectrum_binning.value, self.stream.angular_binning.value)
        else:
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
            colour = wavelength2rgb(ewl_center)
            logging.debug("Synchronising tint to %s", colour)
            self.stream.tint.value = colour

    # Control addition

    def _add_selwidth_ctrl(self):
        lbl_selection_width, sld_selection_width = self.stream_panel.add_specselwidth_ctrl()

        se = SettingEntry(name="selectionwidth", va=self.stream.selectionWidth, stream=self.stream,
                          lbl_ctrl=lbl_selection_width, value_ctrl=sld_selection_width,
                          events=wx.EVT_SLIDER)
        self.entries.append(se)

    def _add_wl_ctrls(self):
        self._sld_spec, txt_spec_center, txt_spec_bw = self.stream_panel.add_specbw_ctrls()

        se = SettingEntry(name="spectrum", va=self.stream.spectrumBandwidth, stream=self.stream,
                          value_ctrl=self._sld_spec, events=wx.EVT_SLIDER)
        self.entries.append(se)

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
        self.entries.append(se)

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
        self.entries.append(se)

    @wxlimit_invocation(0.2)
    def _on_new_spec_data(self, gspec):
        if not self or not self._sld_spec or gspec is None:
            # if no new calibration, or empty data
            return  # already deleted

        logging.debug("New spec data")
        # Display the global spectrum in the visual range slider
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
        self._sld_spec.SetContent(gspec.tolist())

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
            self.stream_panel.set_header_choices(list(dye.DyeDatabase.keys()))
            self.stream_panel.header_change_callback = self._on_new_dye_name

        center_wl = fluo.get_one_center_ex(self.stream.excitation.value, self.stream.emission.value)
        self._add_excitation_ctrl(wavelength2rgb(center_wl))

        # Emission
        center_wl = fluo.get_one_center_em(self.stream.emission.value, self.stream.excitation.value)
        self._add_emission_ctrl(wavelength2rgb(center_wl))

    def _onExcitationChannelChange(self, _):
        """
        Event handler for the Excitation channel combobox selection.
        Update the power slider range with the current power VA's
        """
        if not self._power_entry:
            return
        # Set the slider with min value first (in order to change the range in case new value is > current slider max)
        self._power_entry.value_ctrl.SetValue(self._power_entry.vigilattr.min)
        # Then set the range followed by the actual value (this way no exception is thrown by SetRange)
        self._power_entry.value_ctrl.SetRange(self._power_entry.vigilattr.min, self._power_entry.vigilattr.max)
        self._power_entry.value_ctrl.SetValue(self._power_entry.vigilattr.value)

    def _add_excitation_ctrl(self, center_wl_color=None):
        """
        Add excitation ctrl
        center_wl_color (None or 3 0<= int <= 255): RGB colour. If None, it
          will be guessed.
        """
        if center_wl_color is None:
            center_wl = fluo.get_one_center(self.stream.excitation.value)
            center_wl_color = wavelength2rgb(center_wl)

        band = to_readable_band(self.stream.excitation.value)
        readonly = self.stream.excitation.readonly or len(self.stream.excitation.choices) <= 1

        r = self.stream_panel.add_dye_excitation_ctrl(band, readonly, center_wl_color)
        lbl_ctrl, value_ctrl, self._lbl_exc_peak, self._btn_excitation = r
        self.update_peak_label_fit(self._lbl_exc_peak, self._btn_excitation, None, band)

        if not readonly:

            choices = sorted(self.stream.excitation.choices, key=get_one_center)
            for b in choices:
                value_ctrl.Append(to_readable_band(b), b)
            # Bind the excitation combobox selection event to update the power slider range
            value_ctrl.Bind(wx.EVT_COMBOBOX, self._onExcitationChannelChange)
            # Store power entry to be used in _onExcitationChannelChange event handler
            self._power_entry = next((spe for spe in self.entries if spe.name == "power"), None)

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
                    colour = wavelength2rgb(fluo.get_one_center_ex(value, self.stream.emission.value))
                    self._btn_excitation.set_colour(colour)
                else:
                    self.update_peak_label_fit(self._lbl_exc_peak,
                                               self._btn_excitation,
                                               self._dye_xwl, value)

                # also update emission colour as it's dependent on excitation when multi-band
                if self._dye_ewl is None and self._btn_emission:
                    colour = wavelength2rgb(fluo.get_one_center_em(self.stream.emission.value, value))
                    self._btn_emission.set_colour(colour)

            se = SettingEntry(name="excitation", va=self.stream.excitation, stream=self.stream,
                              lbl_ctrl=lbl_ctrl, value_ctrl=value_ctrl, events=wx.EVT_COMBOBOX,
                              va_2_ctrl=_excitation_2_ctrl, ctrl_2_va=_excitation_2_va)

            self.entries.append(se)

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
            if isinstance(em, str):
                # Unknown colour or non-meaningful
                center_wl_color = None
            else:
                center_wl = fluo.get_one_center(self.stream.emission.value)
                center_wl_color = wavelength2rgb(center_wl)

        r = self.stream_panel.add_dye_emission_ctrl(band, readonly, center_wl_color)
        lbl_ctrl, value_ctrl, self._lbl_em_peak, self._btn_emission = r

        if isinstance(em, str) and em != model.BAND_PASS_THROUGH:
            if not readonly:
                logging.error("Emission band is a string (%s), but not readonly", em)
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
                    colour = wavelength2rgb(fluo.get_one_center_em(value, self.stream.excitation.value))
                    self._btn_emission.set_colour(colour)
                else:
                    self.update_peak_label_fit(self._lbl_em_peak,
                                               self._btn_emission,
                                               self._dye_ewl, value)
                # also update excitation colour as it's dependent on emission when multiband
                if self._dye_xwl is None:
                    colour = wavelength2rgb(fluo.get_one_center_ex(self.stream.excitation.value, value))
                    self._btn_excitation.set_colour(colour)

            se = SettingEntry(name="emission", va=self.stream.emission, stream=self.stream,
                              lbl_ctrl=lbl_ctrl, value_ctrl=value_ctrl, events=wx.EVT_COMBOBOX,
                              va_2_ctrl=_emission_2_ctrl, ctrl_2_va=_emission_2_va)

            self.entries.append(se)

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
        self.entries.append(se)

        # Store a setting entry for the outliers slider
        se = SettingEntry(name="outliers", va=self.stream.auto_bc_outliers, stream=self.stream,
                          value_ctrl=sld_outliers, lbl_ctrl=lbl_bc_outliers, events=wx.EVT_SLIDER)
        self.entries.append(se)

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
        self.entries.append(se)

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
            self.entries.append(se)

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
        self.entries.append(se)

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
        self.entries.append(se)

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
        self.entries.append(se)

    def _add_repetition_ctrl(self):
        """
        Add the repetition/pixelSize/fuzzing settings for the RepetitionStreams
        """
        va_config = OrderedDict((
            ("repetition", {
                "control_type": CONTROL_COMBO,
                "choices": {(1, 1)},  # Actually, it's immediately replaced by _onStreamRep()
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
                # (an alternative would be to override our own va_2_ctrl to the
                # SettingEntry, but currently create_setting_entry() doesn't
                # allow to change it)
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

            if isinstance(band[0], Iterable):  # multi-band
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
    @call_in_wx_main
    def _onStreamRep(self, rep):
        """
        Called when the repetition VAs of a RepetitionStream is modified.
        Recalculate the repetition presets according to the repetition ratio
        """
        ratio = rep[1] / rep[0]

        # Create the entries:
        choices = [(1, 1), rep]  # 1 x 1 should always be there

        # Add a couple values below/above the current repetition
        for m in (1 / 4, 1 / 2, 2, 4, 10):
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

        # Make sure the current value is selected
        self._rep_ctrl.SetSelection(choices.index(rep))


class FastEMStreamController(StreamController):
    def __init__(self, stream_bar, stream, tab_data_model, show_panel=True, view=None,
                 sb_ctrl=None):
        super().__init__(
            stream_bar=stream_bar, stream=stream, tab_data_model=tab_data_model,
            show_panel=show_panel, view=view, sb_ctrl=sb_ctrl
        )

        assert isinstance(stream, acqstream.SEMStream)  # don't show for CCD stream
        self._add_fastem_ctrls()

    def _create_immersion_mode_cbox(self):
        # HACK manually create the checkbox using custom gb_sizer's pos and span
        # The hack is needed because self.stream_panel.add_checkbox_control cannot be overridden,
        # which has pos=(self.num_rows, 1) and span=(1, 2)
        _ = self.stream_panel._add_side_label("Immersion mode")
        cbox_immersion_mode = wx.CheckBox(self.stream_panel._panel, wx.ID_ANY,
                                          style=wx.ALIGN_RIGHT | wx.NO_BORDER)

        self.stream_panel.gb_sizer.Add(cbox_immersion_mode, (self.stream_panel.num_rows, 2), span=(1, 1),
                                       flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.TOP | wx.BOTTOM,
                                       border=5)
        cbox_immersion_mode.SetValue(self.stream.emitter.immersion.value)

        if not self.stream_panel.gb_sizer.IsColGrowable(1):
            self.stream_panel.gb_sizer.AddGrowableCol(1)

        # Re-layout the FoldPanelBar parent
        win = self.stream_panel
        while not isinstance(win, FoldPanelBar):
            win = win.Parent
        win.Layout()
        # Advance the row count for the next control
        self.stream_panel.num_rows += 1
        return cbox_immersion_mode

    def _create_reference_stage_btn(self):
        # HACK manually create the button using custom gb_sizer's pos and span
        # The hack is needed to create a custom GraphicalToggleButtonControl with ImageTextButton button
        # Create horizontal sizer to hold label and toggle buttons
        h_sizer = wx.BoxSizer(wx.HORIZONTAL)
        lbl_ctrl = wx.StaticText(self.stream_panel._panel, -1, "Reference stage")

        # Add label
        h_sizer.Add(lbl_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        # Add spacer
        h_sizer.AddSpacer(10)  # 10 pixels of space

        # Add toggle button
        conf = {
            'size': (-1, 16),
            'choices': [1, 2, 3],
            'labels': ['x', 'y', 'z'],
            'height': 16,
        }
        toggle_ctrl = GraphicalToggleButtonControl(self.stream_panel._panel, -1, style=wx.NO_BORDER,
                                                 **conf)
        toggle_ctrl.SetValue([1, 2, 3])
        h_sizer.Add(toggle_ctrl, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT)

        # Add combined sizer to grid sizer
        self.stream_panel.gb_sizer.Add(h_sizer, (self.stream_panel.num_rows, 0),
                          flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)

        btn_reference_stage = ImageTextButton(self.stream_panel._panel, label="Run", height=16, style=wx.ALIGN_CENTER)
        btn_reference_stage.toggle_btn = toggle_ctrl

        self.stream_panel.gb_sizer.Add(btn_reference_stage, (self.stream_panel.num_rows, 2), span=(1, 1),
                                       flag=wx.ALIGN_CENTRE_VERTICAL | wx.EXPAND | wx.TOP | wx.BOTTOM,
                                       border=5)

        if not self.stream_panel.gb_sizer.IsColGrowable(1):
            self.stream_panel.gb_sizer.AddGrowableCol(1)

        # Re-layout the FoldPanelBar parent
        win = self.stream_panel
        while not isinstance(win, FoldPanelBar):
            win = win.Parent
        win.Layout()
        # Advance the row count for the next control
        self.stream_panel.num_rows += 1
        return btn_reference_stage

    def _add_fastem_ctrls(self):
        self.stream_panel.add_divider()
        # Create the immersion mode button
        cbox_immersion_mode = self._create_immersion_mode_cbox()
        # Create the buttons
        _, self.btn_reference_stage = self.stream_panel.add_run_btn(
            label_text="Reference stage", button_label="Run"
        )
        # TODO: use the custom button instead of the default one, when
        # the user needs to reference the stage as per choices of axes
        # self.btn_reference_stage = self._create_reference_stage_btn()
        _, self.btn_optical_autofocus = self.stream_panel.add_run_btn(
            label_text="Optical autofocus", button_label="Run"
        )
        _, self.btn_auto_brightness_contrast = self.stream_panel.add_run_btn(
            label_text="Auto brightness / contrast", button_label="Run"
        )
        _, self.btn_sem_autofocus = self.stream_panel.add_run_btn(
            label_text="SEM autofocus", button_label="Run"
        )
        _, self.btn_autostigmation = self.stream_panel.add_run_btn(
            label_text="Autostigmation", button_label="Run"
        )

        # Store a setting entry for the immersion mode button
        se = SettingEntry(name="immersion_mode", va=self.stream.emitter.immersion,
                          stream=self.stream, value_ctrl=cbox_immersion_mode, events=wx.EVT_CHECKBOX)
        self.entries.append(se)
