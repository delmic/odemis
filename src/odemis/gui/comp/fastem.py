# -*- coding: utf-8 -*-
"""
:author: Rinze de Laat <laat@delmic.com>
:copyright: © 2012-2021 Rinze de Laat, Philip Winkler, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and manipulate various
data streams coming from the microscope.

"""

import logging
from odemis import gui
from odemis.gui import FG_COLOUR_MAIN, BG_COLOUR_MAIN, BG_COLOUR_STREAM, \
    FG_COLOUR_BUTTON
from odemis.gui import img
from odemis.gui.comp import buttons
from odemis.gui.comp._constants import CAPTION_PADDING_RIGHT, EVT_STREAM_REMOVE, ICON_WIDTH, ICON_HEIGHT
from odemis.gui.comp.combo import ComboBox
from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.comp.slider import UnitFloatSlider
from odemis.gui.comp.text import PatternValidator
import wx
import wx.lib.newevent


class FastEMProjectPanelHeader(wx.Control):
    """
    A widget for expanding and collapsing the project panel. It also contains a remove button and a
    text control for the project name.
    """

    BUTTON_SIZE = (18, 18)  # The pixel size of the button
    BUTTON_BORDER_SIZE = 9  # Border space around the buttons

    def __init__(self, parent, name, wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize, style=wx.NO_BORDER):
        assert (isinstance(parent, FastEMProjectPanel))
        super(FastEMProjectPanelHeader, self).__init__(parent, wid, pos, size, style)

        self.SetBackgroundColour(self.Parent.BackgroundColour)

        # This style enables us to draw the background with our own paint event handler
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        # Create and add sizer and populate with controls
        self._sz = wx.BoxSizer(wx.HORIZONTAL)

        # Fold indicator icon, drawn directly in the background in a fixed position
        self._foldIcons = wx.ImageList(16, 16)
        self._foldIcons.Add(img.getBitmap("icon/arr_down_s.png"))
        self._foldIcons.Add(img.getBitmap("icon/arr_right_s.png"))

        # Add the needed controls to the sizer
        self.btn_remove = self._add_remove_btn()
        self.txt_ctrl = self._add_text_ctrl(name)

        # Add spacer for creating padding on the right side of the header panel
        self._sz.Add((64, 1), 0)

        # Set the sizer of the Control
        self.SetSizerAndFit(self._sz)
        self.Bind(wx.EVT_SIZE, self.on_size)
        self.Layout()

    # Control creation methods
    def _add_remove_btn(self):
        """ Add a button for project removal """
        btn_rem = buttons.ImageButton(self,
                                      bitmap=img.getBitmap("icon/ico_rem_str.png"),
                                      size=self.BUTTON_SIZE)
        btn_rem.bmpHover = img.getBitmap("icon/ico_rem_str_h.png")
        btn_rem.SetToolTip("Remove project")
        self._add_ctrl(btn_rem)
        return btn_rem

    def _add_text_ctrl(self, name):
        """ Add a label control to the header panel """
        txt_ctrl = wx.TextCtrl(self, wx.ID_ANY, name, style=wx.TE_PROCESS_ENTER | wx.BORDER_NONE,
                               validator=PatternValidator(r'[A-Za-z0-9_()-]+'), size=(-1, 35))
        txt_ctrl.SetBackgroundColour(self.Parent.GetBackgroundColour())
        txt_ctrl.SetForegroundColour(FG_COLOUR_MAIN)
        self._add_ctrl(txt_ctrl, stretch=True)
        return txt_ctrl

    def _add_ctrl(self, ctrl, stretch=False):
        """ Add the given control to the header panel

        :param ctrl: (wx.Control) Control to add to the header panel
        :param stretch: True if the control should expand to fill space
        """

        # Only the first element has a left border
        border = wx.ALL if self._sz.IsEmpty() else wx.RIGHT

        self._sz.Add(
            ctrl,
            proportion=1 if stretch else 0,
            flag=(border | wx.ALIGN_CENTRE_VERTICAL | wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
            border=self.BUTTON_BORDER_SIZE
        )

    # Layout and painting
    def on_size(self, event):
        """ Handle the wx.EVT_SIZE event for the Expander class """
        self.SetSize((self.Parent.GetSize().x, -1))
        self.Layout()
        self.Refresh()
        event.Skip()

    def on_draw_expander(self, dc):
        """ Draw the expand/collapse arrow icon

        It needs to be called from the parent's paint event handler.
        """
        win_rect = self.GetRect()
        x_pos = win_rect.GetRight() - ICON_WIDTH - CAPTION_PADDING_RIGHT

        self._foldIcons.Draw(
            1 if self.Parent.collapsed else 0,
            dc,
            x_pos,
            (win_rect.GetHeight() - ICON_HEIGHT) // 2,
            wx.IMAGELIST_DRAW_TRANSPARENT
        )


class FastEMProjectList(wx.Panel):
    """
    The whole panel containing project panels and a button to add more projects.
    """

    DEFAULT_BORDER = 2
    DEFAULT_STYLE = wx.BOTTOM | wx.EXPAND

    def __init__(self, *args, **kwargs):

        add_btn = kwargs.pop('add_button', False)

        wx.Panel.__init__(self, *args, **kwargs)

        self.project_panels = []

        self._sz = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sz)

        self.btn_add_project = buttons.ImageTextButton(
            self, -1,
            label="ADD PROJECT",
            style=wx.ALIGN_CENTER,
            bitmap=img.getBitmap("stream_add_b.png")
        )
        self.btn_add_project.SetForegroundColour(FG_COLOUR_BUTTON)
        self.btn_add_project.SetToolTip("Add a new project. A project can be used to organize "
                                        "regions of acquisition (ROA) of similar type.")
        self._sz.Add(self.btn_add_project, flag=wx.ALL, border=10)
        self.btn_add_project.Show(add_btn)

        self.txt_no_project = wx.StaticText(self, -1, "No projects available.")
        self._sz.Add(self.txt_no_project, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        self.fit_panels()

    # === Event Handlers
    def on_project_remove(self, evt):
        """
        Called when user request to remove a project via the project panel
        """
        p = evt.spanel.project
        logging.debug("User removed project (panel) %s", p.name.value)
        # delete project panel
        self.remove_project_panel(evt.ppanel)

    def on_projectp_destroy(self, evt):
        """
        Called when a project panel is completely removed
        """
        self.fit_panels()

    # === API of the project panel
    def is_empty(self):
        return len(self.project_panels) == 0

    def get_size(self):
        """ Return the number of streams contained within the StreamBar """
        return len(self.project_panels)

    def add_project_panel(self, ppanel, show=True):
        """
        This method adds a project panel to the project bar. The appropriate
        position is automatically determined.
        ppanel (ProjectPanel): a project panel
        """
        ins_pos = len(self.project_panels) + 1
        self.project_panels.append(ppanel)
        self._sz.Insert(ins_pos, ppanel, flag=self.DEFAULT_STYLE, border=self.DEFAULT_BORDER)

        # TODO: instead of a stream_remove message, just take a callable to call
        # when the stream needs to be removed
        ppanel.Bind(EVT_STREAM_REMOVE, self.on_project_remove)
        ppanel.Bind(wx.EVT_WINDOW_DESTROY, self.on_projectp_destroy, source=ppanel)
        ppanel.Layout()

        # hide the stream if the current view is not compatible
        ppanel.Show(show)
        self.fit_panels()

    def remove_project_panel(self, ppanel):
        """
        Removes a project panel
        Deletion of the actual project must be done separately.
        Must be called in the main GUI thread
        """
        # Remove it from the sizer explicitly, because even if the sizer will
        # eventually detect it (via the destroy event), that will be later, and
        # until then the fit_stream will not be correct.
        self._sz.Detach(ppanel)
        self.project_panels.remove(ppanel)
        ppanel.Destroy()

    def enable_buttons(self, enabled):
        for p in self.project_panels:
            p.btn_add_roa.Enable(enabled)
        self.btn_add_project.Enable(enabled)

    def fit_panels(self):
        # When the whole window/app is destroyed, each widget receives a destroy
        # event. In such a case, it's not worthy re-fitting the streams, and
        # especially it can fail because some other objects have already been
        # destroyed.
        if not self or self.IsBeingDeleted():
            logging.debug("Project panelbar is being deleted, not refitting")
            return

        logging.debug("Refitting project panels")
        # Display a warning text when no streams are present
        self.txt_no_project.Show(self.is_empty())

        h = self._sz.GetMinSize().GetHeight()
        self.SetSize((-1, h))

        p = self.Parent
        while not isinstance(p, FoldPanelItem):
            p = p.Parent

        p.Refresh()


class FastEMProjectPanel(wx.Panel):
    """
    Panel for one project, containing multiple ROAPanels.
    """

    DEFAULT_BORDER = 2
    DEFAULT_STYLE = wx.BOTTOM | wx.EXPAND

    def __init__(self, parent,
                 wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, name="ProjectPanel", collapsed=False):

        assert(isinstance(parent, FastEMProjectList))
        wx.Panel.__init__(self, parent, wid, pos, size, style, name)

        # Appearance
        self.SetBackgroundColour(BG_COLOUR_STREAM)
        self.SetForegroundColour(FG_COLOUR_MAIN)

        # State
        self._collapsed = collapsed

        # Counter that keeps track of the number of rows containing controls inside this panel
        self.num_rows = 0
        self.roa_panels = []

        # Create project header
        self._header = FastEMProjectPanelHeader(self, name)
        self._header.Bind(wx.EVT_LEFT_UP, self.on_toggle)
        self._header.Bind(wx.EVT_PAINT, self.on_draw_expander)
        self.Bind(wx.EVT_BUTTON, self.on_button, self._header)

        # Create the control panel
        self._panel = wx.Panel(self, style=wx.TAB_TRAVERSAL | wx.NO_BORDER)
        self._panel.SetBackgroundColour(BG_COLOUR_MAIN)
        self._panel.SetForegroundColour(FG_COLOUR_MAIN)
        self._panel.SetFont(self.GetFont())

        # Add a border sizer so we can create padding for the panel
        self._border_sizer = wx.BoxSizer(wx.VERTICAL)
        self._panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self._border_sizer.Add(self._panel_sizer, border=10, flag=wx.BOTTOM | wx.EXPAND, proportion=1)
        self._panel.SetSizer(self._border_sizer)

        self._main_sizer = wx.BoxSizer(wx.VERTICAL)
        self._main_sizer.Add(self._header, 0, wx.EXPAND)
        self._main_sizer.Add(self._panel, 0, wx.EXPAND)
        self.SetSizer(self._main_sizer)

        # Add roi button
        self.btn_add_roa = buttons.ImageTextButton(
            self._panel, -1,
            label="ADD ROA",
            style=wx.ALIGN_CENTER,
            bitmap=img.getBitmap("stream_add_b.png"),
        )
        self.btn_add_roa.SetForegroundColour(FG_COLOUR_BUTTON)
        self.btn_add_roa.SetToolTip("Add new region of acquisition (ROA) to project.")
        self._panel_sizer.Add(self.btn_add_roa, flag=wx.TOP | wx.LEFT | wx.RIGHT, border=10)
        self.btn_add_roa.Show(True)

        # Make remove button and text control public (for FastEMProjectBarController)
        self.btn_remove = self._header.btn_remove
        self.txt_ctrl = self._header.txt_ctrl

    @property
    def collapsed(self):
        return self._collapsed

    def flatten(self):
        """ Unfold the stream panel and hide the header """
        self.collapse(False)
        self._header.Show(False)

    def collapse(self, collapse):
        """ Collapses or expands the pane window """

        if self._collapsed == collapse:
            return

        self.Freeze()

        # update our state
        self._panel.Show(not collapse)
        self._collapsed = collapse

        # Call after is used, so the fit will occur after everything has been hidden or shown
        wx.CallAfter(self.Parent.fit_panels)

        self.Thaw()

    def OnSize(self, event):
        """ Handles the wx.EVT_SIZE event for FastEMProjectPanel """
        self.Layout()
        event.Skip()

    def on_toggle(self, evt):
        """ Detect click on the collapse button of the FastEMProjectPanel """
        w = evt.GetEventObject().GetSize().GetWidth()

        if evt.GetX() > w * 0.85:
            self.collapse(not self._collapsed)
        else:
            evt.Skip()

    def on_button(self, event):
        """ Handles the wx.EVT_BUTTON event for FastEMProjectPanel """
        if event.GetEventObject() != self._header:
            event.Skip()
            return

        self.collapse(not self._collapsed)

    def on_draw_expander(self, event):
        """ Handle the ``wx.EVT_PAINT`` event for the stream panel
        :note: This is a drawing routine to paint the GTK-style expander.
        """
        dc = wx.AutoBufferedPaintDC(self._header)
        dc.SetBackground(wx.Brush(self.GetBackgroundColour()))
        dc.Clear()

        self._header.on_draw_expander(dc)

    def add_roa_panel(self, roa_panel):
        """ Add a ROA control panel to the project panel, append .roa_panels.
        :param roa_panel: (FastEMROAPanel) panel to be added
        """
        self.num_rows += 1
        self.roa_panels.append(roa_panel)
        self._panel_sizer.Add(roa_panel, border=10, flag=wx.LEFT | wx.RIGHT | wx.EXPAND, proportion=1)
        self.fit_panels()

    def fit_panels(self):
        # When the whole window/app is destroyed, each widget receives a destroy
        # event. In such a case, it's not worthy re-fitting the streams, and
        # especially it can fail because some other objects have already been
        # destroyed.
        if not self or self.IsBeingDeleted():
            logging.debug("ROA panelbar is being deleted, not refitting")
            return

        logging.debug("Refitting ROA panels")

        h = self._panel_sizer.GetMinSize().GetHeight()
        self.SetSize((-1, h))

        p = self.Parent
        while not isinstance(p, FoldPanelItem):
            p = p.Parent

        p.Refresh()


class FastEMROAPanel(wx.Panel):
    """ Panel for one region of acquisition. """
    BUTTON_SIZE = (18, 18)  # The pixel size of the button
    BUTTON_BORDER_SIZE = 9  # Border space around the buttons

    def __init__(self, parent, name, calibrations, wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE):
        """
        name (str): ROA name, default text for the text control
        calibrations (list of str): choices for calibration combobox
        """
        assert (isinstance(parent, FastEMProjectPanel))
        wx.Panel.__init__(self, parent._panel, wid, pos, size, style, name)
        self._parent = parent
        self._panel_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.SetForegroundColour(gui.FG_COLOUR_EDIT)
        self.SetBackgroundColour(gui.BG_COLOUR_MAIN)

        # Add controls
        self.btn_remove = self._add_remove_btn()
        self.txt_ctrl = self._add_text_ctrl(name)
        self.calibration_ctrl = self._add_combobox(calibrations)

        # Fit sizer
        self._panel_sizer.AddSpacer(5)
        self.SetSizerAndFit(self._panel_sizer)
        self.Bind(wx.EVT_SIZE, self._on_size)
        self.Layout()
        self._parent.Refresh()

    def activate(self):
        self.SetBackgroundColour(gui.BG_COLOUR_STREAM)
        self.txt_ctrl.SetBackgroundColour(gui.BG_COLOUR_STREAM)
        self.calibration_ctrl.SetBackgroundColour(gui.BG_COLOUR_STREAM)

    def deactivate(self):
        self.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.txt_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self.calibration_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)

    def _add_remove_btn(self):
        """ Add a button for ROI removal """
        btn_rem = buttons.ImageButton(self, bitmap=img.getBitmap("icon/ico_rem_str.png"), size=self.BUTTON_SIZE)
        btn_rem.bmpHover = img.getBitmap("icon/ico_rem_str_h.png")
        btn_rem.SetToolTip("Remove RoA")
        self._add_ctrl(btn_rem)
        return btn_rem

    def _add_text_ctrl(self, default_text):
        """ Add a text ctrl to the control grid

        :param default_text: (str)
        :return: (wx.TextCtrl)
        """
        txt_ctrl = wx.TextCtrl(self, wx.ID_ANY, default_text, style=wx.TE_PROCESS_ENTER | wx.BORDER_NONE,
                               validator=PatternValidator(r'[A-Za-z0-9_()-]+'), size=(-1, 35))
        txt_ctrl.SetForegroundColour(gui.FG_COLOUR_EDIT)
        txt_ctrl.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        self._add_ctrl(txt_ctrl, True)
        return txt_ctrl

    def _add_combobox(self, choices):
        """ Add a combobox to the control grid

        :param choices: (list of str)
        :return: (wx.ComboBox)
        """
        calibration_ctrl = ComboBox(self, value=choices[0], choices=choices, size=(100, -1),
                                    style=wx.CB_READONLY | wx.BORDER_NONE)
        self._add_ctrl(calibration_ctrl)
        return calibration_ctrl

    def _add_ctrl(self, ctrl, stretch=False):
        """ Add the given control to the panel

        :param ctrl: (wx.Control) Control to add to the header panel
        :param stretch: True if the control should expand to fill space
        """
        self._panel_sizer.Add(
            ctrl,
            proportion=1 if stretch else 0,
            flag=(wx.RIGHT | wx.ALIGN_CENTRE_VERTICAL | wx.RESERVE_SPACE_EVEN_IF_HIDDEN),
            border=self.BUTTON_BORDER_SIZE
        )

    def _on_size(self, event):
        """ Handle the wx.EVT_SIZE event for the Expander class """
        self.SetSize((self._parent.GetSize().x, -1))
        self.Layout()
        self.Refresh()
        event.Skip()


