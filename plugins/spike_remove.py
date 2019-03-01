# -*- coding: utf-8 -*-
'''
Created on 10 May 2016

@author: Lennard Voortman

Gives ability to manually change the overlay-metadata.

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

from __future__ import division
from collections import OrderedDict
import functools
import logging
import math
from odemis import dataio, model, acq
from odemis.acq.stream import SpectrumStream
from odemis.gui.plugin import Plugin, AcquisitionDialog
from odemis.gui.util import call_in_wx_main
from odemis.util.dataio import open_acquisition
from odemis.gui.win.acquisition import ShowAcquisitionFileDialog
from odemis.acq.stream import DataProjection
import wx
import numpy
import os.path

class SpikeRemoval_Plugin(Plugin):
    name = "Spike removal"
    __version__ = "1.1"
    __author__ = "Toon Coenen and Eric Piel"
    __license__ = "Public domain"

    vaconf = OrderedDict((
        ("threshold", {
            "tooltip": "Sensitivity threshold spike removal",
            "range": (1, 20),
            "label": "Threshold",
        }),
        ("npixels", {
         "label": "Corrected pixels",
        }),
        ("nspikes", {
         "label": "Corrected spikes",
        }),
    ))

    def __init__(self, microscope, main_app):
        super(SpikeRemoval_Plugin, self).__init__(microscope, main_app)
        self.addMenu("Data Correction/Spike removal...", self.start)
        self._dlg = None
        # create VA for threshold
        self.threshold = model.FloatContinuous(8, range=(1, 20), unit="")



    def start(self):
        dlg = AcquisitionDialog(self, "Remove spikes from CL data",
                                text="Change the threshold value to determine the sensitivity of the spike removal")
        self._dlg = dlg
        self.tab_data = self.main_app.main_data.tab.value.tab_data_model
        if not self.tab_data.streams.value:
            box = wx.MessageDialog(self.main_app.main_frame,
                       "No stream is present, so it's not possible to correct the data",
                       "No stream", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        self.raw_spec_dat = None
        self._spec_stream = None
        self._is_corrected = False
        self.npixels = model.IntVA(0, unit="", readonly=True)
        self.nspikes = model.IntVA(0, unit="", readonly=True)

        self.npixels.subscribe(self._update_spike_pix)
        self.nspikes.subscribe(self._update_spike_pix)

        for stream in self.tab_data.streams.value:
            #there must be a better way to do this
            if isinstance(stream, SpectrumStream):
                dlg.addStream(stream)
                self._spec_stream = stream
                break  # Only one stream handled
        else:
            # if no spectral data is present spike removal cannot be done (for now)
            box = wx.MessageDialog(self.main_app.main_frame,
                   "No spectral stream is present, so it's not possible to correct the data",
                   "No stream", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        # We keep the original raw data in ._orig_raw, and will update .raw so that the correction is displayed
        try:
            self.raw_spec_dat = self._spec_stream._orig_raw
        except AttributeError:  # No orig_raw yet
            self.raw_spec_dat = self._spec_stream.raw[0]
            stream._orig_raw = self.raw_spec_dat

        dlg.viewport_l.canvas.fit_view_to_content()
        dlg.addSettings(self, conf=self.vaconf)
        # TODO: add a 'reset' button
        dlg.addButton("Close")
        #dlg.addButton("Correct data", self.correct, face_colour='red')
        dlg.addButton("Correct", self.correct_data, face_colour='red')
        dlg.addButton("Save", self.save, face_colour='blue')
        dlg.ShowModal()

        # The end
        dlg.Destroy()
        self._dlg = None
        
        if dlg: # If dlg hasn't been destroyed yet
            dlg.Destroy()

        #self.showAcquisition(cfn)

    def _update_spike_pix(self, _=None):
        """
        Called when VA that affects the expected duration is changed
        """

        self.npixels._set_value(self.npixels_cor, force_write=True)
        self.nspikes._set_value(self.nspikes_cor, force_write=True)


    @call_in_wx_main
    def save(self, dlg):

        if not self._is_corrected:
            box = wx.MessageDialog(self.main_app.main_frame,
                   "No correction was applied",
                   "No correction", wx.OK | wx.ICON_STOP)
            box.ShowModal()
            box.Destroy()
            return

        fn = self.tab_data.acq_fileinfo.value.file_name

        #das = [self._spec_stream.raw[0]]
        # #Todo: also include drift data/preview
        # for stream in self.tab_data.streams.value:
        #     #there must be a better way to do this
        #     if not isinstance(stream, SpectrumStream):
        #         das.extend(stream.raw)

        das_orig = open_acquisition(fn)
        das = []
        for da in das_orig:
            # Is it the stream that we've corrected?
            if (self._spec_stream.raw[0].metadata == da.metadata and
                 self._spec_stream.raw[0].shape == da.shape):
                das.append(self._spec_stream.raw[0])
            else:
                das.append(da)

        # read filename
        exporter = dataio.find_fittest_converter(fn)
        #export corrected file, todo smart naming scheme if file already exists.
        basefn, ext = os.path.splitext(fn)
        cfn = basefn + "_corrected" + ext
        cfn = ShowAcquisitionFileDialog(dlg, cfn)
        if cfn is not None:
            exporter.export(cfn, das)
        else:
            logging.debug("Saving cancelled")

        dlg.Close()
    
    def correct_data(self, dlg):


        corrected_spec = self.removespikes_spec(self.raw_spec_dat)
        full_cordata = model.DataArray(corrected_spec, metadata=self.raw_spec_dat.metadata)
        self._update_spike_pix()
        self._spec_stream.raw[0] = full_cordata
        self._force_update_spec(self._spec_stream)
        #this can probably be done in a more clever way
        self._is_corrected = True

    def removespikes_spec(self, raw_spec_dat):

        spikestep = self.threshold.value
        specdat = numpy.squeeze(raw_spec_dat.copy())
        #this diff calculation requires higher numerical precision than 16 bits because it is squared.
        # 32 uint should be good enough as the max diff < 2**16. However for the summation of ms_step it is more convenient to use float32.
        # Maybe there is a trick to stick with uint32?
        diffspec = numpy.diff(numpy.float32(specdat),axis=0)**2
        size = numpy.shape(diffspec)
    
        if numpy.ndim(diffspec) == 3:
            ms_step = (diffspec/(size[0]*size[1]*size[2])).sum()
        elif numpy.ndim(diffspec) == 2:
            ms_step = (diffspec/(size[0]*size[1])).sum()
        elif numpy.ndim(diffspec) == 1:
            ms_step = (diffspec/(size[0])).sum()
        
        # we are now calculating the threshold based on the global average. Using a more local average could help identifying spikes
        #more precisely although but it is more involved and possibly overkill
        threshold = ms_step * spikestep**2     
         # this sets sensitivity to spikes. Used in combination with
    #    spikewidth = 3 # maximum width of peak that is still considered one spike
        # this number is rather large now, we could be a bit more clever if a spike covers more pixels
        spikeedge = 1 # number of pixels left and right of spike that are also corrected
        #avgpixels = 3 # pix
        spike_spacing = 3  #when spikes are considered to be two seperate spikes
        kCL = 0    #number of corrected pixels counter
        kpix = 0 # spike counter
    
    
        for ii in range(0,size[1]):
                 
            for jj in range(0,size[2]):
               
                specdiff = diffspec[:,ii,jj]
                spec = specdat[:,ii,jj]
                spike_indices = numpy.argwhere(specdiff > threshold) # These are the indices of the spike starts and ends. These need
                num_spike_indices = numpy.size(spike_indices)
             
                if num_spike_indices > 1: # only one step that deviates is no spike.
                  
                    kCL = kCL+1
                    spike_indices = numpy.squeeze(spike_indices)
                    dif_spike = numpy.diff(spike_indices)

                    #check whether there is more than one spike in the spectrum. The first spike always starts at the first element
                    # of spike_indices whereas the last spike always ends on the spike_indices        
                    
                    spike_edges = numpy.argwhere(dif_spike > spike_spacing)
                    #subtract 1 from the num_spike_indices to make it work for the last pixel
                    spike_edges = numpy.append(spike_edges,num_spike_indices-1)


                    #number_of_spikes in spectrum
                    nspikes_inspec = numpy.size(spike_edges)
              
                    #distinguish spikes within spectrum
                    for pp in range(0,nspikes_inspec):
                        
                        kpix = kpix+1
                        
                        if pp == 0:
                            spike_indices1 = spike_indices[0:(spike_edges[pp]+1)]
                            
                        else:
                            spike_indices1 = spike_indices[(spike_edges[pp-1]+1):spike_edges[pp]+1]
                        
                        min_spike = spike_indices1.min()
                        max_spike = spike_indices1.max()
                        min_edge = min_spike-spikeedge
                        max_edge = max_spike+spikeedge

                        if (min_edge > 0) and (max_edge < size[0]):

                            # correction with appropriate slope
                            line = numpy.linspace(spec[min_edge], spec[max_edge], (max_edge - min_edge) + 1)
                            # avg = np.mean(spec[(min_spike-avgpixels):min_spike])
                            spec[min_edge:max_edge + 1] = line

                        # fix for when cosmic ray occurs within first few CCD pixels such that it is impossible to take the average in earlier pixels
                        elif min_edge <= 0:

                            line = numpy.linspace(spec[0], spec[max_edge], max_edge + 1)
                            # avg = np.mean(spec[(max_spike+2):(max_spike+avgpixels+2)])
                            spec[0:max_edge + 1] = line

                        # fix for when cosmic ray occurs within last few CCD pixels such that it is impossible to take the average in earlier pixel
                        elif max_edge >= size[0]:

                            line = numpy.linspace(spec[min_edge], spec[size[0]], size[0] - min_edge + 1)
                            # avg = np.mean(spec[(max_spike+2):(max_spike+avgpixels+2)])
                            spec[min_edge:size[0] + 1] = line
                
                #include corrected spectrum in dataset                 
                    specdat[:,ii,jj] = spec    
        
        logging.debug("Number of corrected spikes %s", kpix)
        logging.debug("Number of corrected scan pixels %s", kCL)          

        self.nspikes_cor = kpix
        self.npixels_cor = kCL
        specdat = numpy.expand_dims(numpy.expand_dims(specdat,1),1)

        return specdat

    def _force_update_spec(self, st):
        """
        Force updating the projection of the given stream
        """
        # Update in the view of the window, and also the current tab
        views = [self._dlg.view]
        views.extend(self.main_app.main_data.tab.value.tab_data_model.views.value)

        for v in views:
            for sp in v.stream_tree.getProjections():  # stream or projection
                if isinstance(sp, DataProjection):
                    s = sp.stream
                else:
                    s = sp
                if s is st:
                    logging.debug("Updating projection %s", sp)
                    if hasattr(s, "_updateCalibratedData"):
                        s._updateCalibratedData(bckg=s.background.value, coef=s.efficiencyCompensation.value)
                    sp._shouldUpdateImage()

