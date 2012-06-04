
# + PyCollapsiblePane (PyPanel)
# |-- GTKExpander
# |-- wx.Panel
#    |  |--- CaptionBar
#    |  |--- wx.Window subclass
#    .  .
#    .  .
#    |  |--- wx.Window subclass
#    |
#    |--+ FoldPanelItem
#       |--- CaptionBar
#       |--- wx.Window subclass
#       .
#       .
#       |--- wx.Window subclass

import wx
import wx.lib.agw.pycollapsiblepane as pcp



class StreamExpander(pcp.GTKExpander):
    def __init__(self, parent, id=wx.ID_ANY, label="", pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=wx.NO_BORDER):
        pcp.GTKExpander.__init__(self, parent, id, label, pos, size, style)


class StreamPanel(pcp.PyCollapsiblePane):
    """ The StreamPanel super class, a special case collapsible pane."""

    def __init__(self, parent, wid=wx.ID_ANY, label="", pos=wx.DefaultPosition,
                 size=wx.DefaultSize, style=wx.CP_DEFAULT_STYLE, agwStyle=0,
                 validator=wx.DefaultValidator, name="CollapsiblePane",
                 collapsed=True):

        # We enforce Delmic's default style
        agwStyle = agwStyle|wx.CP_GTK_EXPANDER|wx.CP_NO_TLW_RESIZE

        pcp.PyCollapsiblePane.__init__(self, parent, wid, label, pos, size,
                                style, agwStyle, validator, name)

        # Process our custom 'collaped' parameter.
        if not collapsed:
            self.Collapse(collapsed)

        # Allow for fast open/close toggling by processing double clicks
        self._pButton.Bind(wx.EVT_LEFT_DCLICK, self.OnButton)

    def OnButton(self, event):
        pcp.PyCollapsiblePane.OnButton(self, event)

class FixedStreamPanel(StreamPanel): #pylint: disable=R0901
    """ A pre-defined stream panel """

    def __init__(self, *args, **kwargs):
        StreamPanel.__init__(self, *args, **kwargs)

class CustomStreamPanel(StreamPanel): #pylint: disable=R0901
    """ A custom made stream panel """

    def __init__(self, *args, **kwargs):
        StreamPanel.__init__(self, *args, **kwargs)
