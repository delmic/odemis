#-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat and Éric Piel, Delmic

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

from odemis.gui import instrmodel
from odemis.gui.log import log
import wx


class ViewSelector(object):
    """
    This class controls the view selector buttons and labels associated with them.
    """

    def __init__(self, micgui, main_frame):
        """
        micgui (MicroscopeGUI): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        """
        self._microscope = micgui
        self._main_frame = main_frame

        # TODO: should create buttons according to micgui views
        # btn -> viewports
        self.buttons = {main_frame.btn_view_all: None, # 2x2 layout
                        main_frame.btn_view_tl: main_frame.pnl_view_tl,
                        main_frame.btn_view_tr: main_frame.pnl_view_tr,
                        main_frame.btn_view_bl: main_frame.pnl_view_bl,
                        main_frame.btn_view_br: main_frame.pnl_view_br}

        for btn in self.buttons:
            btn.Bind(wx.EVT_BUTTON, self.OnClick)

        # TODO buttons should have the name of the view as label next to the image

        # subscribe to layout and view changes
        self._microscope.viewLayout.subscribe(self._onView, init=True)
        self._microscope.currentView.subscribe(self._onView, init=True)

        # subscribe to thumbnails
        self._subscriptions = [] # list of functions
        for btn in [self._main_frame.btn_view_tl, self._main_frame.btn_view_tr,
                    self._main_frame.btn_view_bl, self._main_frame.btn_view_br]:
            def onThumbnail(im):
                btn.set_overlay(im)

            self.buttons[btn].view.thumbnail.subscribe(onThumbnail, init=True)
            # keep ref of the functions so that they are not dropped
            self._subscriptions.append(onThumbnail)

            # also subscribe for updating the 2x2 button
            self.buttons[btn].view.thumbnail.subscribe(self._update22Thumbnail)
        self._update22Thumbnail(None)

        # subscribe to change of name
        for btn, vp in self.buttons.items():
            if not vp: # 2x2 layout
                btn.SetLabel("All") # TODO: is it good name?
                continue

            def onName(name):
                # FIXME: for now the buttons have a separate label next to them
                # probably need a way to link these labels to the button
                btn.SetLabel(name)

            vp.view.name.subscribe(onName, init=True)
            self._subscriptions.append(onName)

    def toggleButtonForView(self, view):
        """
        Toggle the button which represents the view and untoggle the other ones
        view (MicroscopeView or None): the view, or None if the first button
          (2x2) is to be toggled
        Note: it does _not_ change the view
        """
        for b, vp in self.buttons.items():
            # 2x2 => vp is None / 1 => vp exists and vp.view is the view
            if (vp is None and view is None) or (vp and vp.view == view):
                b.SetToggle(True)
            else:
                if vp:
                    log.debug("untoggling button of view %s", vp.view.name.value)
                else:
                    log.debug("untoggling button of view All")
                b.SetToggle(False)

    def _update22Thumbnail(self, im):
        """
        Called when any thumbnail is changed, to recompute the 2x2 thumbnail of
         the first button.
        im (unused)
        """
        # Create an image from the 4 thumbnails in a 2x2 layout with small border
        btn_all = self._main_frame.btn_view_all
        border_width = 2 # px
        size = btn_all.overlay_width, btn_all.overlay_height

        # new black image of 2 times the size + border size *2
        im_22 = wx.EmptyImage((size[0] + border_width) * 2, (size[1] + border_width) * 2)

        for i, btn in enumerate([self._main_frame.btn_view_tl, self._main_frame.btn_view_tr,
                                 self._main_frame.btn_view_bl, self._main_frame.btn_view_br]):
            im = self.buttons[btn].view.thumbnail.value
            if im is None:
                continue # stays black

            # FIXME: not all the thumbnails have the right aspect ratio cf set_overlay
            # Rescale to fit
            sim = im.Scale(size[0], size[1], wx.IMAGE_QUALITY_HIGH)
            # compute placement
            y, x = divmod(i, 2)
            # copy im in the right place
            im_22.Paste(sim, x * (size[0] + border_width), y * (size[1] + border_width))

        # set_overlay will rescale to the correct button size
        btn_all.set_overlay(im_22)

    def _onView(self, view):
        """
        Called when another view is focused, or viewlayout is changed
        """
        # TODO when changing from 2x2 to a view non focused, it will be called
        # twice in row. => optimise to not do it twice

        # if layout is 2x2 => do nothing (first button is selected by _onViewLayout)
        if self._microscope.viewLayout.value == instrmodel.VIEW_LAYOUT_22:
            # otherwise (layout is 2x2) => select the first button
            self.toggleButtonForView(None)
        else:
            # otherwise (layout is 1) => select the right button
            self.toggleButtonForView(view)

    def OnClick(self, evt):
        """
        Navigation button click event handler

        Show the related view(s) and sets the focus if needed.
        """
        log.debug("View button click")
        # The event does not need to be 'skipped' because
        # the button will be toggled when the event for value change is received.

        btn = evt.GetEventObject()
        viewport = self.buttons[btn]

        if viewport is None:
            # 2x2 button
            self._microscope.viewLayout.value = instrmodel.VIEW_LAYOUT_22
        else:
            # It's preferable to change the view before the layout so that
            # if the layout was 2x2 with another view focused, it doesn't first
            # display one big view, and immediately after changes to another view.
            self._microscope.currentView.value = viewport.view
            self._microscope.viewLayout.value = instrmodel.VIEW_LAYOUT_ONE
