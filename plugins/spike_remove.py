# -*- coding: utf-8 -*-
'''
Created on 19 December 2018

@author: Toon Coenen and Eric Piel

Adds a spike removal feature in the GUI, which allows detecting and removing
extreme peaks in (spectral) data. Such peaks are typically caused by cosmic
rays hitting the CCD during acquisition, and are not representative of the sample
observed.

======================================================================
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

The software is provided "as is", without warranty of any kind,
express or implied, including but not limited to the warranties of
merchantability, fitness for a particular purpose and non-infringement.
In no event shall the authors be liable for any claim, damages or
other liability, whether in an action of contract, tort or otherwise,
arising from, out of or in connection with the software or the use or
other dealings in the software.
'''

from collections import OrderedDict
import logging
from odemis import dataio, model
from odemis.acq.stream import SpectrumStream
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import call_in_wx_main
from odemis.util.dataio import open_acquisition
from odemis.gui.win.acquisition import ShowAcquisitionFileDialog
from odemis.acq.stream import DataProjection
import wx
import numpy
import os.path


class SpikeRemovalPlugin(Plugin):
    name = "Spike removal"
    __version__ = "1.1"
    __author__ = "Toon Coenen and Eric Piel"
    __license__ = "Public domain"

    vaconf = OrderedDict((
        ("threshold", {
            "tooltip": "Sensitivity threshold spike removal (the lower, the more sensitive)",
            "range": (1, 20),
        }),
        ("npixels", {
            "label": "Corrected pixels",
            "tooltip": "Number of e-beam positions where a spike (or more) was detected and corrected"
        }),
        ("nspikes", {
            "label": "Corrected spikes",
            "tooltip": "Total number of spikes corrected"
        }),
    ))

    def __init__(self, microscope, main_app):
        super(SpikeRemovalPlugin, self).__init__(microscope, main_app)
        self.addMenu("Data correction/Spike removal...", self.start)
        self._dlg = None
        self._spec_stream = None

        # create VA for threshold
        self.threshold = model.FloatContinuous(8, range=(1, 20), unit="")
        self.npixels = model.IntVA(0, unit="", readonly=True)
        self.nspikes = model.IntVA(0, unit="", readonly=True)

    def start(self):
        dlg = AcquisitionDialog(self, "Remove spikes from CL data",
                                text="Change the threshold value to determine the sensitivity of the spike removal")
        self._dlg = dlg
        self.tab_data = self.main_app.main_data.tab.value.tab_data_model

        # don't allow adding/removing streams
        dlg.streambar_controller.to_static_mode()

        for stream in self.tab_data.streams.value:
            #there must be a better way to do this
            if isinstance(stream, SpectrumStream):
                # Check the stream is really _spectrum_ data, CTYX or TYX are not supported
                raw_shape = stream.raw[0].shape  # CTZYX
                if numpy.prod(raw_shape[1:3]) > 1:  # T * Z
                    logging.info("Skipping stream %s of shape %s, as it not a simple spectral stream",
                                 stream.name, raw_shape)
                    continue
                dlg.addStream(stream)
                self._spec_stream = stream
                break  # Only one stream handled
        else:
            # if no spectral data is present spike removal cannot be done (for now)
            box = wx.MessageDialog(self.main_app.main_frame,
                   "No spectral stream is present, so it's not possible to correct the data",
                   "No spectral stream", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        wx.CallAfter(dlg.viewport_l.canvas.fit_view_to_content)  # async, to call after the stream is added
        dlg.addSettings(self, conf=self.vaconf)
        # TODO: add a 'reset' button
        dlg.addButton("Close")
        dlg.addButton("Correct", self.correct_data, face_colour='red')
        dlg.addButton("Save", self.save, face_colour='blue')
        self._update_save_button()  # It'll be called _after_ the button is added

        dlg.Size = (1000, 600)  # Make it big enough to fit the view and the stream panel
        dlg.ShowModal()

        # The end
        self._spec_stream = None  # drop reference
        dlg.Close()
        self._dlg = None
        
        if dlg: # If dlg hasn't been destroyed yet
            dlg.Destroy()

    def _update_spike_pix(self, npixels, nspikes):
        """
        Updates the npixels and nspikes VAs
        """
        self.npixels._set_value(npixels, force_write=True)
        self.nspikes._set_value(nspikes, force_write=True)

    @call_in_wx_main
    def _update_save_button(self):
        """
        Enable the "Save" button iff the data has already been corrected
        """
        # Note: it's important that it's run with @call_in_wx_main, to ensure
        # that it's run asynchronously, and so at dialog creation, it's called
        # after adding the buttons (which are also asynchronously created).
        self._dlg.buttons[2].Enable(hasattr(self._spec_stream, "_orig_raw"))

    @call_in_wx_main
    def save(self, dlg):

        if not hasattr(self._spec_stream, "_orig_raw"):
            box = wx.MessageDialog(self.main_app.main_frame,
                   "No correction was applied",
                   "No correction", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        fn = self.tab_data.acq_fileinfo.value.file_name

        # Store all the data present in the original file => just open it again.
        das_orig = open_acquisition(fn)
        das = []
        for da in das_orig:
            # Is it the stream that we've corrected?
            if (self._spec_stream.raw[0].metadata == da.metadata and
                 self._spec_stream.raw[0].shape == da.shape):
                das.append(self._spec_stream.raw[0])
            else:
                das.append(da)

        # Ask for filename, with default to original filename + _corrected
        # TODO: smart naming scheme if file already exists.
        basefn, ext = os.path.splitext(fn)
        cfn = basefn + "_corrected" + ext
        cfn = ShowAcquisitionFileDialog(dlg, cfn)
        exporter = dataio.find_fittest_converter(cfn)
        if cfn is not None:
            exporter.export(cfn, das)
        else:
            logging.debug("Saving cancelled")

        dlg.Close()

    def correct_data(self, dlg):
        """
        Remove the spikes of self._spec_stream
        """
        # We keep the original raw data in a special ._orig_raw, and will put the
        # corrected data in .raw (so that the correction is displayed). If the
        # runs the correction again, it will be done on the original data (so
        # the correction is run from scratch every time, and not from the data
        # already cleaned-up).
        try:
            raw_spec_dat = self._spec_stream._orig_raw
        except AttributeError:  # No orig_raw yet
            raw_spec_dat = self._spec_stream.raw[0]
            self._spec_stream._orig_raw = raw_spec_dat

        corrected_spec, npixels, nspikes = self.removespikes_spec(raw_spec_dat)
        full_cordata = model.DataArray(corrected_spec, metadata=raw_spec_dat.metadata)
        self._spec_stream.raw[0] = full_cordata
        self._force_update_spec(self._spec_stream)

        self._update_spike_pix(npixels, nspikes)
        self._update_save_button()

    def removespikes_spec(self, raw_spec_dat):
        """
        raw_spec_dat (numpy.array of shape C11YX)
        returns:
           corrected_data (numpy.array of shape C11YX)
           pixel_corrected (int)
           spikes corrected (int)
        """
        # The spike detection is performed by comparing the signal differential
        # with the average differential in the scan. If the differential for a
        # given pixel exceeds a given threshold (spikestep), it will be marked
        # as a spike. Subsequently, the identified pixels will be corrected
        # using the values in neighboring pixels in the spectrum.

        spikestep = self.threshold.value
        specdat = numpy.squeeze(raw_spec_dat.copy())
        assert specdat.ndim == 3
        # this diff calculation requires higher numerical precision than 16 bits because it is squared.
        # 32 uint should be good enough as the max diff < 2**16. However for the summation of ms_step it is more convenient to use float32.
        # Maybe there is a trick to stick with uint32?
        diffspec = numpy.diff(numpy.float32(specdat), axis=0) ** 2
        size = numpy.shape(diffspec)
        ms_step = (diffspec / numpy.prod(size)).sum()
        
        # We are now calculating the threshold based on the global average.
        # Using a more local average could help identifying spikes
        # more precisely although but it is more involved and possibly overkill
        threshold = ms_step * spikestep**2     
        # this sets sensitivity to spikes. Used in combination with this number
        # is rather large now, we could be a bit more clever if a spike covers more pixels
        spike_margin = 1  # number of pixels left and right of spike that are also corrected
        spike_spacing = 3  # when spikes are considered to be two separate spikes
        npixels = 0  # number of corrected pixels (aka single spectrum)
        nspikes = 0  # spike counter
    
        # Look at each spectrum independently (as they were acquired independently)
        for ii in range(size[1]):
            for jj in range(size[2]):
                specdiff = diffspec[:, ii, jj]
                spec = specdat[:, ii, jj]
                # These are the indices of the spike starts and ends.
                spike_indices = numpy.argwhere(specdiff > threshold)
                num_spike_indices = numpy.size(spike_indices)
             
                if num_spike_indices > 1: # only one step that deviates is no spike.
                    npixels += 1
                    spike_indices = numpy.squeeze(spike_indices)
                    dif_spike = numpy.diff(spike_indices)

                    # check whether there is more than one spike in the spectrum.
                    # The first spike always starts at the first element of
                    # spike_indices whereas the last spike always ends on the spike_indices
                    spike_edges = numpy.argwhere(dif_spike > spike_spacing)
                    # subtract 1 from the num_spike_indices to make it work for the last pixel
                    spike_edges = numpy.append(spike_edges, num_spike_indices - 1)

                    # distinguish spikes within spectrum
                    for pp, se in enumerate(spike_edges):
                        nspikes += 1

                        if pp == 0:
                            spike_indices1 = spike_indices[0:(se + 1)]
                        else:
                            spike_indices1 = spike_indices[(spike_edges[pp - 1] + 1):(se + 1)]

                        min_edge = spike_indices1.min() - spike_margin
                        max_edge = spike_indices1.max() + spike_margin

                        if min_edge > 0 and max_edge < size[0]:
                            # correction with appropriate slope
                            line = numpy.linspace(spec[min_edge], spec[max_edge], (max_edge - min_edge) + 1)
                            spec[min_edge:max_edge + 1] = line

                        # fix for when cosmic ray occurs within first few CCD pixels such that it is impossible to take the average in earlier pixels
                        elif min_edge <= 0:
                            line = numpy.linspace(spec[0], spec[max_edge], max_edge + 1)
                            spec[0:max_edge + 1] = line

                        # fix for when cosmic ray occurs within last few CCD pixels such that it is impossible to take the average in earlier pixel
                        elif max_edge >= size[0]:
                            line = numpy.linspace(spec[min_edge], spec[size[0]], size[0] - min_edge + 1)
                            spec[min_edge:size[0] + 1] = line

                    # include corrected spectrum in dataset
                    specdat[:, ii, jj] = spec

        logging.debug("Number of corrected scan pixels %s", npixels)
        logging.debug("Number of corrected spikes %s", nspikes)

        specdat.shape = raw_spec_dat.shape  # add back the TZ dimensions
        return specdat, npixels, nspikes

    def _force_update_spec(self, st):
        """
        Force updating the projection of the given stream
        """
        if hasattr(st, "_updateCalibratedData"):
            st._updateCalibratedData(bckg=st.background.value, coef=st.efficiencyCompensation.value)
            return
        else:
            logging.warning("Spectrum Stream doesn't have a ._updateCalibratedData")
            # Will use the "standard" way to update the projection

        # Update in the view of the window, and also the current tab
        views = [self._dlg.view]
        views.extend(self.tab_data.views.value)
        for v in views:
            for sp in v.stream_tree.getProjections():  # stream or projection
                if isinstance(sp, DataProjection):
                    s = sp.stream
                else:
                    s = sp
                if s is st:
                    logging.debug("Updating projection %s", sp)
                    sp._shouldUpdateImage()
