"""
Created on Jun 17, 2024

@author: Éric Piel

Copyright © 2024 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import numbers
from typing import Dict, Any, Optional

from odemis import model, util


class DelayConnector(model.HwComponent):
    """
    Wrapper component to connect a pulse generator component with the rest of the streak-cam components.
    """
    def __init__(self, name: str, role: str,
                 dependencies: Optional[Dict[str, model.HwComponent]] = None,
                 **kwargs):
        """
        :param name: component name
        :param role: component role
        :param dependencies: internal role to component:
        * trigger: a component with a .delay VA, which will be used to provide the .triggerDelay
        It can also have a .period VA, which will be used to provide the .triggerRate
        * streak-unit (optional): a component with a .timeRange, to be updated when the triggerDelay is changed
        according to the MD_TIME_RANGE_TO_DELAY metadata.
        """
        super().__init__(name, role, dependencies=dependencies, **kwargs)

        dependencies = dependencies or {}
        try:
            self._trigger = dependencies["trigger"]
        except KeyError:
            raise ValueError(f"DelayConnector needs a 'trigger' dependency, but only got {dependencies.values()}")

        # Just copy the delay VA directly, giving it a different name
        self.triggerDelay = self._trigger.delay

        if model.hasVA(self._trigger, "period"):
            self.triggerRate = model.FloatVA(0, unit="Hz", readonly=True)
            self._trigger.period.subscribe(self._on_trigger_period, init=True)
            if model.hasVA(self._trigger, "power"):
                self._trigger.power.subscribe(self._on_trigger_power)

        if "streak-unit" in dependencies:
            self._streak_unit = dependencies["streak-unit"]
            self._streak_unit.timeRange.subscribe(self._on_time_range)
        else:
            logging.info("DelayConnector has no streak-unit dependency, so it won't update the timeRange VA")

    def _update_trigger_rate(self, period: float, power: bool) -> None:
        if power:
            # There is no reason period could be 0, so no need to check... and if it fails, it'll be logged
            rate = 1 / period
        else:
            rate = 0
        self.triggerRate._set_value(rate, force_write=True)

    def _on_trigger_period(self, period: float) -> None:
        """
        Called when the .period VA of the trigger component is updated.
        :param period: trigger period in s
        """
        if model.hasVA(self._trigger, "power"):
            power = self._trigger.power.value
        else:
            power = True
        self._update_trigger_rate(period, power)

    def _on_trigger_power(self, power: bool) -> None:
        """
        Called when the .power VA of the trigger component is updated.
        :param power: True if the trigger is powered
        """
        period = self._trigger.period.value
        self._update_trigger_rate(period, power)

    def _on_time_range(self, time_range: float) -> None:
        # set corresponding trigger delay
        tr2d = self._metadata.get(model.MD_TIME_RANGE_TO_DELAY)
        if tr2d:
            key = util.find_closest(time_range, tr2d.keys())
            if util.almost_equal(key, time_range):
                self.triggerDelay.value = tr2d[key]
            else:
                logging.warning("Time range %s is not a key in MD for time range to "
                                "trigger delay calibration" % time_range)

    def updateMetadata(self, md: Dict[str, Any]) -> None:
        """
        Update the metadata of the component.
        Overrides the default implementation to check the MD_TIME_RANGE_TO_DELAY metadata.
        :param md:
        """
        if model.MD_TIME_RANGE_TO_DELAY in md:
            tr2d = md[model.MD_TIME_RANGE_TO_DELAY]
            if not isinstance(tr2d, dict):
                raise ValueError(f"MD_TIME_RANGE_TO_DELAY must be a dictionary but got {type(tr2d)}")
            for time_range, delay in tr2d.items():
                if not isinstance(delay, numbers.Real):
                    raise ValueError(f"Trigger delay {delay} corresponding to time range {time_range} is not a float.")
                if not self.triggerDelay.range[0] <= delay <= self.triggerDelay.range[1]:
                    raise ValueError(f"Trigger delay {delay} corresponding to time range {time_range} is not in "
                                     f"range {self.triggerDelay.range}.")

        super().updateMetadata(md)
