# -*- coding: utf-8 -*-
"""
Created on 15 April 2026

@author: Tim Moerkerken

Copyright © 2026 Tim Moerkerken, Delmic

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
import json
import os
from packaging.version import Version
from pathlib import Path
from typing import Any, Dict, List, Iterable, Optional

PROJECT_NAME = "project.json"
PROJECT_VERSION = "1.0"
LEGACY_PROJECT_NAME = "features.json"
IMG_FILENAME = "filename"
IMG_IN_FILE_IDS = "in_file_indices"


def save_project(main_data: "CryoMainGUIData") -> None:
    """
    Save the project to file based on Odemis GUI data.
    :param main_data: the application GUI data
    """
    project_dir = main_data.tab.value.conf.pj_last_path
    filename = Path(project_dir) / PROJECT_NAME
    tmp_filename = filename.with_name(f".{filename.name}.tmp")
    try:
        with open(tmp_filename, "w") as jsonfile:
            json.dump(serialize_project_data(main_data), jsonfile, indent=4)
            jsonfile.flush()
            os.fsync(jsonfile.fileno())
        # Only reached when writing to tmp file succeeded, preventing us from saving corrupted data
        tmp_filename.replace(filename)
    except Exception as e:
        # We don't delete the temporary file here, since it can be useful for debugging post-mortem.
        logging.warning(f"Save failed! The original project file was preserved. Error: {e}")


def read_project_file(project_file: os.PathLike) -> List["CryoFeature"]:
    """
    Read the provided project file and return its contents as is
    :param project_file: path to a json project file
    :return: loaded project contents
    """
    project_file = Path(project_file)
    if not project_file.exists():
        raise ValueError(f"{project_file.name} file doesn't exists in this location. {project_file}")
    with open(project_file, "r") as jsonfile:
        return json.load(jsonfile)


def load_project(project_dir: os.PathLike) -> dict:
    """
    Load a cryo project based on the project directory, and format its contents into a structured manner.
    Handles legacy projects.

    :param project_dir: path to the project directory
    :return: the loaded project in a structured manner (features and overviews)
    """
    project_dir = Path(project_dir)
    try:
        # Read features
        project = read_project_file(project_dir / PROJECT_NAME)
        features = project["features"]
        overviews = project["overviews"]
        project_version = Version(project.get("version", "0.0"))
        # Section where we can handle backwards compatibility later.
        if project_version.major < 1:
            logging.error(f"Project version {project_version} not supported")
    except ValueError:
        try:
            project = read_project_file(project_dir / LEGACY_PROJECT_NAME)
        except ValueError:
            # Graceful, since legacy method already handles logging.
            project = {"feature_list": []}
        finally:
            # Load overview images, mimicking legacy method, but using pathlib instead
            overview_filenames = project_dir.glob("*overview*.ome.tiff")
            # Legacy projects lacked any bookkeeping of deleted files. Since we did not store the original in-file
            # indices to a project file, we need to recover it here. In order to get the real indices, we need to load
            # the imagedata. Let's populate the indices later, where we load the imagedata, so we don't do it double.
            overviews = [{IMG_FILENAME: str(ovf.relative_to(project_dir))} for ovf in overview_filenames]
            features = project["feature_list"]
            for feature in features:
                filenames = project_dir.glob(f"*-{feature['name']}*")
                images = []
                for filename in filenames:
                    images.append({IMG_FILENAME: str(filename)})
                feature["images"] = images

    # Recover absolute image paths for features
    for feature in features:
        feature["images"] = deserialize_images(feature["images"], project_dir)

    # Recover absolute image paths for overview images
    overviews = deserialize_images(overviews, project_dir)

    # Feature streams are intentionally not loaded here; they are lazy-loaded
    # on demand by CryoAcquiredStreamsController when a feature is selected.

    return {"overviews": overviews, "features": features}


def serialize_project_data(main_data: "CryoMainGUIData") -> Dict:
    """
    Convert the in-memory project data with complex structure to a serialized form,
    so it can be nicely persisted on disk.
    :param main_data: the application GUI data
    :return: the serialized project data
    """
    features = main_data.features.value
    overviews = main_data.overviews.value
    project_dir = main_data.tab.value.conf.pj_last_path
    feature_list = []
    for feature in features:
        feature_item = {
            'name': feature.name.value,
            'status': feature.status.value,
            'stage_position': feature.stage_position.value,
            'fm_focus_position': feature.fm_focus_position.value,
            'posture_positions': feature.posture_positions,
            "milling_tasks": {k: v.to_dict() for k, v in feature.milling_tasks.items()},
            'correlation_data': feature.correlation_data.to_dict() if feature.correlation_data  else {},
            'superz_stream_name': feature.superz_stream_name,
            'superz_focused': feature.superz_focused,
            'images': serialize_images(feature.images.value, project_dir),
        }
        if feature.path:
            feature_item['path'] = feature.path
        feature_list.append(feature_item)

    overview_list = serialize_images(overviews, project_dir)
    return {"version": PROJECT_VERSION, "features": feature_list, "overviews": overview_list}


def add_image(images: List[Dict[str, Any]], filename: os.PathLike, indices: Optional[Iterable[int]] = None):
    """
    Add image to a list of images
    :param images: list of images to append to
    :param filename: the filename (full path) of the image to add
    :param indices: the list of sub indices that belong to the image. If not provided, it will be omitted, assuming we
        want to use all channels of the image.
    """
    # Our naming schemes should not allow to add a duplicate filename, so that is not handled here
    images.append({IMG_FILENAME: filename, **({IMG_IN_FILE_IDS: set(indices)} if indices else {})})


def remove_image(images: List[Dict[str, Any]], filename: os.PathLike, indices: Optional[Iterable[int]] = None):
    """
    Remove image from a list of images
    :param images: list of images to remove from
    :param filename: the filename (full path) of the image to remove
    :param indices: the list of sub indices that belong to the image. If not provided, the entire image will be deleted.
    """
    for im in images:
        if Path(filename) == Path(im[IMG_FILENAME]):
            if indices is None:  # If no indices provided, remove the entire image
                images.remove(im)
            else:  # If indices are provided, try to subtract the sets and see if there is anything left
                new_ids = set(im.get(IMG_IN_FILE_IDS, [])) - set(indices)
                if new_ids:  # If there are in-file indices left, overwrite the set when the newly reduced set
                    im[IMG_IN_FILE_IDS] = new_ids
                else:  # Remove entire image if no sub-image is left
                    images.remove(im)
            break  # Nothing left to do


def serialize_images(images: List[Dict[str, Any]], project_dir: os.PathLike) -> List[Dict[str, Any]]:
    """
    Convert a list of images into a serialized form
    :param images: list of images to serialize
    :param project_dir: the project directory
    :return: serialized list of images, containing relative image paths
    """
    images_serialized = []
    for image in images:
        image_path = Path(image[IMG_FILENAME])
        # Everything acquired in Odemis is being stored in the project dir, skip if somehow otherwise.
        if not image_path.is_relative_to(project_dir):
            logging.warning("Skip saving image outside of project directory to project file.")
            continue
        image_filename_rel = image_path.relative_to(project_dir)
        image_serialized = {IMG_FILENAME: str(image_filename_rel)}
        # In-file ids not always known at this point, so we allow to write an image without the in-file ids.
        if IMG_IN_FILE_IDS in image:
            image_serialized[IMG_IN_FILE_IDS] = list(image[IMG_IN_FILE_IDS])
        images_serialized.append(image_serialized)
    return images_serialized


def deserialize_images(images: List[Dict[str, Any]], project_dir: os.PathLike) -> List[Dict[str, Any]]:
    """
    Convert a list of images back into a form with absolute paths
    :param images: list of images to deserialize
    :param project_dir: the project directory
    :return: deserialized list of images, containing absolute image paths
    """
    images_deserialized = []
    for image in images:
        image_path = Path(image[IMG_FILENAME])
        # Only relative paths are allowed in the project file, skip if somehow otherwise.
        if image_path.is_absolute():
            logging.warning(
                "Skip loading image with absolute path from project file. "
                "Only relative paths are allowed."
            )
            continue

        image_deserialized = image.copy()
        image_deserialized[IMG_FILENAME] = str(Path(project_dir) / image_path)
        images_deserialized.append(image_deserialized)
    return images_deserialized
