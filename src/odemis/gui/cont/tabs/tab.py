# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
import wx

import odemis.gui.model as guimod


class Tab(object):
    """ Small helper class representing a tab (tab button + panel) """

    def __init__(self, name, button, panel, main_frame, tab_data):
        """
        :type name: str
        :type button: odemis.gui.comp.buttons.TabButton
        :type panel: wx.Panel
        :type main_frame: odemis.gui.main_xrc.xrcfr_main
        :type tab_data: odemis.gui.model.LiveViewGUIData

        """
        logging.debug("Initialising tab %s", name)

        self.name = name
        self.button = button
        self.panel = panel
        self.main_frame = main_frame
        self.tab_data_model = tab_data
        self.highlighted = False
        self.focussed_viewport = None
        self.label = None

    def Show(self, show=True):
        self.button.SetToggle(show)
        if show:
            self._connect_22view_event()
            self._connect_interpolation_event()
            self._connect_crosshair_event()
            self._connect_pixelvalue_event()

            self.highlight(False)

        self.panel.Show(show)

    def _connect_22view_event(self):
        """ If the tab has a 2x2 view, this method will connect it to the 2x2
        view menu item (or ensure it's disabled).
        """
        if (guimod.VIEW_LAYOUT_22 in self.tab_data_model.viewLayout.choices and
            hasattr(self.tab_data_model, 'views') and
            len(self.tab_data_model.views.value) >= 4):
            def set_22_menu_check(viewlayout):
                """Called when the view layout changes"""
                is_22 = viewlayout == guimod.VIEW_LAYOUT_22
                self.main_frame.menu_item_22view.Check(is_22)

            def on_switch_22(evt):
                """Called when menu changes"""
                if self.tab_data_model.viewLayout.value == guimod.VIEW_LAYOUT_22:
                    self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_ONE
                else:
                    self.tab_data_model.viewLayout.value = guimod.VIEW_LAYOUT_22

            # Bind the function to the menu item, so it keeps the reference.
            # The VigilantAttribute will not unsubscribe it, until replaced.
            self.main_frame.menu_item_22view.vamethod = set_22_menu_check
            self.tab_data_model.viewLayout.subscribe(set_22_menu_check, init=True)
            # Assigning an event handler to the menu item, overrides
            # any previously assigned ones.
            self.main_frame.Bind(wx.EVT_MENU, on_switch_22, id=self.main_frame.menu_item_22view.GetId())
            self.main_frame.menu_item_22view.Enable()
        else:
            self.main_frame.menu_item_22view.Enable(False)
            self.main_frame.menu_item_22view.Check(False)
            self.main_frame.menu_item_22view.vamethod = None  # drop VA subscr.

    def _connect_interpolation_event(self):
        """ Connect the interpolation menu event to the focused view and its
        `interpolate_content` VA to the menu item
        """
        # only if there's a focussed view that we can track
        if hasattr(self.tab_data_model, 'focussedView'):

            def set_interpolation_check(fv):
                """Called when focused view changes"""
                if hasattr(fv, "interpolate_content"):
                    fv.interpolate_content.subscribe(self.main_frame.menu_item_interpolation.Check, init=True)
                    self.main_frame.menu_item_interpolation.Enable(True)
                else:
                    self.main_frame.menu_item_interpolation.Enable(False)
                    self.main_frame.menu_item_interpolation.Check(False)

            def on_switch_interpolation(evt):
                """Called when menu changes"""
                foccused_view = self.tab_data_model.focussedView.value
                # Extra check, which shouldn't be needed since if there's no
                # `interpolate_content`, this code should never be called.
                if hasattr(foccused_view, "interpolate_content"):
                    show = self.main_frame.menu_item_interpolation.IsChecked()
                    foccused_view.interpolate_content.value = show

            # Bind the function to the menu item, so it keeps the reference.
            # The VigilantAttribute will not unsubscribe it, until replaced.
            self.main_frame.menu_item_interpolation.vamethod = set_interpolation_check
            self.tab_data_model.focussedView.subscribe(set_interpolation_check, init=True)
            # Assigning an event handler to the menu item, overrides
            # any previously assigned ones.
            self.main_frame.Bind(wx.EVT_MENU, on_switch_interpolation, id=self.main_frame.menu_item_interpolation.GetId())
            self.main_frame.menu_item_interpolation.Enable()
        else:
            # If the right elements are not found, simply disable the menu item
            self.main_frame.menu_item_interpolation.Enable(False)
            self.main_frame.menu_item_interpolation.Check(False)
            self.main_frame.menu_item_interpolation.vamethod = None  # drop VA subscr.

    def _connect_crosshair_event(self):
        """ Connect the cross hair menu event to the focused view and its
        `show_crosshair` VA to the menu item
        """
        # only if there's a focussed view that we can track
        if hasattr(self.tab_data_model, 'focussedView'):

            def set_cross_check(fv):
                """Called when focused view changes"""
                if hasattr(fv, "show_crosshair"):
                    fv.show_crosshair.subscribe(self.main_frame.menu_item_cross.Check, init=True)
                    self.main_frame.menu_item_cross.Enable(True)
                else:
                    self.main_frame.menu_item_cross.Enable(False)
                    self.main_frame.menu_item_cross.Check(False)

            def on_switch_crosshair(evt):
                """Called when menu changes"""
                foccused_view = self.tab_data_model.focussedView.value
                # Extra check, which shouldn't be needed since if there's no
                # `show_crosshair`, this code should never be called.
                if hasattr(foccused_view, "show_crosshair"):
                    show = self.main_frame.menu_item_cross.IsChecked()
                    foccused_view.show_crosshair.value = show

            # Bind the function to the menu item, so it keeps the reference.
            # The VigilantAttribute will not unsubscribe it, until replaced.
            self.main_frame.menu_item_cross.vamethod = set_cross_check
            self.tab_data_model.focussedView.subscribe(set_cross_check, init=True)
            # Assigning an event handler to the menu item, overrides
            # any previously assigned ones.
            self.main_frame.Bind(wx.EVT_MENU, on_switch_crosshair, id=self.main_frame.menu_item_cross.GetId())
            self.main_frame.menu_item_cross.Enable()
        else:
            # If the right elements are not found, simply disable the menu item
            self.main_frame.menu_item_cross.Enable(False)
            self.main_frame.menu_item_cross.Check(False)
            self.main_frame.menu_item_cross.vamethod = None  # drop VA subscr.

    def _connect_pixelvalue_event(self):
        """ Connect the raw pixel value menu event to the focused view and its
        `show_pixelvalue` VA to the menu item
        """
        # only if there's a focussed view that we can track
        if hasattr(self.tab_data_model, 'focussedView'):

            def set_pixel_value_check(fv):
                """Called when focused view changes"""
                if hasattr(fv, "show_pixelvalue"):
                    fv.show_pixelvalue.subscribe(self.main_frame.menu_item_rawpixel.Check, init=True)
                    self.main_frame.menu_item_rawpixel.Enable(True)
                else:
                    self.main_frame.menu_item_rawpixel.Enable(False)
                    self.main_frame.menu_item_rawpixel.Check(False)

            def on_switch_pixel_value(evt):
                """Called when menu changes"""
                foccused_view = self.tab_data_model.focussedView.value
                # Extra check, which shouldn't be needed since if there's no
                # `show_pixelvalue`, this code should never be called.
                if hasattr(foccused_view, "show_pixelvalue"):
                    show = self.main_frame.menu_item_rawpixel.IsChecked()
                    foccused_view.show_pixelvalue.value = show

            # Bind the function to the menu item, so it keeps the reference.
            # The VigilantAttribute will not unsubscribe it, until replaced.
            self.main_frame.menu_item_rawpixel.vamethod = set_pixel_value_check
            self.tab_data_model.focussedView.subscribe(set_pixel_value_check, init=True)
            # Assigning an event handler to the menu item, overrides
            # any previously assigned ones.
            self.main_frame.Bind(wx.EVT_MENU, on_switch_pixel_value, id=self.main_frame.menu_item_rawpixel.GetId())
            self.main_frame.menu_item_rawpixel.Enable()
        else:
            # If the right elements are not found, simply disable the menu item
            self.main_frame.menu_item_rawpixel.Enable(False)
            self.main_frame.menu_item_rawpixel.Check(False)
            self.main_frame.menu_item_rawpixel.vamethod = None  # drop VA subscr.

    def Hide(self):
        self.Show(False)

    def IsShown(self):
        return self.panel.IsShown()

    def query_terminate(self):
        """
        Called to perform action prior to terminating the tab
        :return: (bool) True to proceed with termination, False for canceling
        """
        return True

    def terminate(self):
        """
        Called when the tab is not used any more
        """
        pass

    def set_label(self, label):
        """
        label (str): Text displayed at the tab selector
        """
        self.label = label
        self.button.SetLabel(label)

    def highlight(self, on=True):
        """ Put the tab in 'highlighted' mode to indicate a change has occurred """
        if self.highlighted != on:
            self.button.highlight(on)
            self.highlighted = on

    @classmethod
    def get_display_priority(cls, main_data):
        """
        Check whether the tab should be displayed for the current microscope
          configuration, and reports important it should be selected at init.
        main_data: odemis.gui.model.MainGUIData
        return (0<=int or None): the "priority", where bigger is more likely to
          be selected by default. None specifies the tab shouldn't be displayed.
        """
        raise NotImplementedError("Child must provide priority")
