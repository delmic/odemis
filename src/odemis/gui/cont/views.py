# -*- coding: utf-8 -*-
"""
Created on 1 Oct 2012

@author: Rinze de Laat

Copyright © 2012-2013 Rinze de Laat and Éric Piel, Delmic

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
from odemis.gui import model
from odemis.gui.model.stream import OPTICAL_STREAMS, EM_STREAMS, \
    SPECTRUM_STREAMS, AR_STREAMS
from odemis.gui.util import call_after
import collections
import logging
import wx

class ViewController(object):

    """ Manages the microscope view updates, change of viewport focus, etc.
    """

    def __init__(self, tab_data, main_frame, viewports):
        """
        tab_data (MicroscopyGUIData) -- the representation of the microscope GUI
        main_frame: (wx.Frame) -- the frame which contains the 4 viewports
        viewports (list of MicroscopeViewport or
                   OrderedDict (MicroscopeViewport -> kwargs)): the viewports to
          update. The first one is the one focused. If it's an OrderedDict, the
          kwargs are passed to the MicroscopeView creation.
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main

        if isinstance(viewports, collections.OrderedDict):
            self._viewports = viewports.keys()
            self._createViewsFixed(viewports)
        else:
            # create the (default) views
            self._viewports = viewports
            self._createViewsAuto()

        # First viewport is focused
        tab_data.focussedView.value = self._viewports[0].microscope_view

        # subscribe to layout and view changes
        tab_data.viewLayout.subscribe(self._onViewLayout, init=True)
        tab_data.focussedView.subscribe(self._onView, init=True)

    @property
    def viewports(self):
        return self._viewports

    def _createViewsFixed(self, viewports):
        """
        Create the different views displayed, according to viewtypes
        viewports (OrderedDict (MicroscopeViewport -> kwargs)): cf init

        To be executed only once, at initialisation.
        """

        for vp, vkwargs in viewports.items():
            # TODO: automatically set some clever values for missing arguments?
            view = model.MicroscopeView(**vkwargs)
            self._tab_data_model.views.append(view)
            vp.setView(view, self._tab_data_model)

    def _createViewsAuto(self):
        """
        Create the different views displayed, according to the current
        microscope.

        To be executed only once, at initialisation.
        """

        # If AnalysisTab for Sparc: SEM/Spec/AR/SEM
        assert not self._tab_data_model.views # should still be empty
        if isinstance(self._tab_data_model, model.AnalysisGUIData):
            assert len(self._viewports) == 4
            # TODO: should be dependent on the type of acquisition, and so
            # updated every time the .file changes
            if self._main_data_model.role == "sparc":
                logging.info("Creating (static) SPARC viewport layout")
                vpv = collections.OrderedDict([
                (self._viewports[0],  # focused view
                 {"name": "SEM",
                  "stream_classes": EM_STREAMS,
                  }),
                (self._viewports[1],
                 {"name": "Spectrum",
                  "stream_classes": OPTICAL_STREAMS + SPECTRUM_STREAMS,
                  }),
                (self._viewports[2],
                 {"name": "Angle resolved",
                  "stream_classes": AR_STREAMS,
                  }),
                (self._viewports[3],
                 {"name": "SEM CL",
                  "stream_classes": EM_STREAMS + OPTICAL_STREAMS + SPECTRUM_STREAMS,
                  }),
                                               ])
                self._createViewsFixed(vpv)
            else:
                logging.info("Creating generic static viewport layout")
                vpv = collections.OrderedDict([
                (self._viewports[0],  # focused view
                 {"name": "SEM",
                  "stream_classes": EM_STREAMS,
                  }),
                (self._viewports[1],
                 {"name": "Optical",
                  "stream_classes": OPTICAL_STREAMS + SPECTRUM_STREAMS,
                  }),
                (self._viewports[2],
                 {"name": "Combined 1",
                  "stream_classes": EM_STREAMS + OPTICAL_STREAMS + SPECTRUM_STREAMS,
                  }),
                (self._viewports[3],
                 {"name": "Combined 2",
                  "stream_classes": EM_STREAMS + OPTICAL_STREAMS + SPECTRUM_STREAMS,
                  }),
                                               ])
                self._createViewsFixed(vpv)

        # If SEM only: all SEM
        # Works also for the Sparc, as there is no other emitter, and we don't
        # need to display anything else anyway
        elif self._main_data_model.ebeam and not self._main_data_model.light:
            logging.info("Creating SEM only viewport layout")
            i = 1
            for viewport in self._viewports:
                view = model.MicroscopeView(
                            "SEM %d" % i,
                            self._main_data_model.stage,
                            focus0=None, # TODO: SEM focus or focus1?
                            stream_classes=EM_STREAMS
                         )
                self._tab_data_model.views.append(view)
                viewport.setView(view, self._tab_data_model)
                i += 1

        # If Optical only: all Optical
        # TODO: first one is brightfield only?
        elif not self._main_data_model.ebeam and self._main_data_model.light:
            logging.info("Creating Optical only viewport layout")
            i = 1
            for viewport in self._viewports:
                view = model.MicroscopeView(
                            "Optical %d" % i,
                            self._main_data_model.stage,
                            focus0=self._main_data_model.focus,
                            stream_classes=OPTICAL_STREAMS
                         )
                self._tab_data_model.views.append(view)
                viewport.setView(view, self._tab_data_model)
                i += 1

        # If both SEM and Optical (=SECOM): SEM/Optical/2x combined
        elif (self._main_data_model.ebeam and self._main_data_model.light and
             len(self._viewports) == 4):
            logging.info("Creating combined SEM/Optical viewport layout")
            vpv = collections.OrderedDict([
            (self._viewports[0],  # focused view
             {"name": "SEM",
              "stage": self._main_data_model.stage,
              "focus0": None, # TODO: SEM focus
              "stream_classes": EM_STREAMS,
              }),
            (self._viewports[1],
             {"name": "Optical",
              "stage": self._main_data_model.stage,
              "focus1": self._main_data_model.focus,
              "stream_classes": OPTICAL_STREAMS,
              }),
            (self._viewports[2],
             {"name": "Combined 1",
              "stage": self._main_data_model.stage,
              "focus0": None, # TODO: SEM focus
              "focus1": self._main_data_model.focus,
            "stream_classes": EM_STREAMS + OPTICAL_STREAMS,
              }),
            (self._viewports[3],
             {"name": "Combined 2",
              "stage": self._main_data_model.stage,
              "focus0": None, # TODO: SEM focus
              "focus1": self._main_data_model.focus,
            "stream_classes": EM_STREAMS + OPTICAL_STREAMS,
              }),
                                           ])
            self._createViewsFixed(vpv)
        else:
            logging.warning("No known microscope configuration, creating %d "
                            "generic views", len(self._viewports))
            i = 1
            for viewport in self._viewports:
                view = model.MicroscopeView(
                            "View %d" % i,
                            self._main_data_model.stage,
                            focus0=self._main_data_model.focus
                         )
                self._tab_data_model.views.append(view)
                viewport.setView(view, self._tab_data_model)
                i += 1

        # TODO: if chamber camera: br is just chamber, and it's the focussedView

    def _onView(self, view):
        """
        Called when another view is focused
        """
        logging.debug("Changing focus to view %s", view.name.value)
        layout = self._tab_data_model.viewLayout.value

        self._viewports[0].Parent.Freeze()

        for viewport in self._viewports:
            if viewport.microscope_view == view:
                viewport.SetFocus(True)
                if layout == model.VIEW_LAYOUT_ONE:
                    # TODO: maybe in that case, it's not necessary to display the focus frame around?
                    viewport.Show()
            else:
                viewport.SetFocus(False)
                if layout == model.VIEW_LAYOUT_ONE:
                    viewport.Hide()

        if layout == model.VIEW_LAYOUT_ONE:
            self._viewports[0].Parent.Layout() # resize viewport

        self._viewports[0].Parent.Thaw()

    def _onViewLayout(self, layout):
        """
        Called when the view layout of the GUI must be changed
        """
        # only called when changed
        self._viewports[0].Parent.Freeze()

        if layout == model.VIEW_LAYOUT_ONE:
            logging.debug("Showing only one view")
            # TODO resize all the viewports now, so that there is no flickering
            # when just changing view
            for viewport in self._viewports:
                if viewport.microscope_view == self._tab_data_model.focussedView.value:
                    viewport.Show()
                else:
                    viewport.Hide()

        elif layout == model.VIEW_LAYOUT_22:
            logging.debug("Showing all views")
            for viewport in self._viewports:
                viewport.Show()

        elif layout == model.VIEW_LAYOUT_FULLSCREEN:
            raise NotImplementedError()
        else:
            raise NotImplementedError()

        self._viewports[0].Parent.Layout()  # resize the viewports
        self._viewports[0].Parent.Thaw()

    def fitCurrentViewToContent(self):
        """
        Adapts the scale (MPP) of the current view to the content
        """
        # find the viewport corresponding to the current view
        for vp in self._viewports:
            if vp.microscope_view == self._tab_data_model.focussedView.value:
                vp.canvas.fitViewToContent()
                break
        else:
            logging.error("Failed to find the current viewport")

