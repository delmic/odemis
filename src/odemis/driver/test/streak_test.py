#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Jun 17, 2024

@author: Éric Piel

Copyright © 2024 Éric Piel, Delmic

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

import unittest

from odemis import model
from odemis.driver import simulated, streak


class DelayConnectorTestCase(unittest.TestCase):

    def setUp(self):
        self.trigger = simulated.GenericComponent("Trigger", "trigger",
                              vas={"delay": {"value": 0.0, "unit": "s", "range": (0, 1e-6)},
                                   "period": {"value": 25e-6, "unit": "s"}
                              }
                              )
        self.streak_unit = simulated.GenericComponent("Streak Unit", "streak-unit",
                              vas={"timeRange": {"value": 1e-12, "unit": "s", "choices": {1e-12, 2e-12, 5e-12}},
                              }
                           )
        self.delay_connector = streak.DelayConnector("test", "delay_connector",
                                 dependencies={"trigger": self.trigger, "streak-unit": self.streak_unit})
        self.delay_connector.updateMetadata({
            model.MD_TIME_RANGE_TO_DELAY: {
                1.e-12: 7.99e-9,
                2.e-12: 9.63e-9,
                5.e-12: 33.2e-9,
            }
        })

    def test_simple(self):
        rate = 1 / self.trigger.period.value
        self.assertAlmostEqual(rate, self.delay_connector.triggerRate.value)

    def test_trigger_rate_update(self):
        self.trigger.period.value = 50e-6
        rate = 1 / self.trigger.period.value
        self.assertAlmostEqual(rate, self.delay_connector.triggerRate.value)

    def test_time_range_update(self):
        self.streak_unit.timeRange.value = 2e-12
        self.assertAlmostEqual(9.63e-9, self.delay_connector.triggerDelay.value)

    def test_update_metadata(self):
        with self.assertRaises(ValueError):
            # Must be a dict
            self.delay_connector.updateMetadata({model.MD_TIME_RANGE_TO_DELAY: [1, 2]})

        self.delay_connector.updateMetadata({
            model.MD_TIME_RANGE_TO_DELAY: {
                1.e-12: 8.0e-9,
                2.e-12: 9.0e-9,
            }
        })


if __name__ == '__main__':
    unittest.main()
