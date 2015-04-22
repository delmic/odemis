****************************************
Acquisition layer
****************************************

Streams
=======

In its essence, a Stream represents one type of acquisition, corresponding to
the coupling of an emitter and a detector. It provides two main functionalities.
First, it allows to acquire new data (either with the is_active VA, or the acquire
method). Second, it can "project" (ie, convert) the raw data acquired into a
visible object for the user according to various criteria.

The following subclasses exist:

- LiveStream:
  To be done?

  - SEMStream:
    Stream containing images obtained via Scanning electron microscope.
    
    It basically knows how to activate the scanning electron and the detector.

  - AlignedSEMStream:
    This is a special SEM stream which automatically first aligns with the
    CCD (using spot alignment) every time the stage position changes.
    
    Alignment correction can either be done via beam shift (=translation), or
    by just updating the image position.
    
  - CameraStream:
    Abstract class representing streams which have a digital camera as a
    detector.

    - BrightfieldStream:
      Stream containing images obtained via optical brightfield illumination.
      
      It basically knows how to select white light and disable any filter.
  
    - CameraCountStream:
      Special stream dedicated to count the entire data, and represent it over
      time.
      
      The .image is a one dimension DataArray with the mean of the whole sensor
      data over time. The last acquired data is the last value in the array.

    - FluoStream:
      Stream containing images obtained via epifluorescence.
      
      It basically knows how to select the right emission/filtered wavelengths,
      and how to taint the image.
      Note: Excitation is (filtered) light coming from a light source and
      emission is the light emitted by the sample.
  
    - RGBCameraStream:
      Stream for RGB camera.
      
      If a light is given, it will turn it on during acquisition.


- MultipleDetectorStream:
  Abstract class for all specialized streams which are actually a combination
  of multiple streams acquired simultaneously. The main difference from a
  normal stream is the init arguments are Streams, and .raw is composed of all
  the .raw from the sub-streams.

  - SEMCCDMDStream:
    Abstract class for multiple detector Stream made of SEM + CCD.
    
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    
  - SEMSpectrumMDStream:
    Multiple detector Stream made of SEM + Spectrum.
    
    It handles acquisition, but not rendering (so .image always returns an empty
    image).

  - SEMARMDStream:
    Multiple detector Stream made of SEM + AR.
    
    It handles acquisition, but not rendering (so .image always returns an empty
    image).

  - SEMMDStream:
    Same as SEMCCDMDStream, but expects two SEM streams: the first one is the 
    one for the SED, and the second one for the CL or Monochromator. 

- RepetitionStream:
  Abstract class for streams which are actually a set multiple acquisition
  repeated over a grid.

  - SpectrumSettingsStream:
    A Spectrum stream.
    
    Be aware that acquisition can be very long so should not be used for live
    view. So it has no .image (for now). See StaticSpectrumStream for displaying
    a stream.

  - ARSettingsStream:
    An angular-resolved stream, for a set of points (on the SEM).
    
    Be aware that acquisition can be very long so
    should not be used for live view. So it has no .image (for now).
    See StaticARStream for displaying a stream, and CameraStream for displaying
    just the current AR view.

  - OverlayStream:
    Fake Stream triggering the fine overlay procedure.

    It's basically a wrapper to the find_overlay function.

    Instead of actually returning an acquired data, it returns an empty DataArray
    with the only metadata being the correction metadata (i.e., MD_*_COR). This
    metadata has to be applied to all the other optical images acquired.
    See img.mergeMetadata() for merging the metadata.

- StaticStream:
  Stream containing one static image.

  For testing and static images.

  - RGBStream:
    A static stream which gets as input the actual RGB image
    
  - StaticSEMStream:
    Same as a StaticStream, but considered a SEM stream
    
  - StaticBrightfieldStream:
    Same as a StaticStream, but considered a Brightfield stream

  - StaticFluoStream:
    Static Stream containing images obtained via epifluorescence.
    
    It basically knows how to show the emission/filtered wavelengths,
    and how to taint the image.

  - StaticARStream:
    A angular resolved stream for one set of data.

  - StaticSpectrumStream:
    A Spectrum stream which displays only one static image/data.
    
    The main difference from the normal streams is that the data is 3D (a cube)
    The metadata should have a MD_WL_POLYNOMIAL or MD_WL_LIST
    Note that the data received should be of the (numpy) shape CYX or C11YX.
    When saving, the data will be converted to CTZYX (where TZ is 11)
