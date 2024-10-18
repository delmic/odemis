# -*- coding: utf-8 -*-

"""
:author:    Rinze de Laat
:copyright: © 2014 Rinze de Laat, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""
import logging
import math
from collections import namedtuple
from typing import Dict, Sequence

import wx

from odemis.gui.comp.viewport import ViewPort

GridLayout = namedtuple("GridLayout", ["pos", "size"])


class ViewportGrid(wx.Panel):
    """ Place multiple viewports on a grid and allow to swap or hide some of them
    It has several sets and subsets of viewports:
    * viewports: all the children viewports of this grid
    * "valid viewports": subset of all the viewports, with only the one connected to a view
    * visible_viewports: all the viewports to be shown at a given moment (there should be only valid viewports)
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.SetBackgroundColour(wx.BLACK)

        self.viewports = None  # Tuple[ViewPort]
        self.visible_viewports = []  # The ViewPorts to be shown, the order matters

        # The size of the viewports when they are hidden
        self.hidden_size = (400, 400)
        self.Bind(wx.EVT_SIZE, self._on_size)

    # #### Viewport showing and hiding #### #

    def _iter_valid_viewports(self):
        """
        Iterates over every viewport which is connected to a view
        :yield: Viewport
        """
        for v in self.viewports:
            try:
                if v.view is not None:
                    yield v
            except AttributeError:
                # Should never happen, unless it's not really a ViewPort.
                # If so, let's not go completely bad
                logging.warning("Viewport %s has no view", v)
                pass

    def set_visible_viewports(self, vis_viewports: Sequence):
        """
        Set the viewports to be shown, in the order given
        (top-left, top-right, bottom-left, bottom-right).
        All other viewports are hidden.
        :param vis_viewports: the list of Viewports to display.
        """
        for vvp in vis_viewports:
            if vvp not in self.viewports:
                raise ValueError(f"Unknown Viewport ({vvp.view.name.value})!")

        self.visible_viewports = tuple(vis_viewports)
        logging.debug("Now showing %d viewports: %s", len(vis_viewports),
                      ", ".join(vvp.view.name.value for vvp in vis_viewports))

        self._layout_viewports()
        self._show_hide_viewports()

    def set_enabled_viewports(self, enabled_viewports):
        """ Enable the given viewports, so they update, and disable the other ones """
        for vp in self.viewports:
            vp.Enable(vp in enabled_viewports)

    # #### END Viewport showing and hiding #### #

    def get_visible_viewport_grid_layout(self) -> Dict[ViewPort, GridLayout]:
        """
        Calculate the grid layout based on visible viewports.

        :return: Dict where key is the visible viewport and value is its layout.
        """
        vvp_grid_layout = {}
        n_visible_vp = len(self.visible_viewports)

        if n_visible_vp:
            # Determine the grid size (rows and columns)
            if n_visible_vp == 1:
                rows, cols = 1, 1
            elif n_visible_vp == 2:
                # Set as top and botttom in previous code meaning 2 rows, 1 column
                rows, cols = 2, 1
            else:
                rows = int(math.sqrt(n_visible_vp))
                cols = (n_visible_vp + rows - 1) // rows  # Ensure that rows * cols >= n_visible_vp

            cs_x, cs_y = self.ClientSize
            viewport_width = cs_x // cols
            viewport_height = cs_y // rows

            for i, vvp in enumerate(self.visible_viewports):
                row = i // cols
                col = i % cols
                vvp_grid_layout[vvp] = GridLayout(
                    (col * viewport_width, row * viewport_height),
                    wx.Size(viewport_width, viewport_height)
                )
        return vvp_grid_layout

    def _on_size(self, _):
        """ Grab the child windows and perform layout when the size changes """
        # Hack: we initialise the visible viewports based on the children which are connected to a view
        # Doing it at init wouldn't work because the viewports are not connected yet to their views,
        # and in some cases they are added as children yet. So we wait
        # for the first size update, which normally happens just after the whole GUI has been initialised.
        if self.viewports is None:
            self.viewports = tuple(self.Children)  # fixed for the rest of the runtime

            valid_viewports = list(self._iter_valid_viewports())
            logging.debug("Initializing grid to %d viewports", len(valid_viewports))
            self.visible_viewports = valid_viewports

        self._layout_viewports()

    def _layout_viewports(self):
        """ Resize, position and display the child viewports

        How the viewports are exactly laid out, depends on their order and which ones are
        visible.

        Number of visible viewports:
        0 - Set all sizes of the viewports to 400x400
        1 - Display the visible viewport 'full screen', completely covering its parent. The other
            viewports are resized to their default hidden size of 400x400 pixels
        2 - Display the visible viewports in a 2*1 vertically stacked grid.
        > 2 - Display the visible viewports in an approximate n*n stacked grid.
        """
        vvp_grid_layout = self.get_visible_viewport_grid_layout()

        if vvp_grid_layout:
            self.hidden_size = vvp_grid_layout[self.visible_viewports[0]].size

        for vvp, layout in vvp_grid_layout.items():
            vvp.SetPosition(layout.pos)
            vvp.SetSize(layout.size)

        # Set the size of the invisible viewport to a relative small value, so we make sure that
        # grabbing the client area for thumbnails will be relatively cheap
        for vp in self.viewports:
            if vp not in self.visible_viewports and vp.Size != self.hidden_size:
                vp.SetSize(self.hidden_size)

    def _show_hide_viewports(self):
        """ Show the visible viewports, and hide the other ones"""
        for vp in self.viewports:
            vp.Show(vp in self.visible_viewports)
