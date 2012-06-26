# -*- coding: utf-8 -*-

# + PyCollapsiblePane (PyPanel)
# |-- GTKExpander
# |-- wx.Panel
#    |--- wx.Window subclass
#    .
#    .
#    |--- wx.Window subclass
#
# TODO:
#
#  **A workaround has been found for the following problem and was implemented
#    using a custom Slider class **

# - When scrolling the main ScrolledWindow with a mouse wheel, we run into
#   trouble when the cursor hits a wx.Slider, since it will start capturing
#   the mouse wheel event, which has two unwanted side effects:
#       1 - The wx.Slider value will change unintentionally
#       2 - The scrolling will stop.
#
#   A posisble workaround needs to be investigated, but it might be tricky
#   since there are big differences between various windowing systems.
#

import wx
import wx.combo
#from wx.lib.imageutils import stepColour

from wx.lib.buttons import GenBitmapTextToggleButton

from odemis.gui.img.data import catalog
from odemis.gui.comp.buttons import ImageButton, ImageToggleButton
from odemis.gui.comp.text import SuggestTextCtrl


TEST_STREAM_LST = ["Aap", u"nöot", "noot", "mies", "kees", "vuur", "quantummechnica",
                   "Repelsteeltje", "", "XXX", "a", "aa", "aaa", "aaaa",
                   "aaaaa", "aaaaaa", "aaaaaaa"]

EXPANDER_HEIGHT = 32

# StreamPanel events
EVT_COLLAPSIBLEPANE_CHANGED = wx.EVT_COLLAPSIBLEPANE_CHANGED

# Button Icons

BMP_ARR_DOWN = catalog['arr_down'].GetBitmap()
BMP_ARR_IRGHT = catalog['arr_right'].GetBitmap()

BMP_REM = catalog['ico_rem_str'].GetBitmap()
BMP_REM_H = catalog['ico_rem_str_h'].GetBitmap()

BMP_EYE_CLOSED = catalog['ico_eye_closed'].GetBitmap()
BMP_EYE_CLOSED_H = catalog['ico_eye_closed_h'].GetBitmap()
BMP_EYE_OPEN = catalog['ico_eye_open'].GetBitmap()
BMP_EYE_OPEN_H = catalog['ico_eye_open_h'].GetBitmap()

BMP_PAUSE = catalog['ico_pause'].GetBitmap()
BMP_PAUSE_H = catalog['ico_pause_h'].GetBitmap()
BMP_PLAY = catalog['ico_play'].GetBitmap()
BMP_PLAY_H = catalog['ico_play_h'].GetBitmap()


class IntegerValidator(wx.PyValidator):
    """ Validator class used for integer input checking and value validation.
    """

    def __init__(self, min_val, max_val):
        """ Constructor """
        wx.PyValidator.__init__(self)
        self.Bind(wx.EVT_CHAR, self.OnChar)

        self.min_val = min_val
        self.max_val = max_val

        # All legal characters
        self.legal = "0123456789"

    def Clone(self):    #pylint: disable=W0221
        """ Required method """
        return IntegerValidator(self.min_val, self.max_val)

    def is_valid(self, val):
        try:
            val = int(val)
            return val >= self.min_val and val <= self.max_val
        except ValueError:
            return False

    def Validate(self):#pylint: disable=W0221,W0613

        fld = self.GetWindow()
        val = fld.GetValue()

        try:
            val = int(val)
            if val < self.min_val or val > self.max_val:
                return False
            #fld.SetValue(str(val))
        except ValueError:
            fld.Focus()
            return False

        return True

    def OnChar(self, event):
        """ This method prevents the entry of illegal characters """
        key = event.GetKeyCode()
        # Allow control keys to propagate
        if key < wx.WXK_SPACE or key == wx.WXK_DELETE or key > 255:
            event.Skip()
            return

        # Allow legal characters to reach the text control
        if chr(key) in self.legal:
            fld = self.GetWindow()
            val = fld.GetValue()
            pos = self.GetWindow().GetInsertionPoint()
            val = val[:pos] + chr(key) + val[pos:]

            if len(val) < 2 or (len(val) > 1 and val[0] != "0"):
                try:
                    val = int(val)
                    if val < self.min_val or val > self.max_val:
                        return
                    event.Skip()
                except ValueError:
                    return
        # 'Eat' the event by not Skipping it, thus preventing it.
        # from reaching the text control
        return

