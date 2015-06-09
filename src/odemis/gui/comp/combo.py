# -*- coding: utf-8 -*-
"""
Created on 22 Feb 2013

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

Combobox and similar controls.

"""
from __future__ import division
import wx.combo

import odemis.gui
import odemis.gui.img.data as img


class ComboBox(wx.combo.OwnerDrawnComboBox):
    """ A simple sub class of OwnerDrawnComboBox that prevents a white border
    from showing around the combobox and allows for left/right caret
    navigation with the arrow keys.

    OwnerDrawnComboBox consists of a ComboCtrl, which a child TextCtrl. The
    problem with the white border comes from the fact that the background colour
    of the ComboCtrl cannot be set. Any call to that method will only result in
    the TextCtrl changing colour.

    Getting rid of margins also didn't help, since the top margin is 'stuck' at
    -1, which causes the ComboCtrl's white background colour to show.

    In the end, the solution was to draw the background ourselves, using the correct colour.

    """

    def __init__(self, *args, **kwargs):

        labels = kwargs.pop('labels', [])
        choices = kwargs.pop('choices', [])

        wx.combo.OwnerDrawnComboBox.__init__(self, *args, **kwargs)
        # SetMargins allow the left margin to be set to 0, but the top
        # margin won't move and stays at the default -1.
        self.SetMargins(0, 0)

        self.SetForegroundColour(odemis.gui.FG_COLOUR_EDIT)
        # Even those this colour sets the right
        self.SetBackgroundColour(self.Parent.GetBackgroundColour())
        self.SetButtonBitmaps(img.getbtn_downBitmap(), pushButtonBg=False)

        # self.Bind(wx.EVT_KEY_DOWN, self.on_key)
        self.Bind(wx.EVT_PAINT, self.on_paint)

        # If no labels are provided, create them from the choices
        if not labels and choices:
            labels = [unicode(c) for c in choices]

        for label, choice in zip(labels, choices):
            self.Append(label, choice)

        def _eat_event(evt):
            """ Quick and dirty empty function used to 'eat' mouse wheel events """

            # TODO: This solution only makes sure that the control's value
            # doesn't accidentally get altered when it gets hit by a mouse
            # wheel event. However, it also stop the event from propagating
            # so the containing scrolled window will not scroll either.
            # (If the event is skipped, the control will change value again)
            # No easy fix found in wxPython 3.0.
            pass

        self.Bind(wx.EVT_MOUSEWHEEL, _eat_event)

    def on_paint(self, evt):
        """ Handle the paint event

        Because OwnerDrawnComboBox showed the white background 'behind' the text control (1px
        at the bottom and to the right), which could not be gotten rid off, we are forced to
        paint the background in the correct colour ourselves.

        """

        dc = wx.BufferedPaintDC(self)
        self.draw(dc)
        evt.Skip()  # Make sure the event propagates, so the drop-down button will be drawn

    def draw(self, dc):
        """ Clear the widget with the correct background colour """
        back_colour = self.Parent.GetBackgroundColour()
        back_brush = wx.Brush(back_colour, wx.BRUSHSTYLE_SOLID)
        dc.SetBackground(back_brush)
        dc.Clear()

    # Doesn't seem needed anymore with wx3.0
    # def on_key(self, evt):
    #     """ The OwnerDrawnComboBox makes the left/right keys change the
    #     selection instead of moving the caret. This method corrects that problem
    #     """
    #     if self.TextCtrl and self.Enabled:
    #         key = evt.GetKeyCode()
    #         ip = self.TextCtrl.GetInsertionPoint()
    #
    #         if key == wx.WXK_RIGHT:
    #             self.TextCtrl.SetInsertionPoint(ip + 1)
    #         elif key == wx.WXK_LEFT:
    #             if ip > 0:
    #                 self.TextCtrl.SetInsertionPoint(ip - 1)
    #         else:
    #             evt.Skip()
    #     else:
    #         evt.Skip()
    #     evt.Skip()
