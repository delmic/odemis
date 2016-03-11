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
import numpy
from odemis import model


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
    if (model.MD_DESCRIPTION in data.metadata) and data.metadata[model.MD_DESCRIPTION] == "Angle-resolved":
        # In case of AR data just dump the array in the csv file
        with open(filename, 'w') as fd:
            csv_writer = csv.writer(fd)
            csv_writer.writerows(data)
    else:
        if not hasattr(data, "metadata"):
            spectrum_range = None
        elif model.MD_WL_POLYNOMIAL in data.metadata:
            wl_polynomial = data.metadata[model.MD_WL_POLYNOMIAL]
            spectrum_range = numpy.arange(wl_polynomial[0], wl_polynomial[0] + len(data) * wl_polynomial[1], wl_polynomial[1])
        elif model.MD_WL_LIST in data.metadata:
            spectrum_range = data.metadata[model.MD_WL_LIST]
        else:
            # corner case where spectrum range is not available in metadata
            spectrum_range = None
            print data.metadata

        # turn range to nm
        headers = ['#intensity']
        spectrum_tuples = data.reshape(data.shape[0], 1)
        if spectrum_range is not None:
            spectrum_tuples = zip(spectrum_range * 1e09, data)
            headers = ['#wavelength(nm)'] + headers
        with open(filename, 'w') as fd:
            csv_writer = csv.writer(fd)
            csv_writer.writerow(headers)
            csv_writer.writerows(spectrum_tuples)
