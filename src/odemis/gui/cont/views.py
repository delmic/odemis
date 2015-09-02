# -*- coding: utf-8 -*-
"""
:created: 2012-10-01
:author: Rinze de Laat
:copyright: © 2012-2013 Rinze de Laat and Éric Piel, Delmic

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

import collections
import logging
from odemis.acq.stream import OpticalStream, EMStream, SpectrumStream, ARStream, \
    CLStream
from odemis.acq.stream import RGBCameraStream, BrightfieldStream, ARSettingsStream, \
    CLSettingsStream, MonochromatorSettingsStream
from odemis.gui import model
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.viewport import MicroscopeViewport, AngularResolvedViewport, PlotViewport, \
    SpatialSpectrumViewport
from odemis.gui.cont import tools
from odemis.gui.util import call_in_wx_main, img
from odemis.model import MD_PIXEL_SIZE
from odemis.model import VigilantAttributeBase
import wx


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
        :param toolbar: ToolBar or None-- toolbar to manage the TOOL_ZOOM_FIT tool.

        .. note::
            If a 2x2 viewport grid is present, the first four viewports in the _viewports attribute
            are expected to belong to this grid.

        """

        self._data_model = tab_data
        self._main_data_model = tab_data.main
        self.tab_panel = tab_panel
        self._toolbar = toolbar

        assert not self._data_model.views.value  # should still be empty

        self._viewports = viewports.keys()
        self._create_views_fixed(viewports)

        # First view is focused
        tab_data.focussedView.value = tab_data.visible_views.value[0]

        # subscribe to layout and view changes
        tab_data.visible_views.subscribe(self._on_visible_views)
        tab_data.viewLayout.subscribe(self._on_view_layout, init=True)
        tab_data.focussedView.subscribe(self._on_focussed_view, init=True)

        # TODO: just let the viewport do that?
        # Track the mpp of the SEM view in order to set the magnification
        ebeam = self._main_data_model.ebeam
        if (ebeam and isinstance(ebeam.horizontalFoV, VigilantAttributeBase)):
            # => Link the SEM FoV with the mpp of the live SEM viewport
            for vp in self.viewports:
                if vp.microscope_view.stream_classes == EMStream:  # For SEM only views
                    vp.track_view_hfw(ebeam.horizontalFoV)

    @property
    def viewports(self):
        return self._viewports

    def _create_views_fixed(self, viewports):
        """ Create the different views displayed, according to viewtypes
        viewports (OrderedDict (MicroscopeViewport -> kwargs)): cf init

        To be executed only once, at initialisation.
        """
        # FIXME: Since we have 2 different Views at the moments and probably more
        # on the way, it's probably going to be beneficial to explicitly define
        # them in the viewport data

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

    def _viewport_by_view(self, view):
        """ Return the ViewPort associated with the given view """

        for vp in self._viewports:
            if vp.microscope_view == view:
                return vp
        raise IndexError("No ViewPort found for view %s" % view)

    def views_to_viewports(self, views):
        """ Return a list of viewports corresponding to the given views, in the same order """
        viewports = []
        for view in views:
            for viewport in self._viewports:
                if viewport.microscope_view == view:
                    viewports.append(viewport)
                    break
        return viewports

    def _set_visible_views(self, visible_views):
        """ Set the order of the viewports so it will match the list of visible views

        This method should normally ben called when the visible_views VA in the MicroscopeGUIData
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

        grid_panel = self._viewports[0].Parent

        try:
            viewport = [vp for vp in self._viewports if vp.microscope_view == view][0]
        except IndexError:
            logging.exception("No associated ViewPort found for view %s", view)
            raise

        if self._data_model.viewLayout.value == model.VIEW_LAYOUT_ONE:
            grid_panel.set_shown_viewports(viewport)
            # Enable/disable ZOOM_FIT tool according to view ability
            if self._toolbar:
                can_fit = hasattr(viewport.canvas, "fit_view_to_content")
                self._toolbar.enable_button(tools.TOOL_ZOOM_FIT, can_fit)

        for vp in self._viewports:
            vp.SetFocus(False)
            vp.Refresh()
        viewport.SetFocus(True)
        viewport.Refresh()

    def _on_view_layout(self, layout):
        """ Called when the view layout of the GUI must be changed

        This method only manipulates ViewPort, since the only thing it needs to
        change is the visibility of ViewPorts.

        """

        grid_panel = self._viewports[0].Parent

        if layout == model.VIEW_LAYOUT_ONE:
            logging.debug("Displaying single viewport")
            for viewport in self._viewports:
                if viewport.microscope_view == self._data_model.focussedView.value:
                    grid_panel.set_shown_viewports(viewport)
                    break
            else:
                raise ValueError("No focussed view found!")

        elif layout == model.VIEW_LAYOUT_22:
            logging.debug("Displaying 2x2 viewport grid")
            if isinstance(grid_panel, ViewportGrid):
                grid_panel.show_grid_viewports()

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
            vp = self._viewport_by_view(self._data_model.focussedView.value)
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


class OverviewController(object):
    """ Small class to connect stage history and overview canvas together """

    def __init__(self, tab_data, overview_canvas):

        self._data_model = tab_data
        self.overview_canvas = overview_canvas

        if tab_data.main.stage:
            tab_data.main.stage.position.subscribe(self.on_stage_pos_change, init=True)

    @call_in_wx_main
    def on_stage_pos_change(self, p_pos):
        """ Store the new position in the overview history when the stage moves """

        p_size = self.calc_stream_size()
        p_center = (p_pos['x'], p_pos['y'])

        # If the 'new' position is identical to the last one in the history, ignore
        # TODO: do not care about the p_size, and override,
        if (
                self._data_model.stage_history.value and
                (p_center, p_size) == self._data_model.stage_history.value[-1]
        ):
            return

        # If max length reached, remove the oldest
        while len(self._data_model.stage_history.value) > 2000:
            logging.info("Discarding old stage position")
            self._data_model.stage_history.value.pop(0)

        self._data_model.stage_history.value.append((p_center, p_size))

    def calc_stream_size(self):
        """ Calculate the physical size of the current view """

        p_size = None

        # Calculate the stream size if the the ebeam is active
        for strm in self._data_model.streams.value:
            if strm.is_active and isinstance(strm, EMStream):
                image = strm.image.value
                if image is not None:
                    pixel_size = image.metadata.get(MD_PIXEL_SIZE, None)
                    if pixel_size is not None:
                        x, y, _ = image.shape
                        p_size = (x * pixel_size[0], y * pixel_size[1])

                        # TODO: tracking doesn't work, since the  pixel size
                        # might not be updated before `track_hfw_history` is
                        # called

                        # for view in self._tab_data_model.views.value:
                        #     if strm in view.stream_tree:
                        #         view.mpp.subscribe(self.track_hfw_history)
                        #         break

                        break
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

            vp.microscope_view.thumbnail.subscribe(on_thumbnail, init=True)
            # keep ref of the functions so that they are not dropped
            self._subscriptions[btn] = {"thumb": on_thumbnail}

            # also subscribe for updating the 2x2 button
            vp.microscope_view.thumbnail.subscribe(self._update_22_thumbnail, init=True)

            def on_name(name, label_ctrl=lbl_ctrl):  # save lbl in scope
                label_ctrl.SetLabel(name)

            btn.Freeze()
            vp.microscope_view.name.subscribe(on_name, init=True)
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
                vp.microscope_view.thumbnail.unsubscribe(subs["thumb"])
                vp.microscope_view.name.unsubscribe(subs["label"])

        self._subscriptions = {}

    def toggle_btn_for_view(self, microscope_view):
        """
        Toggle the button which represents the view and untoggle the other ones
        microscope_view (MicroscopeView or None): the view, or None if the first
                                    button (2x2) is to be toggled
        Note: it does _not_ change the view
        """
        for b, (vp, _) in self.buttons.items():
            # 2x2 => vp is None / 1 => vp exists and vp.view is the view
            if (
                    (vp is None and microscope_view is None) or
                    (vp and vp.microscope_view == microscope_view)
            ):
                b.SetToggle(True)
            else:
                if vp:
                    logging.debug("untoggling button of view %s",
                                  vp.microscope_view.name.value)
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
        im_22 = wx.EmptyImage(*size, clear=False)
        im_22.SetRGBRect(wx.Rect(0, 0, *size),
                         *btn_all.GetBackgroundColour().Get())

        i = 0

        for vp, _ in self.buttons.values():
            if vp is None:  # 2x2 layout
                continue

            im = vp.microscope_view.thumbnail.value

            if im:
                sim = img.wxImageScaleKeepRatio(im, size_sub, wx.IMAGE_QUALITY_HIGH)
            else:
                # Create an empty black image, if no image is set
                sim = wx.EmptyImage(*size_sub)

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
                if vp.microscope_view == view:
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
            self._data_model.focussedView.value = viewport.microscope_view
            self._data_model.viewLayout.value = model.VIEW_LAYOUT_ONE
