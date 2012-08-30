# -*- coding: utf-8 -*-
'''
@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import wx

from .instrmodel import InstrumentalImage
from .util import units
from odemis.gui.log import log
from odemis.gui.util import call_after



class MicroscopeView(object):
    """
    Interface for defining a type of view from the microscope (such as CCD,
    SE...) with all its values in legend.
    """
    def __init__(self, name):
        """
        name (string): user friendly name
        """
        self.name = name #
        self.legend_controls = [] # list of wx.Control to display in the legend
        self.outimage = None # ActiveValue of instrumental image
        self.inimage = InstrumentalImage(None, None, None) # instrumental image
        self.sizer = None

    def Hide(self, combo, sizer):
        # Remove and hide all the previous controls in the sizer
        for c in self.legend_controls:
            sizer.Detach(c)
            c.Hide()

        # For spacers: everything else in the sizer
        for c in sizer.GetChildren():
            sizer.Remove(0)

        if self.outimage:
            self.outimage.value = self.inimage

        self.sizer = None

    def Show(self, outimage):
        self.outimage = outimage
        self.UpdateImage()
#        self.sizer = sizer
#
#        # Put the new controls
#        first = True
#        for c in self.legend_controls:
#            if first:
#                first = False
#            else:
#                sizer.AddStretchSpacer()
#            sizer.Add(c, 0, wx.ALIGN_CENTER_HORIZONTAL|wx.LEFT|wx.RIGHT|wx.EXPAND, 3)
#            c.Show()
#
#        #Update the combobox
#        combo.Selection = combo.FindString(self.name)
#
#        sizer.Layout()

    def UpdateImage(self):
        if self.outimage:
            self.outimage.value = self.inimage

class MicroscopeEmptyView(MicroscopeView):
    """
    Special view containing nothing
    """
    def __init__(self, name="None"):
        MicroscopeView.__init__(self, name)

class MicroscopeImageView(MicroscopeView):
    def __init__(self, parent, iim, viewmodel, name="Image"):
        MicroscopeView.__init__(self, name)

        self.Parent = parent

        self.viewmodel = viewmodel
        self.LegendMag = wx.StaticText(parent)
        self.LegendMag.SetToolTipString("Magnicifaction")
        self.legend_controls.append(self.LegendMag)

        iim.subscribe(self.avImage)

        #viewmodel.mpp.subscribe(self.avMPP, True)

    @call_after
    def avImage(self, value):
        self.inimage = value
        # This method might be called from any thread
        # GUI can be updated only from the GUI thread, so just send an event
        self.UpdateImage()
        self.avMPP(None)

    @call_after
    def avMPP(self, unused):
        # TODO: shall we use the real density of the screen?
        # We could use real density but how much important is it?
        mppScreen = 0.00025 # 0.25 mm/px
        label = ""
        if self.inimage.mpp:
            magIm = mppScreen / self.inimage.mpp # as if 1 im.px == 1 sc.px
            if magIm >= 1:
                label += "×" + str(units.round_significant(magIm, 3))
            else:
                label += "/" + str(units.round_significant(1.0/magIm, 3))
            magDig =  self.inimage.mpp / self.viewmodel.mpp.value
            if magDig >= 1:
                label += " ×" + str(units.round_significant(magDig, 3))
            else:
                label += " /" + str(units.round_significant(1.0/magDig, 3))

        self.LegendMag.SetLabel(label)
        self.Parent.Layout()


class MicroscopeOpticalView(MicroscopeImageView):
    def __init__(self, parent, datamodel, viewmodel, name="Optical"):
        MicroscopeImageView.__init__(self, parent, datamodel.optical_det_image,
                                     viewmodel, name)

        self.datamodel = datamodel

        self.LegendWl = wx.StaticText(parent)
        self.LegendWl.SetToolTipString("Wavelength")
        self.LegendET = wx.StaticText(parent)
        self.LegendET.SetToolTipString("Exposure Time")
        self.legend_controls += [self.LegendWl, self.LegendET]

        datamodel.optical_emt_wavelength.subscribe(self.avWavelength)
        datamodel.optical_det_wavelength.subscribe(self.avWavelength, True)
        datamodel.optical_det_exposure_time.subscribe(self.avExposureTime, True)

    @call_after
    def avWavelength(self, value):
        # need to know both wavelengthes, so just look into the values
        win = self.datamodel.optical_emt_wavelength.value
        wout = self.datamodel.optical_det_wavelength.value

        label = unicode(win) + " nm/" + unicode(wout) + " nm"

        self.LegendWl.SetLabel(label)
        self.Parent.Layout()

    @call_after
    def avExposureTime(self, value):
        label = unicode("%0.2f s" % (value))
        self.LegendET.SetLabel(label)
        self.Parent.Layout()


class MicroscopeSEView(MicroscopeImageView):
    def __init__(self, parent, datamodel, viewmodel, name="SE Detector"):
        MicroscopeImageView.__init__(self, parent, datamodel.sem_det_image,
                                     viewmodel, name)

        self.datamodel = datamodel

        self.LegendDwell = wx.StaticText(parent)
        self.LegendSpot = wx.StaticText(parent)
        self.LegendHV = wx.StaticText(parent)

        self.legend_controls += [self.LegendDwell,
                                 self.LegendSpot,
                                 self.LegendHV]

        datamodel.sem_emt_dwell_time.subscribe(self.avDwellTime, True)
        datamodel.sem_emt_spot.subscribe(self.avSpot, True)
        datamodel.sem_emt_hv.subscribe(self.avHV, True)

    # TODO need to use the right dimensions for the units
    @call_after
    def avDwellTime(self, value):
        label = "Dwell: %ss" % units.to_string_si_prefix(value)
        self.LegendDwell.SetLabel(label)

    @call_after
    def avSpot(self, value):
        label = "Spot: %g" % value
        self.LegendSpot.SetLabel(label)

    @call_after
    def avHV(self, value):
        label = "HV: %sV" % units.to_string_si_prefix(value)
        self.LegendHV.SetLabel(label)
