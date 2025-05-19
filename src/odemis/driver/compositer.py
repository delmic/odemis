# -*- coding: utf-8 -*-
"""
Created on 9 May 2025

@author: Éric Piel

Copyright © 2025 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""

import logging
from typing import Dict, Any

from odemis import model


HIDDEN_VAS = {"children", "dependencies", "affects"}

class CompositedComponent(model.HwComponent):
    """
    Wrapper component to merge multiple components together, seen as one, providing all the
    VigilantAttributes from all the components.
    The metadata is taken from the first component.
    For more specific use cases, see MultiplexLight, MultiplexActuator, and CompositedScanner.
    """
    def __init__(self, name, role, dependencies: Dict[str, model.HwComponent], **kwargs):
        """
        :param dependencies: internal role -> sub-component. The components to wrap together.
        The internal role is used to sort the components (alphabetically). If several
        components have the same VA, the one from the first component is used.
        """
        model.HwComponent.__init__(self, name, role, dependencies=dependencies, **kwargs)
        if not dependencies:
            raise ValueError("CompositedComponent needs dependencies")

        sorted_deps = sorted(dependencies.items(), key=lambda x: x[0])
        self._base_comp = sorted_deps[0][1]  # The first component is the base component (for metadata)

        # Add all the VAs, without overriding the ones already present on the previous components
        for role, comp in sorted_deps:
            if not isinstance(comp, model.ComponentBase):
                raise ValueError(f"Dependency {role} is not a HwComponent.")

            for vaname, va in model.getVAs(comp).items():
                if vaname in HIDDEN_VAS:
                    continue
                if hasattr(self, vaname):
                    logging.info("Skipping %s.%s for composition, as already present on previous dependency",
                                 comp.name, vaname)
                    continue

                logging.debug("Using %s.%s for composition",comp.name, vaname)
                setattr(self, vaname, va)

    def getMetadata(self) -> Dict[str, Any]:
        """
        Get the metadata of the component.
        """
        return self._base_comp.getMetadata()

    def updateMetadata(self, md: Dict[str, Any]):
        """
        Update the metadata of the component.
        """
        self._base_comp.updateMetadata(md)
