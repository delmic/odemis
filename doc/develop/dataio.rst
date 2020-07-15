****************************************
Acquisition data storage and file format
****************************************


Data representation and basic manipulation in Python
====================================================

Within Python, all the Odemis data is represented as a :py:class:`DataArray`.
Essentially, this is a standard numpy array with an extra attribute ``.metadata``
which holds information about the acquisition, such as the pixel size, the
date of acquisition, or the camera gain. By convention, each array can have up
to 5 dimensions, which are ``C`` (wavelength), ``T`` (time), ``Z``, ``Y``, and ``X``
(space). Note that order, which is opposite to the most usual one. If an array
has less dimensions, it is considered that the first ones are omitted.

For example, if a DataArray has a shape of ``(3, 1, 1, 2000, 2000)``, it means
that it has data of 2000 x 2000 px in the XY plane, and over 3 wavelengths.
A DataArray of shape ``(512x256)`` would mean a grayscale image of 256 x 512 px
(in the XY plane). See :ref:`data-and-metadata` for more information.

Storing and reading data
------------------------

The ``dataio`` module provides simple function to store and read back data in
Python. For each supported file format (currently, OME-TIFF and HDF5), there is
a dedicated submodule (respectively, dataio.tiff, and dataio.hdf5). Each module
provides four functions:

.. py:function:: export(filename, data, thumbnail=None)

    Write a file with the given image and metadata

    :param unicode filename: filename of the file to create (including path)
    :param data: the data to export, 
        must be 2D or more of int or float. Metadata is taken directly from the data 
        object. If it's a list, a multiple page file is created. The order of the
        dimensions is Channel, Time, Z, Y, X. It tries to be smart and if 
        multiple data appears to be the same acquisition at different C, T, Z, 
        they will be aggregated into one single acquisition.
    :type data: list of model.DataArray, or model.DataArray
    :param thumbnail: (optional) Image used as thumbnail for the file. Can be of any
      (reasonable) size. Must be either 2D array (greyscale) or 3D with last 
      dimension of length 3 (RGB). If the exporter doesn't support it, it will
      be dropped silently.
    :type thumbnail: None or model.DataArray

.. py:function:: read_data(filename)

    Read a file and return its content (skipping the thumbnail).
    
    :param unicode filename: filename of the file to read
    :returns: the data to import (with the metadata as .metadata).
     It might be empty.
    :rtype: list of model.DataArray
    :raises IOError: in case the file format is not as expected.

.. py:function:: read_thumbnail(filename)

    Read the thumbnail data of a given file.

    :param unicode filename: filename of the file to read
    :return: the thumbnails attached to the file. 
     If the file contains multiple thumbnails, all of them are returned.
     If it contains none, an empty list is returned.
    :rtype: list of model.DataArray
    :raises IOError: in case the file format is not as expected.

.. py:function:: open_data(filename)

    Parses a file, and provides a way to read it via an AcquisitionData instance.
    This function is optional (and currently only provided by the tiff module).
    It provides the same functionality as ``read_data()`` and ``read_thumbnail()``,
    but it doesn't actually load the data in memory. The data is only loaded
    when requested via the ``AcquisitionData.content[].getData()`` or ``.getTile()``
    methods.

    :param unicode filename: path to the file
    :returns: an opened file
    :rtype: AcquisitionData

.. TODO: describe the helper functions of dataio and util.dataio
.. TODO: describe AcquisitionData class

Example usage
-------------

To store data (from two DataArrays ``da0`` and ``da1``) in HDF5 format,
one could write:

.. code-block:: python

   from odemis.dataio import hdf5
   hdf5.export("path/to/the.h5", [da0, da1])

To read data from an OME-TIFF file, one could write:

.. code-block:: python

   from odemis.dataio import tiff
   from odemis import model
   das = tiff.read_data("path/to/my.ome.tiff")
   # das is the list of DataArrays from the acquisition
   print(das[0].metadata[model.MD_PIXEL_SIZE])
   print(das[0].metadata[model.MD_LENS_MAG])
   print(das[0].metadata)

