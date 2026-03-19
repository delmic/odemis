# -*- coding: utf-8 -*-
"""
Created on 11 Mar 2026

@author: Tim Moerkerken

Copyright © 2014-2026 Tim Moerkerken, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from odemis.acq.stream import Stream

PROJECT_STATE_FILENAME = "project_state.json"
PROJECT_STATE_VERSION = 1

STREAM_FILENAME_ATTR = "_project_stream_filename"
STREAM_INDEX_ATTR = "_project_stream_index"
STREAM_ORIGIN_FILENAME_MD = "Stream filename"
STREAM_ORIGIN_INDEX_MD = "Stream index"


def _normalize_record_filename(project_dir: str, filename: str) -> str:
    """Normalize overview record filename to a project-relative path.

    :param project_dir: Project directory.
    :param filename: Raw filename from state or stream origin.
    :return: Normalized project-relative path.
    """
    if os.path.isabs(filename):
        try:
            filename = os.path.relpath(filename, project_dir)
        except ValueError:
            filename = os.path.basename(filename)
    return os.path.normpath(filename)


def normalize_stream_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one persisted stream-record mapping.

    :param record: Potentially incomplete record mapping.
    :return: Sanitized record with required keys.
    """
    filename = record.get("filename")
    if not isinstance(filename, str):
        return {"filename": "", "stream_indices": [], "deleted_stream_indices": []}

    stream_indices = sorted(
        {
            int(index)
            for index in record.get("stream_indices", [])
            if isinstance(index, int) and index >= 0
        }
    )
    deleted_stream_indices = sorted(
        {
            int(index)
            for index in record.get("deleted_stream_indices", [])
            if isinstance(index, int) and index >= 0
        }
    )
    normalized_record = {
        "filename": os.path.normpath(filename),
        "stream_indices": stream_indices,
        "deleted_stream_indices": deleted_stream_indices,
    }
    return normalized_record


def normalize_stream_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize stream records while dropping empty filenames.

    :param records: Raw records list.
    :return: Sanitized records.
    """
    normalized: List[Dict[str, Any]] = []
    for record in records:
        normalized_record = normalize_stream_record(record)
        if normalized_record["filename"]:
            normalized.append(normalized_record)
    return normalized


def get_state_filename(project_dir: str) -> str:
    """Return the project-state filename for a project directory."""
    return os.path.join(project_dir, PROJECT_STATE_FILENAME)


def read_project_state(project_dir: str) -> Dict[str, Any]:
    """Load global project state from disk.

    :param project_dir: Project directory.
    :return: Parsed state dictionary or empty mapping when absent.
    """
    filename = get_state_filename(project_dir)
    if not os.path.exists(filename):
        return {}
    try:
        with open(filename, "r") as jsonfile:
            state = json.load(jsonfile)
    except (OSError, IOError, json.JSONDecodeError):
        return {}
    if not isinstance(state, dict):
        return {}
    return state


def write_project_state(project_dir: str, state: Dict[str, Any]) -> None:
    """Persist global project state to disk.

    :param project_dir: Project directory.
    :param state: State mapping.
    """
    filename = get_state_filename(project_dir)
    with open(filename, "w") as jsonfile:
        json.dump(state, jsonfile)


def read_overview_records(project_dir: str) -> List[Dict[str, Any]]:
    """Read persisted overview stream records from global state.

    :param project_dir: Project directory.
    :return: Normalized overview stream records.
    """
    state = read_project_state(project_dir)
    overview_records = state.get("overview_records", [])
    if not isinstance(overview_records, list):
        overview_records = []
    records = normalize_stream_records(overview_records)
    for record in records:
        record["filename"] = _normalize_record_filename(project_dir, record["filename"])
    return records


def save_overview_records(
    project_dir: str,
    overview_records: List[Dict[str, Any]],
    state_updates: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist overview stream records in global state.

    :param project_dir: Project directory.
    :param overview_records: Normalized overview records.
    :param state_updates: Optional additional top-level state entries to merge.
    """
    state = read_project_state(project_dir)
    state["state_version"] = PROJECT_STATE_VERSION
    state["overview_records"] = normalize_stream_records(overview_records)
    if state_updates is not None:
        state.update(state_updates)
    write_project_state(project_dir, state)


