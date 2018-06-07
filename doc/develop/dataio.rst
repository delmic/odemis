****************************************
Data formats
****************************************

hdf5
=======

Extension names possible: .h5 and .hdf5.


We are trying to follow the same format as SVI, as defined here:
http://www.svi.nl/HDF5

**Structure**

A file follows this structure:

**Groups**

1.  Preview (this is our extension, to contain thumbnails)

    *  RGB image (HDF5 Image with Dimension Scales)
    *  DimensionScale (x, y??)
    *  *Offset (position on the axis: x,y,z??)
    *  Shear
    *  Rotation
.. TODO asterix for optional??

2.  AcquisitionName (one per set of emitter/detector??? example? always see Acquisition plus number)

    *  ImageData
    *  PhysicalData
    *  SVIData (Not necessary for us)
.. TODO why  not necessary? delmic is mentioned


Each file contains a separate group (HDF Group)??? for each image acquired.

1. Acquisition0: This group is always the SEM "survey" image???
2. Acquisition1 to n: Those groups are images recorded with the optical setup???

There are three sub-groups of metadata (HDF Group) associated with each image within the acquisition file.

1.  "ImageData"
2.  "PhysicalData"
3.  "SVIData

.. TODO what is "StateEnumeration ?

**ImageData**
This group contains following metadata (*: optional metadata depending on hardware):

    *  Image: HDF5 image with dimension scales CTZXY [data array?]
    *  DimensionScale: TZYX??? [??]
    *  *Offset: relative position on the axis ?? [??]


**PhysicalData**
This group contains following metadata (*: optional metadata depending on hardware):

    *   MicroscopeMode: mode of the microscope (e.g.......???) [str]
    *   *Polarization: position of the polarization analyzer consisting of quarter wave plate and linear polarizer [str]
    *   *QuarterWavePlate: physical position of the quarter wave plate [rad]
    *   *LinearPolarizer: physical position of the linear polarizer [rad]

**SVIData**
This group contains following metadata (*: optional metadata depending on hardware):

    *   Company (default: delmic)
    *   FileSpecificationCompatibility
    *   FileSpecificationVersion
    *   ImageHistory
    *   URL (default: www.delmic.com)


Image is an official extension to HDF5:
http://www.hdfgroup.org/HDF5/doc/ADGuide/ImageSpec.html

TODO: Document all our extensions (Preview, AR images, Rotation...)

Angular Resolved images (acquired by the SPARC) are recorded such as:
* Each CCD image is a separate acquisition, containing the raw data
* For each acquisition, the offset contains the position of the ebeam
* For each acquisition, a PhysicalData/PolePosition contains the X,Y
  coordinates (in px) of the mirror pole on the raw data.

Rotation information is saved in ImageData/Rotation as a series of floats
(of 3 or more dimensions, corresponding to X, Y, Z dimensions). It represents
the rotation vector (with right-hand rule). See the wikipedia article for
details. It's basically a vector which represents the plan of rotation by its
direction, and the angle (in rad) by its norm. The rotation is always applied
on the center of the data. For example, to rotate a 2D image by 0.7 rad
counter clockwise, the rotation vector would be 0, 0, 0.7

Data is normally always recoded as 5 dimensions in order CTZYX. One exception
is for the RGB (looking) data, in which case it's recorded only in 3
dimensions, CYX (that allows to easily open it in hdfview).

h5py doesn't implement explicitly HDF5 image, and is not willing to cf:
http://code.google.com/p/h5py/issues/detail?id=157




read hdf5
---------------------------------------

write hdf5
----------------------------------------