Image position metadata
=======================
Most of the data stored corresponds to "spatial" data, i.e., an image representing
the sample with axes in X and Y. In order to ensure a perfect overlay between data from
different acquisition types, the metadata describes precisely how the image should
be positioned. The following metadata has influence:

* ``MD_PIXEL_SIZE``: size of a pixel (in m) in X and Y. In other words, this is the scale.
* ``MD_POS``: the position (in m) of the *center of the image* in X and Y. In other words, this is the translation.
* ``MD_ROTATION``: counter-clockwise rotation (in radians) applied to the image from its center
* ``MD_SHEAR``: *vertical* shear

All values default to 0, excepted for the ``MD_PIXEL_SIZE`` which is required.

Note that during acquisition, all these metadata have a twin-brother named with an extra
`_COR` in order to record the correction on the image display. The function ``util.img.mergeMetadata()``
can be used to merge these corrections into the main metadata. Before saving data
to a file, the correction is automatically merged. So typically, after opening a
file, the data will not have any of these extra correction metadata.

When converting from pixel coordinates to "physical" coordinates (in meters), the
first thing to pay attention is that pixel coordinates are "left-handed": the Y
axis goes from the "top of the screen" to the "bottom" (following the convention
in computer software). On the opposite, physical coordinates in Odemis are "right-handed".
The Y axis increases while going towards the top of the screen (following the convention
used in mathematics and physics).

For a given pixel situated at coordinates *p = i, j* in an image of size *sx, sy*,
its "physical" position *P = x, y* (in meters) can be computed by first computing
*pc = [i - sx / 2, -(j - sy / 2)]* and then applying the following formula *P = RSLpc + T*,
where *R* is the rotation, *S* is the scale, *L* is the shear, and *T* is the translation.

In Python, this can be done with:

.. code-block:: python

   from odemis import model
   from odemis.util.transform import AffineTransform

   img_size = da.shape[-1], da.shape[-2]
   pxs = da.metadata[model.MD_PIXEL_SIZE]
   translation = da.metadata.get(model.MD_POS, (0, 0))
   rotation = da.metadata.get(model.MD_ROTATION, 0)
   shear = da.metadata.get(model.MD_SHEAR, 0)

   tform = AffineTransform(rotation=rotation, scale=pxs, translation=translation)
   # Shear is computed apart because the AffineTransform uses a horizontal shear
   L = numpy.array([(1, 0), (-shear, 1)])
   tform.transformation_matrix = numpy.dot(tform.transformation_matrix, L)

   pc = (p[0] - img_size[0] / 2), -(p[1] - img_size[1] / 2)
   P = tform(pc)

   # To convert back to pixel coordinates
   pc = tform.inverse()(P)
   p = (pc[0] + img_size[0] / 2), -(pc[1] - img_size[1] / 2)


OME-TIFF
========
It attempts to follow the OME specification, as `defined by the Open Microscopy
Environment <https://docs.openmicroscopy.org/ome-model/5.6.4/>`_.

The actual data is stored according to the `TIFF v6 specification <https://www.itu.int/itudoc/itu-t/com16/tiff-fx/docs/>`_.
The basic metadata is (also) stored as standard TIFF metadata, which is well
`documented here <https://www.awaresystems.be/imaging/tiff/tifftags.html>`_.

As defined by OME, the metadata is actually stored in XML format in the description
tag of the first TIFF page. The exact `XML schema can be found here 
<http://www.openmicroscopy.org/Schemas/Documentation/Generated/OME-2016-06/ome.html>`_.

.. TODO: describe in more details. Especially, the pyramidal format, and the OME extensions (eg for polarymetry, AR)

HDF5
====
The HDF5 format is defined by the `HDF group <https://support.hdfgroup.org/HDF5/doc/index.html>`_.

The data organisation and the metadata storage follow the `convention defined by SVI 
<https://svi.nl/HDF5>`_.

.. TODO: describe in more details. Especially, extensions (eg for polarymetry, AR)