def mark_overview_stream_deleted(project_dir: str, filename: str, stream_index: int) -> bool:
    """Mark one overview stream index as deleted in global state.

    :param project_dir: Project directory.
    :param filename: Relative filename for overview data.
    :param stream_index: Index in overview file.
    :return: ``True`` when the state was updated.
    """
    records = read_overview_records(project_dir)
    filename = _normalize_record_filename(project_dir, filename)

    for record in records:
        if record["filename"] != filename:
            continue
        if stream_index not in record["stream_indices"]:
            record["stream_indices"].append(stream_index)
            record["stream_indices"].sort()
        if stream_index not in record["deleted_stream_indices"]:
            record["deleted_stream_indices"].append(stream_index)
            record["deleted_stream_indices"].sort()
            save_overview_records(project_dir, records)
        return True

    records.append(
        normalize_stream_record(
            {
                "filename": filename,
                "stream_indices": [stream_index],
                "deleted_stream_indices": [stream_index],
            }
        )
    )
    save_overview_records(project_dir, records)
    return True


def register_overview_streams(project_dir: str, streams: List[Stream]) -> bool:
    """Persist overview stream links for the given streams.

    :param project_dir: Project directory.
    :param streams: Overview streams to register.
    :return: ``True`` when records were updated.
    """
    records = read_overview_records(project_dir)
    changed = False
    by_filename: Dict[str, set[int]] = {}

    for stream_obj in streams:
        filename, stream_index = get_stream_origin(stream_obj)
        if not isinstance(filename, str) or not isinstance(stream_index, int):
            continue
        filename = _normalize_record_filename(project_dir, filename)
        by_filename.setdefault(filename, set()).add(stream_index)

    for filename, indices_set in by_filename.items():
        indices = sorted(indices_set)
        record = next((r for r in records if r["filename"] == filename), None)
        if record is None:
            records.append(
                normalize_stream_record(
                    {
                        "filename": filename,
                        "stream_indices": indices,
                        "deleted_stream_indices": [],
                    }
                )
            )
            changed = True
            continue

        merged = sorted(set(record["stream_indices"]).union(indices))
        if merged != record["stream_indices"]:
            record["stream_indices"] = merged
            changed = True
        filtered_deleted = [index for index in record["deleted_stream_indices"] if index in merged]
        if filtered_deleted != record["deleted_stream_indices"]:
            record["deleted_stream_indices"] = filtered_deleted
            changed = True

    if changed:
        save_overview_records(project_dir, records)
    return changed


def is_overview_stream_deleted(project_dir: str, filename: str, stream_index: int) -> bool:
    """Check whether an overview stream index is marked deleted in state.

    :param project_dir: Project directory.
    :param filename: Stream filename (absolute or relative).
    :param stream_index: Stream index.
    :return: ``True`` if the stream index is marked deleted.
    """
    normalized_filename = _normalize_record_filename(project_dir, filename)
    for record in read_overview_records(project_dir):
        if record["filename"] != normalized_filename:
            continue
        return stream_index in record["deleted_stream_indices"]
    return False


def set_stream_origin(stream: Stream, filename: str, stream_index: int) -> None:
    """Attach persisted origin information to a stream instance.

    :param stream: Stream instance to annotate.
    :param filename: Relative filename in project directory.
    :param stream_index: Index in decoded static stream list.
    """
    setattr(stream, STREAM_FILENAME_ATTR, filename)
    setattr(stream, STREAM_INDEX_ATTR, stream_index)


def get_stream_origin(stream: Stream) -> Tuple[Optional[str], Optional[int]]:
    """Read persisted origin information from a stream instance.

    :param stream: Stream instance.
    :return: ``(filename, stream_index)`` tuple or ``(None, None)``.
    """
    filename = getattr(stream, STREAM_FILENAME_ATTR, None)
    stream_index = getattr(stream, STREAM_INDEX_ATTR, None)

    if not isinstance(filename, str):
        for key, value in vars(stream).items():
            if key.endswith("_stream_filename") and isinstance(value, str):
                filename = value
                break
    if not isinstance(stream_index, int):
        for key, value in vars(stream).items():
            if key.endswith("_stream_index") and isinstance(value, int):
                stream_index = value
                break
    if not isinstance(filename, str):
        return None, None
    if not isinstance(stream_index, int):
        return filename, None
    return filename, stream_index


def set_stream_origin_from_raw(stream: Stream) -> None:
    """Attach stream origin from first raw metadata when available.

    :param stream: Stream instance to annotate.
    """
    if not getattr(stream, "raw", None):
        return
    metadata = getattr(stream.raw[0], "metadata", {})
    filename = metadata.get(STREAM_ORIGIN_FILENAME_MD)
    stream_index = metadata.get(STREAM_ORIGIN_INDEX_MD)
    if isinstance(filename, str) and isinstance(stream_index, int):
        set_stream_origin(stream, filename, stream_index)
