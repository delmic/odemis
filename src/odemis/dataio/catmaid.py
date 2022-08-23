# -*- coding: utf-8 -*-
"""
Created on 18 Feb 2019

@author: Thera Pals

Copyright © 2019 Thera Pals, Delmic

The functions reponse_to_array and format_tile_url are based on code in catpy, https://github.com/catmaid/catpy.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""
import configparser
import logging
import math
import numpy
import re
import requests
from future.moves.urllib.parse import urlparse, parse_qs

from PIL import Image
from io import BytesIO
from requests import HTTPError
from requests.auth import HTTPBasicAuth

from odemis import model
from odemis.dataio import AuthenticationError
from odemis.model import AcquisitionData, DataArrayShadow
from odemis.util.conversion import get_tile_md_pos


# User-friendly name
FORMAT = "Catmaid"
# list of file-name extensions possible, the first one is the default when saving a file
EXTENSIONS = []
PREFIXES = [u"catmaid://", u"catmaids://"]

LOSSY = True

SUPPORTED_CONTENT_TYPES = {
    'image/png',
    'image/jpeg'
}

# A config file should contain the token, username and password corresponding to a Catmaid instance.
# through catmaid admin interface click Auth token and then Add.
# The file has the base URL without trailing slash as section name.
# The section contains a token, username and password:
# [http://localhost:8000]
# token = 1234567890
# username = my_username
# password = my_password
#
# [http://catmaid.neurodata.io/catmaid]
# token = 1234567890
# username = my_username
# password = my_password
#
# The file can also be created with the following code:
# >>> config = configparser.ConfigParser()
# >>> config.add_section(base_url)
# >>> config.set(base_url, 'token', token)
# >>> config.set(base_url, 'username', username)
# >>> config.set(base_url, 'password', password)
# >>> with open(KEY_PATH, 'w') as configfile:
# >>>     config.write(configfile)

KEY_PATH = "~/.local/share/odemis/catmaid.key"

# Tile Source Types
FILE_BASED = 1
REQUEST_QUERY = 2
HDF5 = 3
FILE_BASED_WITH_ZOOM_DIRS = 4
DIR_BASED = 5
DVID_IMAGEBLK = 6
RENDER_SERVICE = 7
DVID_IMAGETILE = 8
FLIXSERVER = 9
H2N5_TILES = 10


def open_data(url):
    """
    Opens a Catmaid URL, and returns an AcquisitionData instance
    url (string): URL where the Catmaid instance is hosted. Project id and stack id can be added to the URL by appending
        ?pid=1&sid0=1 , if this left out the first available project id and stack id are used. The URL should start
        with catmaid:// or catmaids:// for http and https protocols respectively.
    return (AcquisitionData): an opened Catmaid instance
    """
    if any(url.startswith(p) for p in PREFIXES):
        url = re.sub(r"catmaid([s]?)://", r"http\1://", url, 1)
    else:
        raise IOError("URL should start with catmaid:// or catmaids://")
    urlo = urlparse(url)
    parameters = parse_qs(urlo.query)
    base_url = '{uri.scheme}://{uri.netloc}{uri.path}'.format(uri=urlo).rstrip("/")
    project_id = int(parameters["pid"][0]) if "pid" in parameters.keys() else None
    stack_id = int(parameters["sid0"][0]) if "sid0" in parameters.keys() else None

    if project_id is None or stack_id is None:
        try:
            token, _, _ = read_config_file(base_url, token=True)
            auth = CatmaidApiTokenAuth(token) if token else None
            project_url = "{url}/projects/".format(url=base_url)
            response = requests.get(project_url, auth=auth)
            if response.status_code == 401:
                raise AuthenticationError("Wrong token while getting project info at {}".format(project_url))
            else:
                response.raise_for_status()
            project_info = response.json()
            if project_info == [] and token is None:
                raise AuthenticationError(
                    "No project at {}, this Catmaid instance does not contain projects"
                    "or a token should have been provided.".format(project_url))
            project_id = project_info[0]["id"] if project_id is None else project_id
            # loop through the projects to get the info of the project matching the project id.
            project = [p for p in project_info if p["id"] == project_id]
            stack_id = project[0]["stacks"][0]["id"] if stack_id is None else stack_id
        except Exception:
            # Try with project id and stack id is 1 if the project info cannot be accessed.
            project_id = 1 if project_id is None else project_id
            stack_id = 1 if stack_id is None else stack_id
        logging.info(
            "Project id and/or stack id not entered, using project {} and stack {}.".format(project_id, stack_id))
    return AcquisitionDataCatmaid(base_url, project_id, stack_id)


class DataArrayShadowPyramidalCatmaid(DataArrayShadow):
    """
    This class implements the read of a Pyramidal Catmaid instance.
    """

    def __init__(self, stack_info, base_url):
        """
        Constructor
        stack_info (dict): information about the Catmaid stack and tiles in the stack.
        base_url (str): URL where the Catmaid instance is hosted.
        dtype (numpy.dtype): the data type
        metadata (dict str->val): The metadata
        """
        shape = (stack_info["dimension"]["x"], stack_info["dimension"]["y"])
        tile_shape = (stack_info["mirrors"][0]["tile_width"], stack_info["mirrors"][0]["tile_height"])
        # resolution is given in nanometers by catmaid.
        resolution = {k: v * 1e-9 for k, v in stack_info["resolution"].items()}
        metadata = {model.MD_PIXEL_SIZE: (resolution["x"], resolution["y"])}
        dtype = numpy.uint8

        maxzoom = stack_info.get("num_zoom_levels", -1)
        if maxzoom == -1:
            # if num_zoom_levels is -1 the maxzoom must be estimated by using: 2**maxzoom = max_dim / min_size
            # Rewriting this results in maxzoom = log(max_dim / min_size) / log(2)
            max_dim = max(shape)
            min_size = min(tile_shape)
            maxzoom = int(math.ceil(math.log(max_dim / min_size, 2)))

        DataArrayShadow.__init__(self, shape, dtype, metadata, maxzoom=maxzoom, tile_shape=tile_shape)

        self._base_url = base_url
        self._session = requests.Session()
        _, username, password = read_config_file(self._base_url, username=True, password=True)
        self._auth = (username, password)
        self._stack_info = stack_info
        file_extension = self._stack_info["mirrors"][0]["file_extension"]
        self._file_extension = file_extension[1:] if file_extension.startswith(".") else file_extension

    def getTile(self, x, y, zoom, depth=0):
        """
        Fetches one tile
        x (0<=int): X index of the tile.
        y (0<=int): Y index of the tile
        zoom (0<=int): zoom level to use. The total shape of the image is shape / 2**zoom.
            The number of tiles available in an image is ceil((shape//zoom)/tile_shape)
        depth (0<=int): The Z index of the stack.
        return:
            tile (DataArray): tile containing the image data and the relevant metadata.
        """
        tile_width, tile_height = self.tile_shape
        tile_url = format_tile_url(
            tile_source_type=self._stack_info["mirrors"][0]["tile_source_type"],
            image_base=self._stack_info["mirrors"][0]["image_base"],
            zoom=zoom,
            depth=depth,
            col=x,
            row=y,
            file_extension=self._file_extension,
            tile_width=tile_width,
            tile_height=tile_height,
        )
        try:
            image = response_to_array(self._session.get(tile_url, auth=self._auth))
        except HTTPError as e:
            if e.response.status_code == 401:
                raise AuthenticationError("Authentication failed while getting tiles at {}".format(tile_url))
            else:
                logging.error("No tile at %s (error %s), returning blank tile", tile_url, e.response.status_code)
                image = numpy.zeros((tile_width, tile_height), dtype=self.dtype)

        tile = model.DataArray(image, self.metadata.copy())
        orig_pixel_size = self.metadata.get(model.MD_PIXEL_SIZE, (1e-6, 1e-6))
        # calculate the pixel size of the tile for the zoom level
        tile_pixel_size = tuple(ps * 2 ** zoom for ps in orig_pixel_size)
        tile.metadata[model.MD_PIXEL_SIZE] = tile_pixel_size

        # calculate the center of the tile
        tile.metadata[model.MD_POS] = get_tile_md_pos((x, y), self.tile_shape, tile, self)

        return tile

    def getData(self):
        """Abstract method of DataArrayShadow"""
        raise NotImplementedError()


class AcquisitionDataCatmaid(AcquisitionData):
    """
    Implements AcquisitionData for Catmaid instances
    """

    def __init__(self, base_url, project_id, stack_id):
        """
        Constructor
        base_url (str): URL where the Catmaid instance is hosted.
        project_id (int): project id, indicating which of the projects to use at this Catmaid instance.
        stack_id (int): stack id, indicating which of the stacks to use at this Catmaid instance.
        """
        stack_info = get_stack_info(base_url, project_id, stack_id)
        data = [DataArrayShadowPyramidalCatmaid(stack_info, base_url)]
        AcquisitionData.__init__(self, tuple(data))


TILE_URLS = {
    FILE_BASED: '{image_base}{depth}/{row}_{col}_{zoom}.{file_extension}',
    FILE_BASED_WITH_ZOOM_DIRS: '{image_base}{depth}/{zoom}/{row}_{col}.{file_extension}',
    DIR_BASED: '{image_base}{zoom}/{depth}/{row}/{col}.{file_extension}',
    RENDER_SERVICE: '{image_base}largeDataTileSource/{tile_width}/{tile_height}/{zoom}/'
                    '{depth}/{row}/{col}.{file_extension}',
    FLIXSERVER: '{image_base}{depth}/{row}_{col}_{zoom}.{file_extension}',
}


def format_tile_url(tile_source_type, image_base, zoom, depth, row, col, file_extension, tile_width, tile_height):
    """
    Format a URL that can be used to request a tile from the server.
    https://catmaid.readthedocs.io/en/stable/tile_sources.html

    tile_source_type (int): A number indicating which tile source type to use for this project.
    image_base (str): URL where the images are located.
    zoom (0<=int): zoom level to use.
    depth (0<=int): The Z index of the stack.
    row (0<=int): Y index of the tile.
    col (0<=int): X index of the tile.
    file_extension (str): File extension of the image.
    tile_width (int): Width of the tiles in pixels.
    tile_height (int): Height of the tiles in pixels.
    return:
        tile_url (str): URL where the tile is located.
    """

    try:
        tile_url = TILE_URLS[tile_source_type]
    except KeyError:
        raise LookupError("Tile Source Type {} is not supported".format(tile_source_type))
    return tile_url.format(image_base=image_base, zoom=zoom, depth=depth, row=row, col=col,
                           file_extension=file_extension, tile_width=tile_width, tile_height=tile_height)


def response_to_array(response):
    """
    response (Response): http response for the requested image.
    return:
       image (numpy array): the requested image from the response.
    """
    response.raise_for_status()
    content_type = response.headers['Content-Type']

    if content_type in SUPPORTED_CONTENT_TYPES:
        buffer = BytesIO(response.content)  # opening directly from raw response doesn't work for JPEGs
        raw_img = Image.open(buffer).convert('L')
        return numpy.array(raw_img)
    else:
        raise ValueError('Image fetching is only implemented for greyscale PNG and JPEG, not {}'.format(
            content_type.upper().split('/')[1]))


STACK_URL = "{base_url}/{project_id}/stack/{stack_id}/info"


def get_stack_info(base_url, project_id, stack_id):
    """Get the stack info for a project hosted at the base url."""
    token, _, _ = read_config_file(base_url, token=True)
    stack_url = STACK_URL.format(base_url=base_url, project_id=project_id, stack_id=stack_id)
    auth = CatmaidApiTokenAuth(token) if token else None
    response = requests.get(stack_url, auth=auth)
    # A PermissionError is raised when authentication fails.
    if response.status_code == 404:
        raise ValueError("no stack info at this url, check the base url {base_url}".format(base_url=base_url))
    elif "PermissionError" in response.text:
        raise AuthenticationError("Failed authentication getting stack info at {stack_url}".format(stack_url=stack_url))
    elif "DoesNotExist" in response.text:
        # Catmaid returns a 200 code with an error text field, when there's no stack info at that url.
        raise ValueError(
            "No stack info at this url {stack_url}, check project id {project_id} and stack id {stack_id}".format(
                stack_url=stack_url, project_id=project_id, stack_id=stack_id))
    stack_info = response.json()
    return stack_info


class CatmaidApiTokenAuth(HTTPBasicAuth):
    """Attaches HTTP X-Authorization Token headers to the given Request.
    Optionally, Basic HTTP Authentication can be used in parallel.
    """

    def __init__(self, token, username=None, password=None):
        super(CatmaidApiTokenAuth, self).__init__(username, password)
        self.token = token

    def __call__(self, r):
        r.headers['X-Authorization'] = 'Token {}'.format(self.token)
        if self.username and self.password:
            super(CatmaidApiTokenAuth, self).__call__(r)
        return r


def read_config_file(base_url, token=False, username=False, password=False):
    """
    Read a config file and return the token, username and password corresponding to the given base_url.

    base_url (str): URL where the Catmaid instance is hosted.
    token (bool): set to True if the token should be returned, if False None is returned for the token
    username (bool): set to True if the token should be returned, if False None is returned for the username
    password (bool): set to True if the token should be returned, if False None is returned for the password
    return:
        token: the token from the config file
        username: the username from the config file
        password: the password from the config file
    """
    config = configparser.ConfigParser()
    config.read(KEY_PATH)
    if not config.has_section(base_url):
        logging.debug("No authentication configuration found for this url {url}.".format(url=base_url))
        return None, None, None
    token = config.get(base_url, "token") if token else None
    username = config.get(base_url, "username") if username else None
    password = config.get(base_url, "password") if password else None
    return token, username, password
