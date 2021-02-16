# -*- coding: utf-8 -*-

"""
@author: Anders Muskens

Copyright © 2020 Anders Muskens, Delmic

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

import wx
import collections
import numpy
import time
import logging
from odemis.gui.util import call_in_wx_main
import odemis.gui as gui
from odemis.gui.comp import popup
from odemis import model

MIN_PERIOD_WARNING = 60  # in seconds, represents the time before notifications are shown again


@call_in_wx_main
def set_label_colour(label, colour, weight=wx.NORMAL):
    """
    Set a wx.StaticText foreground colour and font style to bold/unbold
    label: wx.StaticText object
    colour: wx.Colour object
    bold: ( wx.FontWeight) A  wx.FontWeight preset
    """
    label.SetForegroundColour(colour)
    font = label.GetFont()
    font.SetWeight(weight)
    label.SetFont(font)


class TemperatureController(object):
    '''
    Temperature Controller
    Displays the temperature from a thermostat component in the main GUI on a predefined control
    and updates at a set sampling period, which is polled.
    '''

    def __init__(self, main_frame, thermostat, max_duration=5):
        '''
        main_frame: (wx) main xrc frame of the GUI
        thermostat: (HwComponent) a component with the VA "temperature"
        max_duration: (float) in seconds, the maximum duration
        '''

        if not hasattr(thermostat, "temperature") or not hasattr(thermostat, "targetTemperature"):
            raise ValueError("Thermostat is missing the temperature and/or targetTemperature VA's")
        self.thermostat = thermostat
        self.main_frame = main_frame
        self.temperature_label = main_frame.temperature_display
        self._fg_color = self.temperature_label.GetForegroundColour()
        self.temperature_label.Show()
        self._time_last_warning = 0  # time of the last warning
        self._time_last_speed_warning = 0

        # temperature history is a list of tuples (timestamp, temperature)
        self.temperature_history = [(time.time(), self.thermostat.temperature.value)]
        self.max_duration = max_duration
        self.thermostat.temperature.subscribe(self._updateTemperature)

    def _updateTemperature(self, temperature):
        """
        Subscriber to the temperature VA
        """
        # add the new temperature to the history with a timestamp
        self.temperature_history.append((time.time(), temperature))
        wx.CallAfter(self.temperature_label.SetLabel, u"Sample: {temp:.1f}°C".format(temp=temperature))
        
        # clear out values of the history that are too old i.e. older than the max_duration
        while len(self.temperature_history) > 0:
            t, _ = self.temperature_history[0]
            if t < time.time() - self.max_duration:
                self.temperature_history.pop(0)
            else:
                break

        # Get boundaries
        safe_range = self.thermostat.getMetadata().get(model.MD_SAFE_REL_RANGE, None)
        safe_speed = self.thermostat.getMetadata().get(model.MD_SAFE_SPEED_RANGE, None)

        # Calculate a linear regression of the temperature history to determine the speed
        l = list(zip(*self.temperature_history))  # convert [(time, temp), ...] to [[time, ...], [temp...]]
        speed = 0
        # calculate speed (only possible if there is data in the history
        if len(self.temperature_history) > 1:
            try:
                speed, _ = numpy.polyfit(l[0], l[1], 1)
            except:
                logging.exception("Could not compute the rate of change of temperature.")

        # determine setting the warning
        target_temperature = self.thermostat.targetTemperature.value
        if (safe_range is not None and
            not target_temperature + safe_range[0] <= temperature <= target_temperature + safe_range[1]
            ):
            # temperature is out of range
            # change colour to red
            set_label_colour(self.temperature_label, gui.FG_COLOUR_ERROR, weight=wx.BOLD)
            text = u"Temperature {temp:.2f}°C is outside of the target range of {lo:.2f}°C to {hi:.2f}°C!".format(
                    temp=temperature,
                    lo=target_temperature + safe_range[0],
                    hi=target_temperature + safe_range[1])
            logging.warning(text)
            # display messagebox warning, but only once per minute
            if time.time() - self._time_last_warning > MIN_PERIOD_WARNING:
                popup.show_message(self.main_frame, "Temperature Warning", message=text, level=logging.WARNING)
                self._time_last_warning = time.time()

        elif safe_speed is not None and not safe_speed[0] <= speed <= safe_speed[1]:
            # temperature is changing at an unsafe speed
            # change colour to yellow
            set_label_colour(self.temperature_label, gui.FG_COLOUR_ERROR, weight=wx.BOLD)
            text = u"Temperature {temp:.2f}°C is changing at an unsafe rate of {speed:.2f}°C/s!".format(temp=temperature, speed=speed)
            logging.warning(text)
            # display messagebox warning, but only once per minute
            if time.time() - self._time_last_speed_warning > MIN_PERIOD_WARNING:
                popup.show_message(self.main_frame, "Temperature Warning", message=text, level=logging.WARNING)
                self._time_last_speed_warning = time.time()

        else:
            # temperature is normal. Set the colour back to normal
            set_label_colour(self.temperature_label, self._fg_color, weight=wx.NORMAL)
            # reset warnings so they can be invoked again.
            self._time_last_speed_warning = 0
            self._time_last_warning = 0

