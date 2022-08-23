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
import wx

from odemis.gui.util import AttrDict


class ViewportGrid(wx.Panel):
    """ Place multiple viewports on a grid and allow to swap or hide some of them """

    def __init__(self, *args, **kwargs):
        super(ViewportGrid, self).__init__(*args, **kwargs)

        self.SetBackgroundColour(wx.BLACK)

        # We need a separate attribute that contains all the child viewports, because the default
        # Children property in wx.Python does not allow for reordering.
        self.viewports = None

        self._visible_viewports = []
        self._invisible_viewports = []

        self.grid_layout = None
        # The size of the viewports when they are hidden
        self.hidden_size = (400, 400)
        self.Bind(wx.EVT_SIZE, self.on_size)

    @property
    def visible_viewports(self):
        """ Return all the viewports that are visible """
        return self._visible_viewports

    @visible_viewports.setter
    def visible_viewports(self, visible_viewports):
        self._visible_viewports = visible_viewports
        self._invisible_viewports = [vp for vp in self.viewports if vp not in visible_viewports]

    @property
    def invisible_viewports(self):
        """ Return all the viewports that are invisible """
        return self._invisible_viewports

    @invisible_viewports.setter
    def invisible_viewports(self, invisible_viewports):
        self._invisible_viewports = invisible_viewports
        self._visible_viewports = [vp for vp in self.viewports if vp not in invisible_viewports]

    def _show_hide_viewports(self):
        """ Call the Show and Hide method an the appropriate Viewports """

        for vp in self._visible_viewports:
            vp.Show()

        for vp in self._invisible_viewports:
            vp.Hide()

    # #### Viewport showing and hiding #### #

    def set_shown_viewports(self, *show_viewports):
        """ Show the given viewports and hide the rest """

        self.visible_viewports = show_viewports
        self._layout_viewports()
        self._show_hide_viewports()

    def set_hidden_viewports(self, *hide_viewports):
        """ Hide the given viewports and show the rest """

        self.invisible_viewports = hide_viewports
        self._layout_viewports()
        self._show_hide_viewports()

    def hide_all_viewports(self):
        """ Hide all viewports """
        self.set_shown_viewports()

    def get_4_viewports(self):
        """
        Gets the first 4 valid viewports to display in the 2 x 2 grid (ie,
          viewports connected to a view)
        return ([ViewPort])
        """
        viewports = []
        for v in self.viewports:
            try:
                if v.view is not None:
                    viewports.append(v)
            except AttributeError:
                # Should never happen, unless it's not really a ViewPort.
                # If so, let's not go completely bad
                logging.exception("View %s has no view", v)
                viewports.append(v)
            if len(viewports) >= 4:
                break

        return viewports

    def get_2_viewports(self):
        """
        Gets the first 2 valid viewports to display
        return ([ViewPort])
        """
        viewports = []

        if len(self.visible_viewports) == 2:
            logging.debug("Only two viewports are visible so these are shown.")
            return self.visible_viewports

        else:
            # If more/less than 2 viewports are visible use the first 2 viewports with a view
            for v in self.viewports:
                try:
                    if v.view is not None and v.Shown:
                        viewports.append(v)
                except AttributeError:
                    # Should never happen, unless it's not really a ViewPort.
                    # If so, let's not go completely bad
                    logging.exception("Viewport %s has no view", v)
                    viewports.append(v)
                if len(viewports) >= 2:
                    logging.warning("Found more than two viewports to show, only the first 2 are displayed in this layout.")
                    break

            return viewports

    def show_2_vert_stacked_viewports(self):
        """ Show the first two viewports in a 2x1 grid"""
        self.visible_viewports = self.get_2_viewports()
        self._layout_viewports()
        self._show_hide_viewports()

    def show_grid_viewports(self):
        """ Show all grid viewports """
        self.visible_viewports = self.get_4_viewports()
        self._layout_viewports()
        self._show_hide_viewports()

    def show_viewport(self, viewport):
        """ Show the given viewport """
        if viewport not in self.visible_viewports:
            self._visible_viewports.append(viewport)
            self._invisible_viewports.remove(viewport)

            self._layout_viewports()
            self._show_hide_viewports()

    def hide_viewport(self, viewport):
        """ Hide the given viewport """
        if viewport not in self.invisible_viewports:
            self._invisible_viewports.append(viewport)
            self._visible_viewports.remove(viewport)

            self._layout_viewports()
            self._show_hide_viewports()

    def set_enabled_viewports(self, enabled_viewports):
        """ Disable the given viewports, so they won't update """
        for viewport in self.viewports:
            viewport.Enable(viewport in enabled_viewports)

    def set_disabled_viewports(self, disable_viewports):
        """ Disable the given viewports, so they won't update """
        for viewport in self.viewports:
            viewport.Enable(viewport not in disable_viewports)

    # #### END Viewport showing and hiding #### #

    def on_size(self, _):
        """ Grab the child windows and perform layout when the size changes """
        if self.viewports is None:
            self.viewports = list(self.Children)
            if len(self.viewports) == 1 or len(self.viewports) == 3:
                # Cannot put uneven numbers on a grid => default to full screen for the 1st one.
                # For numbers > 4 only the first four are displayed.
                self.visible_viewports = self.viewports[:1]
            elif len(self.viewports) == 2:
                self.visible_viewports = self.get_2_viewports()
            else:
                self.visible_viewports = self.get_4_viewports()

        self.grid_layout = AttrDict({
            'tl': AttrDict({
                'pos': (0, 0),
                'size': wx.Size(self.ClientSize.x // 2,
                                self.ClientSize.y // 2)
            }),
            'tr': AttrDict({
                'pos': (self.ClientSize.x // 2, 0),
                'size': wx.Size(self.ClientSize.x - (self.ClientSize.x // 2),
                                self.ClientSize.y // 2)
            }),
            'bl': AttrDict({
                'pos': (0, self.ClientSize.y // 2),
                'size': wx.Size(self.ClientSize.x // 2,
                                self.ClientSize.y - (self.ClientSize.y // 2))
            }),
            'br': AttrDict({
                'pos': (self.ClientSize.x // 2, self.ClientSize.y // 2),
                'size': wx.Size(self.ClientSize.x - (self.ClientSize.x // 2),
                                self.ClientSize.y - (self.ClientSize.y // 2))
            }),
        })

        self.hidden_size = self.grid_layout.tl.size
        self._layout_viewports()

    def _layout_viewports(self):
        """ Resize, position and display the child viewports

        How the viewports are exactly layed out, depends on the their order and which ones are
        visible.

        The first 4 viewports are considered to be in the 2x2 grid. Their order corresponds to
        top left, top right, bottom left and bottom right.

        Number of visible viewports:

        0 - Set all sizes of the viewports to 400x400
        1 - Display the visible viewport 'full screen', completely covering it's parent. The other
            viewports are resized to their default hidden size of 400x400 pixels
        2 - Display the visible viewports in a 2*1 vertically stacked grid.
        X - If there is more than 2 visible viewports, we start looking at their positions,
            because we only consider the first 4 to be in the 2x2 view.

        Number of visible viewports in the first 4 positions:

        < X - Raise an error, since when multiple viewports are visible, they should all be located
              in the first four positions of the 2x2 grid
        >=X - The rule of thumb we use is, that we iterate over the visible viewports in order and
              they will expand into the space of any invisible neighbour.
              (That's the point of forcing the first 4 viewports to be 'special')

        """

        vvps = self.visible_viewports
        num_vis_total = len(vvps)

        # Everything hidden, no layout
        if num_vis_total == 0:
            # Set the size of the invisible viewport to a relative small value, so we make sure that
            # grabbing the client area for thumbnails will be relatively cheap
            for viewport in [vp for vp in self.viewports if vp.Size != self.hidden_size]:
                viewport.SetSize(self.hidden_size)
        # One shown, make the viewport match the size of the parent
        elif num_vis_total == 1:
            vvps[0].SetSize(self.ClientSize)
            vvps[0].SetPosition((0, 0))
            # TODO: Make invisible ones small!

        elif num_vis_total == 2:
            gvps = self.get_2_viewports()
            num_vis_grid = sum(vp in vvps for vp in gvps)
            if num_vis_grid != num_vis_total:
                raise ValueError("If viewports are set to be visible in a 2x1 grid, they should all reside within this "
                                 "2x1 grid! (%d shown, %d in grid)" % (num_vis_total, num_vis_grid))

            top, bottom = gvps
            top_view, bottom_view = [vp in vvps for vp in gvps]

            gl = self.grid_layout

           # Set the topview position and size
            if top_view:
                pos = gl.tl.pos
                size = (gl.tl.size.x + (gl.tr.size.x),
                        gl.tl.size.y)
                top.SetPosition(pos)
                top.SetSize(size)
            elif top.Size != self.hidden_size:
                top.SetSize(self.hidden_size)

            # Set the bottom view position and size
            if bottom_view:
                pos = (0, gl.bl.pos[1])
                size = (gl.bl.size.x + (gl.br.size.x),
                        gl.bl.size.y)

                bottom.SetPosition(pos)
                bottom.SetSize(size)
            elif bottom.Size != self.hidden_size:
                bottom.SetSize(self.hidden_size)

        else:
            gvps = self.get_4_viewports()
            num_vis_grid = sum(vp in vvps for vp in gvps)
            if num_vis_grid != num_vis_total:
                raise ValueError("If multiple viewports are visible, they should all reside in the "
                                 "2x2 grid! (%d shown, %d in grid)" % (num_vis_total, num_vis_grid))

            tl, tr, bl, br = gvps
            tlv, trv, blv, brv = [vp in vvps for vp in gvps]

            gl = self.grid_layout

            if tlv:
                pos = gl.tl.pos
                size = (gl.tl.size.x + (gl.tr.size.x if not trv else 0),
                        gl.tl.size.y + (gl.bl.size.y if not blv and not brv and trv else 0))

                tl.SetPosition(pos)
                tl.SetSize(size)
                logging.debug("Layout top left: %s, %s", pos, size)
            elif tl.Size != self.hidden_size:
                tl.SetSize(self.hidden_size)

            if trv:
                pos = (gl.tr.pos[0] - (gl.tr.size.x if not tlv else 0), 0)
                size = (gl.tr.size.x + (gl.tl.size.x if not tlv else 0),
                        gl.tr.size.y + (gl.br.size.y if not brv and not blv and tlv else 0))

                tr.SetPosition(pos)
                tr.SetSize(size)
                logging.debug("Layout top right: %s, %s", pos, size)
            elif tr.Size != self.hidden_size:
                tr.SetSize(self.hidden_size)

            if blv:
                pos = (0, gl.bl.pos[1] - (gl.tl.size.y if not tlv and not trv else 0))
                size = (gl.bl.size.x + (gl.br.size.x if not brv else 0),
                        gl.bl.size.y + (gl.tl.size.y if not tlv and not trv and brv else 0))

                bl.SetPosition(pos)
                bl.SetSize(size)
                logging.debug("Layout bottom left (%s): %s, %s", bl, pos, size)
            elif bl.Size != self.hidden_size:
                bl.SetSize(self.hidden_size)

            if brv:
                pos = (gl.br.pos[0] - (gl.bl.size.x if not blv else 0),
                       gl.br.pos[1] - (gl.tr.size.y if not trv and not tlv and blv else 0))
                size = (gl.br.size.x + (gl.bl.size.x if not blv else 0),
                        gl.br.size.y + (gl.tr.size.y if not trv and not tlv and blv else 0))

                br.SetPosition(pos)
                br.SetSize(size)
                logging.debug("Layout bottom right: %s, %s", pos, size)
            elif br.Size != self.hidden_size:
                br.SetSize(self.hidden_size)

    def _swap_viewports(self, vpa, vpb):
        """
        Switch the position of two viewports.
        """
        if vpa is vpb:
            return
        a, b = self.viewports.index(vpa), self.viewports.index(vpb)
        self.viewports[a], self.viewports[b] = self.viewports[b], self.viewports[a]

    def set_visible_viewports(self, vis_viewports):
        """ Set the viewports to be shown

        This method will move the viewports to be shown to the front of the viewport list (in
        order) and will show them while hiding all others.

        """

        self.visible_viewports = vis_viewports

        for i, vvp in enumerate(vis_viewports):
            if vvp not in self.viewports:
                raise ValueError("Unknown Viewport!")
            # Swap won't happen if the viewports are the same
            self._swap_viewports(vvp, self.viewports[i])

        self._layout_viewports()
        self._show_hide_viewports()

    def get_win_grid_pos(self, win):
        return self.viewports.index(win)
