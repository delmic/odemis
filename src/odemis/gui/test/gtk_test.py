"""
This file contain a small GTK2 customisation test

Invoking GTK2 customisations turns out to be as really easy. All that has to be done is set the
GTK2_RC_FILES environment variable to the full path af a gtkrc file, containing the styles you want
to customise in your application. This variable should be set *before* the wxApp is created!

There are the following unresolved problems regarding the wxCheckbox control (called GtkCheckButton
in GTK2):

- The hover effect, where the background color changes, can be changed. The problem is that,
so far, the only way to do that is to give the background a different color, which only really works
when you know the color of the parent window background.

- When proving a label, GTK will create a checkbox and a label control. I've been unsuccesful in
preventing the background color of the label from changing when the control has the focus.

- When creating a checkbox in wxPython without a label, the label defaults to an empty string. This
in turn will cause GTK to render a label with no text. Yet, the background color of this empty label
still changes, as described in the previous point, causing a weird line to appear.

- When creating a checkbox in wxPython and using the wx.ALIGN_RIGHT style, it will display the label
at the left side of the checkbox instead of the right. But even though the control switch places,
the GTK customisations do not, making the label invisible and giving the checkbox a weird
background color.


There are also a few question concerning GTK in general that need answering:

- It's still unclear on how to only select certain checkboxes in wxPython for customisation. The way
it's implemented in this test gtkrc file, it will also change the look of checkboxes in dialog
windows for example.
- Finding good information online is somewhat troublesome, because google keeps giving mixed
answers pertaining to GTK1.2, GTK2 and GTK3. Also, it's hard to get results concerning just GTK
theming and not GTK programming in general


"""

import wx
import os

import odemis.gui.test as test

gtkrc_path = os.path.dirname(os.path.realpath(__file__))

os.environ['GTK2_RC_FILES'] = os.path.join(gtkrc_path, 'gtkrc')


class OwnerDrawnComboBoxTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def test_gtk(self):
        test.goto_manual()

        cbr = wx.CheckBox(self.panel, -1, "Label text")
        self.add_control(cbr)

        cb = wx.CheckBox(self.panel, -1, "")
        self.add_control(cb)

        cbl = wx.CheckBox(self.panel, -1, "Label left", style=wx.ALIGN_RIGHT)
        self.add_control(cbl)
