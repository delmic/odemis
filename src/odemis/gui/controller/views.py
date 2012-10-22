# -*- coding: utf-8 -*-
'''
Created on 1 Oct 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.gui import instrmodel
from odemis.gui.log import log

class ViewController(object):
    """
    Manages the microscope view updates, change of viewport focus, etc.
    """

    def __init__(self, micgui, main_frame):
        '''
        micgui (MicroscopeGUI): the representation of the microscope GUI
        main_frame: (wx.Frame): the frame which contains the 4 viewports
        '''
        # This doesn't not need any stream to work. It's important as streams
        # will be created later on.

        self._microscope = micgui
        self._main_frame = main_frame

        # list of all the viewports (widgets that show the views)
        self._viewports = [main_frame.pnl_view_tl, main_frame.pnl_view_tr,
                           main_frame.pnl_view_bl, main_frame.pnl_view_br]

        # create the (default) views and set currentView
        self._createViews()

        # subscribe to layout and view changes
        self._microscope.viewLayout.subscribe(self._onViewLayout, init=True)
        self._microscope.currentView.subscribe(self._onView, init=False)

        # TODO when microscope get turned on (=state changes to on for the first time),
        # set the default visible streams to different values for each view
        # eg: if only SEM, and both BSD and SED => first 2 views have just one of them

        # Set the default focus
        main_frame.pnl_view_tr.SetFocus(True)
        self._microscope.currentView.value = self._viewports[1].getView()

    def _createViews(self):
        """
        Create the different views displayed, according to the current microscope.
        To be executed only once, at initialisation.
        """
        # If SEM only: all SEM
        if self._microscope.ebeam and not self._microscope.light:
            i = 1
            for v in self._viewports:
                view = instrmodel.MicroscopeView("SEM %d" % i,
                         self._microscope.stage,
                         focus0=None, # TODO: SEM focus or focus1?
                         stream_classes=(instrmodel.SEMStream,)
                         )
                v.setView(view, self._microscope)
                i += 1
            self._microscope.currentView.value = self._viewports[0].view
        # If Optical only: all Optical
        # TODO: first one is brightfield only?
        elif not self._microscope.ebeam and self._microscope.light:
            i = 1
            for v in self._viewports:
                view = instrmodel.MicroscopeView("Optical %d" % i,
                         self._microscope.stage,
                         focus0=self._microscope.focus,
                         stream_classes=(instrmodel.BrightfieldStream, instrmodel.FluoStream)
                         )
                v.setView(view, self._microscope)
                i += 1
            self._microscope.currentView.value = self._viewports[0].view
        # If both SEM and Optical: SEM/Optical/2x combined
        elif self._microscope.ebeam and self._microscope.light:
            view = instrmodel.MicroscopeView("SEM",
                     self._microscope.stage,
                     focus0=None, # TODO: SEM focus
                     stream_classes=(instrmodel.SEMStream, )
                     )
            self._viewports[0].setView(view, self._microscope)
            self._microscope.sem_view = view


            view = instrmodel.MicroscopeView("Optical",
                     self._microscope.stage,
                     focus0=self._microscope.focus,
                     stream_classes=(instrmodel.BrightfieldStream, instrmodel.FluoStream)
                     )
            self._viewports[1].setView(view, self._microscope)
            self._microscope.optical_view = view


            view = instrmodel.MicroscopeView("Combined 1",
                     self._microscope.stage,
                     focus0=self._microscope.focus,
                     focus1=None, # TODO: SEM focus
                     )
            self._viewports[2].setView(view, self._microscope)
            self._microscope.combo1_view = view


            view = instrmodel.MicroscopeView("Combined 2",
                     self._microscope.stage,
                     focus0=self._microscope.focus,
                     focus1=None, # TODO: SEM focus
                     )
            self._viewports[3].setView(view, self._microscope)
            self._microscope.combo2_view = view

            #self._microscope.currentView.value = self._viewports[1].view # starts with optical
        else:
            log.warning("No known microscope configuration, creating 4 generic views")
            i = 1
            for v in self._viewports:
                view = instrmodel.MicroscopeView("View %d" % i,
                         self._microscope.stage,
                         focus0=self._microscope.focus
                         )
                v.setView(view, self._microscope)
                i += 1
            self._microscope.currentView.value = self._viewports[0].view

        # TODO: if chamber camera: br is just chamber, and it's the currentView


    def _onView(self, view):
        """
        Called when another view is focused
        """
        log.debug("Changing focus to view %s", view.name.value)
        layout = self._microscope.viewLayout.value

        self._main_frame.pnl_tab_live.Freeze()
        for v in self._viewports:
            if v.view == view:
                v.SetFocus(True)
                if layout == instrmodel.VIEW_LAYOUT_ONE:
                    v.Show()
            else:
                v.SetFocus(False)
                if layout == instrmodel.VIEW_LAYOUT_ONE:
                    v.Hide()

        if layout == instrmodel.VIEW_LAYOUT_ONE:
            self._main_frame.pnl_tab_live.Layout()  # resize the viewport

        self._main_frame.pnl_tab_live.Thaw()

    def _onViewLayout(self, layout):
        """
        Called when the view layout of the GUI must be changed
        """
        # only called when changed
        self._main_frame.pnl_tab_live.Freeze()

        if layout == instrmodel.VIEW_LAYOUT_ONE:
            log.debug("Showing only one view")
            # TODO resize all the viewports now, so that there is no flickering
            # when just changing view
            for v in self._viewports:
                if v.view == self._microscope.currentView.value:
                    v.Show()
                else:
                    v.Hide()

        elif layout == instrmodel.VIEW_LAYOUT_22:
            log.debug("Showing all views")
            for v in self._viewports:
                v.Show()

        elif layout == instrmodel.VIEW_LAYOUT_FULLSCREEN:
            raise NotImplementedError()
        else:
            raise NotImplementedError()

        self._main_frame.pnl_tab_live.Layout()  # resize the viewports
        self._main_frame.pnl_tab_live.Thaw()
