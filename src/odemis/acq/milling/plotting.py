"""
@author: Patrick Cleeve

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


### Purpose ###

This module contains plotting utils related to milling tasks.

"""
from typing import Dict, List

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from odemis import model
from odemis.acq.milling.patterns import (
    MicroexpansionPatternParameters,
    RectanglePatternParameters,
    TrenchPatternParameters,
)
from odemis.acq.milling.tasks import MillingTaskSettings

# milling pattern colours
COLOURS = [
    "yellow","cyan", "magenta", "lime",
    "orange","hotpink", "green", "blue",
    "red", "purple",
]

def _draw_trench_pattern(image: model.DataArray, params: TrenchPatternParameters, colour: str = "yellow", name: str = "Task") -> List[mpatches.Rectangle]:
    # get parameters
    width = params.width.value
    height = params.height.value
    spacing = params.spacing.value
    mx, my = params.center.value

    # position in metres from image centre
    pixel_size = image.metadata[model.MD_PIXEL_SIZE][0] # assume isotropic
    pmx, pmy = mx / pixel_size, my / pixel_size

    # convert to image coordinates
    cy, cx = image.shape[0] / 2, image.shape[1] / 2
    px = cx + pmx
    py = cy - pmy

    # convert parameters to pixels
    width = width / pixel_size
    height = height / pixel_size
    spacing = spacing / pixel_size

    rect1 = mpatches.Rectangle(
        xy=(px - width / 2, py + spacing / 2),
        width=width,
        height=height,
        linewidth=1,
        edgecolor=colour,
        facecolor=colour,
        alpha=0.3,
        label=f"{name}",
    )
    rect2 = mpatches.Rectangle(
        xy=(px - width / 2, py - spacing / 2 - height),
        width=width,
        height=height,
        linewidth=1,
        edgecolor=colour,
        facecolor=colour,
        alpha=0.3,
    )

    return [rect1, rect2]


def _draw_rectangle_pattern(image: model.DataArray, params: RectanglePatternParameters, colour: str = "yellow", name: str = "Task") -> List[mpatches.Rectangle]:
    # get parameters
    width = params.width.value
    height = params.height.value
    mx, my = params.center.value

    # position in metres from image centre
    pixel_size = image.metadata[model.MD_PIXEL_SIZE][0] # assume isotropic
    pmx, pmy = mx / pixel_size, my / pixel_size

    # convert to image coordinates
    cy, cx = image.shape[0] / 2, image.shape[1] / 2
    px = cx + pmx
    py = cy - pmy

    # convert parameters to pixels
    width = width / pixel_size
    height = height / pixel_size

    rect = mpatches.Rectangle(
        xy=(px - width / 2, py + height / 2),
        width=width,
        height=height,
        linewidth=1,
        edgecolor=colour,
        facecolor=colour,
        alpha=0.3,
        label=f"{name}",
    )

    return [rect]

def _draw_microexpansion_pattern(image: model.DataArray, params: MicroexpansionPatternParameters, colour: str = "yellow", name: str = "Task") -> List[mpatches.Rectangle]:

    # get parameters
    width = params.width.value
    height = params.height.value
    spacing = params.spacing.value / 2
    mx, my = params.center.value

    # position in metres from image centre
    pixel_size = image.metadata[model.MD_PIXEL_SIZE][0] # assume isotropic
    pmx, pmy = mx / pixel_size, my / pixel_size

    # convert to image coordinates
    cy, cx = image.shape[0] / 2, image.shape[1] / 2
    px = cx + pmx
    py = cy - pmy

    # convert parameters to pixels
    width = width / pixel_size
    height = height / pixel_size
    spacing = spacing / pixel_size

    rect1 = mpatches.Rectangle(
        xy=(px - spacing, py - height / 2),
        width=width,
        height=height,
        linewidth=1,
        edgecolor=colour,
        facecolor=colour,
        alpha=0.3,
        label=f"{name}",
    )
    rect2 = mpatches.Rectangle(
        xy=(px + spacing - width / 2, py - height / 2),
        width=width,
        height=height,
        linewidth=1,
        edgecolor=colour,
        facecolor=colour,
        alpha=0.3,
    )

    return [rect1, rect2]

drawing_functions = {
    RectanglePatternParameters: _draw_rectangle_pattern,
    TrenchPatternParameters: _draw_trench_pattern,
    MicroexpansionPatternParameters: _draw_microexpansion_pattern,

}

def draw_milling_tasks(image: model.DataArray, milling_tasks: Dict[str, MillingTaskSettings]) -> plt.Figure:
    """Draw the milling tasks on the given image using matplotlib. The patterns are drawn in different colours for each task.
    This is primarily for debugging and visualisation purposes.
    :param image: the image to draw the patterns on
    :param milling_tasks: the milling tasks to draw
    :return: the figure containing the image and patterns
    """
    fig, ax = plt.subplots(1, 1, figsize=(10, 10))
    plt.imshow(image, cmap="gray")

    for i, (task_name, task) in enumerate(milling_tasks.items()):

        colour = COLOURS[i%len(COLOURS)]
        for p in task.patterns:
            patches = []

            patches = drawing_functions[type(p)](image, p, colour=colour, name=task_name)

            for patch in patches:
                ax.add_patch(patch)
    plt.legend()

    return fig
