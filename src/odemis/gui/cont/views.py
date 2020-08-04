# -*- coding: utf-8 -*-
"""
:created: 2012-10-01
:author: Rinze de Laat
:copyright: © 2012-2015 Rinze de Laat and Éric Piel, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division

import logging
import numpy
import wx
import copy

from odemis.gui import model
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.evt import EVT_KNOB_PRESS
from odemis.gui.model import CHAMBER_PUMPING
from odemis.gui.util import call_in_wx_main, img
from odemis.util import limit_invocation
from odemis.model import MD_POS, MD_PIXEL_SIZE, DataArray, MD_DIMS, \
                         MD_AT_OVV_FULL, MD_AT_OVV_TILES, MD_AT_HISTORY
from odemis.gui.util.img import insert_tile_to_image, merge_screen
import odemis.acq.stream as acqstream


class ViewPortController(object):
    """ Manage the display of various viewports in a tab """

    def __init__(self, tab_data, tab_panel, viewports, toolbar=None):
        """
        :param tab_data: MicroscopyGUIData -- the representation of the microscope GUI
        :param tab_panel: wx.Frame -- the frame which contains the 4 viewports
        :param viewports (OrderedDict(MicroscopeViewport -> {}): the
            viewports to update. The first one is the one focused.
            The kwargs are passed to the MicroscopeView creation. A special kwarg "cls"
            can be used to use a specific class for the View (instead of MicroscopeView)
            If there are more than 4 viewports, only the first 4 will be made visible and any others
            will be hidden.
        :param toolbar: ToolBar or None-- toolbar to manage the TOOL_ACT_ZOOM_FIT tool.

        .. note::
            If a 2x2 viewport grid is present, the first four viewports in the _viewports attribute
            are expected to belong to this grid.

        """

        self._data_model = tab_data
        self._main_data_model = tab_data.main
        self.tab_panel = tab_panel
        self._toolbar = toolbar

        assert not self._data_model.views.value  # should still be empty

        self._viewports = list(viewports.keys())
        self._create_views_fixed(viewports)

        # First view is focused
        tab_data.focussedView.value = tab_data.visible_views.value[0]

        # subscribe to layout and view changes
        tab_data.visible_views.subscribe(self._on_visible_views)
        self._grid_panel = self._viewports[0].Parent
        if isinstance(self._grid_panel, ViewportGrid):
            tab_data.viewLayout.subscribe(self._on_view_layout, init=True)
            tab_data.focussedView.subscribe(self._on_focussed_view, init=True)
        elif len(self._viewports) != 1:
            self._grid_panel = None
            logging.info("Multiple viewports, but no ViewportGrid to manage them")

    @property
    def viewports(self):
        return self._viewports

    def _create_views_fixed(self, viewports):
        """ Create the different views displayed, according to viewtypes
        viewports (OrderedDict (MicroscopeViewport -> kwargs)): cf init

        To be executed only once, at initialisation.
        """
        views = []
        visible_views = []

        for vp, vkwargs in viewports.items():
            # TODO: automatically set some clever values for missing arguments?
            vcls = vkwargs.pop("cls", model.MicroscopeView)
            view = vcls(**vkwargs)

            views.append(view)
            if vp.Shown:
                visible_views.append(view)

            vp.setView(view, self._data_model)

        self._data_model.views.value = views
        self._data_model.visible_views.value = visible_views

    def get_viewport_by_view(self, view):
        """ Return the ViewPort associated with the given view """

        for vp in self._viewports:
            if vp.view == view:
                return vp
        raise IndexError("No ViewPort found for view %s" % view)

    def views_to_viewports(self, views):
        """ Return a list of viewports corresponding to the given views, in the same order """
        viewports = []
        for view in views:
            for viewport in self._viewports:
                if viewport.view == view:
                    viewports.append(viewport)
                    break
        return viewports

    def _set_visible_views(self, visible_views):
        """ Set the order of the viewports so it will match the list of visible views

        This method should normally be called when the visible_views VA in the MicroscopeGUIData
        object gets changed.

        """

        msg = "Resetting views to %s"
        msgdata = [str(v) for v in visible_views] if visible_views is not None else "default"
        logging.debug(msg, msgdata)

        parent = self._viewports[0].Parent

        parent.Freeze()

        try:
            visible_viewports = self.views_to_viewports(visible_views)

            if isinstance(parent, ViewportGrid):
                parent.set_visible_viewports(visible_viewports)
                parent.set_enabled_viewports(visible_viewports)

        finally:
            wx.CallAfter(parent.Thaw)

    def _on_visible_views(self, visible_views):
        """ This method is called when the visible views in the data model change """

        logging.debug("Visible view change detected")
        # Test if all provided views are known
        for view in visible_views:
            if view not in self._data_model.views.value:
                raise ValueError("Unknown view %s!" % view)

        self._set_visible_views(visible_views)

        # Ensure the focused view is always one that is visible
        if self._data_model.focussedView.value not in visible_views:
            self._data_model.focussedView.value = visible_views[0]

    def _on_focussed_view(self, view):
        """ Called when another focussed view changes.

        :param view: (MicroscopeView) The newly focussed view

        """

        logging.debug("Changing focus to view %s", view.name.value)

        for vp in self._viewports:
            if vp.view is view:
                viewport = vp
                vp.canvas.Bind(EVT_KNOB_PRESS, self._on_knob_press)
                break
        else:
            raise ValueError("No associated ViewPort found for view %s" % (view,))

        if self._data_model.viewLayout.value == model.VIEW_LAYOUT_ONE:
            self._grid_panel.set_shown_viewports(viewport)
            # Enable/disable ZOOM_FIT tool according to view ability
            if self._toolbar:
                can_fit = hasattr(viewport.canvas, "fit_view_to_content")
                self._toolbar.enable_button(model.TOOL_ACT_ZOOM_FIT, can_fit)

        for vp in self._viewports:
            vp.SetFocus(False)
            vp.Refresh()
        viewport.SetFocus(True)
        viewport.Refresh()

    def _on_knob_press(self, _):
        """ Advance the focus to the next grid Viewport, if any """

        if self._grid_panel is None:
            return

        fv = self._data_model.focussedView.value
        grid_vis = self._grid_panel.visible_viewports

        for i, vp in enumerate(grid_vis):
            if vp.view == fv and vp in grid_vis:
                try:
                    self._data_model.focussedView.value = grid_vis[i + 1].view
                except IndexError:
                    self._data_model.focussedView.value = grid_vis[0].view

    def _on_view_layout(self, layout):
        """ Called when the view layout of the GUI must be changed

        This method only manipulates ViewPort, since the only thing it needs to
        change is the visibility of ViewPorts.

        """

        if layout == model.VIEW_LAYOUT_ONE:
            logging.debug("Displaying single viewport")
            for viewport in self._viewports:
                if viewport.view == self._data_model.focussedView.value:
                    self._grid_panel.set_shown_viewports(viewport)
                    break
            else:
                raise ValueError("No focussed view found!")

        elif layout == model.VIEW_LAYOUT_22:
            logging.debug("Displaying 2x2 viewport grid")
            self._grid_panel.show_grid_viewports()

        elif layout == model.VIEW_LAYOUT_FULLSCREEN:
            raise NotImplementedError()
        else:
            raise NotImplementedError()

    def fitViewToContent(self, unused=None):
        """
        Adapts the scale (MPP) of the current view to the content
        """
        # find the viewport corresponding to the current view
        try:
            vp = self.get_viewport_by_view(self._data_model.focussedView.value)
            vp.canvas.fit_view_to_content()
        except IndexError:
            logging.error("Failed to find the current viewport")
        except AttributeError:
            # TODO: The toolbar button/menu should be disabled if the current
            # view doesn't support "fit_view_to_content"
            logging.info("Requested to fit content for a view not able to")

    def focusViewWithStream(self, stream):
        """
        Ensures that the focussed view is one that displays the given stream.
        If the focussed view fits, it will be picked preferably.
        Note: if the stream is not in any view, nothing will happen.
        stream (Stream): the stream to look for
        """
        fv = self._data_model.focussedView.value

        # first try to pick a view which has the stream visible
        pviews = []
        for v in self._data_model.visible_views.value:
            if stream in v.getStreams():
                pviews.append(v)

        if fv in pviews:
            return  # nothing to do
        if pviews:
            self._data_model.focussedView.value = pviews[0]
            return

        # Try to pick a view which is compatible with the stream
        pviews = []
        for v in self._data_model.visible_views.value:
            if isinstance(stream, v.stream_classes):
                pviews.append(v)

        if fv in pviews:
            return  # nothing to do
        if pviews:
            self._data_model.focussedView.value = pviews[0]

        logging.debug("Failed to find any view compatible with stream %s", stream.name.value)


OVV_SHAPE = (1200, 1200, 3)  # px
MAX_OVV_SIZE = 0.05  # m


class OverviewController(object):
    """ Class to connect stage history and overview canvas together and to control the overview image  """

    def __init__(self, main_data, tab_data, overview_canvas, m_view):
        self._data_model = tab_data
        self.canvas = overview_canvas
        self.m_view = m_view
        self.main_data = main_data

        self.curr_s = None

        # Timer to detect when the stage ends moving
        self._timer_pos = wx.PyTimer(self.add_pos_to_history)

        if hasattr(m_view, "merge_ratio"):
            m_view.merge_ratio.subscribe(self._on_merge_ratio_change)

        if tab_data.main.stage:
            tab_data.main.stage.position.subscribe(self.on_stage_pos_change, init=True)
            tab_data.main.chamberState.subscribe(self._on_chamber_state)
            tab_data.streams.subscribe(self._on_current_stream)

        # Global overview image (Delphi)
        if main_data.overview_ccd:
            # Overview camera can be RGB => in that case len(shape) == 4
            if len(main_data.overview_ccd.shape) == 4:
                overview_stream = acqstream.RGBCameraStream("Overview", main_data.overview_ccd,
                                                            main_data.overview_ccd.data, None,
                                                            acq_type=MD_AT_OVV_FULL)
            else:
                overview_stream = acqstream.BrightfieldStream("Overview", main_data.overview_ccd,
                                                              main_data.overview_ccd.data, None,
                                                              acq_type=MD_AT_OVV_FULL)
            self.m_view.addStream(overview_stream)
            # TODO: add it to self.tab_data_model.streams?
        else:
            # black image to display history overlay separately from built-up ovv image
            # controlled by merge slider
            da, _ = self._initialize_ovv_im(OVV_SHAPE)
            history_stream = acqstream.RGBUpdatableStream("History Stream", da, acq_type=MD_AT_HISTORY)
            self.m_view.addStream(history_stream)

        # Built-up overview image
        self.ovv_im, self.m_view.mpp.value = self._initialize_ovv_im(OVV_SHAPE)

        # Initialize individual ovv images for optical and sem stream
        self.im_opt = copy.deepcopy(self.ovv_im)
        self.im_sem = copy.deepcopy(self.ovv_im)

        # Add stream to view
        self.upd_stream = acqstream.RGBUpdatableStream("Overview Stream", self.ovv_im,
                                                       acq_type=MD_AT_OVV_TILES)
        self.m_view.addStream(self.upd_stream)

    def _initialize_ovv_im(self, shape):
        """
        Initialize an overview image, i.e. a black DataArray with corresponding
        metadata. 
        shape: XYC tuple 
        returns: DataArray of shape XYC, mpp value 
        """
        # Initialize the size of the ovv image with the stage size if the stage is small (< 5cm),
        # otherwise fall back to OVV_SHAPE
        ax_x = self.main_data.stage.axes["x"]
        ax_y = self.main_data.stage.axes["y"]
        mpp = max(MAX_OVV_SIZE / shape[0], MAX_OVV_SIZE / shape[1])
        if hasattr(ax_x, "range") and hasattr(ax_y, "range"):
            max_x = ax_x.range[1] - ax_x.range[0]
            max_y = ax_y.range[1] - ax_y.range[0]
            if max_x < MAX_OVV_SIZE and max_y < MAX_OVV_SIZE:
                mpp = max(max_x / shape[0], max_y / shape[1])

        ovv_im = DataArray(numpy.zeros(shape, dtype=numpy.uint8))
        ovv_im.metadata[MD_DIMS] = "YXC"
        ovv_im.metadata[MD_PIXEL_SIZE] = (mpp, mpp)
        ovv_im.metadata[MD_POS] = self.m_view.view_pos.value
        return ovv_im, mpp

    def reset_ovv(self):
        """
        Reset the overview image and history after a new sample has been loaded
        """
        self.ovv_im[:] = 0
        self.im_opt[:] = 0
        self.im_sem[:] = 0

        # Empty the stage history, as the interesting locations on the previous
        # sample have probably nothing in common with this new sample
        self._data_model.stage_history.value = self._data_model.stage_history.value[-1:]

        self.upd_stream.update(self.ovv_im)
        self.canvas.fit_view_to_content()

    def _on_merge_ratio_change(self, ratio):
        self.canvas.history_overlay.set_merge_ratio(ratio)

    def on_stage_pos_change(self, pos):
        """ Store the new position in the overview history when the stage moves,
        update the overview image """

        # If the stage hasn't moved within the next 0.5 s, we will considered it's
        # stopped, and so will update the position. Without doing so, every stage position
        # would be drawn, resulting in a very cluttered view.
        # wx.CallLater can only be used from main thread, therefore we need CallAfter
        # to get the same functionality. The timer is reset every time the function is
        # called (_timer_pos.Stop), so that we always wait for the correct number
        # of milliseconds.
        wx.CallAfter(self._timer_pos.Stop)
        wx.CallAfter(self._timer_pos.Start, milliseconds=500, oneShot=True)

    def add_pos_to_history(self):
        """ Add position to history and draw corresponding rectangle. """
        p_pos = self.main_data.stage.position.value
        p_size = self.calc_stream_size()
        p_center = (p_pos['x'], p_pos['y'])
        stage_history = self._data_model.stage_history.value

        # If the new position is at the same place as the latest one, replace it
        if stage_history and p_center == stage_history[-1][0]:
            stage_history.pop()

        # If max length reached, remove the oldest
        while len(stage_history) > 2000:
            logging.info("Discarding old stage position")
            stage_history.pop(0)

        stage_history.append((p_center, p_size))
        self._data_model.stage_history.value = stage_history

    def _on_current_stream(self, streams):
        """
        Called when some VAs affecting the current stream change
        """
        # Unsubscribe from previous stream
        if self.curr_s:
            self.curr_s.image.unsubscribe(self._onNewImage)

        # Try to get the current stream
        try:
            self.curr_s = streams[0]
        except IndexError:
            self.curr_s = None

        if self.curr_s:
            self.curr_s.image.subscribe(self._onNewImage)

    @limit_invocation(1)  # max 1 Hz
    def _onNewImage(self, _):
        # update overview whenever the streams change, limited to a frequency of 1 Hz
        self._update_ovv()

    def _on_chamber_state(self, state):
        # We don't wait for CHAMBER_VACUUM, as the optical stream can already
        # be used as soon as the sample is inserted
        if state == CHAMBER_PUMPING:
            # Reset the built-up overview image and history overlay after loading a new sample.
            self.reset_ovv()

    def _update_ovv(self):
        """ Update the overview image with the currently active stream. """
        if self.curr_s and self.curr_s.image.value is not None:
            s = self.curr_s
            img = s.image.value
            if isinstance(s, acqstream.OpticalStream):
                self.im_opt = insert_tile_to_image(img, self.im_opt)
            elif isinstance(s, acqstream.EMStream):
                self.im_sem = insert_tile_to_image(img, self.im_sem)
            else:
                logging.info("%s not added to overview image as it's not optical nor EM", s)

            # Merge optical and sem overview images
            self.ovv_im = merge_screen(self.im_opt, self.im_sem)

            # Update display
            self.upd_stream.update(self.ovv_im)
            self.canvas.fit_view_to_content()

    def calc_stream_size(self):
        """ Calculate the physical size of the current view """

        p_size = None
        # Calculate the stream size (by using the latest stream used)
        for strm in self._data_model.streams.value:
            image = strm.image.value
            if image is not None:
                try:
                    pixel_size = image.metadata[MD_PIXEL_SIZE]
                    x, y, _ = image.shape
                    p_size = (x * pixel_size[0], y * pixel_size[1])
                    break
                except KeyError:
                    pass
        if p_size is None:
            if self.main_data.ebeam:
                # fallback to using the SEM FoV (if no stream has any image)
                p_size = (self.main_data.ebeam.shape[0] * self.main_data.ebeam.pixelSize.value[0],
                          self.main_data.ebeam.shape[1] * self.main_data.ebeam.pixelSize.value[1])
            else:
                p_size = (5, 5)
        return p_size


class ViewButtonController(object):
    """ This class controls the view selector buttons and labels associated with them. """

    def __init__(self, tab_data, tab_panel, buttons, viewports):
        """

        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the 4 viewports
        buttons (OrderedDict : btn -> label): View buttons and their associated labels

        *important*: The first button has no viewport, for the 2x2 view.

        """

        self._data_model = tab_data
        self.tab_panel = tab_panel

        self.buttons = buttons  # Remember, this is an ordered dictionary!
        self.viewports = viewports

        for btn in self.buttons:
            btn.Bind(wx.EVT_BUTTON, self.on_btn_click)

        self._subscriptions = {}  # btn -> dict(str -> subscriber)
        self._subscribe()

        # subscribe to layout and view changes
        self._data_model.viewLayout.subscribe(self._on_layout_change)
        self._data_model.visible_views.subscribe(self._on_visible_views_change)
        self._data_model.focussedView.subscribe(self._on_focus_change, init=True)

    def _subscribe(self):
        """
        Subscribe to change of thumbnail & name
        """
        for btn, (vp, lbl_ctrl) in self.buttons.items():
            if vp is None:  # 2x2 layout
                lbl_ctrl.SetLabel("All")
                continue

            @call_in_wx_main
            def on_thumbnail(im, b=btn):  # save btn in scope
                # import traceback
                # traceback.print_stack()
                b.set_overlay_image(im)

            vp.view.thumbnail.subscribe(on_thumbnail, init=True)
            # keep ref of the functions so that they are not dropped
            self._subscriptions[btn] = {"thumb": on_thumbnail}

            # also subscribe for updating the 2x2 button
            vp.view.thumbnail.subscribe(self._update_22_thumbnail, init=True)

            def on_name(name, label_ctrl=lbl_ctrl):  # save lbl in scope
                label_ctrl.SetLabel(name)

            btn.Freeze()
            vp.view.name.subscribe(on_name, init=True)
            btn.Parent.Layout()
            btn.Thaw()

            self._subscriptions[btn]["label"] = on_name

    def _unsubscribe(self):
        """
        Unsubscribe from the thumbnail and name VAs for all the buttons
        """
        # Explicitly unsubscribe the current event handlers
        for btn, subs in self._subscriptions.items():
            vp, lbl = self.buttons[btn]
            if vp is not None:
                vp.view.thumbnail.unsubscribe(subs["thumb"])
                vp.view.name.unsubscribe(subs["label"])

        self._subscriptions = {}

    def toggle_btn_for_view(self, view):
        """
        Toggle the button which represents the view and untoggle the other ones
        view (MicroscopeView or None): the view, or None if the first
                                    button (2x2) is to be toggled
        Note: it does _not_ change the view
        """
        for b, (vp, _) in self.buttons.items():
            # 2x2 => vp is None / 1 => vp exists and vp.view is the view
            if (
                    (vp is None and view is None) or
                    (vp and vp.view == view)
            ):
                b.SetToggle(True)
            else:
                if vp:
                    logging.debug("untoggling button of view %s",
                                  vp.view.name.value)
                else:
                    logging.debug("untoggling button of view All")
                b.SetToggle(False)

    @call_in_wx_main
    def _update_22_thumbnail(self, _):
        """ Called when any thumbnail is changed, to recompute the 2x2 thumbnail of the first button

        :param _: (Image) Unused

        """

        # Create an image from the 4 thumbnails in a 2x2 layout with small
        # border. The button without a viewport attached is assumed to be the
        # one assigned to the 2x2 view
        btn_all = [b for b, (vp, _) in self.buttons.items() if vp is None][0]
        border_width = 2  # px
        size = max(1, btn_all.thumbnail_size.x), max(1, btn_all.thumbnail_size.y)
        size_sub = (max(1, (size[0] - border_width) // 2),
                    max(1, (size[1] - border_width) // 2))
        # starts with an empty image with the border colour everywhere
        im_22 = wx.Image(*size, clear=False)
        im_22.SetRGB(wx.Rect(0, 0, *size),
                     *(btn_all.GetBackgroundColour().Get(includeAlpha=False)))

        i = 0

        for vp, _ in self.buttons.values():
            if vp is None:  # 2x2 layout
                continue

            im = vp.view.thumbnail.value

            if im:
                sim = img.wxImageScaleKeepRatio(im, size_sub, wx.IMAGE_QUALITY_HIGH)
            else:
                # Create an empty black image, if no image is set
                sim = wx.Image(*size_sub)

            # compute placement
            y, x = divmod(i, 2)
            # copy im in the right place
            im_22.Paste(sim,
                        x * (size_sub[0] + border_width),
                        y * (size_sub[1] + border_width))

            i += 1

        # set_overlay_image will rescale to the correct button size
        btn_all.set_overlay_image(im_22)

    def _on_visible_views_change(self, visible_views):
        """ Associate each button with the correct visible viewport """

        if not self.viewports:
            logging.warn("Could not handle view change, viewports unknown!")
            return

        self._unsubscribe()

        # update viewport of each button
        vis_viewports = []
        for view in visible_views:
            for vp in self.viewports:
                if vp.view == view:
                    vis_viewports.append(vp)

        vp_buttons = [(b, (vp, l)) for b, (vp, l) in self.buttons.items() if vp is not None]
        for (btn, (btn_vp, btn_lbl)), vis_vp in zip(vp_buttons, vis_viewports):
            self.buttons[btn] = (vis_vp, btn_lbl)

        self._subscribe()

    def _on_layout_change(self, _):
        """ Called when another view is focused, or viewlayout is changed """
        logging.debug("Updating view selector")

        # TODO when changing from 2x2 to a view non focused, it will be called
        # twice in row. => optimise to not do it twice

        if self._data_model.viewLayout.value == model.VIEW_LAYOUT_22:
            # (layout is 2x2) => select the first button
            self.toggle_btn_for_view(None)
        else:
            # otherwise (layout is 1) => select the right button
            self.toggle_btn_for_view(self._data_model.focussedView.value)

    _on_focus_change = _on_layout_change

    def on_btn_click(self, evt):
        """
        Navigation button click event handler

        Show the related view(s) and sets the focus if needed.
        """

        # The event does not need to be 'skipped' because
        # the button will be toggled when the event for value change is
        # received.

        btn = evt.GetEventObject()
        viewport = self.buttons[btn][0]

        if viewport is None:
            # 2x2 button
            # When selecting the overview, the focused viewport should not
            # change
            self._data_model.viewLayout.value = model.VIEW_LAYOUT_22
        else:
            # It's preferable to change the view before the layout so that
            # if the layout was 2x2 with another view focused, it doesn't first
            # display one big view, and immediately after changes to another
            # view.
            self._data_model.focussedView.value = viewport.view
            self._data_model.viewLayout.value = model.VIEW_LAYOUT_ONE
