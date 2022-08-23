# -*- coding: utf-8 -*-
"""
:created: 2016-28-01
:author: Kimon Tsitsikas
:copyright: © 2015-2016 Kimon Tsitsikas, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from collections import OrderedDict
import logging
from odemis.util import dataio as udataio
from odemis.dataio import get_converter
from odemis.gui.comp import popup
from odemis.gui.conf import get_acqui_conf
from odemis.gui.util import call_in_wx_main, formats_to_wildcards
from odemis.gui.util.img import ar_to_export_data, spectrum_to_export_data, \
    images_to_export_data, line_to_export_data, temporal_spectrum_to_export_data, \
    chronogram_to_export_data, angular_spectrum_to_export_data, theta_to_export_data
from odemis.util.dataio import splitext
import os
import time
import wx


PR_PREFIX = "Print-ready"
PP_PREFIX = "Post-processing"

# Dict str -> (tuple of str, tuple of str):
#   export type -> possible exporters for PR, possible exporters for PP
EXPORTERS = {"spatial": (("PNG", "TIFF"), ("Serialized TIFF",)),
             "AR": (("PNG", "TIFF"), ("CSV",)),
             "spectrum": (("PNG", "TIFF"), ("CSV",)),
             "spectrum-line": (("PNG", "TIFF"), ("CSV",)),
             "spectrum-temporal": (("PNG", "TIFF"), ("CSV",)),
             "spectrum-angular": (("PNG", "TIFF"), ("CSV",)),
             "angle": (("PNG", "TIFF"), ("CSV",)),
             "spectrum-time": (("PNG", "TIFF"), ("CSV",)),
             }


class ExportController(object):
    """
    Manages the export of the data displayed in the focused view of a tab to
    an easy for post-process format.
    """

    def __init__(self, tab_data, main_frame, tab_panel, viewports):
        """
        tab_data: MicroscopyGUIData -- the representation of the microscope GUI
        main_frame: (wx.Frame): the whole GUI frame
        """
        self._data_model = tab_data
        self._main_frame = main_frame
        self._tab_panel = tab_panel
        self._viewports = list(viewports.keys())
        self._conf = get_acqui_conf()

        # Listen to "export" button and menu
        self._tab_panel.btn_secom_export.Bind(wx.EVT_BUTTON, self.on_export)
        self._main_frame.Bind(wx.EVT_MENU, self.on_export, id=self._main_frame.menu_item_export_as.GetId())
        self._main_frame.menu_item_export_as.Enable(False)

        # subscribe to get notified about tab changes
        self._prev_streams = None # To unsubscribe afterwards
        tab_data.main.tab.subscribe(self.on_tab_change, init=True)

    @call_in_wx_main
    def on_tab_change(self, tab):
        """ Subscribe to the the current tab """
        # Only let Export to be enabled in "our" tab, ie the analysis tab
        if tab is not None and tab.tab_data_model is self._data_model:
            self._data_model.focussedView.subscribe(self.on_view_change, init=True)
        else:
            self._data_model.focussedView.unsubscribe(self.on_view_change)
            self._main_frame.menu_item_export_as.Enable(False)
            self._tab_panel.btn_secom_export.Enable(False)

    def on_view_change(self, view):
        """
        Check whether the current view has any stream to export
        """
        if self._prev_streams:
            self._prev_streams.unsubscribe(self.on_streams_change)
            self._prev_streams = None

        view.stream_tree.flat.subscribe(self.on_streams_change, init=True)
        self._prev_streams = view.stream_tree.flat

    @call_in_wx_main
    def on_streams_change(self, streams):
        """ Enable Export menu item iff the tab has at least one stream """

        enabled = (len(streams) > 0)
        self._main_frame.menu_item_export_as.Enable(enabled)
        self._tab_panel.btn_secom_export.Enable(enabled)

    def on_export(self, event):
        """
        Runs the whole export process, from asking the user to pick a file, to
        showing the file has been successfully exported.
        Must be run in GUI main thread
        """
        filepath, export_format, export_type = self._get_export_info()
        if filepath is not None:
            # TODO: run the real export function in a future, and based on the
            # result, show a message box or popup or log message
            self.export_viewport(filepath, export_format, export_type)

    def _get_export_info(self):
        """
        Return str, str, str: full filename, exporter name, export type
          Full filename is None if cancelled by user
        """
        # Set default to the first of the list
        view = self._data_model.focussedView.value
        export_type = self.get_export_type(view)
        formats = EXPORTERS[export_type]
        if self._conf.export_raw:
            default_exporter = get_converter(formats[1][0])
        else:
            default_exporter = get_converter(formats[0][0])
        extension = default_exporter.EXTENSIONS[0]

        batch_export = False

        # Suggested name= current file name + stream/view name + extension of default format
        fi = self._data_model.acq_fileinfo.value
        if fi is not None and fi.file_name:
            basename = os.path.basename(fi.file_name)
            # Remove the extension
            basename, _ = udataio.splitext(basename)

            # Use stream name, if there is just one stream, otherwise use the view name
            streams = view.getStreams()
            if len(streams) == 1:
                basename += " " + streams[0].name.value
            else:
                # TODO: remove numbers from the view name?
                basename += " " + view.name.value

            # Special batch export for AR view of polarization or polarimetry,
            # as they contain typically 6 or 24 images.
            if (export_type == 'AR' and streams and
                 (hasattr(streams[0], "polarimetry") or
                  (hasattr(streams[0], "polarization") and len(streams[0].polarization.choices) > 1)
               )):
                batch_export = True

        else:
            basename = time.strftime("%Y%m%d-%H%M%S", time.localtime())

        filepath = os.path.join(self._conf.last_export_path, basename + extension)
        # filepath will be None if cancelled by user

        return self.ShowExportFileDialog(filepath, default_exporter, batch_export)

    @call_in_wx_main
    def export_viewport(self, filepath, export_format, export_type):
        """ Export the image from the focused view to the filesystem.

        :param filepath: (str) full path to the destination file
        :param export_format: (str) the format name
        :param export_type: (str) spatial, AR, spectrum or spectrum-line
        """
        try:
            exporter = get_converter(export_format)
            raw = export_format in EXPORTERS[export_type][1]
            self._conf.export_raw = raw
            self._conf.last_export_path = os.path.dirname(filepath)

            exported_data = self.export(export_type, raw)

            # batch export
            # TODO: for now we do not create a new folder where all files are saved

            if isinstance(exported_data, dict):
                # get the file names
                filename_dict = {}
                n_exist = 0
                dir_name, base = os.path.split(filepath)
                base_name, file_extension = splitext(base)
                for key in exported_data.keys():
                    # use the filename defined by the user and add the MD_POL_MODE to the filename
                    filename = os.path.join(dir_name, base_name + "_" + key + file_extension)

                    # detect we'd overwrite an existing file => show our own warning
                    if os.path.exists(filename):
                        n_exist += 1

                    filename_dict[key] = filename

                if n_exist:
                    dlg = wx.MessageDialog(self._main_frame,
                                           "Some files (%d/%d) already exists.\n"
                                           "Do you want to replace them?" % (n_exist, len(filename_dict)),
                                           "Files already exist",
                                           wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
                    ret = dlg.ShowModal()
                    dlg.Destroy()
                    if ret == wx.ID_NO:
                        return

                # record everything to a file
                for key, data in exported_data.items():
                    exporter.export(filename_dict[key], data)

                # TODO need to be adapted for batch export as now redundant
                popup.show_message(self._main_frame,
                                   "Images exported",
                                   "Stored as %s"
                                   % (os.path.join(dir_name, base_name + "_" + "xxx" + file_extension),),
                                   timeout=3
                                   )

                logging.info("Exported a %s view into files with name of type '%s'.",
                             export_type, os.path.join(dir_name, base_name + "_" + "mode" + file_extension))

            else:  # single file export
                exporter.export(filepath, exported_data)

                popup.show_message(self._main_frame,
                                   "Image exported",
                                   "Stored in %s" % (filepath,),
                                   timeout=3
                                   )

                logging.info("Exported a %s view into file '%s'.", export_type, filepath)
        except LookupError as ex:
            logging.info("Export of a %s view as %s seems to contain no data.",
                         export_type, export_format, exc_info=True)
            dlg = wx.MessageDialog(self._main_frame,
                                   "Failed to export: %s\n"
                                   "Please make sure that at least one stream is visible in the current view." % (ex,),
                                   "No data to export",
                                   wx.OK | wx.ICON_WARNING)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception:
            logging.exception("Failed to export a %s view as %s", export_type, export_format)

    def get_export_type(self, view):
        """
        Based on the given view gives the corresponding export type
        return (string): spatial, AR, spectrum or spectrum-line
        """
        view_name = view.name.value
        # TODO: just use another dict
        if view_name == 'Angle-resolved' or view_name == 'Polarimetry':
            export_type = 'AR'
        elif view_name == 'Spectrum plot':
            export_type = 'spectrum'
        elif view_name == 'Spectrum cross-section':
            export_type = 'spectrum-line'
        elif view_name == 'Temporal spectrum':
            export_type = 'spectrum-temporal'
        elif view_name == 'AR Spectrum':
            export_type = 'spectrum-angular'
        elif view_name == 'Theta':
            export_type = 'angle'
        elif view_name == 'Chronograph':
            export_type = 'spectrum-time'
        else:
            export_type = 'spatial'
        return export_type

    def export(self, export_type, raw=False):
        """
        Returns the data to be exported with respect to the settings and options.

        :param export_type (string): spatial, AR, spectrum, spectrum-temporal,
            spectrum-time, or spectrum-line, spectrum-angular
        :param raw (boolean): raw data format if True
        :param interpolate_data (boolean): apply interpolation on data if True

        returns DataArray or list of DataArray: the data to be exported, either
          an RGB image or raw data.
        raises:
            LookupError: if no data found to export
        """
        fview = self._data_model.focussedView.value
        vp = self.get_viewport_by_view(fview)
        streams = fview.stream_tree.getProjections()  # Stream tree to get the DataProjection
        if export_type == 'AR':
            exported_data = ar_to_export_data(streams, raw)
        elif export_type == 'spectrum':
            exported_data = spectrum_to_export_data(streams[0], raw, vp)
        elif export_type == 'spectrum-line':
            exported_data = line_to_export_data(streams[0], raw)
        elif export_type == 'spectrum-temporal':
            exported_data = temporal_spectrum_to_export_data(streams[0], raw)
        elif export_type == 'spectrum-angular':
            exported_data = angular_spectrum_to_export_data(streams[0], raw)
        elif export_type == 'spectrum-time':
            exported_data = chronogram_to_export_data(streams[0], raw, vp)
        elif export_type == 'angle':
            exported_data = theta_to_export_data(streams[0], raw, vp)
        else:
            view_px = tuple(vp.canvas.ClientSize)
            view_mpp = fview.mpp.value
            view_hfw = (view_mpp * view_px[0], view_mpp * view_px[1])
            view_pos = fview.view_pos.value
            draw_merge_ratio = fview.stream_tree.kwargs.get("merge", 0.5)
            interpolate_data = fview.interpolate_content.value
            exported_data = images_to_export_data(streams,
                                                  view_hfw, view_pos,
                                                  draw_merge_ratio, raw, vp.canvas,
                                                  interpolate_data=interpolate_data,
                                                  logo=self._main_frame.legend_logo)

        return exported_data

    def get_viewport_by_view(self, view):
        """ Return the ViewPort associated with the given view """

        for vp in self._viewports:
            if vp.view == view:
                return vp
        raise LookupError("No ViewPort found for view %s" % view)

    def ShowExportFileDialog(self, filename, default_exporter, batch_export):
        """
        filename (string): full filename to propose by default
        default_exporter (module): default exporter to be used
        batch_export (bool): If True, indicates that multiple files will be created (using the filename as a prefix).
        return (string or None): the new filename (or the None if the user cancelled)
                (string): the format name
                (string): spatial, AR, spectrum or spectrum-line
        """
        # Find the available formats (and corresponding extensions) according
        # to the export type
        export_type = self.get_export_type(self._data_model.focussedView.value)
        formats_to_ext = self.get_export_formats(export_type)

        # current filename
        path, base = os.path.split(filename)
        uformats_to_ext = OrderedDict(formats_to_ext.values())
        wildcards, uformats = formats_to_wildcards(uformats_to_ext, suffix="")

        # TODO: want to show a DirDialog for batch export
        #  need to write own class which shows the possible fileextensions in dirdialog

        dialog = wx.FileDialog(self._main_frame,
                                   message="Choose a filename and destination",
                                   defaultDir=path,
                                   defaultFile="",
                                   style=wx.FD_SAVE,  # | wx.FD_OVERWRITE_PROMPT,
                                   wildcard=wildcards)

        # TODO adjust code for dirdialog
        # Select the default format
        default_fmt = default_exporter.FORMAT
        try:
            uf = formats_to_ext[default_fmt][0]
            idx = uformats.index(uf)
        except ValueError:
            idx = 0
        dialog.SetFilterIndex(idx)

        # Strip the extension, so that if the user changes the file format,
        # it will not have 2 extensions in a row.
        if base.endswith(default_exporter.EXTENSIONS[0]):
            base = base[:-len(default_exporter.EXTENSIONS[0])]
        dialog.SetFilename(base)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return None, default_fmt, export_type

        # New location and name have been selected...
        # Store the path
        path = dialog.GetDirectory()

        # Store the format
        ufmt = uformats[dialog.GetFilterIndex()]
        for f, (uf, _) in formats_to_ext.items():
            if uf == ufmt:
                fmt = f
                break
        else:
            logging.debug("Failed to link %s to a known format", ufmt)
            fmt = default_fmt

        # Check the filename has a good extension, or add the default one
        fn = dialog.GetFilename()
        ext = None
        for extension in formats_to_ext[fmt][1]:
            if fn.endswith(extension) and len(extension) > len(ext or ""):
                ext = extension

        if ext is None:
            if fmt == default_fmt and default_exporter.EXTENSIONS[0] in formats_to_ext[fmt]:
                # if the format is the same (and extension is compatible): keep
                # the extension. This avoid changing the extension if it's not
                # the default one.
                ext = default_exporter.EXTENSIONS[0]
            else:
                ext = formats_to_ext[fmt][1][0]  # default extension
            fn += ext

        fullfn = os.path.join(path, fn)

        # As we strip the extension from the filename, the normal dialog cannot
        # detect we'd overwrite an existing file => show our own warning
        if os.path.exists(fullfn) and not batch_export:
            # TODO if batch_export implemented, check all filenames here!
            dlg = wx.MessageDialog(self._main_frame,
                                   "A file named \"%s\" already exists.\n"
                                   "Do you want to replace it?" % (fn,),
                                   "File already exists",
                                   wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
            ret = dlg.ShowModal()
            dlg.Destroy()
            if ret == wx.ID_NO:
                return None, default_fmt, export_type

        return fullfn, fmt, export_type

    def get_export_formats(self, export_type):
        """
        Find the available file formats for the given export_type
        export_type (string): spatial, AR, spectrum or spectrum-line
        return (dict string -> (string, list of strings)):
             name of each format -> nice name of the format, list of extensions.
        """
        pr_formats, pp_formats = EXPORTERS[export_type]

        export_formats = OrderedDict()
        # Look dynamically which format is available
        # First the print-ready formats
        for format_data in pr_formats:
            exporter = get_converter(format_data)
            export_formats[exporter.FORMAT] = (PR_PREFIX + " " + exporter.FORMAT, exporter.EXTENSIONS)
        # Now for post-processing formats
        for format_data in pp_formats:
            exporter = get_converter(format_data)
            export_formats[exporter.FORMAT] = (PP_PREFIX + " " + exporter.FORMAT, exporter.EXTENSIONS)

        if not export_formats:
            logging.error("No file converter found!")

        return export_formats