class ViewSelector(object):
    """ This class controls the view selector buttons and labels associated with
    them.
    """

    def __init__(self, tab_data, main_frame, buttons):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope
            GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        buttons (OrderedDict : btn -> (viewport, label)): 5 buttons and the
            viewport (for the image) and label (for changing the name)
            associated.
            The first button has no viewport, for the 2x2 view.
        """
        self._tab_data_model = tab_data

        self.buttons = buttons

        for btn in self.buttons:
            btn.Bind(wx.EVT_BUTTON, self.OnClick)

        # subscribe to thumbnails
        self._subscriptions = [] # list of functions

        # subscribe to change of name
        for btn, (vp, lbl) in self.buttons.items():
            if vp is None: # 2x2 layout
                lbl.SetLabel("Overview")
                continue

            @call_after
            def onThumbnail(im, btn=btn): # save btn in scope
                btn.set_overlay(im)

            vp.microscope_view.thumbnail.subscribe(onThumbnail, init=True)
            # keep ref of the functions so that they are not dropped
            self._subscriptions.append(onThumbnail)

            # also subscribe for updating the 2x2 button
            vp.microscope_view.thumbnail.subscribe(self._update22Thumbnail)

            def onName(name, lbl=lbl): # save lbl in scope
                lbl.SetLabel(name)

            vp.microscope_view.name.subscribe(onName, init=True)
            self._subscriptions.append(onName)

        # subscribe to layout and view changes
        self._tab_data_model.viewLayout.subscribe(self._onViewChange)
        self._tab_data_model.focussedView.subscribe(self._onViewChange, init=True)

    def toggleButtonForView(self, microscope_view):
        """
        Toggle the button which represents the view and untoggle the other ones
        microscope_view (MicroscopeView or None): the view, or None if the first button
                                           (2x2) is to be toggled
        Note: it does _not_ change the view
        """
        for b, (vp, lbl) in self.buttons.items():
            # 2x2 => vp is None / 1 => vp exists and vp.view is the view
            if ((vp is None and microscope_view is None) or
                (vp and vp.microscope_view == microscope_view)):
                b.SetToggle(True)
            else:
                if vp:
                    logging.debug("untoggling button of view %s",
                                  vp.microscope_view.name.value)
                else:
                    logging.debug("untoggling button of view All")
                b.SetToggle(False)

    @call_after
    def _update22Thumbnail(self, im):
        """
        Called when any thumbnail is changed, to recompute the 2x2 thumbnail of
        the first button.
        im (unused)
        """

        # Create an image from the 4 thumbnails in a 2x2 layout with small
        # border. The button without a viewport attached is assumed to be the
        # one assigned to the 2x2 view
        btn_all = [b for b, (vp, lbl) in self.buttons.items() if vp is None][0]
        border_width = 2 # px
        size = max(1, btn_all.overlay_width), max(1, btn_all.overlay_height)
        size_sub = (max(1, (size[0] - border_width) // 2),
                    max(1, (size[1] - border_width) // 2))
        # starts with an empty image with the border colour everywhere
        im_22 = wx.EmptyImage(*size, clear=False)
        im_22.SetRGBRect(wx.Rect(0, 0, *size), *btn_all.GetBackgroundColour().Get())

        i = 0

        for vp, lbl in self.buttons.values():
            if vp is None: # 2x2 layout
                continue

            im = vp.microscope_view.thumbnail.value
            if im:
                # im doesn't have the same aspect ratio as the actual thumbnail
                # => rescale and crop on the center
                # Rescale to have the smallest axis as big as the thumbnail
                rsize = list(size_sub)
                if (size_sub[0] / im.Width) > (size_sub[1] / im.Height):
                    rsize[1] = int(im.Height * (size_sub[0] / im.Width))
                else:
                    rsize[0] = int(im.Width * (size_sub[1] / im.Height))
                sim = im.Scale(*rsize, quality=wx.IMAGE_QUALITY_HIGH)

                # crop to the right shape
                lt = ((size_sub[0] - sim.Width) // 2, (size_sub[1] - sim.Height) // 2)
                sim.Resize(size_sub, lt)

                # compute placement
                y, x = divmod(i, 2)
                # copy im in the right place
                im_22.Paste(sim, x * (size_sub[0] + border_width), y * (size_sub[1] + border_width))
            else:
                # black image
                # Should never happen
                pass #sim = wx.EmptyImage(*size_sub)

            i += 1

        # set_overlay will rescale to the correct button size
        btn_all.set_overlay(im_22)

    def _onViewChange(self, unused):
        """
        Called when another view is focused, or viewlayout is changed
        """
        logging.debug("Updating view selector")

        # TODO when changing from 2x2 to a view non focused, it will be called
        # twice in row. => optimise to not do it twice

        if self._tab_data_model.viewLayout.value == model.VIEW_LAYOUT_22:
            # (layout is 2x2) => select the first button
            self.toggleButtonForView(None)
        else:
            # otherwise (layout is 1) => select the right button
            self.toggleButtonForView(self._tab_data_model.focussedView.value)


    def OnClick(self, evt):
        """
        Navigation button click event handler

        Show the related view(s) and sets the focus if needed.
        """

        # The event does not need to be 'skipped' because
        # the button will be toggled when the event for value change is received.

        btn = evt.GetEventObject()
        viewport = self.buttons[btn][0]

        if viewport is None:
            # 2x2 button
            # When selecting the overview, the focussed viewport should not change
            self._tab_data_model.viewLayout.value = model.VIEW_LAYOUT_22
        else:
            # It's preferable to change the view before the layout so that
            # if the layout was 2x2 with another view focused, it doesn't first
            # display one big view, and immediately after changes to another view.
            self._tab_data_model.focussedView.value = viewport.microscope_view
            self._tab_data_model.viewLayout.value = model.VIEW_LAYOUT_ONE
