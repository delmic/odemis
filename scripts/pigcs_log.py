#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 28 Oct 2013

Log info about PI controllers. Run it once, with all the controllers you're 
interested in connected. The result will be saved in ./pigcs.log

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
from odemis.driver import pigcs


if __name__ == '__main__':
    handler = logging.FileHandler("pigcs.log")
    logging.getLogger().setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter('%(asctime)s (%(module)s) %(levelname)s: %(message)s'))
    logging.getLogger().addHandler(handler)

    devices = pigcs.Bus.scan()
    if len(devices) == 0:
        logging.error("No PI controller found")
        print("No PI controller found")

    for name, kwargs in devices:
        print("Opening ", name)
        ctls = pigcs.Bus("test", "logging", **kwargs)
        # Just starting it logs lots of info.
        logging.info("swVersion: %s", ctls.swVersion)
        logging.info("hwVersion: %s", ctls.hwVersion)

        # Last thing we need is the list of parameters value
        # We use twice in a row private members, yeah!
        visited = set()
        for axis, (controller, channel) in ctls._axis_to_cc.items():
            if controller not in visited:
                visited.add(controller)
                prms = controller._sendQueryCommand("SPA?\n")
                logging.info("Parameters of controller %d:\n%s",
                             controller.address, "\n".join(prms))



