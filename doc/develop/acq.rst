*****************
Acquisition layer
*****************

Streams
=======

In its essence, a *Stream* represents one type of acquisition, corresponding to
the coupling of an emitter and a detector. It provides two main functionalities.
First, it allows to acquire new data (either with the ``is_active`` VigilanteAttribute (VA),
or the ``acquire()`` method).
Second, it can "project" (ie, convert) the raw data acquired (*.raw*) into a
visible object (``.image``) for the user according to various criteria.

Some streams (typically, the StaticStreams) do not provide by themselves this projection functionality.
In such case, they do not have a ``.image`` VA.
In order to visualize such stream, a *DataProjection* must be instantiated with
the stream. This provides the flexibility to select which type of projection is
needed. For example, on a SpectrumStream (which has 3 dimensions, X, Y and C),
one could use a *RGBSpatialProjection* to see the average intensity on each
pixel, or a *SinglePointSpectrumProjection* to see the spectrum plot at a given
pixel.

Note that in order to separate streams from an acquisition type point-of-view,
most of the streams are also inheriting from one of the extra abstract classes:
OpticalStream, EMStream, CLStream, SpectrumStream, TemporalSpectrumStream, ARStream.

**.raw:**

List of DataArray containing the latest raw data acquired by the Stream.

**.image:**

It's a VA containing a DataArray representing the .raw in a form useful for the user.
For a standard image, the data should be projected to a RGB 2D image (XYC).

The basic stream class is *Stream* (/acq/stream/_base.py).

The following subclasses exist:

    1. **LiveStream(Stream)** (/acq/stream/_live.py)
    2. **MultipleDetectorStream(Stream)** (/acq/stream/_sync.py)
    3. **RepetitionStream(LiveStream)** (/acq/stream/_helper.py)
    4. **StaticStream(Stream)** (/acq/stream/_static.py)

LiveStream(Stream)
------------------

   Abstract class for any stream that can do continuous acquisition. It is mainly used for displaying detector data.
   For every acquisition type it always has an equivalent in *_static.py*

   It uses the *is_active* VA.

    - **SEMStream(LiveStream):**

      Stream containing images obtained via Scanning electron microscope.
      It basically knows how to activate the scanning electron and the detector.

    - **AlignedSEMStream(LiveStream):**

      This is a special SEM stream which automatically first aligns with the
      CCD (using spot alignment) every time the stage position changes.
    
      Alignment correction can either be done via beam shift (=translation), or
      by just updating the image position.

    - **SpotSEMStream(LiveStream):**

      Stream which forces the SEM to be in spot mode when active.
	  
    - **ScannedRemoteTCStream(LiveStream):**

      Stream used to run a FLIM acquisition. Requires a time correlator to operate. 
	  
	  Works in coordination with a helper stream (ScannedTCSettingsStream) which is used to set up the measurement. 

    - **CameraStream(LiveStream):**

      Abstract class representing streams which have a digital camera as a
      detector.

        - *BrightfieldStream(CameraStream):*

          Stream containing images obtained via optical brightfield illumination.
      
          It basically knows how to select white light and disable any filter.
  
        - *CameraCountStream(CameraStream):*

          Special stream dedicated to count the entire data, and represent it over
          time.
      
          The .image is a one dimension DataArray with the mean of the whole sensor
          data over time. The last acquired data is the last value in the array.

        - *FluoStream(CameraStream):*

          Stream containing images obtained via epifluorescence.
      
          It basically knows how to select the right emission/filtered wavelengths,
          and how to taint the image.
          Note: Excitation is (filtered) light coming from a light source and
          emission is the light emitted by the sample.

        - *ScannedFluoStream(CameraStream):*

          Stream containing images obtained via epifluorescence using a "scanner"
          (ie, a confocal microscope).

        - *RGBCameraStream(CameraStream):*

          Stream for RGB camera.
      
          If a light is given, it will turn it on during acquisition.

