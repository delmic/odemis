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
from odemis import model
from odemis.util import spectrum


FORMAT = "CSV"
# list of file-name extensions possible, the first one is the default when saving a file
EXTENSIONS = [u".csv"]

LOSSY = True  # because it only supports AR in phi/theta and spectrum in wavelength/intensity format export
CAN_SAVE_PYRAMID = False


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
    if data.metadata.get(model.MD_ACQ_TYPE, None) == model.MD_AT_SPECTRUM:
        try:
            spectrum_range, unit = spectrum.get_wavelength_per_pixel(data), "nm"
            spectrum_range = [s * 1e9 for s in spectrum_range]
        except Exception:
            # Calculate wavelength in pixels if not given
            max_bw = data.shape[0] // 2
            min_bw = (max_bw - data.shape[0]) + 1
            spectrum_range, unit = range(min_bw, max_bw + 1), "px"
        if data.ndim == 1:
            logging.debug("Exporting spectrum data to CSV")

            if unit == "nm":
                # turn range to nm
                spectrum_tuples = [(s, d) for s, d in zip(spectrum_range, data)]
                headers = ['# wavelength (nm)', 'intensity']
            else:
                logging.info("Exporting spectrum without wavelength information")
                spectrum_tuples = data.reshape(data.shape[0], 1)
                headers = ['# intensity']

            with open(filename, 'w') as fd:
                csv_writer = csv.writer(fd)
                csv_writer.writerow(headers)
                csv_writer.writerows(spectrum_tuples)
        elif data.ndim == 2:
            # FIXME: For now it handles the rest of 2d data as spectrum-line
            logging.debug("Exporting spectrum-line data to CSV")

            # attach wavelength as first column
            wavelength_lin = numpy.array(spectrum_range)
            qz_masked = numpy.append(wavelength_lin.reshape(data.shape[0], 1), data, axis=1)
            # attach distance as first row
            line_length = data.shape[1] * data.metadata[model.MD_PIXEL_SIZE][1]
            distance_lin = numpy.linspace(0, line_length, data.shape[1])
            distance_lin.shape = (1, distance_lin.shape[0])
            distance_lin = numpy.append([[0]], distance_lin, axis=1)
            qz_masked = numpy.append(distance_lin, qz_masked, axis=0)
            data = model.DataArray(qz_masked, data.metadata)

            # Data should be in the form of (Y+1, X+1), with the first row and column the
            # distance_from_origin\wavelength
            with open(filename, 'w') as fd:
                csv_writer = csv.writer(fd)
                # Set the 'header' in the 0,0 element
                first_row = ['wavelength(' + unit + ')\distance_from_origin(m)'] + [d for d in data[0, 1:]]
                csv_writer.writerow(first_row)
                # dump the array
                csv_writer.writerows(data[1:, :])
        else:
            raise IOError("Unknown type of data to be exported as CSV")
    elif data.metadata.get(model.MD_ACQ_TYPE, None) == model.MD_AT_AR:
        logging.debug("Exporting AR data to CSV")
        # Data should be in the form of (Y+1, X+1), with the first row and column the angles
        with open(filename, 'w') as fd:
            csv_writer = csv.writer(fd)
            # Set the 'header' in the 0,0 element
            first_row = ['theta\phi(rad)'] + [d for d in data[0, 1:]]
            csv_writer.writerow(first_row)
            # dump the array
            csv_writer.writerows(data[1:, :])
