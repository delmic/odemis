# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Patrick Cleeve

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import collections
import gc
import logging
import os.path
import wx
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html

from odemis import dataio
from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui.cont.export as exportcont
from odemis.gui.cont.stream_bar import StreamBarController
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
import odemis.gui.util as guiutil
from odemis.acq import calibration
from odemis.acq.stream import OpticalStream, SpectrumStream, \
    CLStream, EMStream, \
    ARStream, \
    RGBStream, \
    SinglePointSpectrumProjection, LineSpectrumProjection, \
    PixelTemporalSpectrumProjection, SinglePointTemporalProjection, \
    SinglePointAngularProjection, PixelAngularSpectrumProjection, \
    ARRawProjection, ARPolarimetryProjection
from odemis.gui.comp.viewport import MicroscopeViewport, AngularResolvedViewport, \
    PlotViewport, LineSpectrumViewport, TemporalSpectrumViewport, ChronographViewport, \
    AngularSpectrumViewport, ThetaViewport
from odemis.gui.conf import get_acqui_conf
from odemis.gui.cont import settings
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.model import TOOL_POINT, TOOL_LINE, TOOL_ACT_ZOOM_FIT, TOOL_RULER, TOOL_LABEL, \
    TOOL_NONE
from odemis.gui.util import call_in_wx_main
from odemis.util.dataio import data_to_static_streams, open_acquisition, open_files_and_stitch


