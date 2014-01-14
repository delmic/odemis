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

    Getting rid op margins also didn't help, since the top margin is 'stuck' at
    -1, which causes the ComboCtrl's white background colour to show.

    In the end, the solution was to force the TextCtrl to be one pixel higher
    than calculated by the control. See the on_size method.
    """

    def __init__(self, *args, **kwargs):
        wx.combo.OwnerDrawnComboBox.__init__(self, *args, **kwargs)
        # SetMargins allow the left margin to be set to 0, but the top
        # margin won't move and stays at the default -1.
        self.SetMargins(0, 0)
        self.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
        self.SetBackgroundColour(self.Parent.GetBackgroundColour())
        self.SetButtonBitmaps(img.getbtn_downBitmap(), pushButtonBg=False)

        self.Bind(wx.EVT_SIZE, self.on_size)
        self.Bind(wx.EVT_KEY_DOWN, self.on_key)

        # Grab a reference
        self.txt_ctrl = self.GetTextCtrl()

    def on_size(self, evt):
        """ Force the TextCtrl to cover the white 'border' at the bottom
        on each resize.
        """

        # If the ComboBox if given the wx.CB_READONLY style, it does not contain
        # a child TextCtrl, so it seems.
        if self.txt_ctrl:
            p_size = self.GetSize()
            wx.CallAfter(self.txt_ctrl.SetSize, (-1, p_size[1] + 1))
        evt.Skip()

    def on_key(self, evt):
        """ The OwnerDrawnComboBox makes the left/right keys change the
        selection instead of moving the caret. This method corrects that problem
        """
        key = evt.GetKeyCode()
        ip = self.txt_ctrl.GetInsertionPoint()

        if key == wx.WXK_RIGHT:
            self.txt_ctrl.SetInsertionPoint(ip + 1)
        elif key == wx.WXK_LEFT and ip > 0:
            self.txt_ctrl.SetInsertionPoint(ip - 1)
        else:
            evt.Skip()