def _create_image_dataset(group, dataset_name, image, **kwargs):
    """
    Create a dataset respecting the HDF5 image specification
    http://www.hdfgroup.org/HDF5/doc/ADGuide/ImageSpec.html

    group (HDF group): the group that will contain the dataset
    dataset_name (string): name of the dataset
    image (numpy.ndimage): the image to create. It should have at least 2 dimensions
    returns the new dataset

def _read_image_dataset(dataset):
    """
    Get a numpy array from a dataset respecting the HDF5 image specification.
    returns (numpy.ndimage): it has at least 2 dimensions and if RGB, it has
     a 3 dimensions and the metadata MD_DIMS indicates the order.
    raises
     IOError: if it doesn't conform to the standard
     NotImplementedError: if the image uses so fancy standard features
    """

def _add_image_info(group, dataset, image):
    """
    Adds the basic metadata information about an image (scale, offset, and rotation)
    group (HDF Group): the group that contains the dataset
    dataset (HDF Dataset): the image dataset
    image (DataArray >= 2D): image with metadata, the last 2 dimensions are Y and X (H,W)
    """
    # Time
    # Surprisingly (for such a usual type), time storage is a mess in HDF5.
    # The documentation states that you can use H5T_TIME, but it is
    # "is not supported. If H5T_TIME is used, the resulting data will be readable
    # and modifiable only on the originating computing platform; it will not be
    # portable to other platforms.". It appears many format are allowed.
    # In addition in h5py, it's indicated as "deprecated" (although it seems
    # it was added in the latest version of HDF5).
    # Moreover, the only types available are 32 and 64 bits integers as number
    # of seconds since epoch. No past, no milliseconds, no time-zone.
    # So there are other proposals like in in F5
    # (http://sciviz.cct.lsu.edu/papers/2007/F5TimeSemantics.pdf) to represent
    # time with a float, a unit and an offset.
    # KNMI uses a string like this: DD-MON-YYYY;HH:MM:SS.sss.
    # (cf http://www.knmi.nl/~beekhuis/documents/publicdocs/ir2009-01_hdftag36.pdf)
    # So, to not solve anything, we save the date as a float representing the
    # Unix time. At least it makes Huygens happy.
    # Moreover, in Odemis we store two types of time:
    # * MD_ACQ_DATE, which is the (absolute) time at which the acquisition
    #   was performed. It's stored in TOffset as a float of s since epoch.
    # * MD_TIME_OFFSET, which is the (relative) time of the first element of
    #   the time dimension compared to the acquisition event (eg, energy
    #   release on the sample). It's stored in the TOffsetRelative in s.
    # Finally, there is MD_PIXEL_DUR which is the duration between each
    # element on the time dimension scale.
    # TODO: in retrospective, it would have been more logical to store the
    # relative time in TOffset, and the acquisition date (which is not essential
    # to the data) in PhysicalData/AcquisitionDate.


def _read_image_info(group):
    """
    Read the basic metadata information about an image (scale and offset)
    group (HDF Group): the group "ImageData" that contains the image (named "Image")
    return (dict (MD_* -> Value)): the metadata that could be read
    """
    # Wavelength is only if the data has a C dimension and it has two numbers
    # that represent the range of the monochromator bandwidth or the offset and
    # scale (linear polynomial) or it has a list of wavelengths (one per pixel).
    # To distinguish between polynomial and monochromator wavelength we just
    # check if the shape of the dataset equals to 1, which implies single-pixel
    # data coming from the monochromator.
    # Note that not all data has information, for example RGB images, or
    # fluorescence images have no scale (but the SVI flavour has several
    # metadata related in the PhysicalData group).


def _parse_physical_data(pdgroup, da):
    """
    Parse the metadata found in PhysicalData, and cut the DataArray if necessary.
    pdgroup (HDF Group): the group "PhysicalData" associated to an image
    da (DataArray): the DataArray that was obtained by reading the ImageData
    returns (list of DataArrays): The same data, but broken into smaller
      DataArrays if necessary, and with additional metadata.
    """
    # The information in PhysicalData might be different for each channel (e.g.
    # fluorescence image). In this case, the DA must be separated into smaller
    # ones, per channel.
    # For now, we detect this by only checking the shape of the metadata (>1),
    # and just ChannelDescription

def _h5svi_set_state(dataset, state):
    """
    Set the "State" of a dataset: the confidence that can be put in the value
    dataset (Dataset): the dataset
    state (int or list of int): the state value (ST_*) which will be duplicated
     as many times as the shape of the dataset. If it's a list, it will be directly
     used, as is.
    """

def _h5svi_get_state(dataset, default=None):
    """
    Read the "State" of a dataset: the confidence that can be put in the value
    dataset (Dataset): the dataset
    default: to be returned if no state is present
    return state (int or list of int): the state value (ST_*) which will be duplicated
     as many times as the shape of the dataset. If it's a list, it will be directly
     used, as is. If not state available, default is returned.
    """

def _h5py_enum_commit(group, name, dtype):
    """
    Commit (=save under a name) a enum to a group
    group (h5py.Group)
    name (string)
    dtype (dtype)
    """

def _add_image_metadata(group, image, mds):
    """
    Adds the basic metadata information about an image (scale and offset)
    group (HDF Group): the group that will contain the metadata (named "PhysicalData")
    image (DataArray): image (with global metadata)
    mds (None or list of dict): metadata for each channel
    """

def _add_svi_info(group):
    """
    Adds the information to indicate this file follows the SVI format
    group (HDF Group): the group that will contain the information
    """

def _add_acquistion_svi(group, data, mds, **kwargs):
    """
    Adds the acquisition data according to the sub-format by SVI
    group (HDF Group): the group that will contain the metadata (named "PhysicalData")
    data (DataArray): image with (global) metadata, all the images must
      have the same shape.
    mds (None or list of dict): metadata for each C of the image (if different)
    """

def _findImageGroups(das):
    """
    Find groups of images which should be considered part of the same acquisition
    (be a channel of an Image in HDF5 SVI).
    das (list of DataArray): all the images, with dimensions ordered C(TZ)YX
    returns (list of list of DataArray): a list of "groups", each group is a list
     of DataArrays
    Note: it's a slightly different function from tiff._findImageGroups()
    """

def _adjustDimensions(da):
    """
    Ensure the DataArray has 5 dimensions ordered CTZXY (as dictated by the HDF5
    SVI convention). If it seems to contain RGB data, an exception is made to
    return just CYX data.
    da (DataArray)
    returns (DataArray): a new DataArray (possibly just a view)
    """

def _groupImages(das):
    """
    Group images into larger ndarray, to follow the HDF5 SVI flavour.
    In practice, this only consists in merging data for multiple channels into
    one, and ordering/extending the shape to CTZYX.
    das (list of DataArray): all the images
    returns :
      acq (list of DataArrays): each group of data, with the (general) metadata
      metadatas (list of (list of dict, or None)): for each item of acq, either
       None if the metadata is fully in acq or one metadata per channel.
    """

def _updateRGBMD(da):
    """
    update MD_DIMS of the DataArray containing RGB if needed. Trying to guess
     according to the shape if necessary.
    da (DataArray): DataArray to update
    """

def _thumbFromHDF5(filename):
    """
    Read thumbnails from an HDF5 file.
    Expects to find them as IMAGE in Preview/Image.
    return (list of model.DataArray)
    """

def _dataFromSVIHDF5(f):
    """
    Read microscopy data from an HDF5 file using the SVI convention.
    Expects to find them as IMAGE in XXX/ImageData/Image + XXX/PhysicalData.
    f (h5py.File): the root of the file
    return (list of model.DataArray)
    """

def _dataFromHDF5(filename):
    """
    Read microscopy data from an HDF5 file.
    filename (string): path of the file to read
    return (list of model.DataArray)
    """

def _mergeCorrectionMetadata(da):
    """
    Create a new DataArray with metadata updated to with the correction metadata
    merged.
    da (DataArray): the original data
    return (DataArray): new DataArray (view) with the updated metadata
    """

def _saveAsHDF5(filename, ldata, thumbnail, compressed=True):
    """
    Saves a list of DataArray as a HDF5 (SVI) file.
    filename (string): name of the file to save
    ldata (list of DataArray): list of 2D (up to 5D) data of int or float.
     Should have at least one array.
    thumbnail (None or DataArray): see export
    compressed (boolean): whether the file is compressed or not.
    """

def export(filename, data, thumbnail=None):
    '''
    Write an HDF5 file with the given image and metadata
    filename (unicode): filename of the file to create (including path)
    data (list of model.DataArray, or model.DataArray): the data to export,
        must be 2D or more of int or float. Metadata is taken directly from the data
        object. If it's a list, a multiple page file is created. The order of the
        dimensions is Channel, Time, Z, Y, X. It tries to be smart and if
        multiple data appears to be the same acquisition at different C, T, Z,
        they will be aggregated into one single acquisition.
    thumbnail (None or model.DataArray): Image used as thumbnail for the file. Can be of any
      (reasonable) size. Must be either 2D array (greyscale) or 3D with last
      dimension of length 3 (RGB). If the exporter doesn't support it, it will
      be dropped silently.
    '''

def read_data(filename):
    """
    Read an HDF5 file and return its content (skipping the thumbnail).
    filename (unicode): filename of the file to read
    return (list of model.DataArray): the data to import (with the metadata
     as .metadata). It might be empty.
     Warning: reading back a file just exported might give a smaller number of
     DataArrays! This is because export() tries to aggregate data which seems
     to be from the same acquisition but on different dimensions C, T, Z.
     read_data() cannot separate them back explicitly.
    raises:
        IOError in case the file format is not as expected.
    """

def read_thumbnail(filename):
    """
    Read the thumbnail data of a given HDF5 file.
    filename (unicode): filename of the file to read
    return (list of model.DataArray): the thumbnails attached to the file. If
     the file contains multiple thumbnails, all of them are returned. If it
     contains none, an empty list is returned.
    raises:
        IOError in case the file format is not as expected.
    """


tiff-ome
=======

test