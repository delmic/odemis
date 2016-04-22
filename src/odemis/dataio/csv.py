# -*- coding: utf-8 -*-
'''
Created on 17 Feb 2016

@author: Kimon Tsitsikas

Copyright © 2016 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import absolute_import, division

import csv
import logging
import numpy
from odemis.util import spectrum


FORMAT = "CSV"
# list of file-name extensions possible, the first one is the default when saving a file
EXTENSIONS = [u".csv"]

LOSSY = True  # because it only supports AR in phi/theta and spectrum in wavelength/intensity format export


def export(filename, data):
    '''
    Write a CSV file:
        - If the given data is AR data then just dump the phi/data array
        - If the given data is spectrum data write it as series of wavelength/intensity
    filename (unicode): filename of the file to create (including path).
    data (model.DataArray): the data to export.
       Metadata is taken directly from the DA object.
    raises:
        IOError in case the spectrum does not contain wavelength metadata.
    '''
    if data.shape[0] > 1 and numpy.prod(data.shape) == data.shape[0]:
        logging.debug("Exporting spectrum data to CSV")
        try:
            spectrum_range = spectrum.get_wavelength_per_pixel(data)
        except Exception:
            # corner case where spectrum range is not available in metadata
            logging.info("Exporting spectrum without wavelength information")
            spectrum_range = None

        if spectrum_range is not None:
            # turn range to nm
            spectrum_tuples = [(s * 1e9, d) for s, d in zip(spectrum_range, data)]
            headers = ['# wavelength (nm)', 'intensity']
        else:
            spectrum_tuples = data.reshape(data.shape[0], 1)
            headers = ['# intensity']

        with open(filename, 'w') as fd:
            csv_writer = csv.writer(fd)
            csv_writer.writerow(headers)
            csv_writer.writerows(spectrum_tuples)
    elif data.ndim == 2 and all(s >= 2 for s in data.shape):
        logging.debug("Exporting AR data to CSV")
        # Data should be in the form of (Y+1, X+1), with the first row and colum the angles
        with open(filename, 'w') as fd:
            csv_writer = csv.writer(fd)
            # Set the 'header' in the 0,0 element
            first_row = ['theta\phi(rad)'] + [d for d in data[0, 1:]]
            csv_writer.writerow(first_row)
            # dump the array
            csv_writer.writerows(data[1:, :])
