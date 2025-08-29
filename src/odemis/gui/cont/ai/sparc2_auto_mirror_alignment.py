# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2025 Nandish Patel, Delmic

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
import threading

import msgpack_numpy
import numpy
import Pyro5.api
from Pyro5.errors import CommunicationError

SERVER_URI = "PYRO:PredictionManager@localhost:5555"

Pyro5.api.config.SERIALIZER = "msgpack"
msgpack_numpy.patch()


class AutoMirrorAlignment:
    """
    Client interface for communicating with the remote PredictionManager Pyro5 server.

    This class manages a thread-safe connection to the prediction server, allowing users to:
      - List available trained models on the server.
      - Submit data for prediction using a specified model.
    """

    def __init__(self, address: str = SERVER_URI):
        """
        Initialize the AutoMirrorAlignment client and connect to the prediction server.

        :param address: The Pyro5 URI of the prediction server.
        """
        self._proxy_access = threading.Lock()
        try:
            self.server = Pyro5.api.Proxy(address)
            self.server._pyroTimeout = 30  # seconds
        except CommunicationError:
            logging.exception(
                "Failed to connect to prediction server '%s'. Check that the "
                "uri is correct and prediction server is"
                " connected to the network." % address
            )

    def list_available_models(self):
        """
        Retrieve a list of available model configurations from the prediction server.

        :returns: Names or identifiers of available models.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.list_available_models()

    def make_prediction(self, model_name: str, image: numpy.ndarray) -> numpy.ndarray:
        """
        Submit image data to the server for prediction using the specified model.

        :param model: The name or identifier of the model to use.
        :param image: The input image data for prediction.

        :returns: The prediction result from the server.
        """
        with self._proxy_access:
            self.server._pyroClaimOwnership()
            return self.server.make_prediction(model_name, image)
