# -*- coding: utf-8 -*-
"""
Created on 24 Jan 2024

@author: Karishma Kumar

Copyright © 2024 Karishma Kumar, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
import json
import logging
import os
from typing import List, Dict, Any

from odemis import model, util
from odemis.acq.stream import FluoStream
from odemis.util.img import md_format_to_tint, tint_to_md_format

# Power value depends on selected excitation
# Emission value depends on selected excitation
# Tint can automatically change if excitation and emission are changed
# Therefore, the below VAs are changed first in the below order and then the rest of stream VAs
SETTINGS_ORDER = ["excitation", "power", "emission"]
# The below VAs are not important for loading the settings of a specific stream
NON_SETTINGS_VA = ['acquisitionType', 'auto_bc', 'background', 'histogram', 'image', 'intensityRange', 'is_active',
                   'roi', 'should_update', 'single_frame_acquisition', 'status']


def get_settings_order(stream):
    """
    Get the VAs for loading a stream setting and sort them so it can applied in the given order while loading
    a stream later.
    :param stream: stream object that consists of VAs
    """
    stream_vas = model.getVAs(stream)
    settings = set(stream_vas.keys()).difference(NON_SETTINGS_VA)
    settings_order = util.sorted_according_to(settings, SETTINGS_ORDER)

    return settings_order


class StreamSettingsConfig:
    """
    Read and write a list of most recently used (mru) settings from and to a JSON file respectively. The first element
    in the list will be the latest used setting and the last element will be the oldest used setting. The list will
    consist of finite number of entries which will update in the above mention order each time the list of stream
    settings is updated.
    """

    def __init__(self, file_path, max_entries):
        """
        :param file_path: full directory path to save the JSON file
        :param max_entries: number of entries to save in the JSON file
        """
        self.max_entries = max_entries
        self.config_data: List[Dict[str, Any]] = []
        self.file_path = file_path
        try:
            self._read()  # update the config_data by reading the JSON file
        except FileNotFoundError:
            # Log that the file doesn't exist, which is expected on the first run
            logging.info("Stream settings file does not exist.")
        except Exception as e:
            logging.exception("Error in reading the stream settings.")

    def _read(self):
        logging.debug(f"Reading the most recently used stream settings from {self.file_path}")
        if os.path.exists(self.file_path):
            with open(self.file_path, 'r') as jsonfile:
                loaded_data = json.load(jsonfile)
            if isinstance(loaded_data, list) and all(isinstance(item, dict) for item in loaded_data):
                self.config_data = loaded_data
            else:
                # Log a warning and reset config_data to an empty list
                logging.warning("The loaded JSON data is not of the expected format.")
                self.config_data = []

    def _write(self):
        logging.debug(f"Writing the most recently used stream settings in {self.file_path}")
        if self.config_data:
            with open(self.file_path, 'w') as jsonfile:
                json.dump(self.config_data, jsonfile, indent=2)

    @property
    def entries(self):
        return [e["name"] for e in self.config_data]

    def _get_config_index(self, config_data: list, name: str) -> int:
        """
        Get the index of the config entry with the given name
        :param config_data: list of config entries
        :param name: name of config entry
        :return: index of config entry with given name
        """
        try:
            index = [x["name"] for x in config_data].index(name)
        except ValueError:
            return None
        except TypeError:
            return None
        return index

    def update_data(self, new_entries: List[Dict[str, Any]]):
        """
        Update the saved stream setting with the list of new_entries. First element in JSON file
        is the latest entry while the last element is the oldest stream setting. The file updates
        its entries based on the new_entries while maintaining a fixed length of entries in the file.
        Each entry must have a key "name".
        :param new_entries: Each element in the list represent the setting
         of the stream saved as a list from oldest to latest stream setting
        """
        for e in new_entries:
            # find if the new entry name exists in the saved self.config_data
            index = self._get_config_index(self.config_data, e["name"])
            # pop the entry from the list if the stream name is same to avoid duplication
            if index is not None:
                self.config_data.pop(index)
            # insert the new entry at the first position
            self.config_data.insert(0, e)
            # length of list of entries should be constant
            self.config_data = self.config_data[:self.max_entries]

        try:
            self._write()
        except Exception:
            logging.exception(f"Error in writing the stream settings")

    def update_entries(self, streams: list):
        """
        Update the streams settings from the list of streams to update the entries in the JSON file.
        :param streams: (list of streams) List of vigilant attributes of each stream settings
        """
        local_entries = []

        for s in streams:
            settings_order = get_settings_order(s)
            entries = {}
            # set the settings for the stream from a settings json file
            # self.stream_settings.get_stream_settings(self.stream_controllers)
            for attr_name in settings_order:
                # tint requires special handling
                if attr_name == "tint":
                    entries["tint"] = tint_to_md_format(s.tint.value)
                else:
                    entries.update({attr_name: getattr(s, attr_name).value})

            local_entries.append(entries)

        # read and update the stream settings in the JSON file
        self.update_data(local_entries)

    def apply_settings(self, stream: FluoStream, name: str):
        """
        Apply the values from the saved data of the given name to the stream
        controller settings.
        :param stream: FluoStream that needs to be set with the values from the JSON file
        :param name: Name of the saved setting in the JSON file
        """
        index = self._get_config_index(self.config_data, name)
        if index is not None:
            settings_order = get_settings_order(stream)
            prev_setting = self.config_data[index]
            # Follow the order for setting the values as listed in setting_keys
            for key in settings_order:
                current_value = prev_setting[key]  # Get the previous value if it exists
                # tint requires special handling
                if key == "tint":
                    current_value = md_format_to_tint(prev_setting["tint"])
                # Special handling for attributes that need to be converted to tuples
                elif isinstance(current_value, list) and all(isinstance(x, (int, float)) for x in current_value):
                    current_value = tuple(current_value)
                # Set the attribute value
                getattr(stream, key).value = current_value