class FastEMCalibrationPanelHeader(wx.Panel):
    """
    The whole panel containing the panel with the calibration buttons.
    """

    DEFAULT_BORDER = 2
    DEFAULT_STYLE = wx.BOTTOM | wx.EXPAND

    def __init__(self, *args, **kwargs):
        kwargs.pop('add_button', False)  # remove add_button kwarg
        wx.Panel.__init__(self, *args, **kwargs)

        self._sz = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sz)

    def add_calibration_panel(self, panel):
        """
        This method adds the calibration panel to the calibration header. Should only be called once.
        :param panel: (FastEMCalibrationPanel) The calibration panel to be added.
        """
        self._sz.Insert(0, panel, flag=self.DEFAULT_STYLE, border=self.DEFAULT_BORDER)
        panel.Layout()


class FastEMCalibrationPanel(wx.Panel):
    """
    Panel for the calibration buttons.
    """

    def __init__(self, parent, layout,
                 wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, name="CalibrationPanel"):
        """
        :param layout: (list of lists of int) Layout of scintillator grid, given as 2D list of scintillator positions,
        e.g. [[6, 5, 4], [3, 2, 1]]
        """
        assert(isinstance(parent, FastEMCalibrationPanelHeader))
        wx.Panel.__init__(self, parent, wid, pos, size, style, name)

        self.buttons = {}  # wx.ToggleButton (scintillator's toggle button) --> int (scintillator number)

        self._panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._panel_sizer)

        # Calibration Grid
        nrows = len(layout)
        ncols = max(len(row) for row in layout)
        calgrid_sz = wx.GridBagSizer(nrows, ncols)
        for row_idx, row in enumerate(layout):
            for col_idx, elem in enumerate(row):
                subsz = wx.BoxSizer(wx.HORIZONTAL)
                # Hotfix: The button size is set to 35x35 for Ubuntu 20.04 so that we can see
                # the label, 30x30 size works fine for Ubuntu 18.04. Ideally the button size
                # should be set based on the label text length, but the button looks larger than
                # needed. Also tried using wx.Font to lower the label text size for 30x30, but
                # does not make a difference.
                # btn.SetSize(btn.GetSizeFromTextSize(btn.GetTextExtent("OK"))) returns (-1, -1)
                btn = wx.ToggleButton(self, label="?", size=(35, 35))
                btn.SetBackgroundColour(FG_COLOUR_BUTTON)
                subsz.Add(btn)
                subsz.AddSpacer(8)

                calgrid_sz.Add(subsz, pos=(row_idx, col_idx))
                txt = wx.StaticText(self, wx.ALL | wx.ALIGN_CENTER, str(elem), size=(10, -1))
                subsz.Add(txt, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 5)
                subsz.AddSpacer(20)

                self.buttons[btn] = elem

        self._panel_sizer.Add(calgrid_sz, 0, wx.ALL | wx.ALIGN_CENTER, 10)
        self._panel_sizer.AddSpacer(10)


