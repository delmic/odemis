#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 2 Jun 2014

@author: Karishma Kumar

Copyright © 2023 Karishma Kumar, Delmic

This is a script to attempt to read Odemis images and convert them into ImageJ compatible format.
The resultant image opens directly in ImageJ and Bio-format plugin in ImageJ can also read the ome data
associated with the image data.

run as:
python3 <PYTHON SCRIPT> <OUTPUT FILE PATH> <IMAGE FILE PATH>
For e.g. python3 /home/dev/development/odemis/scripts/imagej_libtiff.py
        /home/dev/Desktop/meteor_zstack/copies_testing/modified_ome_tiff.ome.tiff
        /home/dev/Desktop/meteor_zstack/copies_testing/20230509-Group2.ome.tiff
"""
import argparse

import numpy
import libtiff

from odemis.dataio.tiff import read_data, _convertToOMEMD


def write_tiff(filename, image_data):
    """
    Write a multiple-channel Z-stack to an ome tiff file using libtiff library.

    :param filename (str): The output TIFF file path.
    :param image_data: (numpy.ndarray | list): 3D array containing the image data. Shape can
     be (num_channels, time_series, num_slices, height, width).
    """
    tif = libtiff.TIFF.open(filename, mode='w')
    num_channels = len(image_data)

    try:
        _, _, num_slices, height, width = image_data[0].shape
    except:
        height, width = image_data[0].shape
        num_slices = 1

    tif.SetField('ImageWidth', width)
    tif.SetField('ImageLength', height)
    tif.SetField('SamplesPerPixel', 1)
    tif.SetField('BitsPerSample', 16)
    tif.SetField('SampleFormat', 1)
    tif.SetField('PlanarConfig', 1)  # Contiguous planar configuration
    tif.SetField('Photometric', 1)  # MinIsBlack

    # Extract pixel size from the metadat
    metadata_all = image_data[0].metadata
    pixel_size_x = metadata_all['Pixel size'][0] / 1e-06
    pixel_size_y = metadata_all['Pixel size'][1] / 1e-06
    tif.SetField('Xresolution', pixel_size_x)
    tif.SetField('Yresolution', pixel_size_y)

    # Add ome data in image description
    ometxt = _convertToOMEMD(image_data)
    description = (f"ImageJ=1.11a\nimages={num_slices * num_channels}\nchannels={num_channels}\nslices={num_slices}\nunit=um\n"
                   f"hyperstack=true\n")#mode=composite\n")
    tif.SetField('ImageDescription', description.encode('ascii')+ometxt)

    # Rearrange the dimensions of image_data[0] [1, T=1, Z, Y, X] -> [Z, C, Y, X]
    for i in range(len(image_data)):
        size = image_data[i].shape
        if size[1] == 1 and len(size) == 5:
            data = numpy.reshape(image_data[i], (size[2], 1, size[3], size[4]))
            if i == 0:
                merged_data = data
            else:
                merged_data = numpy.append(merged_data, data, axis=1)
        else:
            break

    # Write each image plane as a separate tiff file
    for k in range(num_channels):
        if num_slices == 1:
            tif.write_image(image_data[k])
            # Extract the pixel size as it may vary per channel
            metadata_all = image_data[k].metadata
            pixel_size_x = metadata_all['Pixel size'][0] / 1e-06
            pixel_size_y = metadata_all['Pixel size'][1] / 1e-06
            tif.SetField('Xresolution', pixel_size_x)
            tif.SetField('Yresolution', pixel_size_y)
        else:
            for i in range(num_slices):
                tif.write_image(merged_data[i, k, :, :])

    tif.close()


# Example usage:
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ImageJ compatible TIFF formats")
    parser.add_argument(dest="outputpath",
                        help="filepath of the image to be stored")
    parser.add_argument(dest="imagepath",
                        help="filepath of the image to be modified for ImageJ")
    args = parser.parse_args()
    # Read Odemis images
    ometiff_filename = args.imagepath
    image_data = read_data(ometiff_filename)
    # Output file path to save ImageJ compatible file
    file_path = args.outputpath

    # Call the function to save the TIFF file
    write_tiff(file_path, image_data)
