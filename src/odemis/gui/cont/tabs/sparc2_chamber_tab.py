# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Stefan Sneep

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

import collections
from concurrent.futures import CancelledError
import logging
import math
import wx
# IMPORTANT: wx.html needs to be imported for the HTMLWindow defined in the XRC
# file to be correctly identified. See: http://trac.wxwidgets.org/ticket/3626
# This is not related to any particular wxPython version and is most likely permanent.
import wx.html

from odemis import model
import odemis.acq.stream as acqstream
import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont.tabs import MIRROR_BAD, MIRROR_ENGAGED, MIRROR_NOT_REFD, \
    MIRROR_ONPOS_RADIUS, MIRROR_PARKED, MIRROR_POS_PARKED
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main


class ChamberTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        """ SPARC2 chamber view tab """

        tab_data = guimod.ChamberGUIData(main_data)
        super(ChamberTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("CHAMBER")

        # future to handle the move
        self._move_future = model.InstantaneousFuture()
        # list of moves that still need to be executed, FIFO
        self._next_moves = collections.deque()  # tuple callable, arg

        # Position to where to go when requested to be engaged
        try:
            self._pos_engaged = main_data.mirror.getMetadata()[model.MD_FAV_POS_ACTIVE]
        except KeyError:
            raise ValueError("Mirror actuator has no metadata FAV_POS_ACTIVE")

        self._update_mirror_status()

        # Create stream & view
        self._stream_controller = streamcont.StreamBarController(
            tab_data,
            panel.pnl_streams,
            locked=True
        )

        # create a view on the microscope model
        vpv = collections.OrderedDict((
            (self.panel.vp_chamber,
                {
                    "name": "Chamber view",
                }
            ),
        ))
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)
        view = self.tab_data_model.focussedView.value
        view.interpolate_content.value = False
        view.show_crosshair.value = False
        view.show_pixelvalue.value = False

        # With the lens, the image must be flipped to keep the mirror at the top and the sample
        # at the bottom.
        self.panel.vp_chamber.SetFlip(wx.VERTICAL)

        if main_data.ccd:
            # Just one stream: chamber view
            if main_data.focus and main_data.ccd.name in main_data.focus.affects.value:
                ccd_focuser = main_data.focus
            else:
                ccd_focuser = None
            self._ccd_stream = acqstream.CameraStream("Chamber view",
                                      main_data.ccd, main_data.ccd.data,
                                      emitter=None,
                                      focuser=ccd_focuser,
                                      detvas=get_local_vas(main_data.ccd, main_data.hw_settings_config),
                                      forcemd={model.MD_POS: (0, 0),  # Just in case the stage is there
                                               model.MD_ROTATION: 0}  # Force the CCD as-is
                                      )
            # Make sure image has square pixels and full FoV
            if hasattr(self._ccd_stream, "detBinning"):
                self._ccd_stream.detBinning.value = (1, 1)
            if hasattr(self._ccd_stream, "detResolution"):
                self._ccd_stream.detResolution.value = self._ccd_stream.detResolution.range[1]
            ccd_spe = self._stream_controller.addStream(self._ccd_stream)
            ccd_spe.stream_panel.flatten()  # No need for the stream name
            self._ccd_stream.should_update.value = True
        else:
            # For some very limited SPARCs
            logging.info("No CCD found, so chamber view will have no stream")

        panel.btn_switch_mirror.Bind(wx.EVT_BUTTON, self._on_switch_btn)
        panel.btn_cancel.Bind(wx.EVT_BUTTON, self._on_cancel)

        # Show position of the stage via the progress bar
        # Note: we could have used the progress bar to represent the progress of
        # the move. However, it's a lot of work because the future is not a
        # progressive future, so the progress would need to be guessed based on
        # time or position. In addition, it would not give any cue to the user
        # about the current position of the mirror when no move is happening.
        main_data.mirror.position.subscribe(self._update_progress_bar, init=True)
        self._pulse_timer = wx.PyTimer(self._pulse_progress_bar)  # only used during referencing

    @classmethod
    def _get_mirror_state(cls, mirror):
        """
        Return the state of the mirror stage (in term of position)
        Note: need self._pos_engaged
        return (MIRROR_*)
        """
        if not all(mirror.referenced.value.values()):
            return MIRROR_NOT_REFD

        pos = mirror.position.value
        dist_parked = math.hypot(pos["l"] - MIRROR_POS_PARKED["l"],
                                 pos["s"] - MIRROR_POS_PARKED["s"])
        if dist_parked <= MIRROR_ONPOS_RADIUS:
            return MIRROR_PARKED

        try:
            pos_engaged = mirror.getMetadata()[model.MD_FAV_POS_ACTIVE]
        except KeyError:
            return MIRROR_BAD
        dist_engaged = math.hypot(pos["l"] - pos_engaged["l"],
                                  pos["s"] - pos_engaged["s"])
        if dist_engaged <= MIRROR_ONPOS_RADIUS:
            return MIRROR_ENGAGED
        else:
            return MIRROR_BAD

    @call_in_wx_main
    def _update_progress_bar(self, pos):
        """
        Update the progress bar, based on the current position/state of the mirror.
        Called when the position of the mirror changes.
        pos (dict str->float): current position of the mirror stage
        """
        # If not yet referenced, the position is pretty much meaning less
        # => just say it's "somewhere in the middle".
        mirror = self.tab_data_model.main.mirror
        if not all(mirror.referenced.value.values()):
            # In case it pulses, wxGauge still holds the old value, so it might
            # think it doesn't need to refresh unless the value changes.
            self.panel.gauge_move.Value = 0
            self.panel.gauge_move.Value = 50  # Range is 100 => 50%
            return

        # We map the position between Parked -> Engaged. The basic is simple,
        # we could just map the current position on the segment. But we also
        # want to show a position "somewhere in the middle" when the mirror is
        # at a random bad position.
        dist_parked = math.hypot(pos["l"] - MIRROR_POS_PARKED["l"],
                                 pos["s"] - MIRROR_POS_PARKED["s"])
        dist_engaged = math.hypot(pos["l"] - self._pos_engaged["l"],
                                  pos["s"] - self._pos_engaged["s"])
        tot_dist = math.hypot(MIRROR_POS_PARKED["l"] - self._pos_engaged["l"],
                              MIRROR_POS_PARKED["s"] - self._pos_engaged["s"])
        ratio_to_engaged = dist_engaged / tot_dist
        ratio_to_parked = dist_parked / tot_dist
        if ratio_to_parked < ratio_to_engaged:
            val = ratio_to_parked * 100
        else:
            val = (1 - ratio_to_engaged) * 100

        val = min(max(0, int(round(val))), 100)
        logging.debug("dist (%f/%f) over %f m -> %d %%", dist_parked, dist_engaged,
                      tot_dist, val)
        self.panel.gauge_move.Value = val

    def _pulse_progress_bar(self):
        """
        Stupid method that changes the progress bar. Used to indicate something
         is happening (during referencing).
        """
        mirror = self.tab_data_model.main.mirror
        if not all(mirror.referenced.value.values()):
            self.panel.gauge_move.Pulse()

    def _on_switch_btn(self, evt):
        """
        Called when the Park/Engage button is pressed.
          Start move either for referencing/parking, or for putting the mirror in position
        """
        if not evt.isDown:
            # just got unpressed -> that means we need to stop the current move
            return self._on_cancel(evt)

        if self._next_moves:
            logging.warning("Going to move mirror while still %d moves scheduled",
                            len(self._next_moves))

        # Decide which move to do
        # Note: s axis can only be moved when near engaged pos. So:
        #  * when parking/referencing => move s first, then l
        #  * when engaging => move l first, then s
        # Some systems are special, default is overridden with MD_AXES_ORDER_REF
        mirror = self.tab_data_model.main.mirror
        axes_order = mirror.getMetadata().get(model.MD_AXES_ORDER_REF, ("s", "l"))
        if set(axes_order) != {"s", "l"}:
            logging.warning("Axes order of mirror is %s, while should have s and l axes",
                            axes_order)
        mstate = self._get_mirror_state(mirror)

        moves = []
        if mstate == MIRROR_PARKED:
            # => Engage
            for a in reversed(axes_order):
                moves.append((mirror.moveAbs, {a: self._pos_engaged[a]}))
            btn_text = "ENGAGING MIRROR"
        elif mstate == MIRROR_NOT_REFD:
            # => Reference
            for a in axes_order:
                moves.append((mirror.reference, {a}))
            btn_text = "PARKING MIRROR"
            # position doesn't update during referencing, so just pulse
            self._pulse_timer.Start(250)  # 4 Hz
        else:
            # => Park
            # Use standard move to show the progress of the mirror position, but
            # finish by (normally fast) referencing to be sure it really moved
            # to the parking position.
            for a in axes_order:
                moves.append((mirror.moveAbs, {a: MIRROR_POS_PARKED[a]}))
                moves.append((mirror.reference, {a}))
            btn_text = "PARKING MIRROR"

        logging.debug("Will do the following moves: %s", moves)
        self._next_moves.extend(moves)
        c, a = self._next_moves.popleft()
        self._move_future = c(a)
        self._move_future.add_done_callback(self._on_move_done)

        self.tab_data_model.main.is_acquiring.value = True

        self.panel.btn_cancel.Enable()
        # We focus the cancel button mostly to not keep the focus on the switch
        # mirror button, because having the focus means that when pressing "space"
        # key the button is pressed (immediately). That can be quite dangerous after
        # parking the mirror, if the user is not paying close attention to the
        # keyboard. So that's a safety measure.
        self.panel.btn_cancel.SetFocus()
        self.panel.btn_switch_mirror.SetLabel(btn_text)

    def _on_move_done(self, future):
        """
        Called when one (sub) move is over, (either successfully or not)
        """
        try:
            future.result()
        except Exception as ex:
            # Something went wrong, don't go any further
            if not isinstance(ex, CancelledError):
                logging.warning("Failed to move mirror: %s", ex)
            logging.debug("Cancelling next %d moves", len(self._next_moves))
            self._next_moves.clear()
            self._on_full_move_end()

        # Schedule next sub-move
        try:
            c, a = self._next_moves.popleft()
        except IndexError:  # We're done!
            self._on_full_move_end()
        else:
            self._move_future = c(a)
            self._move_future.add_done_callback(self._on_move_done)

    def _on_cancel(self, evt):
        """
        Called when the cancel button is pressed, or the move button is untoggled
        """
        # Remove any other queued moves
        self._next_moves.clear()
        # Cancel the running move
        self._move_future.cancel()
        logging.info("Mirror move cancelled")

    @call_in_wx_main
    def _on_full_move_end(self):
        """
        Called when a complete move (L+S) is over (either successfully or not)
        """
        # In case it was referencing
        self._pulse_timer.Stop()

        # It's a toggle button, and the user toggled down, so need to untoggle it
        self.panel.btn_switch_mirror.SetValue(False)
        self.panel.btn_cancel.Disable()
        self.tab_data_model.main.is_acquiring.value = False
        self._update_mirror_status()
        # Just in case the referencing updated position before the pulse was stopped
        self._update_progress_bar(self.tab_data_model.main.mirror.position.value)

    def _update_mirror_status(self):
        """
        Check the current hardware status and update the button text and info
        text based on this.
        Note: must be called within the main GUI thread
        """
        mstate = self._get_mirror_state(self.tab_data_model.main.mirror)

        if mstate == MIRROR_NOT_REFD:
            txt_warning = ("Parking the mirror is required at least once in order "
                           "to reference the actuators.")
        elif mstate == MIRROR_BAD:
            txt_warning = "The mirror is neither fully parked nor entirely engaged."
        else:
            txt_warning = None

        self.panel.pnl_ref_msg.Show(txt_warning is not None)
        if txt_warning:
            self.panel.txt_warning.SetLabel(txt_warning)
            self.panel.txt_warning.Wrap(self.panel.pnl_ref_msg.Size[0] - 16)

        if mstate == MIRROR_PARKED:
            btn_text = "ENGAGE MIRROR"
        else:
            btn_text = "PARK MIRROR"

        self.panel.btn_switch_mirror.SetLabel(btn_text)

        # If the mirror is parked, we still allow the user to go to acquisition
        # but it's unlikely to be a good idea => indicate that something needs
        # to be done here first. Also prevent to use alignment tab, as we don't
        # want to let the use move the mirror all around the chamber.
        self.highlight(mstate != MIRROR_ENGAGED)

        try:
            tab_align = self.tab_data_model.main.getTabByName("sparc2_align")
            tab_align.button.Enable(mstate == MIRROR_ENGAGED)
        except LookupError:
            logging.debug("Failed to find the alignment tab")

    def Show(self, show=True):
        Tab.Show(self, show=show)

        # Start chamber view when tab is displayed, and otherwise, stop it
        if self.tab_data_model.main.ccd:
            self._ccd_stream.should_update.value = show

        # If there is an actuator, disable the lens
        if show:
            self.tab_data_model.main.opm.setPath("chamber-view")
            # Update if the mirror has been aligned
            self._pos_engaged = self.tab_data_model.main.mirror.getMetadata()[model.MD_FAV_POS_ACTIVE]
            # Just in case the mirror was moved from another client (eg, cli)
            self._update_mirror_status()

        # When hidden, the new tab shown is in charge to request the right
        # optical path mode, if needed.

    def terminate(self):
        self._ccd_stream.is_active.value = False

    @classmethod
    def get_display_priority(cls, main_data):
        # For SPARCs with a "parkable" mirror.
        # Note: that's actually just the SPARCv2 + one "hybrid" SPARCv1 with a
        # redux stage
        if main_data.role in ("sparc", "sparc2"):
            mirror = main_data.mirror
            if mirror and set(mirror.axes.keys()) == {"l", "s"}:
                mstate = cls._get_mirror_state(mirror)
                # If mirror stage not engaged, make this tab the default
                if mstate != MIRROR_ENGAGED:
                    return 10
                else:
                    return 2

        return None
