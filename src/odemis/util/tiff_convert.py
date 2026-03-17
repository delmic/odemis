# -*- coding: utf-8 -*-
"""
Created on 17 Mar 2026

@author: Tim Moerkerken

Copyright © 2014-2026 Tim Moerkerken, Delmic

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
import os
from typing import Any, Callable, Optional

import wx
from odemis.dataio import tiff

logger = logging.getLogger(__name__)


def get_conversion_output_path(
    source_path: str,
    project_folder: Optional[str] = None,
) -> str:
    """
    Determine the output path for a converted pyramidal TIFF file.

    If a project folder is provided, stores there.
    Otherwise stores next to the source file.

    :param source_path: Path to the original TIFF file
    :param project_folder: Optional project folder path
    :return: Full path where the converted file should be stored
    """
    filename = os.path.basename(source_path)
    name_without_ext = os.path.splitext(filename)[0]
    ext = os.path.splitext(filename)[1]

    # Ensure extension is valid TIFF format
    if ext.lower() not in ['.tif', '.tiff']:
        ext = '.tif'

    converted_filename = f"converted_{name_without_ext}{ext}"
    if project_folder:
        return os.path.join(project_folder, converted_filename)
    source_dir = os.path.dirname(source_path)
    return os.path.join(source_dir, converted_filename)


def convert_to_pyramidal_with_progress(
    src_filename: str,
    dst_filename: str,
    compression: str = "lzw",
    progress_callback: Optional[Callable[[float], None]] = None,
) -> None:
    """
    Convert a non-pyramidal TIFF file to pyramidal format.

    Uses libtiff and the dataio module's export function with pyramid=True.

    :param src_filename: Path to source non-pyramidal TIFF file
    :param dst_filename: Path where converted pyramidal TIFF will be saved
    :param compression: Compression type ('lzw', 'lz4', 'zstd', None for uncompressed)
    :param progress_callback: Optional callable to report progress (0.0 to 1.0)
    :raises IOError: If conversion fails
    :raises ValueError: If source file doesn't exist or is not a valid TIFF
    """
    if not os.path.isfile(src_filename):
        raise ValueError(f"Source file not found: {src_filename}")

    try:
        dst_dir = os.path.dirname(dst_filename)
        if progress_callback:
            progress_callback(0.1)

        if dst_dir:
            os.makedirs(dst_dir, exist_ok=True)

        if progress_callback:
            progress_callback(0.5)

        logger.info("Converting to pyramidal format and saving to: %s", dst_filename)

        # Determine if we need to compress
        compressed = compression is not None and compression.lower() != "none"

        tiff.convert_to_pyramidal(src_filename, dst_filename, compressed=compressed)

        if progress_callback:
            progress_callback(1.0)

        logger.info(f"Successfully converted {os.path.basename(src_filename)} to pyramidal TIFF")

    except Exception as e:
        logger.error(f"Error converting TIFF to pyramidal format: {e}")
        # Clean up partial file if it exists
        if os.path.exists(dst_filename):
            try:
                os.remove(dst_filename)
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up partial file {dst_filename}: {cleanup_error}")
        raise IOError(f"Failed to convert TIFF file: {e}")


def ensure_pyramidal_tiff(
    filename: str,
    project_folder: Optional[str] = None,
    standalone_mode: bool = False,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> str:
    """
    Ensure a TIFF file is pyramidal, converting if necessary.

    This is the main orchestrator function that:
    1. Checks if the file is already pyramidal
    2. If not, converts it to pyramidal format
    3. Returns the path to the (converted or original) pyramidal TIFF file

    Conversion is always performed if the file is non-pyramidal (no caching).

    :param filename: Path to the TIFF file to process
    :param project_folder: Project folder path (required in normal mode)
    :param standalone_mode: True if running in standalone/Viewer mode
    :param progress_callback: Optional callable to report progress (called during conversion)
    :return: Path to pyramidal TIFF file (either original or converted)
    :raises IOError: If the file is invalid or conversion fails
    """
    if not os.path.isfile(filename):
        raise IOError(f"File not found: {filename}")

    if not filename.lower().endswith((".tif", ".tiff")):
        return filename

    try:
        # Check if already pyramidal
        if tiff.is_pyramidal(filename):
            logger.debug(f"File {os.path.basename(filename)} is already pyramidal")
            return filename

        # File is non-pyramidal, convert it
        logger.info(f"File {os.path.basename(filename)} is non-pyramidal, converting...")

        target_project_folder = None if standalone_mode else project_folder
        output_path = get_conversion_output_path(filename, target_project_folder)

        # If converted file already exists, we'll overwrite it
        # (no caching per requirements)
        convert_to_pyramidal_with_progress(filename, output_path, progress_callback=progress_callback)

        return output_path

    except IOError:
        # Re-raise IOError as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error in ensure_pyramidal_tiff: {e}")
        raise IOError(f"Unexpected error processing TIFF file: {e}")


def _get_storage_context(main_data: Any) -> tuple[bool, Optional[str]]:
    """
    Resolve standalone mode and project folder from GUI main data.

    :param main_data: Main GUI data model
    :return: Tuple of (standalone_mode, project_folder)
    """
    standalone = bool(getattr(main_data, "is_viewer", False))
    project_folder = None
    if not standalone and hasattr(main_data, "project_path"):
        project_folder = main_data.project_path.value
    return standalone, project_folder


def ensure_pyramidal_tiff_for_file_gui(filename: str, parent: Any, main_data: Any) -> str:
    """
    Ensure a single import file is pyramidal TIFF, converting when needed.

    :param filename: Source filename
    :param parent: wx parent window for progress dialog
    :param main_data: Main GUI data model
    :return: Source filename or converted filename
    """
    if not filename.lower().endswith((".tif", ".tiff")):
        return filename

    try:
        if tiff.is_pyramidal(filename):
            return filename

        logger.info("Converting non-pyramidal TIFF: %s", os.path.basename(filename))
        standalone, project_folder = _get_storage_context(main_data)

        dlg = wx.ProgressDialog(
            "Converting TIFF File",
            f"Converting {os.path.basename(filename)} to pyramidal format...",
            maximum=100,
            parent=parent,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
        )

        try:
            def progress_callback(progress: float) -> None:
                """Update progress dialog (0.0 to 1.0)."""
                dlg.Update(int(progress * 100))

            return ensure_pyramidal_tiff(
                filename,
                project_folder=project_folder,
                standalone_mode=standalone,
                progress_callback=progress_callback,
            )
        finally:
            dlg.Destroy()
    except IOError as exc:
        logger.error("Failed to convert TIFF file %s: %s", filename, exc)
        return filename


def ensure_pyramidal_tiff_for_tileset_gui(filenames: list[str], parent: Any, main_data: Any) -> list[str]:
    """
    Ensure all TIFF files in a tileset are pyramidal, converting when needed.

    :param filenames: Input filenames
    :param parent: wx parent window for progress dialog
    :param main_data: Main GUI data model
    :return: Converted-or-original filenames
    """
    tiff_files = [fn for fn in filenames if fn.lower().endswith((".tif", ".tiff"))]
    total_tiff = len(tiff_files)
    if total_tiff == 0:
        return filenames

    standalone, project_folder = _get_storage_context(main_data)
    dlg = wx.ProgressDialog(
        "Converting TIFF Tileset",
        f"Converting tileset files (0/{total_tiff}) to pyramidal format...",
        maximum=100,
        parent=parent,
        style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
    )

    converted_files: list[str] = []
    converted_tiff_index = 0
    try:
        for filename in filenames:
            if not filename.lower().endswith((".tif", ".tiff")):
                converted_files.append(filename)
                continue

            tiff_idx = converted_tiff_index
            converted_tiff_index += 1

            try:
                if tiff.is_pyramidal(filename):
                    converted_files.append(filename)
                    dlg.Update(int(((tiff_idx + 1) / total_tiff) * 100))
                    continue
            except IOError as exc:
                logger.error("Failed to inspect TIFF file %s: %s", filename, exc)
                converted_files.append(filename)
                dlg.Update(int(((tiff_idx + 1) / total_tiff) * 100))
                continue

            dlg.Update(
                int((tiff_idx / total_tiff) * 100),
                f"Converting tileset files ({tiff_idx + 1}/{total_tiff}) to pyramidal format...",
            )

            def progress_callback(progress: float, current_idx: int = tiff_idx) -> None:
                """Update global tileset progress (0.0 to 1.0 for current file)."""
                dlg.Update(int(((current_idx + progress) / total_tiff) * 100))

            try:
                converted_file = ensure_pyramidal_tiff(
                    filename,
                    project_folder=project_folder,
                    standalone_mode=standalone,
                    progress_callback=progress_callback,
                )
            except IOError as exc:
                logger.error("Failed to convert TIFF file %s: %s", filename, exc)
                converted_file = filename

            converted_files.append(converted_file)
            dlg.Update(int(((tiff_idx + 1) / total_tiff) * 100))
    finally:
        dlg.Destroy()

    return converted_files