MultipleDetectorStream(Stream)
------------------------------

   Abstract class for all specialized streams which are actually a combination
   of multiple streams acquired simultaneously. The main difference from a
   normal stream is the init arguments are Streams (one is a SettingsStream from _helper.py),
   and .raw is composed of all
   the .raw from the sub-streams. It is mainly used for SPARC and confocal acquisitions.

   Acquisition can be conducted using one detector + one scanner but also multiple detectors are possible.
   The acquisition time can be from minutes to hours.

   It uses the *acquire* method (don't support acquire continuously, only update).

    - **SEMCCDMDStream(MultipleDetectorStream):**

      Abstract class for multiple detector Stream made of SEM + CCD.
    
      It handles acquisition, but not rendering (so .image always returns an empty
      image).

        - *SEMSpectrumMDStream(SEMCCDMDStream):*

          Multiple detector Stream made of SEM + Spectrum.

          It handles acquisition, but not rendering (so .image always returns an empty
          image).

        - *SEMARMDStream(SEMCCDMDStream):*

          Multiple detector Stream made of SEM + AR.

          It handles acquisition, but not rendering (so .image always returns an empty
          image).

    - **SEMMDStream(MultipleDetectorStream):**

      Same as SEMCCDMDStream, but expects two SEM streams: the first one is the
      one for the SED, and the second one for the CL or Monochromator.

    - **ScannedFluoMDStream(MultipleDetectorStream):**

      Stream to acquire multiple ScannedFluoStreams simultaneously.

RepetitionStream(LiveStream)
----------------------------

   Abstract class for streams which are actually a set of multiple acquisitions
   repeated over a grid.
   It is a *LiveStream* plus extra options (Settings streams). It is mainly used for SPARC and confocal acquisitions.
   Extra option can be the *repetition* or the *region of acquisition (ROA)*.

   It uses the *is_active* VA (as the other LiveStreams). It will start an acquisition useful for configuring the settings by the user.

    - **CCDSettingsStream(RepetitionStream):**

        - *SpectrumSettingsStream(CCDSettingsStreamStream):*

          A Spectrum stream.

          Be aware that acquisition can be very long so should not be used for live
          view. So it has no .image (for now). See StaticSpectrumStream for displaying
          a stream.

        - *ARSettingsStream(CCDSettingsStreamStream):*

          An angular-resolved stream, for a set of points (on the SEM).
    
          Be aware that acquisition can be very long so
          should not be used for live view. So it has no .image (for now).
          See StaticARStream for displaying a stream, and CameraStream for displaying
          just the current AR view.

    - **PMTSettingsStream(RepetitionStream):**

        - *MonochromatorSettingsStream(PMTSettingsStream):*

          A stream acquiring a count corresponding to the light at a given wavelength,
          typically with a counting PMT as a detector via a spectrograph.

        - *CLSettingsStream(PMTSettingsStream):*

          A spatial cathodoluminescense stream, typically with a PMT as a detector.
		  
    - **ScannedTCSettingsStream(RepetitionStream):**

      Helper stream used to set up a FLIM acquisition. Requires a time correlator to operate. 

    - **OverlayStream(Stream):**

      Fake Stream triggering the fine overlay procedure.

      It's basically a wrapper to the find_overlay function.

      Instead of actually returning an acquired data, it returns an empty DataArray
      with the only metadata being the correction metadata (i.e., MD_*_COR). This
      metadata has to be applied to all the other optical images acquired.
      See img.mergeMetadata() for merging the metadata.

StaticStream(Stream)
--------------------

   Stream containing one static image (passed as a DataArray). It's mainly for displaying data from a file,
   and also for testing and displaying static images.
   Approximately, there is one for each acquisition type supported by Odemis.

   Note: It has an *is_active* VA, because it inherits from *Stream*.
   However, nothing happens when it is changed and no code should intent to use it.

    - **Static2DStream(StaticStream):**

      Stream containing one static image. For testing and static images.
    
        - *StaticSEMStream(Static2DStream):*

          Same as a StaticStream, but considered a SEM stream.

        - *StaticCLStream(Static2DStream):*

          Same as a StaticStream, but has a emission wavelength.
    
        - *StaticBrightfieldStream(Static2DStream):*

          Same as a StaticStream, but considered a Brightfield stream.

        - *StaticFluoStream(Static2DStream):*

          Static Stream containing images obtained via epifluorescence.
    
          It basically knows how to show the emission/filtered wavelengths,
          and how to taint the image.

    - **RGBStream(StaticStream):**

      A static stream which gets as input the actual RGB image.

    - **RGBUpdatableStream(StaticStream):**

      Similar to RGBStream, but contains an update function that allows to modify the
      raw data.

    - **StaticARStream(StaticStream):**

      A angular resolved stream for one set of data.

      There is no directly nice (=obvious) format to store AR data.
      The difficulty is that data is somehow 4 dimensions: SEM-X, SEM-Y, CCD-X,
      CCD-Y. CCD-dimensions do not correspond directly to quantities, until
      converted into angle/angle (knowing the position of the pole).

      As it's possible that positions on the SEM are relatively random, and it
      is convenient to have a simple format when only one SEM pixel is scanned,
      we've picked the following convention:

        * each CCD image is a separate DataArray
        * each CCD image contains metadata about the SEM position (MD_POS, in m)
          pole (MD_AR_POLE, in px), and acquisition time (MD_ACQ_DATE)
        * multiple CCD images are grouped together in a list

      VAs:

        * *.background*: This VA is used to keep track of the image background and is subtracted from the raw image when
          displayed, otherwise a baseline value is used.
        * *.point*: This VA is used to keep track of the SEM position, which is displayed.
          If it is (None, None), no point selected.

    - **StaticSpectrumStream(StaticStream):**

      A stream which displays only a static image/data with spectrum and/or time dimension.

      The main difference from the normal streams is that the data is 3D (a cube)
      or 4D (hypercube).
      The metadata should have a MD_WL_POLYNOMIAL or MD_WL_LIST, or MD_TIME_LIST.
      Note that the data received should be of the (numpy) shape CYX, TYX or CT1YX.
      
      It requires the use of a *DataProjection* to be visualized. The compatible
      projections are: RGBSpartialProjection, LineSpectrumProjection, 
      MeanSpectrumProjection, PixelTemporalSpectrumProjection,
      SinglePointSpectrumProjection, and SinglePointTemporalProjection.

      The histogram corresponds to the data after calibration, and selected via
      the spectrumBandwidth VA.

      VAs:

        * *.background*: If background VA is set, it is subtracted from the raw image data when displayed, otherwise a
          baseline value is used.
        * *.efficiencyCompensation*: This VA is used to keep track of the detection sensitivity compensation for the
          raw data.
          It corrects the displayed data for differences in the detection efficiency depending on the wavelength.
          The spectrum efficiency compensation data is None or a DataArray. See also acq.calibration.py.
        * *.fitToRGB*: This VA keeps track of whether the (per bandwidth) display should be split intro 3 sub-bands,
          which are applied to RGB (map color).
          It's only present if the data has a spectrum dimension.
        * *.selected_pixel*: This VA is used to keep track of any selected pixel within the data for the
          display of a spectrum (wavelength: x-axis; intensity: y-axis).
        * *.selected_line*: This VA is used to keep track of any selected line within the data for the
          display of a spectrum. The first point and the second point are in pixels. It must be 2 elements long.
          The spectrum displays the different wavelengths (y-axis) for each pixel on the line selected (x-axis).
        * *.selected_time*: This VA is used to keep track of the time pixel selected within the data for the
          display of a chronograph (time: x-axis; intensity: y-axis).
          It's only present if the data has a time dimension. 
        * *.peak_method*: This VA is used to keep track of which method is used to fit the peak of a spectrum
          (Gaussian, Lorentzian).
          None if spectrum peak fitting curve is not displayed (Peak method index).
        * *.selectionWidth*: This VA is used to keep track of the spatial (xy) thickness of a point (pixel) or a line,
          which is selected (shared). Pixels within the defined range are binned to one value.
          A point of width W leads to the average value between all the pixels, which are within W/2 from the center
          of the point (disc with radius W/2).
          A line of width W leads to a 1D spectrum taking into account all the pixels,
          which fit on an orthogonal line to the selected line at a distance <= W/2 (rectangle with thickness W/2).
        * *.spectrumBandwidth*: This VA is used to keep track of the thickness of the spectral range selected for display.
          For each selected pixel it maps the selected spectral (wavelength) range from the
          hypercube into one pixel value.
          It's only present if the data has a spectrum dimension.


Blanker behaviour
=================
Three kinds of blanker behaviour:
 * No control at all, it's entirely controlled by the SEM software
 * Automatic: In this case, the e-beam has .blanker with choices None (automatically unblanked when
   scanning), True (blanked), False (unblanked). The default is None. Usually Odemis
   doesn't touch this setting, so the user can override the blanker state by using the
   command line (``odemis-cli --set-attr e-beam blanker True``).
   This is the case when the SEM blanker is controlled via the e-beam scan control cable,
   (via the semcomedi). This is also done when the SEM API is also used to acquire
   the image (eg, Phenom).
 * Only forced: the e-beam will have a .blanker with choices True (blanked) and
   False (unblanked). In such case, typically the user has the ability to override
   the blanking state using the SEM software, until the next action of Odemis.
   This is the case when the blanker is controlled via the SEM API, although the
   acquisition is done via the scan cable (eg, Zeiss, Tescan, Hitachi, JEOL).

SECOM alignment:
----------------
If blanker doesn't support automatic control, the blanker is forced off/on when
playing the SEM stream (which is always active).
This is done by passing the blanker argument to the SEMStream.

During fine alignment, if blanker doesn't support automatic control, the blanker
is disabled, and activated again after the end of the procedure.

SECOM acquisition:
------------------
If blanker doesn't support automatic control, the blanker is forced off/on when
playing the SEM stream (which is always active).
This is done by passing the blanker argument to the SEMStream.

DELPHI:
-------
Nothing special in the GUI: the blanker is automatically activated whenever the
e-beam is not in use.

SPARC alignment:
----------------
In mirror alignment mode, during background acquisition, the SEM is stopped to
automatically activate the blanker (which will happen only if it's in the automatic setting).

Optical Path Manager
====================
The goal of the Optical Path Manager is to the position actuators affecting the
light path such that they are in the right configuration for a given acquisition type.
It is found in ``acq.path.OpticalPathManager`` and is typically used as a singleton.
The GUI takes care of instantiating it at init, and then it can be accessed via
``MainGUIData.opm``. In practice, this is almost exclusively used on the SPARC.


It has one important method:

.. py:method:: setPath(mode, detector=None)

   Sets all the necessary components of the optical path to the positions
   required to achieve the requested mode.

   :param path: The stream or the optical path mode
   :type path: stream.Stream or str
   :param detector:
      When the `path` is a str, this allows defining which detector will be targeted on this path.
      That is useful in case the mode can be used with multiple detectors (eg,
      ``fiber-align`` on a SPARC with multiple spectrometers). When path is a
      Stream, the ``Stream.detector`` is always used.
   :type detector: Component or None
   :return: a Future allowing to follow the status of the path update.
   :rtype: Future
   :raise: ValueError if the given mode does not exist
           IOError if a detector is missing

The list of supported `paths` (ie, optical modes) can be found in the ``acq.path`` module
in the ``SPARC_MODES`` and ``SPARC2_MODES`` dictionaries.
At runtime, they are limited by the microscope type and the available components.

Currently, in Odemis, the Optical Path Manager is called from two places:

 * In ``Stream.prepare()``. This function is in charge of preparing all the
   microscope components prior to the stream acquisition.
   In Odemis, it is called explicitly in some cases. It is also called implicitly,
   every time a stream is about to start acquiring data.
 * In the SPARC alignment tab, whenever the alignment mode is changed.

In terms of implementation, for each optical mode, there are two types of actuators:

 * The path selectors which, typically, are mirrors with 2 positions. The position
   of each selector is derived from the "affects" property of all the components
   in the microscope. The "affects" are defined in the
   microscope file. Each "affects" point towards the next component along the optical
   path. This forms a directed graph going from the actuators to the detectors. 
   By traversing the graph backwards, the optical path manager
   can find out for a given detector which selector must move, and to which position.   
 * The acquisition type actuators which configure how the light can be seen. For
   instance in AR mode, the lens 2 needs to be activated, while it should be removed
   during spectrum mode.


CCD fan control
---------------
As an extra function, the Optical Path Manager also takes care of disabling the
fan of the CCDs during an SEM acquisition on the SECOM and DELPHI platforms.
This ensures that there is no vibration during a high quality SEM image acquisition.
To allow this feature, the manager has this extra method:

.. py:method:: setAcqQuality(quality)

   Updates the acquisition quality required during the following acquisitions.

   :param quality: the acquisition quality
   :type quality: ACQ_QUALITY_FAST or ACQ_QUALITY_BEST

In ``ACQ_QUALITY_FAST``, the CCD fan is always on. Which ensures the camera is cooled
down sufficiently.
In ``ACQ_QUALITY_BEST``, before an optical acquisition, the CCD fan is turned on, and
it is ensured that the CCD temperature is at the target. Before an SEM acquisition,
the CCD fan is turned off, to avoid the vibrations.
