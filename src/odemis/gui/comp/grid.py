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
from typing import Sequence

import wx

from odemis.gui.util import AttrDict


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

        self.grid_layout = None
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
        :param vis_viewports: the list of Viewports to display. Must be of length compatible with
        the grid (1, 2, or 4).
        """
        for vvp in vis_viewports:
            if vvp not in self.viewports:
                raise ValueError(f"Unknown Viewport ({vvp.view.name.value})!")

        if len(vis_viewports) not in (1, 2, 4):
            raise ValueError(f"Can only show 1, 2, or 4 viewports, but {len(vis_viewports)} requested")

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

    def _on_size(self, _):
        """ Grab the child windows and perform layout when the size changes """
        # Hack: we initialise the visible viewports based on the children which are connected to a view
        # Doing it at init wouldn't work because the viewports are not connected yet to their views,
        # and in some cases they are added as children yet. So we wait
        # for the first size update, which normally happens just after the whole GUI has been initialised.
        if self.viewports is None:
            self.viewports = tuple(self.Children)  # fixed for the rest of the runtime

            valid_viewports = list(self._iter_valid_viewports())
            # Pick the biggest number of valid viewports which can be displayed in a grid
            n_visible_vp = len(valid_viewports)
            n_visible_vp = max(n for n in (0, 1, 2, 4) if n <= n_visible_vp)
            logging.debug("Initializing grid to %d viewports", n_visible_vp)
            self.visible_viewports = valid_viewports[:n_visible_vp]

        cs_x, cs_y = self.ClientSize
        self.grid_layout = AttrDict({
            'tl': AttrDict({
                'pos': (0, 0),
                'size': wx.Size(cs_x // 2, cs_y // 2)
            }),
            'tr': AttrDict({
                'pos': (cs_x // 2, 0),
                'size': wx.Size(cs_x - (cs_x // 2),  # so that tl+tr sum precisely to the full client size
                                cs_y // 2)
            }),
            'bl': AttrDict({
                'pos': (0, cs_y // 2),
                'size': wx.Size(cs_x // 2, cs_y - (cs_y // 2))
            }),
            'br': AttrDict({
                'pos': (cs_x // 2, cs_y // 2),
                'size': wx.Size(cs_x - (cs_x // 2), cs_y - (cs_y // 2))
            }),
        })

        self.hidden_size = self.grid_layout.tl.size
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
        4 - Along the 2x2 view.  Their order corresponds to top left, top right, bottom left and bottom right.
        """
        vvps = self.visible_viewports
        num_vis_total = len(vvps)

        # Everything hidden, no layout
        if num_vis_total == 0:
            pass
        # One shown, make the viewport match the size of the parent
        elif num_vis_total == 1:
            vvps[0].SetSize(self.ClientSize)
            vvps[0].SetPosition((0, 0))
        elif num_vis_total == 2:
            gl = self.grid_layout
            top, bottom = vvps

            top.SetPosition(gl.tl.pos)
            top.SetSize((gl.tl.size.x + gl.tr.size.x, gl.tl.size.y))
            bottom.SetPosition((0, gl.bl.pos[1]))
            bottom.SetSize((gl.bl.size.x + gl.br.size.x, gl.bl.size.y))
        elif num_vis_total == 4:
            gl = self.grid_layout
            for vp, layout in zip(vvps, (gl.tl, gl.tr, gl.bl, gl.br)):
                vp.SetPosition(layout.pos)
                vp.SetSize(layout.size)

        else:
            raise ValueError(f"Doesn't know how to display {num_vis_total} viewports in a grid")

        # Set the size of the invisible viewport to a relative small value, so we make sure that
        # grabbing the client area for thumbnails will be relatively cheap
        for vp in self.viewports:
            if vp not in self.visible_viewports and vp.Size != self.hidden_size:
                vp.SetSize(self.hidden_size)

    def _show_hide_viewports(self):
        """ Show the visible viewports, and hide the other ones"""
        for vp in self.viewports:
            vp.Show(vp in self.visible_viewports)
