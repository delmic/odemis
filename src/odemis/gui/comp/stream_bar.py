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
from odemis import acq
from odemis.gui import FG_COLOUR_BUTTON
from odemis.gui.comp import buttons
from odemis.gui.comp.foldpanelbar import FoldPanelItem
from odemis.gui.evt import EVT_STREAM_REMOVE
import wx


class StreamBar(wx.Panel):
    """
    The whole panel containing stream panels and a button to add more streams
    There are multiple levels of visibility of a stream panel:
     * the stream panel is shown in the panel and has the visible icon on:
        The current view is compatible with the stream and has it in its list
        of streams.
     * the stream panel is shown in the panel and has the visible icon off:
        The current view is compatible with the stream, but the stream is not
        in its list of streams
     * the stream panel is not present in the panel (hidden):
        The current view is not compatible with the stream
    """

    DEFAULT_BORDER = 2
    DEFAULT_STYLE = wx.BOTTOM | wx.EXPAND
    # the order in which the streams are displayed
    STREAM_ORDER = (
        acq.stream.ScannerSettingsStream,
        acq.stream.SEMStream,
        acq.stream.StaticSEMStream,
        acq.stream.EBICSettingsStream,
        acq.stream.BrightfieldStream,
        acq.stream.StaticStream,
        acq.stream.FluoStream,
        acq.stream.CLStream,
        acq.stream.CameraStream,
        acq.stream.FIBStream,
        acq.stream.ARSettingsStream,
        acq.stream.SpectrumSettingsStream,
        acq.stream.AngularSpectrumSettingsStream,
        acq.stream.ScannedTemporalSettingsStream,
        acq.stream.TemporalSpectrumSettingsStream,
        acq.stream.MonochromatorSettingsStream,
        acq.stream.CameraCountStream,
        acq.stream.ScannedTCSettingsStream,
    )

    def __init__(self, *args, **kwargs):

        add_btn = kwargs.pop('add_button', False)

        wx.Panel.__init__(self, *args, **kwargs)

        self.stream_panels = []
        self._on_destroy_callbacks = {}  # StreamPanel -> Callable()

        self._sz = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sz)

        msg = "No streams available."
        self.txt_no_stream = wx.StaticText(self, -1, msg)
        self._sz.Add(self.txt_no_stream, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        self.btn_add_stream = buttons.PopupImageButton(
            self, -1,
            label="ADD STREAM",
            style=wx.ALIGN_CENTER
        )
        self.btn_add_stream.SetForegroundColour(FG_COLOUR_BUTTON)
        self._sz.Add(self.btn_add_stream, flag=wx.ALL, border=10)
        self.btn_add_stream.Show(add_btn)

        self.btn_add_overview = buttons.PlusImageButton(
            self, -1,
            label="ADD OVERVIEW",
            style=wx.ALIGN_CENTER,
        )

        self.btn_add_overview.SetForegroundColour(FG_COLOUR_BUTTON)
        self._sz.Add(self.btn_add_overview, flag=wx.ALL, border=15)
        self.btn_add_overview.Show(False)

        self.fit_streams()

    def fit_streams(self):
        # When the whole window/app is destroyed, each widget receives a destroy
        # event. In such a case, it's not worthy re-fitting the streams, and
        # especially it can fail because some other objects have already been
        # destroyed.
        if not self or self.IsBeingDeleted():
            logging.debug("Stream panelbar is being deleted, not refitting")
            return

        logging.debug("Refitting stream panels")
        self._set_warning()

        h = self._sz.GetMinSize().GetHeight()
        self.SetSize((-1, h))

        p = self.Parent
        while not isinstance(p, FoldPanelItem):
            p = p.Parent

        self.Layout()
        p.Refresh()

    # TODO: maybe should be provided after init by the controller (like key of
    # sorted()), to separate the GUI from the model ?
    def _get_stream_order(self, stream):
        """ Gives the "order" of the given stream, as defined in STREAM_ORDER.

        Args:
            stream (Stream): a stream

        Returns:
            (int >= 0): the order

        """

        for i, c in enumerate(self.STREAM_ORDER):
            if isinstance(stream, c):
                return i

        msg = "Stream %s of unknown order type %s"
        logging.warning(msg, stream.name.value, stream.__class__.__name__)
        return len(self.STREAM_ORDER)

    # === VA handlers

    # Moved to stream controller

    # === Event Handlers

    def on_stream_remove(self, evt):
        """
        Called when user request to remove a stream via the stream panel
        """
        st = evt.spanel.stream
        logging.debug("User removed stream (panel) %s", st.name.value)
        # delete stream panel (which will "Destroy" it, which will trigger on_streamp_destroy())
        self.remove_stream_panel(evt.spanel)

    def on_streamp_destroy(self, evt: wx.Event):
        """
        Called when a stream panel is completely removed
        """
        spanel = evt.GetEventObject()
        on_destroy = self._on_destroy_callbacks.pop(spanel, None)
        if on_destroy:
            on_destroy()

        self.fit_streams()

    # === API of the stream panel
    def show_add_button(self):
        self.btn_add_stream.Show()
        self.fit_streams()

    def hide_add_button(self):
        self.btn_add_stream.Hide()
        self.fit_streams()

    def show_overview_button(self):
        self.btn_add_overview.Show()
        self.fit_streams()

    def hide_overview_button(self):
        self.btn_add_overview.Hide()
        self.fit_streams()

    def is_empty(self):
        return len(self.stream_panels) == 0

    def get_size(self):
        """ Return the number of streams contained within the StreamBar """
        return len(self.stream_panels)

    def add_stream_panel(self, spanel, show=True, on_destroy=None):
        """
        This method adds a stream panel to the stream bar. The appropriate
        position is automatically determined.
        spanel (StreamPanel): a stream panel
        show (bool): if True, immediately shows the stream panel, otherwise it
        will be hidden.
        on_destroy (Callable or None): function to call back when the stream
        panel is destroyed
        """
        # Insert the spanel in the order of STREAM_ORDER. If there are already
        # streams with the same type, insert after them.
        ins_pos = 0
        order_s = self._get_stream_order(spanel.stream)
        for e in self.stream_panels:
            order_e = self._get_stream_order(e.stream)
            if order_s < order_e:
                break
            ins_pos += 1

        logging.debug("Inserting %s at position %s", spanel.stream.__class__.__name__, ins_pos)

        self.stream_panels.insert(ins_pos, spanel)

        if on_destroy:
            self._on_destroy_callbacks[spanel] = on_destroy

        if self._sz is None:
            self._sz = wx.BoxSizer(wx.VERTICAL)
            self.SetSizer(self._sz)

        self._sz.Insert(ins_pos, spanel,
                              flag=self.DEFAULT_STYLE,
                              border=self.DEFAULT_BORDER)

        # TODO: instead of a stream_remove message, just take a callable to call
        # when the stream needs to be removed
        spanel.Bind(EVT_STREAM_REMOVE, self.on_stream_remove)
        spanel.Bind(wx.EVT_WINDOW_DESTROY, self.on_streamp_destroy, source=spanel)
        spanel.Layout()

        # hide the stream if requested
        spanel.Show(show)
        self.fit_streams()

    def remove_stream_panel(self, spanel):
        """
        Removes a stream panel
        Deletion of the actual stream must be done separately.
        Must be called in the main GUI thread
        """
        # Remove it from the sizer explicitly, because even if the sizer will
        # eventually detect it (via the destroy event), that will be later, and
        # until then the fit_stream will not be correct.
        self._sz.Detach(spanel)
        self.stream_panels.remove(spanel)
        spanel.Destroy()

    def clear(self):
        """
        Remove all stream panels
        Must be called in the main GUI thread
        """
        for p in list(self.stream_panels):
            # Prematurely stop listening to the Destroy event, to only refit the
            # (empty) bar once, after all streams are gone.
            p.Unbind(wx.EVT_WINDOW_DESTROY, source=p, handler=self.on_streamp_destroy)
            # So call the destroy handler explicitly. Needs to be done *before* it's actually destroyed.
            on_destroy = self._on_destroy_callbacks.pop(p, None)
            if on_destroy:
                on_destroy()

            self.remove_stream_panel(p)

        self.fit_streams()

    def _set_warning(self):
        """ Display a warning text when no streams are present, or show it
        otherwise.
        """
        self.txt_no_stream.Show(self.is_empty())
