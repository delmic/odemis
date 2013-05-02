# -*- coding: utf-8 -*-
"""
Created on 1 Oct 2012

@author: Rinze de Laat

Copyright © 2012-2013 Rinze de Laat and Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division
from odemis.gui import instrmodel
from odemis.gui.model import OPTICAL_STREAMS, EM_STREAMS, SPECTRUM_STREAMS, \
    AR_STREAMS
from odemis.gui.model.stream import SEMStream, BrightfieldStream, FluoStream
import logging
import wx

# TODO: The next comments were copied from instrmodel. Read/implement/remove
# viewport controller (to be merged with stream controller?)
# Creates the 4 microscope views at init, with the right names, depending on
#   the available microscope hardware.
# (The 4 viewports canvas are already created, the main interface connect
#   them to the view, by number)
# In charge of switching between 2x2 layout and 1 layout.
# In charge of updating the view focus
# In charge of updating the view thumbnails???
# In charge of ensuring they all have same zoom and center position
# In charge of applying the toolbar actions on the right viewport
# in charge of changing the "hair-cross" display

class ViewController(object):

    """ Manages the microscope view updates, change of viewport focus, etc.
    """

    def __init__(self, micgui, main_frame, viewports):
        """
        micgui (MicroscopeModel) -- the representation of the microscope GUI
        main_frame: (wx.Frame) -- the frame which contains the 4 viewports
        viewports (list of MicroscopeViewport): the viewports to update
        """

        self._interface_model = micgui
        self._main_frame = main_frame

        # list of all the viewports (widgets that show the views)
        self._viewports = viewports

        # create the (default) views and set focussedView
        self._createViews()

        # subscribe to layout and view changes
        self._interface_model.viewLayout.subscribe(self._onViewLayout, init=True)
        self._interface_model.focussedView.subscribe(self._onView, init=True)

    def _createViews(self):
        """
        Create the different views displayed, according to the current
        microscope.

        To be executed only once, at initialisation.
        """

        # If AnalysisTab for Sparc: SEM/Spec/AR/SEM
        if (isinstance(self._interface_model, instrmodel.AnalysisGUIModel) and
            self._interface_model.microscope.role == "sparc"):
            # TODO: should be dependent on the type of acquisition, and so
            # updated every time the .file changes
            assert len(self._viewports) == 4
            assert not self._interface_model.views # should still be empty
            logging.info("Creating (static) SPARC viewport layout")

            view = instrmodel.MicroscopeView(
                        "SEM",
                        self._interface_model.stage,
                        stream_classes=EM_STREAMS
                     )
            self._interface_model.views.append(view)
            self._viewports[0].setView(view, self._interface_model)

            view = instrmodel.MicroscopeView(
                        "Spectrum",
                        self._interface_model.stage,
                        focus0=self._interface_model.focus, # TODO: change center wavelength?
                        # TODO: focus1 changes bandwidth?
                        stream_classes=SPECTRUM_STREAMS
                     )
            self._interface_model.views.append(view)
            self._viewports[1].setView(view, self._interface_model)

            # TODO: need a special View?
            view = instrmodel.MicroscopeView(
                        "Angle Resolved",
                        stream_classes=AR_STREAMS
                     )
            self._interface_model.views.append(view)
            self._viewports[2].setView(view, self._interface_model)

            view = instrmodel.MicroscopeView(
                        "SEM CL",
                        self._interface_model.stage,
                        stream_classes=(EM_STREAMS + SPECTRUM_STREAMS)
                     )
            self._interface_model.views.append(view)
            self._viewports[3].setView(view, self._interface_model)

            # Start off with the 2x2 view
            # Focus defaults to the top right viewport
            self._interface_model.focussedView.value = self._viewports[1].mic_view

        # If SEM only: all SEM
        # Works also for the Sparc, as there is no other emitter, and we don't
        # need to display anything else anyway
        elif self._interface_model.ebeam and not self._interface_model.light:
            logging.info("Creating SEM only viewport layout")
            i = 1
            for viewport in self._viewports:
                view = instrmodel.MicroscopeView(
                            "SEM %d" % i,
                            self._interface_model.stage,
                            focus0=None, # TODO: SEM focus or focus1?
                            stream_classes=(SEMStream,)
                         )
                self._interface_model.views.append(view)
                viewport.setView(view, self._interface_model)
                i += 1
            self._interface_model.focussedView.value = self._interface_model.views[0]

        # If Optical only: all Optical
        # TODO: first one is brightfield only?
        elif not self._interface_model.ebeam and self._interface_model.light:
            logging.info("Creating Optical only viewport layout")
            i = 1
            for viewport in self._viewports:
                view = instrmodel.MicroscopeView(
                            "Optical %d" % i,
                            self._interface_model.stage,
                            focus0=self._interface_model.focus,
                            stream_classes=(BrightfieldStream, FluoStream)
                         )
                self._interface_model.views.append(view)
                viewport.setView(view, self._interface_model)
                i += 1
            self._interface_model.focussedView.value = self._interface_model.views[0]

        # If both SEM and Optical (=SECOM): SEM/Optical/2x combined
        elif ((self._interface_model.ebeam and self._interface_model.light) or
              (isinstance(self._interface_model, instrmodel.AnalysisGUIModel) and
              self._interface_model.microscope.role == "secom")):
            assert len(self._viewports) == 4
            assert not self._interface_model.views # should still be empty
            logging.info("Creating combined SEM/Optical viewport layout")

            view = instrmodel.MicroscopeView(
                        "SEM",
                        self._interface_model.stage,
                        focus0=None, # TODO: SEM focus
                        stream_classes=EM_STREAMS
                     )
            self._interface_model.views.append(view)
            self._viewports[0].setView(view, self._interface_model)

            view = instrmodel.MicroscopeView(
                        "Optical",
                        self._interface_model.stage,
                        focus0=self._interface_model.focus,
                        stream_classes=OPTICAL_STREAMS
                     )
            self._interface_model.views.append(view)
            self._viewports[1].setView(view, self._interface_model)

            view = instrmodel.MicroscopeView(
                        "Combined 1",
                        self._interface_model.stage,
                        focus0=self._interface_model.focus,
                        focus1=None, # TODO: SEM focus
                     )
            self._interface_model.views.append(view)
            self._viewports[2].setView(view, self._interface_model)

            view = instrmodel.MicroscopeView(
                        "Combined 2",
                        self._interface_model.stage,
                        focus0=self._interface_model.focus,
                        focus1=None, # TODO: SEM focus
                     )
            self._interface_model.views.append(view)
            self._viewports[3].setView(view, self._interface_model)

            # Start off with the 2x2 view
            # Focus defaults to the top right viewport
            self._interface_model.focussedView.value = self._viewports[1].mic_view
        else:
            msg = "No known microscope configuration, creating 4 generic views"
            logging.warning(msg)
            i = 1
            for viewport in self._viewports:
                view = instrmodel.MicroscopeView(
                            "View %d" % i,
                            self._interface_model.stage,
                            focus0=self._interface_model.focus
                         )
                self._interface_model.views.append(view)
                viewport.setView(view, self._interface_model)
                i += 1
            self._interface_model.focussedView.value = self._interface_model.views[0]

        # TODO: if chamber camera: br is just chamber, and it's the focussedView


    def _onView(self, view):
        """
        Called when another view is focused
        """
        logging.debug("Changing focus to view %s", view.name.value)
        layout = self._interface_model.viewLayout.value

        self._viewports[0].Parent.Freeze()

        for viewport in self._viewports:
            if viewport.mic_view == view:
                viewport.SetFocus(True)
                if layout == instrmodel.VIEW_LAYOUT_ONE:
                    # TODO: maybe in that case, it's not necessary to display the focus frame around?
                    viewport.Show()
            else:
                viewport.SetFocus(False)
                if layout == instrmodel.VIEW_LAYOUT_ONE:
                    viewport.Hide()

        if layout == instrmodel.VIEW_LAYOUT_ONE:
            self._viewports[0].Parent.Layout() # resize viewport

        self._viewports[0].Parent.Thaw()

    def _onViewLayout(self, layout):
        """
        Called when the view layout of the GUI must be changed
        """
        # only called when changed
        self._viewports[0].Parent.Freeze()

        if layout == instrmodel.VIEW_LAYOUT_ONE:
            logging.debug("Showing only one view")
            # TODO resize all the viewports now, so that there is no flickering
            # when just changing view
            for viewport in self._viewports:
                if viewport.mic_view == self._interface_model.focussedView.value:
                    viewport.Show()
                else:
                    viewport.Hide()

        elif layout == instrmodel.VIEW_LAYOUT_22:
            logging.debug("Showing all views")
            for viewport in self._viewports:
                viewport.Show()

        elif layout == instrmodel.VIEW_LAYOUT_FULLSCREEN:
            raise NotImplementedError()
        else:
            raise NotImplementedError()

        self._viewports[0].Parent.Layout()  # resize the viewports
        self._viewports[0].Parent.Thaw()


class ViewSelector(object):
    """
    This class controls the view selector buttons and labels associated with
    them.
    """

    def __init__(self, micgui, main_frame, buttons):
        """
        micgui (MicroscopeModel): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self._microscope_gui = micgui
        self._main_frame = main_frame

        self.buttons = buttons

        for btn in self.buttons:
            btn.Bind(wx.EVT_BUTTON, self.OnClick)

        # subscribe to layout and view changes
        # FIXME: viewLayout disabled, because it was sending wrong (integer)
        # views to _onView
        #self._microscope_gui.viewLayout.subscribe(self._onView, init=True)
        #self._microscope_gui.focussedView.subscribe(self._onView, init=True)

        # subscribe to thumbnails
        self._subscriptions = [] # list of functions

        # subscribe to change of name
        for btn, view_label in self.buttons.items():
            if view_label.vp is None: # 2x2 layout
                view_label.lbl.SetLabel("Overview")
                continue

            def onThumbnail(im, btn=btn): # save btn in scope
                btn.set_overlay(im)

            self.buttons[btn].vp.mic_view.thumbnail.subscribe(onThumbnail, init=True)
            # keep ref of the functions so that they are not dropped
            self._subscriptions.append(onThumbnail)

            # also subscribe for updating the 2x2 button
            self.buttons[btn].vp.mic_view.thumbnail.subscribe(self._update22Thumbnail)

            def onName(name, view_label=view_label): # save view_label
                view_label.lbl.SetLabel(name)

            view_label.vp.mic_view.name.subscribe(onName, init=True)
            self._subscriptions.append(onName)

        # Select the overview by default
        # Fixme: should be related to the layout in MicroscopeModel and/or the
        # focussed viewport. ('None' selects the overview button)
        self.toggleButtonForView(None)

    def toggleButtonForView(self, mic_view):
        """
        Toggle the button which represents the view and untoggle the other ones
        mic_view (MicroscopeView or None): the view, or None if the first button
                                           (2x2) is to be toggled
        Note: it does _not_ change the view
        """
        for b, vl in self.buttons.items():
            # 2x2 => vp is None / 1 => vp exists and vp.view is the view
            if (vl.vp is None and mic_view is None) or (vl.vp and vl.vp.mic_view == mic_view):
                b.SetToggle(True)
            else:
                if vl.vp:
                    logging.debug("untoggling button of view %s", vl.vp.mic_view.name.value)
                else:
                    logging.debug("untoggling button of view All")
                b.SetToggle(False)

    def _update22Thumbnail(self, im):
        """
        Called when any thumbnail is changed, to recompute the 2x2 thumbnail of
        the first button.
        im (unused)
        """
        # Create an image from the 4 thumbnails in a 2x2 layout with small
        # border. The button without a viewport attached is assumed to be the
        # one assigned to the 2x2 view
        btn_all = [b for b, vl in self.buttons.items() if vl.vp is None][0]
        border_width = 2 # px
        size = max(1, btn_all.overlay_width), max(1, btn_all.overlay_height)
        size_sub = (max(1, (size[0] - border_width) // 2),
                    max(1, (size[1] - border_width) // 2))
        # starts with an empty image with the border colour everywhere
        im_22 = wx.EmptyImage(*size, clear=False)
        im_22.SetRGBRect(wx.Rect(0, 0, *size), *btn_all.GetBackgroundColour().Get())

        for i, (_, view_label) in enumerate(self.buttons.items()):
            if view_label.vp is None: # 2x2 layout
                continue

            im = view_label.vp.mic_view.thumbnail.value
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

        # set_overlay will rescale to the correct button size
        btn_all.set_overlay(im_22)

    def _onView(self, view):
        """
        Called when another view is focused, or viewlayout is changed
        """

        logging.debug("View changed")

        try:
            if view is not None:
                assert isinstance(view, instrmodel.MicroscopeView)
        except AssertionError:
            logging.exception("Wrong type of view parameter! %s", view)
            raise

        # TODO when changing from 2x2 to a view non focused, it will be called
        # twice in row. => optimise to not do it twice

        self.toggleButtonForView(view)

        # if layout is 2x2 => do nothing (first button is selected by _onViewLayout)
        # if self._microscope_gui.viewLayout.value == instrmodel.VIEW_LAYOUT_22:
        #     # otherwise (layout is 2x2) => select the first button
        #     self.toggleButtonForView(None)
        # else:
        #     # otherwise (layout is 1) => select the right button
        #     self.toggleButtonForView(view)


    def OnClick(self, evt):
        """
        Navigation button click event handler

        Show the related view(s) and sets the focus if needed.
        """

        # The event does not need to be 'skipped' because
        # the button will be toggled when the event for value change is received.

        btn = evt.GetEventObject()
        viewport = self.buttons[btn].vp

        if viewport is None:
            logging.debug("Overview button click")
            self.toggleButtonForView(None)
            # 2x2 button
            # When selecting the overview, the focussed viewport should not change
            self._microscope_gui.viewLayout.value = instrmodel.VIEW_LAYOUT_22
        else:
            logging.debug("View button click")
            self.toggleButtonForView(viewport.mic_view)
            # It's preferable to change the view before the layout so that
            # if the layout was 2x2 with another view focused, it doesn't first
            # display one big view, and immediately after changes to another view.
            self._microscope_gui.focussedView.value = viewport.mic_view
            self._microscope_gui.viewLayout.value = instrmodel.VIEW_LAYOUT_ONE
