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
import math
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
        ValueError in case the spectrum does not contain wavelength metadata.
    '''
    if data.metadata.get(model.MD_ACQ_TYPE, None) == model.MD_AT_SPECTRUM:
        dims = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim:])
        if dims == "C" and data.ndim == 1:
            spectrum_range, unit = spectrum.get_spectrum_range(data)
            if unit == "m":
                spectrum_range = [s * 1e9 for s in spectrum_range]
                unit = "nm"

            logging.debug("Exporting spectrum data to CSV")

            if unit == "nm":
                # turn range to nm
                spectrum_tuples = [(s, d) for s, d in zip(spectrum_range, data)]
                headers = ['# wavelength (nm)', 'intensity']
            else:
                logging.info("Exporting spectrum without wavelength information")
                spectrum_tuples = data.reshape(data.shape[0], 1)
                headers = ['# intensity']

            with open(filename, 'w', newline='') as fd:
                csv_writer = csv.writer(fd)
                csv_writer.writerow(headers)
                csv_writer.writerows(spectrum_tuples)

        elif dims == "T" and data.ndim == 1:
            logging.debug("Exporting chronogram data to CSV")
            time_range, unit = spectrum.get_time_range(data)
            if unit == "s":
                # Adjust range values to ps
                time_range = [t * 1e12 for t in time_range]
                unit = "ps"
                time_tuples = [(s, d) for s, d in zip(time_range, data)]
                headers = ['# Time (ps)', 'intensity']
            else:
                logging.info("Exporting chronogram without time list information")
                time_tuples = data.reshape(data.shape[0], 1)
                headers = ['# intensity']

            with open(filename, 'w', newline='') as fd:
                csv_writer = csv.writer(fd)
                csv_writer.writerow(headers)
                csv_writer.writerows(time_tuples)

        elif data.ndim == 2 and dims == "XC":
            logging.debug("Exporting spectrum-line data to CSV")

            spectrum_range, unit = spectrum.get_spectrum_range(data)
            if unit == "m":
                spectrum_range = [s * 1e9 for s in spectrum_range]
                unit = "nm"

            # attach distance as first row
            line_length = data.shape[0] * data.metadata[model.MD_PIXEL_SIZE][1]
            distance_lin = numpy.linspace(0, line_length, data.shape[0])
            distance_lin.shape = (distance_lin.shape[0], 1)
            data = numpy.append(distance_lin, data, axis=1)

            # Data should be in the form of (X, C+1), with the first row and column the
            # distance_from_origin\wavelength
            with open(filename, 'w', newline='') as fd:
                csv_writer = csv.writer(fd)
                # Set the 'header' in the 0,0 element
                first_row = ['distance_from_origin(m)\\wavelength(' + unit + ')'] + spectrum_range
                csv_writer.writerow(first_row)
                # dump the array
                csv_writer.writerows(data)
        else:
            raise ValueError("Unknown type of data to be exported as CSV")
        
    elif data.metadata.get(model.MD_ACQ_TYPE, None) == model.MD_AT_TEMPSPECTRUM:
        logging.debug("Exporting temporal spectrum data to CSV")
        spectrum_range, unit_c = spectrum.get_spectrum_range(data)
        if unit_c == "m":
            spectrum_range = [s * 1e9 for s in spectrum_range]
            unit_c = "nm"
        time_range, unit_t = spectrum.get_time_range(data)
        if unit_t == "s":
            time_range = [t * 1e12 for t in time_range]
            unit_t = "ps"

        headers = ["time(" + unit_t + ")\\wavelength(" + unit_c + ")"] + spectrum_range
        rows = [(t,) + tuple(d) for t, d in zip(time_range, data)]

        with open(filename, 'w', newline='') as fd:
            csv_writer = csv.writer(fd)
            csv_writer.writerow(headers)
            csv_writer.writerows(rows)

    elif data.metadata.get(model.MD_ACQ_TYPE, None) == model.MD_AT_AR:
        logging.debug("Exporting AR data to CSV")
        # Data should be in the form of (Y+1, X+1), with the first row and column the angles
        with open(filename, 'w', newline='') as fd:
            csv_writer = csv.writer(fd)

            # TODO: put theta/phi angles in metadata? Read back from MD theta/phi and then add as additional line/column
            # add the phi and theta values as an extra line/column in order to be displayed in the csv-file
            # attach theta as first column
            theta_lin = numpy.linspace(0, math.pi / 2, data.shape[0])
            data = numpy.append(theta_lin.reshape(theta_lin.shape[0], 1), data, axis=1)
            # attach phi as first row
            phi_lin = numpy.linspace(0, 2 * math.pi, data.shape[1]-1)
            phi_lin = numpy.append([[0]], phi_lin.reshape(1, phi_lin.shape[0]), axis=1)
            data = numpy.append(phi_lin, data, axis=0)

            # Set the 'header' in the 0,0 element
            first_row = ['theta\\phi[rad]'] + [d for d in data[0, 1:]]
            csv_writer.writerow(first_row)
            # dump the array
            csv_writer.writerows(data[1:, :])
            
    else:
        raise ValueError("Unknown acquisition type of data to be exported as CSV")