class AnalysisTab(Tab):
    """ Handle the loading and displaying of acquisition files
    """
    def __init__(self, name, button, panel, main_frame, main_data):
        """
        microscope will be used only to select the type of views
        """
        # During creation, the following controllers are created:
        #
        # ViewPortController
        #   Processes the given viewports by creating views for them, and
        #   assigning them to their viewport.
        #
        # StreamBarController
        #   Keeps track of the available streams, which are all static
        #
        # ViewButtonController
        #   Connects the views to their thumbnails and show the right one(s)
        #   based on the model.
        #
        # In the `load_data` method the file data is loaded using the
        # appropriate converter. It's then passed on to the `display_new_data`
        # method, which analyzes which static streams need to be created. The
        # StreamController is then asked to create the actual stream object and
        # it also adds them to every view which supports that (sub)type of
        # stream.

        # TODO: automatically change the display type based on the acquisition
        # displayed
        tab_data = guimod.AnalysisGUIData(main_data)
        super(AnalysisTab, self).__init__(name, button, panel, main_frame, tab_data)
        if main_data.role in ("sparc-simplex", "sparc", "sparc2"):
            # Different name on the SPARC to reflect the slightly different usage
            self.set_label("ANALYSIS")
        else:
            self.set_label("GALLERY")

        # Connect viewports
        viewports = panel.pnl_inspection_grid.viewports
        # Viewport type checking to avoid mismatches
        for vp in viewports[:4]:
            assert(isinstance(vp, MicroscopeViewport))
        assert(isinstance(viewports[4], AngularResolvedViewport))
        assert(isinstance(viewports[5], PlotViewport))
        assert(isinstance(viewports[6], LineSpectrumViewport))
        assert(isinstance(viewports[7], TemporalSpectrumViewport))
        assert(isinstance(viewports[8], ChronographViewport))
        assert(isinstance(viewports[9], AngularResolvedViewport))
        assert(isinstance(viewports[10], AngularSpectrumViewport))
        assert(isinstance(viewports[11], ThetaViewport))

        vpv = collections.OrderedDict([
            (viewports[0],  # focused view
             {"name": "Optical",
              "stream_classes": (OpticalStream, SpectrumStream, CLStream),
              "zPos": self.tab_data_model.zPos,
              }),
            (viewports[1],
             {"name": "SEM",
              "stream_classes": EMStream,
              "zPos": self.tab_data_model.zPos,
              }),
            (viewports[2],
             {"name": "Combined 1",
              "stream_classes": (EMStream, OpticalStream, SpectrumStream, CLStream, RGBStream),
              "zPos": self.tab_data_model.zPos,
              }),
            (viewports[3],
             {"name": "Combined 2",
              "stream_classes": (EMStream, OpticalStream, SpectrumStream, CLStream, RGBStream),
              "zPos": self.tab_data_model.zPos,
              }),
            (viewports[4],
             {"name": "Angle-resolved",
              "stream_classes": ARStream,
              "projection_class": ARRawProjection,
              }),
            (viewports[5],
             {"name": "Spectrum plot",
              "stream_classes": (SpectrumStream,),
              "projection_class": SinglePointSpectrumProjection,
              }),
            (viewports[6],
             {"name": "Spectrum cross-section",
              "stream_classes": (SpectrumStream,),
              "projection_class": LineSpectrumProjection,
              }),
            (viewports[7],
             {"name": "Temporal spectrum",
              "stream_classes": (SpectrumStream,),
              "projection_class": PixelTemporalSpectrumProjection,
              }),
            (viewports[8],
             {"name": "Chronograph",
              "stream_classes": (SpectrumStream,),
              "projection_class": SinglePointTemporalProjection,
              }),
            (viewports[9],
             {"name": "Polarimetry",  # polarimetry analysis (if ar with polarization)
              "stream_classes": ARStream,
              "projection_class": ARPolarimetryProjection,
              }),
            (viewports[10],
             {"name": "AR Spectrum",
              "stream_classes": (SpectrumStream,),
              "projection_class": PixelAngularSpectrumProjection,
              }),
            (viewports[11],
             {"name": "Theta",
              "stream_classes": (SpectrumStream,),
              "projection_class": SinglePointAngularProjection,
              }),
        ])

        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)
        self.export_controller = exportcont.ExportController(tab_data, main_frame, panel, vpv)

        # Connect view selection button
        buttons = collections.OrderedDict([
            (
                panel.btn_inspection_view_all,
                (None, panel.lbl_inspection_view_all)
            ),
            (
                panel.btn_inspection_view_tl,
                (panel.vp_inspection_tl, panel.lbl_inspection_view_tl)
            ),
            (
                panel.btn_inspection_view_tr,
                (panel.vp_inspection_tr, panel.lbl_inspection_view_tr)
            ),
            (
                panel.btn_inspection_view_bl,
                (panel.vp_inspection_bl, panel.lbl_inspection_view_bl)
            ),
            (
                panel.btn_inspection_view_br,
                (panel.vp_inspection_br, panel.lbl_inspection_view_br)
            )
        ])

        self._view_selector = viewcont.ViewButtonController(tab_data, panel, buttons, viewports)

        # Toolbar
        self.tb = panel.ana_toolbar
        # TODO: Add the buttons when the functionality is there
        # tb.add_tool(TOOL_ZOOM, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_RULER, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_POINT, self.tab_data_model.tool)
        self.tb.enable_button(TOOL_POINT, False)
        self.tb.add_tool(TOOL_LABEL, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_LINE, self.tab_data_model.tool)
        self.tb.enable_button(TOOL_LINE, False)

        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self.view_controller.fitViewToContent)

        # save the views to be able to reset them later
        self._def_views = list(tab_data.visible_views.value)

        # Show the streams (when a file is opened)
        self._stream_bar_controller = StreamBarController(
            tab_data,
            panel.pnl_inspection_streams,
            static=True
        )
        self._stream_bar_controller.add_action("From file...", self._on_add_file)
        self._stream_bar_controller.add_action("From tileset...", self._on_add_tileset)

        # Show the file info and correction selection
        self._settings_controller = settings.AnalysisSettingsController(
            panel,
            tab_data
        )
        self._settings_controller.setter_ar_file = self.set_ar_background
        self._settings_controller.setter_spec_bck_file = self.set_spec_background
        self._settings_controller.setter_temporalspec_bck_file = self.set_temporalspec_background
        self._settings_controller.setter_angularspec_bck_file = self.set_temporalspec_background
        self._settings_controller.setter_spec_file = self.set_spec_comp

        if main_data.role is None:
            # HACK: Move the logo to inside the FileInfo bar, as the tab buttons
            # are hidden when only one tab is present (ie, no backend).
            capbar = panel.fp_fileinfo._caption_bar
            capbar.set_logo(main_frame.logo.GetBitmap())

        self.panel.btn_open_image.Bind(wx.EVT_BUTTON, self.on_file_open_button)

    @property
    def stream_bar_controller(self):
        return self._stream_bar_controller

    def select_acq_file(self, extend=False, tileset: bool = False):
        """ Open an image file using a file dialog box

        extend (bool): if False, will ensure that the previous streams are closed.
          If True, will add the new file to the current streams opened.
        tileset (bool): if True, open files as tileset and stitch together
        return (boolean): True if the user did pick a file, False if it was
        cancelled.
        """
        # Find the available formats (and corresponding extensions)
        formats_to_ext = dataio.get_available_formats(os.O_RDONLY)

        fi = self.tab_data_model.acq_fileinfo.value

        if fi and fi.file_name:
            path, _ = os.path.split(fi.file_name)
        else:
            config = get_acqui_conf()
            path = config.last_path

        wildcards, formats = guiutil.formats_to_wildcards(formats_to_ext, include_all=True)
        dialog = wx.FileDialog(self.panel,
                               message="Choose a file to load",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
                               wildcard=wildcards)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return False

        # Detect the format to use
        fmt = formats[dialog.GetFilterIndex()]

        if tileset:
            filenames = dialog.GetPaths()
            self.load_tileset(filenames, extend=extend)
        else:
            for filename in dialog.GetPaths():
                if extend:
                    logging.debug("Extending the streams with file %s", filename)
                else:
                    logging.debug("Current file set to %s", filename)

                self.load_data(filename, fmt, extend=extend)
                extend = True  # If multiple files loaded, the first one is considered main one

        return True

    def on_file_open_button(self, _):
        self.select_acq_file()

    def _on_add_file(self):
        """
        Called when the user requests to extend the current acquisition with
        an extra file.
        """
        # If no acquisition file, behave as just opening a file normally
        extend = bool(self.tab_data_model.streams.value)
        self.select_acq_file(extend)

    def _on_add_tileset(self):
        self.select_acq_file(extend=True, tileset=True)

    def load_tileset(self, filenames, extend=False):
        data = open_files_and_stitch(filenames) # TODO: allow user defined registration / weave methods
        self.display_new_data(filenames[0], data, extend=extend)

    def load_data(self, filename, fmt=None, extend=False):
        data = open_acquisition(filename, fmt)
        self.display_new_data(filename, data, extend=extend)

    def _get_time_spectrum_streams(self, spec_streams):
        """
        Sort spectrum streams into the substreams according to types spectrum, temporal spectrum,
        angular spectrum and time correlator streams.
        :param spec_streams: (list of streams) Streams to separate.
        :returns: (4 lists of streams) spectrum, temporal spectrum, angular spectrum and time correlator
        """
        spectrum = [s for s in spec_streams
                    if hasattr(s, "selected_wavelength") and not hasattr(s, "selected_time") and not hasattr(s, "selected_angle")]
        temporalspectrum = [s for s in spec_streams
                            if hasattr(s, "selected_wavelength") and hasattr(s, "selected_time")]
        timecorrelator = [s for s in spec_streams
                          if not hasattr(s, "selected_wavelength") and hasattr(s, "selected_time")]
        angularspectrum = [s for s in spec_streams
                          if hasattr(s, "selected_wavelength") and hasattr(s, "selected_angle")]
        # TODO currently "selected_wavelength" is always created, so all timecorrelator streams
        # are considered temporal spectrum streams -> adapt StaticSpectrumStream

        return spectrum, temporalspectrum, timecorrelator, angularspectrum

    @call_in_wx_main
    def display_new_data(self, filename, data, extend=False):
        """
        Display a new data set (and removes all references to the current one)

        filename (str or None): Name of the file containing the data.
          If None, just the current data will be closed.
        data (list of DataArray(Shadow)): List of data to display.
          Should contain at least one array
        extend (bool): if False, will ensure that the previous streams are closed.
          If True, will add the new file to the current streams opened.
        """
        if not extend:
            # Remove all the previous streams
            self._stream_bar_controller.clear()
            # Clear any old plots
            self.panel.vp_inspection_tl.clear()
            self.panel.vp_inspection_tr.clear()
            self.panel.vp_inspection_bl.clear()
            self.panel.vp_inspection_br.clear()
            self.panel.vp_inspection_plot.clear()
            self.panel.vp_linespec.clear()
            self.panel.vp_temporalspec.clear()
            self.panel.vp_angularspec.clear()
            self.panel.vp_timespec.clear()
            self.panel.vp_thetaspec.clear()
            self.panel.vp_angular.clear()
            self.panel.vp_angular_pol.clear()

        gc.collect()
        if filename is None:
            return

        if not extend:
            # Reset tool, layout and visible views
            self.tab_data_model.tool.value = TOOL_NONE
            self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22

            # Create a new file info model object
            fi = guimod.FileInfo(filename)

            # Update the acquisition date to the newest image present (so that if
            # several acquisitions share one old image, the date is still different)
            md_list = [d.metadata for d in data]
            acq_dates = [md[model.MD_ACQ_DATE] for md in md_list if model.MD_ACQ_DATE in md]
            if acq_dates:
                fi.metadata[model.MD_ACQ_DATE] = max(acq_dates)
            self.tab_data_model.acq_fileinfo.value = fi

        # Create streams from data
        streams = data_to_static_streams(data)
        all_streams = streams + self.tab_data_model.streams.value

        # Spectrum and AR streams are, for now, considered mutually exclusive

        # all spectrum streams including spectrum, temporal spectrum and time correlator (chronograph), angular spectrum
        spec_streams = [s for s in all_streams if isinstance(s, acqstream.SpectrumStream)]
        ar_streams = [s for s in all_streams if isinstance(s, acqstream.ARStream)]

        new_visible_views = list(self._def_views)  # Use a copy

        # TODO: Move viewport related code to ViewPortController
        # TODO: to support multiple (types of) streams (eg, AR+Spec+Spec), do
        # this every time the streams are hidden/displayed/removed.

        # handle display when both data types are present.
        # TODO: Add a way within the GUI to toggle between AR and spectrum mode
        if spec_streams and ar_streams:
            dlg = wx.MessageDialog(wx.GetApp().GetTopWindow() , "The file contains both spectrum and AR data. Which data type should be displayed?",
                caption="Incompatible stream types", style=wx.YES_NO)
            dlg.SetYesNoLabels("Spectrum", "AR")

            if dlg.ShowModal() == wx.ID_YES:
                # Show Spectrum
                ar_streams = []
            else:
                # Show AR
                spec_streams = []

        if spec_streams:
            # ########### Track pixel and line selection

            # FIXME: This temporary "fix" only binds the first spectrum stream to the pixel and
            # line overlays. This is done because in the PlotViewport only the first spectrum stream
            # gets connected. See connect_stream in viewport.py, line 812.
            # We need a clean way to connect the overlays

            spec_stream = spec_streams[0]
            sraw = spec_stream.raw[0]

            # We need to get the dimensions so we can determine the
            # resolution. Remember that in numpy notation, the
            # number of rows (is vertical size), comes first. So we
            # need to 'swap' the values to get the (x,y) resolution.
            height, width = sraw.shape[-2], sraw.shape[-1]
            pixel_width = sraw.metadata.get(model.MD_PIXEL_SIZE, (100e-9, 100e-9))[0]
            center_position = sraw.metadata.get(model.MD_POS, (0, 0))

            # Set the PointOverlay values for each viewport
            for viewport in self.view_controller.viewports:
                if hasattr(viewport.canvas, "pixel_overlay"):
                    ol = viewport.canvas.pixel_overlay
                    ol.set_data_properties(pixel_width, center_position, (width, height))
                    ol.connect_selection(spec_stream.selected_pixel, spec_stream.selectionWidth)

                # TODO: to be done by the MicroscopeViewport or DblMicroscopeCanvas (for each stream with a selected_line)
                if hasattr(viewport.canvas, "line_overlay") and hasattr(spec_stream, "selected_line"):
                    ol = viewport.canvas.line_overlay
                    ol.set_data_properties(pixel_width, center_position, (width, height))
                    ol.connect_selection(
                        spec_stream.selected_line,
                        spec_stream.selectionWidth,
                        spec_stream.selected_pixel
                    )

            for s in spec_streams:
                # Adjust the viewport layout (if needed) when a pixel or line is selected
                s.selected_pixel.subscribe(self._on_pixel_select, init=True)
                if hasattr(s, "selected_line"):
                    s.selected_line.subscribe(self._on_line_select, init=True)

            # ########### Combined views and spectrum view visible
            if hasattr(spec_stream, "selected_time"):
                new_visible_views[0] = self._def_views[2]  # Combined
                new_visible_views[1] = self.panel.vp_timespec.view
                new_visible_views[2] = self.panel.vp_inspection_plot.view
                new_visible_views[3] = self.panel.vp_temporalspec.view
            elif hasattr(spec_stream, "selected_angle"):
                new_visible_views[0] = self._def_views[2]  # Combined
                new_visible_views[1] = self.panel.vp_thetaspec.view
                new_visible_views[2] = self.panel.vp_inspection_plot.view
                new_visible_views[3] = self.panel.vp_angularspec.view
            else:
                new_visible_views[0:2] = self._def_views[2:4]  # Combined
                new_visible_views[2] = self.panel.vp_linespec.view
                self.tb.enable_button(TOOL_LINE, True)
                new_visible_views[3] = self.panel.vp_inspection_plot.view

            # ########### Update tool menu
            self.tb.enable_button(TOOL_POINT, True)

        elif ar_streams:

            # ########### Track point selection

            for ar_stream in ar_streams:
                for viewport in self.view_controller.viewports:
                    if hasattr(viewport.canvas, "points_overlay"):
                        ol = viewport.canvas.points_overlay
                        ol.set_point(ar_stream.point)

                ar_stream.point.subscribe(self._on_point_select, init=True)

            # ########### Combined views and Angular view visible

            new_visible_views[0] = self._def_views[1]  # SEM only
            new_visible_views[1] = self._def_views[2]  # Combined 1
            new_visible_views[2] = self.panel.vp_angular.view

            # Note: Acquiring multiple AR streams is not supported/suggested in the same acquisition,
            # but there are ways that the user would get in such state. Either by having multiple AR
            # streams at acquisition, or adding extra acquisitions in the same analysis tab. The rule
            # then is: Don't raise errors in such case (but it's fine if the view is not good).
            for ar_stream in ar_streams:
                if hasattr(ar_stream, "polarimetry"):
                    new_visible_views[3] = self.panel.vp_angular_pol.view
                    break
                else:
                    new_visible_views[3] = self._def_views[3]  # Combined 2

            # ########### Update tool menu

            self.tb.enable_button(TOOL_POINT, True)
            self.tb.enable_button(TOOL_LINE, False)
        else:
            # ########### Update tool menu
            self.tb.enable_button(TOOL_POINT, False)
            self.tb.enable_button(TOOL_LINE, False)

        # Only show the panels that fit the current streams
        spectrum, temporalspectrum, chronograph, angularspectrum = self._get_time_spectrum_streams(spec_streams)
        # TODO extend in case of bg support for time correlator data
        self._settings_controller.show_calibration_panel(len(ar_streams) > 0, len(spectrum) > 0,
                                                         len(temporalspectrum) > 0, len(angularspectrum) > 0)

        self.tab_data_model.visible_views.value = new_visible_views

        # Load the Streams and their data into the model and views
        for s in streams:
            scont = self._stream_bar_controller.addStream(s, add_to_view=True)
            # when adding more streams, make it easy to remove them
            scont.stream_panel.show_remove_btn(extend)

        # Reload current calibration on the new streams (must be done after .streams is set)
        if spectrum:
            try:
                self.set_spec_background(self.tab_data_model.spec_bck_cal.value)
            except ValueError:
                logging.warning(u"Calibration file not accepted any more '%s'",
                                self.tab_data_model.spec_bck_cal.value)
                self.tab_data_model.spec_bck_cal.value = u""  # remove the calibration

        if temporalspectrum:
            try:
                self.set_temporalspec_background(self.tab_data_model.temporalspec_bck_cal.value)
            except ValueError:
                logging.warning(u"Calibration file not accepted any more '%s'",
                                self.tab_data_model.temporalspec_bck_cal.value)
                self.tab_data_model.temporalspec_bck_cal.value = u""  # remove the calibration

        if angularspectrum:
            try:
                self.set_temporalspec_background(self.tab_data_model.angularspec_bck_cal.value)
            except ValueError:
                logging.warning(u"Calibration file not accepted any more '%s'",
                                self.tab_data_model.angularspec_bck_cal.value)
                self.tab_data_model.angularspec_bck_cal.value = u""  # remove the calibration

        if spectrum or temporalspectrum or angularspectrum:
            try:
                self.set_spec_comp(self.tab_data_model.spec_cal.value)
            except ValueError:
                logging.warning(u"Calibration file not accepted any more '%s'",
                                self.tab_data_model.spec_cal.value)
                self.tab_data_model.spec_cal.value = u""  # remove the calibration

        if ar_streams:
            try:
                self.set_ar_background(self.tab_data_model.ar_cal.value)
            except ValueError:
                logging.warning(u"Calibration file not accepted any more '%s'",
                                self.tab_data_model.ar_cal.value)
                self.tab_data_model.ar_cal.value = u""  # remove the calibration

        # if all the views are either empty or contain the same streams,
        # display in full screen by default (with the first view which has streams)
        # Show the 1 x 1 view if:
        # - the streams are all the same in every view
        # - views are empty with no projection

        def projection_list_eq(list1, list2):
            """ Returns true if two projection lists are the same,
            i.e. they are projecting the same streams, even if the objects are different
            list1 (list of Projections)
            list2 (list of Projections) to be
            """

            if len(list1) != len(list2):
                return False

            # since it is impossible to order the lists, use O(n²) comparison
            # each proj in list1 should have at least one match in list2
            for proj1 in list1:
                # there should be at least one corresponding match in the second list
                for proj2 in list2:
                    # matches are of the same type projecting the same stream reference
                    if type(proj1) == type(proj2) and proj1.stream == proj2.stream:
                        break
                else:  # no projection identical to proj1 found
                    return False

            # all projections identical
            return True

        same_projections = []
        # Are there at least two views different (not including the empty ones)?
        for view in self.tab_data_model.visible_views.value:
            view_projections = view.getProjections()
            # if no projections, the view is empty
            if not view_projections:
                pass
            elif not same_projections:
                same_projections = view_projections
            elif not projection_list_eq(same_projections, view_projections):
                break  # At least two views are different => leave the display as-is.

        else:  # All views are identical (or empty)
            self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_ONE

        # Change the focused view to the first non empty view, then display fullscreen
        # set the focused view to the view with the most streams.
        self.tab_data_model.focussedView.value = max(self.tab_data_model.visible_views.value,
                                                         key=lambda v:len(v.getStreams()))

        if not extend:
            # Force the canvases to fit to the content
            for vp in [self.panel.vp_inspection_tl,
                       self.panel.vp_inspection_tr,
                       self.panel.vp_inspection_bl,
                       self.panel.vp_inspection_br]:
                vp.canvas.fit_view_to_content()

        gc.collect()

    def set_ar_background(self, fn):
        """
        Load the data from the AR background file and apply to streams
        return (unicode): the filename as it has been accepted
        raise ValueError if the file is not correct or calibration cannot be applied
        """
        try:
            if fn == u"":
                logging.debug("Clearing AR background")
                cdata = None
            else:
                logging.debug("Loading AR background data")
                converter = dataio.find_fittest_converter(fn, mode=os.O_RDONLY)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_ar_data(data)

            # Apply data to the relevant streams
            ar_strms = [s for s in self.tab_data_model.streams.value
                        if isinstance(s, acqstream.ARStream)]

            # This might raise more exceptions if calibration is not compatible
            # with the data.
            for strm in ar_strms:
                strm.background.value = cdata

        except Exception as err:
            logging.info("Failed using file %s as AR background", fn, exc_info=True)
            msg = "File '%s' not suitable as angle-resolved background:\n\n%s"
            dlg = wx.MessageDialog(self.main_frame,
                                   msg % (fn, err),
                                   "Unusable AR background file",
                                   wx.OK | wx.ICON_STOP)
            dlg.ShowModal()
            dlg.Destroy()
            raise ValueError("File '%s' not suitable" % fn)

        return fn

    def set_spec_background(self, fn):
        """
        Load the data from a spectrum (background) file and apply to streams.
        :param fn: (str) The file name for the background image.
        :return (unicode): The filename as it has been accepted.
        :raise ValueError: If the file is not correct or calibration cannot be applied.
        """
        try:
            if fn == u"":
                logging.debug("Clearing spectrum background")
                cdata = None
            else:
                logging.debug("Loading spectrum background")
                converter = dataio.find_fittest_converter(fn, mode=os.O_RDONLY)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_spectrum_data(data)  # get the background image (can be an averaged image)

            # Apply data to the relevant streams
            spec_strms = [s for s in self.tab_data_model.streams.value
                        if isinstance(s, acqstream.StaticSpectrumStream)]
            spectrum = self._get_time_spectrum_streams(spec_strms)[0]

            for strm in spectrum:
                strm.background.value = cdata  # update the background VA on the stream -> recomputes image displayed

        except Exception as err:
            logging.info("Failed using file %s as background for currently loaded data", fn, exc_info=True)
            msg = "File '%s' not suitable as background for currently loaded data:\n\n%s"
            dlg = wx.MessageDialog(self.main_frame,
                                   msg % (fn, err),
                                   "Unusable spectrum background file",
                                   wx.OK | wx.ICON_STOP)
            dlg.ShowModal()
            dlg.Destroy()
            raise ValueError("File '%s' not suitable" % fn)

        return fn

    def set_temporalspec_background(self, fn):
        """
        Load the data from a temporal or angular spectrum (background) file and apply to streams.
        :param fn: (str) The file name for the background image.
        :return: (unicode) The filename as it has been accepted.
        :raise: ValueError, if the file is not correct or calibration cannot be applied.
        """
        try:
            if fn == u"":
                logging.debug("Clearing temporal spectrum background")
                cdata = None
            else:
                logging.debug("Loading temporal spectrum background")
                converter = dataio.find_fittest_converter(fn, mode=os.O_RDONLY)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_temporalspectrum_data(data)

            # Apply data to the relevant streams
            spec_strms = [s for s in self.tab_data_model.streams.value
                        if isinstance(s, acqstream.StaticSpectrumStream)]
            temporalspectrum = self._get_time_spectrum_streams(spec_strms)[1]
            angularspectrum = self._get_time_spectrum_streams(spec_strms)[3]

            for strm in temporalspectrum:
                strm.background.value = cdata

            for strm in angularspectrum:
                strm.background.value = cdata

        except Exception as err:
            logging.info("Failed using file %s as background for currently loaded file", fn, exc_info=True)
            msg = "File '%s' not suitable as background for currently loaded file:\n\n%s"
            dlg = wx.MessageDialog(self.main_frame,
                                   msg % (fn, err),
                                   "Unusable temporal spectrum background file",
                                   wx.OK | wx.ICON_STOP)
            dlg.ShowModal()
            dlg.Destroy()
            raise ValueError("File '%s' not suitable" % fn)

        return fn

    def set_spec_comp(self, fn):
        """
        Load the data from a spectrum calibration file and apply to streams
        return (unicode): the filename as it has been accepted
        raise ValueError if the file is not correct or calibration cannot be applied
        """
        try:
            if fn == u"":
                logging.debug("Clearing spectrum efficiency compensation")
                cdata = None
            else:
                logging.debug("Loading spectrum efficiency compensation")
                converter = dataio.find_fittest_converter(fn, mode=os.O_RDONLY)
                data = converter.read_data(fn)
                # will raise exception if doesn't contain good calib data
                cdata = calibration.get_spectrum_efficiency(data)

            spec_strms = [s for s in self.tab_data_model.streams.value
                          if isinstance(s, acqstream.SpectrumStream)]

            for strm in spec_strms:
                strm.efficiencyCompensation.value = cdata

        except Exception as err:
            logging.info("Failed using file %s as spec eff coef", fn, exc_info=True)
            msg = "File '%s' not suitable for spectrum efficiency compensation:\n\n%s"
            dlg = wx.MessageDialog(self.main_frame,
                                   msg % (fn, err),
                                   "Unusable spectrum efficiency file",
                                   wx.OK | wx.ICON_STOP)
            dlg.ShowModal()
            dlg.Destroy()
            raise ValueError("File '%s' not suitable" % fn)

        return fn

    def _on_point_select(self, point):
        """ Bring the angular viewport to the front when a point is selected in the 1x1 view """
        if None in point:
            return  # No point selected => nothing to force
        # TODO: should we just switch to 2x2 as with the pixel and line selection?
        if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
            self.tab_data_model.focussedView.value = self.panel.vp_angular.view

    def _on_pixel_select(self, pixel):
        """ Switch the the 2x2 view when a pixel is selected """
        if None in pixel:
            return  # No pixel selected => nothing to force
        if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
            self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22

    def _on_line_select(self, line):
        """ Switch the the 2x2 view when a line is selected """
        if (None, None) in line:
            return  # No line selected => nothing to force
        if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_ONE:
            self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22

    @classmethod
    def get_display_priority(cls, main_data):
        # high priority for viewer
        if main_data.is_viewer:
            return 1
        # Don't display tab for FastEM
        if main_data.role in ("mbsem",):
            return None
        else:
            return 0
