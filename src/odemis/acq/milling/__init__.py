"""
@author: Éric Piel

Copyright © 2025 Delmic

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

import functools
import logging
import os

DEFAULT_MILLING_TASKS_PATH = os.path.join(os.path.dirname(__file__),  "milling_tasks.yaml")



@functools.cache
def is_milling_enabled() -> bool:
    """
    Check whether milling can be offered to the user: it requires both the licence
    and fibsemOS, as the milling is entirely done via fibsemOS.
    fibsemOS is only imported on the first call, as it is a heavy import.
    :returns: True if the milling licence is enabled and fibsemOS is installed.
    """
    from odemis.util.licences import LICENCE_MILLING_ENABLED
    if not LICENCE_MILLING_ENABLED:
        return False

    from odemis.acq.milling.fibsemos import FIBSEMOS_INSTALLED
    if not FIBSEMOS_INSTALLED:
        logging.warning("odemis-advanced milling licence is present, but fibsemOS is not installed: "
                        "milling is disabled")
        return False
    return True