class IntegerTextCtrl(wx.TextCtrl):

    def __init__(self, *args, **kwargs):
        wx.TextCtrl.__init__(self, *args, **kwargs)

    def SetValue(self, value):
        if self.GetValidator().is_valid(value):
            wx.TextCtrl.SetValue(self, value)

class Expander(wx.PyControl):
    """ An Expander is a header/button control at the top of a StreamPanel.
    It provides a means to expand or collapse the StreamPanel, as wel as a label
    and various buttons offering easy access to much used functionality.
    """

    def __init__(self, parent, label="", wid=wx.ID_ANY, pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=wx.NO_BORDER):
        wx.PyControl.__init__(self, parent, wid, pos, size, style)

        # This style *needs* to be set on in MS Windows
        self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        self._parent = parent
        self._label = label
        self._label_ctrl = None

        self._label_color = parent.GetForegroundColour()

        self.Bind(wx.EVT_SIZE, self.OnSize)

        # ===== Fold icons

        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(BMP_ARR_DOWN)
        self._foldIcons.Add(BMP_ARR_IRGHT)

        # ===== Remove button

        self._btn_rem = ImageToggleButton(self, -1, BMP_REM, (10, 8), (18, 18))
        self._btn_rem.SetBitmaps(BMP_REM_H)
        self._btn_rem.SetToolTipString("Remove stream")

        # ===== Visibility button

        self._btn_vis = ImageToggleButton(self, -1, BMP_EYE_CLOSED, (10, 8),
                                          (18, 18))
        self._btn_vis.SetBitmaps(BMP_EYE_CLOSED_H, BMP_EYE_OPEN, BMP_EYE_OPEN_H)
        self._btn_vis.SetToolTipString("Show/hide stream")

        # ===== Play button

        self._btn_play = ImageToggleButton(self, -1, BMP_PAUSE, (10, 8),
                                           (18, 18))
        self._btn_play.SetBitmaps(BMP_PAUSE_H, BMP_PLAY, BMP_PLAY_H)
        self._btn_play.SetToolTipString("Capture stream")


        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        self._sz.Add(self._btn_rem, 0, wx.ALL | wx.ALIGN_CENTRE_VERTICAL, 8)
        self._sz.AddStretchSpacer(1)
        self._sz.Add(self._btn_vis, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)
        self._sz.Add(self._btn_play, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 72)

        self.SetSizer(self._sz)

        self._sz.Fit(self)
        self.Layout()

        # ==== Bind events

        self._btn_rem.Bind(wx.EVT_BUTTON, self.on_remove)


    def on_remove(self, evt):
        self._parent.Destroy()

    def DoGetBestSize(self, *args, **kwargs):
        """ Return the best size, which is the width of the parent and the
        height or the content (determined through the sizer).
        """

        width, dummy = self._parent.GetSize()
        dummy, height = self._sz.GetSize()

        return wx.Size(width, height)

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for the Expander class.
        :param `event`: a `wx.SizeEvent` event to be processed.
        """
        width = self._parent.GetSize().GetWidth()
        self.SetSize((width, -1))
        self.Layout()
        self.Refresh()

    def OnDrawExpander(self, dc):
        """ This method draws the expand/collapse icons.
        It needs to be called from the parent's paint event
        handler.
        """
        wndRect = self.GetRect()
        drw = wndRect.GetRight() - 16 - 15
        self._foldIcons.Draw(self._parent._collapsed, dc, drw,
                             (wndRect.GetHeight() - 16) / 2,
                             wx.IMAGELIST_DRAW_TRANSPARENT)


class FixedExpander(Expander):

    def __init__(self, parent, label, wid=wx.ID_ANY):
        Expander.__init__(self, parent, label, wid)

        self._label_ctrl = wx.StaticText(self, -1, label)
        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)

class CustomExpander(Expander):

    DEFAULT_COLOR = "#88BA38"

    def __init__(self, parent, label, wid=wx.ID_ANY):
        Expander.__init__(self, parent, label, wid)

        self.stream_color = None
        self._btn_color = ImageButton(self, -1, bitmap=catalog['arr_down'].GetBitmap())
        self.set_stream_color()
        self._btn_color.SetToolTipString("Select colour")
        self._btn_color.Bind(wx.EVT_BUTTON, self.on_color_click)

        self._sz.Insert(2, self._btn_color, 0, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)

        #self._label_ctrl = wx.TextCtrl(self, -1, label, style=wx.NO_BORDER)
        self._label_ctrl = SuggestTextCtrl(self, id=-1, value=label)
        self._label_ctrl.SetChoices(TEST_STREAM_LST)
        self._label_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        self._label_ctrl.SetForegroundColour("#2FA7D4")

        self._sz.Remove(1)
        self._sz.Insert(1, self._label_ctrl, 1, wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL, 8)

    def set_stream_color(self, color=None):

        self.stream_color = color or self.DEFAULT_COLOR
        brush = wx.Brush(self.stream_color)
        pen = wx.Pen(self.stream_color)

        bmp = catalog['empty'].GetBitmap()
        mdc = wx.MemoryDC()
        mdc.SelectObject(bmp)
        mdc.SetBrush(brush)
        mdc.SetPen(pen)
        mdc.DrawRectangle(4, 4, 10, 10)
        mdc.SelectObject(wx.NullBitmap)

        self._btn_color.SetBitmapLabel(bmp)

        bmp = catalog['empty_h'].GetBitmap()
        mdc = wx.MemoryDC()
        mdc.SelectObject(bmp)
        mdc.SetBrush(brush)
        mdc.SetPen(pen)
        mdc.DrawRectangle(4, 4, 10, 10)
        mdc.SelectObject(wx.NullBitmap)

        self._btn_color.SetBitmaps(bmp)

    def get_stream_color(self):
        return self.stream_color

    def on_color_click(self, evt):

        # Remove the hover effect
        self._btn_color.OnLeave(evt)

        dlg = wx.ColourDialog(self)

        if dlg.ShowModal() == wx.ID_OK:
            data = dlg.GetColourData()
            self.set_stream_color(data.GetColour().GetAsString(wx.C2S_HTML_SYNTAX))

    def on_remove(self, evt):
        self._label_ctrl.Destroy()
        Expander.on_remove(self, evt)

class Slider(wx.Slider):
    """ This custom Slider class was implemented so it would not capture
    mouse wheel events, which were causing problems when the user wanted
    to scroll through the main fold panel bar.
    """

    def __init__(self, *args, **kwargs):
        wx.Slider.__init__(self, *args, **kwargs)
        self.Bind(wx.EVT_MOUSEWHEEL, self.pass_to_scollwin)

    def pass_to_scollwin(self, evt):
        """ This event handler prevents anything from happening to the Slider on
        MOUSEWHEEL events and passes the event on to any parent ScrolledWindow
        """

        # Find the parent ScolledWindow
        win = self.Parent
        while win and not isinstance(win, wx.ScrolledWindow):
            win = win.Parent

        # If a ScrolledWindow was found, pass on the event
        if win:
            win.GetEventHandler().ProcessEvent(evt)


class StreamPanel(wx.PyPanel):
    """ The StreamPanel super class, a special case collapsible pane."""

    expander_class = FixedExpander

    def __init__(self, parent, wid=wx.ID_ANY, label="no label",
                 pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, agwStyle=0,
                 validator=wx.DefaultValidator, name="CollapsiblePane",
                 collapsed=True):

        wx.PyPanel.__init__(self, parent, wid, pos, size, style, name)

        self._label = label
        self._collapsed = True
        self._agwStyle = agwStyle | wx.CP_NO_TLW_RESIZE #|wx.CP_GTK_EXPANDER


        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        self._panel.Hide()

        self._gbs = wx.GridBagSizer(3, 5)
        self._gbs.AddGrowableCol(1, 1)
        self._panel.SetSizer(self._gbs)

        self._expander = None

        self._sz = wx.BoxSizer(wx.HORIZONTAL)


        self.Bind(wx.EVT_SIZE, self.OnSize)

        # Process our custom 'collapsed' parameter.
        if not collapsed:
            self.collapse(collapsed)


    def collapse(self, collapse=True):
        """ Collapses or expands the pane window.
        """

        if self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse
        self.Thaw()

        # update button label
        # NB: this must be done after updating our "state"
        #self._expander.SetLabel(self._label)

        #self.on_status_change(self.GetBestSize())


    # def on_status_change(self, sz):
    #     """ Handles the status changes (collapsing/expanding).
    #     :param `sz`: an instance of `wx.Size`.
    #     """
    #     # minimal size has priority over the best size so set here our min size
    #     self.SetMinSize(sz)
    #     self.SetSize(sz)


    def finalize(self):
        """ This method builds all the child controls
        A delay was needed in order for all the settings to be loaded from the
        XRC file (i.e. Font and background/foregroung colors).
        """

        # ====== Add an expander button

        self.set_button(self.expander_class(self, self._label))
        self._sz.Add(self._expander, 0, wx.EXPAND)


        self._expander.Bind(wx.EVT_PAINT, self.on_draw_expander)
        if wx.Platform == "__WXMSW__":
            self._expander.Bind(wx.EVT_LEFT_DCLICK, self.on_button)

        self._expander.Bind(wx.EVT_LEFT_UP, self.OnToggle)

        # ====== Build panel controls

        self._panel.SetBackgroundColour(self.GetBackgroundColour())
        self._panel.SetForegroundColour("#DDDDDD")
        self._panel.SetFont(self.GetFont())

        # ====== Top row, auto contrast toggle button

        self._btn_auto_contrast = GenBitmapTextToggleButton(self._panel, -1,
                                    catalog['ico_contrast'].GetBitmap(), "Auto",
                                    size=(60, 24))
        self._btn_auto_contrast.SetForegroundColour("#000000")
        self._gbs.Add(self._btn_auto_contrast, (0, 0), flag=wx.LEFT, border=34)


        # ====== Second row, brightness label, slider and value

        lbl_brightness = wx.StaticText(self._panel, -1, "brightness:")
        self._gbs.Add(lbl_brightness, (1, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL,
                      border=34)

        self._sld_brightness = Slider(
            self._panel, -1, 128, 0, 255, (30, 60), (-1, 10),
            wx.SL_HORIZONTAL)

        self._gbs.Add(self._sld_brightness, (1, 1), flag=wx.EXPAND)


        self._txt_brightness = IntegerTextCtrl(self._panel, -1,
                str(self._sld_brightness.GetValue()),
                style=wx.NO_BORDER | wx.TE_PROCESS_ENTER,
                size=(30, -1),
                validator=IntegerValidator(self._sld_brightness.GetMin(),
                                           self._sld_brightness.GetMax()))
        self._txt_brightness.SetForegroundColour("#2FA7D4")
        self._txt_brightness.SetBackgroundColour(self.GetBackgroundColour())

        self._gbs.Add(self._txt_brightness, (1, 2),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)

        # ====== Third row, brightness label, slider and value

        lbl_contrast = wx.StaticText(self._panel, -1, "contrast:")
        self._gbs.Add(lbl_contrast, (2, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)

        self._sld_contrast = Slider(
            self._panel, -1, 128, 0, 255, (30, 60), (-1, 10),
            wx.SL_HORIZONTAL)

        self._gbs.Add(self._sld_contrast, (2, 1), flag=wx.EXPAND)


        self._txt_contrast = IntegerTextCtrl(self._panel, -1,
                str(self._sld_contrast.GetValue()),
                style=wx.NO_BORDER | wx.TE_PROCESS_ENTER,
                size=(30, -1),
                validator=IntegerValidator(self._sld_contrast.GetMin(),
                                           self._sld_contrast.GetMax()))
        self._txt_contrast.SetForegroundColour("#2FA7D4")
        self._txt_contrast.SetBackgroundColour(self.GetBackgroundColour())

        self._gbs.Add(self._txt_contrast, (2, 2),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)

        # Bind events

        self._sld_brightness.Bind(wx.EVT_COMMAND_SCROLL, self.on_brightness_slide)
        self._txt_brightness.Bind(wx.EVT_TEXT_ENTER, self.on_brightness_entered)
        self._txt_brightness.Bind(wx.EVT_CHAR, self.on_brightness_key)

        self._sld_contrast.Bind(wx.EVT_COMMAND_SCROLL, self.on_contrast_slide)
        self._txt_contrast.Bind(wx.EVT_TEXT_ENTER, self.on_contrast_entered)
        self._txt_contrast.Bind(wx.EVT_CHAR, self.on_contrast_key)

        self._btn_auto_contrast.Bind(wx.EVT_BUTTON, self.on_toggle_autocontrast)

    def on_toggle_autocontrast(self, evt):
        enabled = not self._btn_auto_contrast.GetToggle()

        self._sld_brightness.Enable(enabled)
        self._txt_brightness.Enable(enabled)

        self._sld_contrast.Enable(enabled)
        self._txt_contrast.Enable(enabled)

    def on_brightness_key(self, evt):
        key = evt.GetKeyCode()

        if key == wx.WXK_UP:
            val = int(self._txt_brightness.GetValue() or 0)
            val += 1
        elif key == wx.WXK_DOWN:
            val = int(self._txt_brightness.GetValue() or 0)
            val -= 1
        else:
            evt.Skip()
            return

        self._sld_brightness.SetValue(val)
        self._txt_brightness.SetValue(str(val))

    def on_brightness_entered(self, evt):
        self._sld_brightness.SetValue(int(self._txt_brightness.GetValue()))

    def on_brightness_slide(self, evt):
        self._txt_brightness.SetValue(str(self._sld_brightness.GetValue()))

    def on_contrast_entered(self, evt):
        self._sld_contrast.SetValue(int(self._txt_contrast.GetValue()))

    def on_contrast_slide(self, evt):
        self._txt_contrast.SetValue(str(self._sld_contrast.GetValue()))

    def on_contrast_key(self, evt):
        key = evt.GetKeyCode()
        val = int(self._txt_contrast.GetValue())

        if key == wx.WXK_UP:
            val += 1
        elif key == wx.WXK_DOWN:
            val -= 1

        self._sld_contrast.SetValue(val)
        self._txt_contrast.SetValue(str(val))

        evt.Skip()

    def OnToggle(self, evt):
        self.collapse(not self._collapsed)

        # this change was generated by the user - send the event
        ev = wx.CollapsiblePaneEvent(self, self.GetId(), self._collapsed)
        self.GetEventHandler().ProcessEvent(ev)
        evt.Skip()

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for StreamPanel
        """
        self.Layout()

    def on_button(self, event):
        """ Handles the wx.EVT_BUTTON event for StreamPanel
        """

        if event.GetEventObject() != self._expander:
            event.Skip()
            return

        self.collapse(not self._collapsed)

        # this change was generated by the user - send the event
        ev = wx.CollapsiblePaneEvent(self, self.GetId(), self._collapsed)
        self.GetEventHandler().ProcessEvent(ev)

    def get_panel(self):
        """ Returns a reference to the pane window. Use the returned `wx.Window`
        as the parent of widgets to make them part of the collapsible area.
        """
        return self._panel

    def set_button(self, button):
        """ Assign a new expander button to the stream panel.

        :param `button`: can be the standard `wx.Button` or any of the generic
         implementations which live in `wx.lib.buttons`.
        """

        if self._expander:
            self._sz.Replace(self._expander, button)
            self.Unbind(wx.EVT_BUTTON, self._expander)
            self._expander.Destroy()

        self._expander = button
        self.SetLabel(button.GetLabel())
        self.Bind(wx.EVT_BUTTON, self.on_button, self._expander)

        if self._panel:
            self._expander.MoveBeforeInTabOrder(self._panel)
        self.Layout()


    def get_button(self):
        """ Returns the button associated with StreamPanel. """
        return self._expander

    def on_draw_expander(self, event):
        """ Handles the ``wx.EVT_PAINT`` event for the stream panel.
        :note: This is a drawing routine to paint the GTK-style expander.
        """

        dc = wx.AutoBufferedPaintDC(self._expander)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        self._expander.OnDrawExpander(dc)

    def Layout(self, *args, **kwargs):
        """ Layout the StreamPanel. """

        if not self._expander or not self._panel or not self._sz:
            return False     # we need to complete the creation first!

        oursz = self.GetSize()

        # move & resize the button and the static line
        self._sz.SetDimension(0, 0, oursz.GetWidth(),
                              self._sz.GetMinSize().GetHeight())
        self._sz.Layout()

        if not self._collapsed:
            # move & resize the container window
            yoffset = self._sz.GetSize().GetHeight()
            self._panel.SetDimensions(0, yoffset, oursz.x, oursz.y - yoffset)

            # this is very important to make the pane window layout show
            # correctly
            self._panel.Show()
            self._panel.Layout()

        return True

    def DoGetBestSize(self, *args, **kwargs):
        """ Gets the size which best suits the window: for a control, it would
        be the minimal size which doesn't truncate the control, for a panel -
        the same size as it would have after a call to `Fit()`.
        """

        # do not use GetSize() but rather GetMinSize() since it calculates
        # the required space of the sizer
        sz = self._sz.GetMinSize()

        # when expanded, we need more space
        if not self._collapsed:
            pbs = self._panel.GetBestSize()
            sz.width = max(sz.GetWidth(), pbs.x)
            sz.height = sz.y + pbs.y

        return sz

    # def Layout(self):
    #     pcp.PyCollapsiblePane.Layout(self)
    #     #self._panel.SetBackgroundColour(self.GetBackgroundColour())


class FixedStreamPanel(StreamPanel): #pylint: disable=R0901
    """ A pre-defined stream panel """

    expander_class = FixedExpander

    def __init__(self, *args, **kwargs):
        StreamPanel.__init__(self, *args, **kwargs)

    def finalize(self):
        StreamPanel.finalize(self)

class CustomStreamPanel(StreamPanel): #pylint: disable=R0901
    """ A custom made stream panel """

    expander_class = CustomExpander

    def __init__(self, *args, **kwargs):
        StreamPanel.__init__(self, *args, **kwargs)


    def finalize(self):
        StreamPanel.finalize(self)

        lbl_excitation = wx.StaticText(self._panel, -1, "excitation:")
        self._gbs.Add(lbl_excitation, (3, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)

        self._txt_excitation = IntegerTextCtrl(self._panel, -1, "0",
                style=wx.NO_BORDER | wx.TE_PROCESS_ENTER,
                size=(40, -1),
                validator=IntegerValidator(0, 1000))
        self._txt_excitation.SetForegroundColour("#2FA7D4")
        self._txt_excitation.SetBackgroundColour(self.GetBackgroundColour())

        self._gbs.Add(self._txt_excitation, (3, 1),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)

        lbl_emission = wx.StaticText(self._panel, -1, "emission:")
        self._gbs.Add(lbl_emission, (4, 0),
                      flag=wx.LEFT | wx.ALIGN_CENTRE_VERTICAL, border=34)

        self._txt_emission = IntegerTextCtrl(self._panel, -1, "0",
                style=wx.NO_BORDER | wx.TE_PROCESS_ENTER,
                size=(40, -1),
                validator=IntegerValidator(0, 1000))
        self._txt_emission.SetForegroundColour("#2FA7D4")
        self._txt_emission.SetBackgroundColour(self.GetBackgroundColour())

        self._gbs.Add(self._txt_emission, (4, 1),
                      flag=wx.ALIGN_CENTRE_VERTICAL | wx.RIGHT,
                      border=10)