class FastEMOverviewSelectionPanel(wx.Panel):
    """
    Panel containing scintillator toggle buttons based on the layout of scintillator grid.
    """

    def __init__(self, parent,
                 wid=wx.ID_ANY, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.CP_DEFAULT_STYLE, name="OverviewSelectionPanel"):
        """
        layout (list of lists of int): layout of scintillator grid, given as 2D list of scintillator positions,
        e.g. [[6, 5, 4], [3, 2, 1]]
        """
        wx.Panel.__init__(self, parent, wid, pos, size, style, name)
        self.buttons = {}  # wx.ToggleButton (scintillator's toggle button) --> int (scintillator number)
        # Dwell time slider variables
        self.dwell_time_slider_ctrl = None
        self._dwell_time_grid_sz = wx.GridBagSizer()
        self._dwell_time_grid_sz.SetEmptyCellSize((0, 0))

        self._panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._panel_sizer)

    def _add_dwell_time_slider(self, label_text: str = "Dwell time", value: float = None, conf: dict = None):
        """ Add a dwell time float value slider to the overview acquisition panel

        :param label_text: (str) Label text to display
        :param value: (None or float) Value to display
        :param conf: (None or dict) Dictionary containing parameters for the control

        """
        # Create label
        lbl_ctrl = wx.StaticText(self, -1, str(label_text))
        self._dwell_time_grid_sz.Add(lbl_ctrl, (0, 0),
                                     flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL, border=5)
        # Create float slider
        value_ctrl = UnitFloatSlider(self, value=value, **conf)
        self._dwell_time_grid_sz.Add(value_ctrl, (0, 1),
                                     flag=wx.ALL | wx.EXPAND | wx.ALIGN_CENTER_VERTICAL, border=5)
        return value_ctrl

    def create_controls(self, layout, dwell_time_slider_conf: dict = None):
        """
        Create overview selection panel controls.

        :param layout: (list of lists of int) Layout of scintillator grid, given as 2D list
            of scintillator positions, e.g. [[6, 5, 4], [3, 2, 1]]
        :param dwell_time_slider_conf: (dict) Dictionary containing parameters for the dwell
            time slider control.

        """
        if dwell_time_slider_conf is not None:
            # Add dwell time slider
            self._panel_sizer.Add(
                self._dwell_time_grid_sz, proportion=1, flag=wx.ALL | wx.EXPAND, border=5
            )
            self.dwell_time_slider_ctrl = self._add_dwell_time_slider(
                value=dwell_time_slider_conf["min_val"], conf=dwell_time_slider_conf
            )
            self._dwell_time_grid_sz.AddGrowableCol(1)
            # Add divider
            line_ctrl = wx.StaticLine(self, size=(-1, 1))
            line_ctrl.SetBackgroundColour(gui.BG_COLOUR_SEPARATOR)
            self._dwell_time_grid_sz.Add(line_ctrl, (1, 0), span=(1, 2),
                                         flag=wx.ALL | wx.EXPAND, border=5)
        # Create a GridBagSizer to add scintillator toggle buttons based on
        # the layout of scintillator grid
        nrows = len(layout)
        ncols = max(len(row) for row in layout)
        calgrid_sz = wx.GridBagSizer(nrows, ncols)
        for row_idx, row in enumerate(layout):
            for col_idx, elem in enumerate(row):
                subsz = wx.BoxSizer(wx.HORIZONTAL)
                btn = wx.ToggleButton(self, size=(30, 30))
                btn.SetBackgroundColour("#999999")
                subsz.Add(btn)
                subsz.AddSpacer(8)

                calgrid_sz.Add(subsz, pos=(row_idx, col_idx))
                txt = wx.StaticText(self, wx.ALL | wx.ALIGN_CENTER, str(elem), size=(10, -1))
                subsz.Add(txt, wx.LEFT | wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 5)
                subsz.AddSpacer(20)

                self.buttons[btn] = elem
        self._panel_sizer.Add(calgrid_sz, 0, wx.ALL | wx.ALIGN_CENTER, 10)
