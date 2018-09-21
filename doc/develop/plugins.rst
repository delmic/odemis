********************************
Graphical User Interface Plugins
********************************

The goal is to provide additional functionality to the GUI. Plugins are an
intermediate level between basic console scripts (easy to write, but not
integrated) and directly modifying the GUI code (fully integrated but difficult
to write and maintain).


Plugin loading
==============

A plugin is defined by a class present in a python file (called "module"), which
must be installed in a specific directory.
On Linux, plugins are searched in these three places:

 * ``/usr/share/odemis/plugins/``

 * ``/usr/local/share/odemis/plugins/``
 
 * ``~/.local/share/odemis/plugins/``

On Windows, plugins are searched in these two places:

 * ``plugins\`` of the Odemis program directory (ex: ``C:\Program Files (x86)\OdemisViewer\plugins``)

 * ``.config\odemis\plugins\`` for the user directory (ex: ``C:\Users\Bob\.config\odemis\plugins``)

Each ``.py`` file in these directories is loaded at GUI start as a python module
and for each subclass of :py:class:`Plugin` present in the module, an instance is created.


Plugin class
============

Each plugin is a defined as a class, which must inherit from
odemis.gui.plugin.Plugin.

Each plugin class must provide the following attributes and function:

   .. py:attribute:: name

      A string representing a user friendly name of the plugin. 

   .. py:attribute:: __author__ 

      A string containing the name of the author(s).

   .. py:attribute:: __version__

      A string containing the version number of the plugin.

   .. py:attribute:: __license__

     A string representing the license under which the plugin is released.

   .. py:method:: __init__(microscope, main_app)

      Creator function for the plugin. It is called at GUI initialisation,
      and should be used to add the entry points to the feature provided by the
      plugin into the GUI.

      :param microscope: The root of the current microscope (provided by
         the back-end). If it is None, it means Odemis is running as a viewer.

      :type microscope: Microscope or None

      :param main_app: The wx.App that represents the entire GUI. 

      :type main_app: OdemisGUIApp


Helper functions and class
==========================

The Plugin class provides a few helper functions: 

.. py:method:: Plugin.addMenu(path, callable)

   menu path (including the name of the action),
   and a function to call, which will be called without any argument.


.. py:method:: Plugin.showAcquisition(path)

   Shows a acquisition file in the analysis tab (and
   automatically switch to the analysis tab).


.. py:class:: AcquisitionDialog()

   This is a special generic dialog box that allows to easily ask settings and
   show the progress of an acquisition.
   Grossly, it's similar to the SECOM acquisition window, but parts which are not
   explicitly specified are by default hidden. The different parts are:

      * Text (on the whole top)

      * Settings (on the top right)

      * View (on the left) + Streams (on the bottom right)

      * Information text (at the bottom)

      * Progress bar (at the bottom)

      * Buttons (at the very bottom)

    .. py:method:: __init__(plugin, title, text=None)

      Creates a window for acquisition.
      Note that when not used anymore, it _must_ be deleted by calling `Destroy()`.

      :param plugin: The plugin that creates that window (ie, 'self').
      :param title: The title of the window.
      :type title: str
      :param text: Informational text displayed at the top. If None, the text
          is hidden.
      :type text: str or None

    .. py:method:: addSettings(objWithVA, conf=None)

      Adds settings as one widget on a line for each VigilantAttribute in the object.

      :param objWithVA: An object that contains :py:class:`VigilantAttribute` s.
      :param conf: Allows to override the automatic selection of the widget.
         Among other things, it allows to force a StringVA to specify a filename with
         a file selection dialog.  See odemis.gui.conf.data for documentation.
      :type conf: dict str -> dict

    .. py:method:: addButton(label, callback=None, face_colour='def')

      Add a button at the bottom of the window. The button is added at the
      right of the current buttons. In other words, the buttons are positioned
      in order, from left to right, and assigned increasing
      numbers starting from 0. If callback is None, pressing the button will close
      the window and the button number will be the return code of the dialog.

      :param label: text displayed on the button
      :param callback: is the function to be called 
         when the button is pressed (with the event and the dialog as arguments).
      :param face_colour: Colour of the button, among "def", "blue", "red", and
         "orange".

    .. py:method:: addStream(stream, index=0)

       Adds a stream to the viewport, and a stream entry to the panel box.
       It also ensures the panel box and viewport are shown.
       If this method is not called, the stream entry and viewports are hidden.

       :param stream: Stream to be shown.
       :param index: Index of the viewport to add the stream to.
          0 = left, 1 = right, 2 = spectrum viewport. If None, it will not show the stream
          on any viewport (and it will be added to the ``.hidden_view``)
       :type index: int or None

    .. py:method:: showProgress(future)

       Shows a progress bar, based on the status of the progressive future given.
       If future is None, it will hide the progress bar.
       As long as progress is active, the buttons are disabled. 
       If future is cancellable, show a cancel button next to the progress bar.

    .. py:method:: setAcquisitionInfo(text=None, lvl=logging.INFO)

       Displays information label above progress bar.

       :param text: text to be displayed. If None is passed, the information
          label will be hidden.
       :type text: str or None
       :param lvl: log level, which selects the display colour.
       :type lvl: int, from logging.*
       
    .. py:method:: pauseSettings()

       Freezes the settings and stream controls in the window to prevent user changes.
       Typically done while acquiring.
       
    .. py:method:: resumeSettings()

       Unfreezes the settings and stream controls in the window to allow user changes.
       Typically done when acquiring is cancelled.   

    .. py:method:: ShowModal()

       Inherited from the standard wx.Dialog. It shows the window and prevents from
       accessing the rest of the GUI until the window is closed.

    .. py:method:: EndModal(retCode)

       Request to close the window, and pass a specific return code.
       Inherited from the standard wx.Dialog.
       Make sure to call .Destroy() when not using the dialog anymore.
       :param retCode: the return code
       :type retCode: int

    .. py:method:: Close()

       Request to close the window.
       Inherited from the standard wx.Dialog.
       Make sure to call .Destroy() when not using the dialog anymore.

    .. py:method:: Destroy()

       Inherited from the standard wx.Dialog. Hides the window, and cleans it up
       from the memory. It should *always* be called after the window is not used.
       It is not safe to call it several times. You can protect from calling it
       on an already destroyed Dialog *dlg* by using ``if dlg:``.

    .. py:attribute:: text

       (wx.StaticText): the widget containing the description text. Allows to 
       change the text displayed.

    .. py:attribute:: buttons

       (list of wx.Button): The buttons which were added.
       It allows enabling/disabling buttons and change label.

    .. py:attribute:: view

       (MicroscopeView): The view that shows the streams. It allows adding overlays,
       and modifying the field of view.


Debugging tips
==============
Because plugins are loaded dynamically in a separate Python program (ie, Odemis),
they can be a bit harder to debug with the standard tools.

To display debug or information text in the debug panel of Odemis, use "logging".
For example:

.. code-block:: python

    logging.debug("This is a message that appears in the debug panel")
    logging.warning("This is a warning that appears in the debug panel")

All the plugins loaded are listed in the Help/About/Credits window, with their
name and authors. If it's not listed, it hasn't been loaded.

Loading errors are not displayed in the standard debug panel (because they happen
before the window is displayed). However, it is possible to find them in the
odemis-gui.log file. It's also possible to look for such errors by simply loading
the plugin with python. For instance, do a in terminal:

``python ~/.local/share/odemis/plugins/Myplugin.py``

This shouldn't display anything, unless there are errors.


Example plugins
===============

You can find example plugins in the Odemis source directory in ``plugins/``.
An example of plugin that can also be used from the command line, as a script,
can be found in ``scripts/monochromator-scan.py`` .

Note that by convention the file name of python are always in lowercase and
without spaces (replaced by ``_``).

Below is an example of a very simple plugin which will create a menu entry.
When that entry is selected, it shows an acquisition window and then acquire
10 images from the CCD with the selected exposure time.

.. code-block:: python

    class SimplePlugin(Plugin):
        name = "Example plugin"
        __version__ = "1.0.1"
        __author__ = "Éric Piel"
        __license__ = "GNU General Public License 2"

        def __init__(self, microscope, main_app):
            super(SimplePlugin, self).__init__(microscope, main_app)
            if not microscope:
                return

            self.main_data = self.main_app.main_data
            if not self.main_data.ccd:
                return

            self.addMenu("Acquisition/Fancy acquisition...", self.start)
            self.exposureTime = model.FloatContinuous(2, (0, 10), unit="s")
            self.filename = model.StringVA("boo.h5")

        def start(self):
            dlg = AcquisitionDialog(self, "Fancy Acquisition", "Enter everything")
            dlg.addSettings(self, conf={"filename": {"control_type": CONTROL_SAVE_FILE}})
            dlg.addButton("Cancel")
            dlg.addButton("Acquire", self.acquire, face_colour='blue')

            ans = dlg.ShowModal()
            if ans == 1:
                self.showAcquisition(self.filename.value)

            dlg.Destroy()

        def acquire(self, dlg):
            ccd = self.main_data.ccd
            exp = self.exposureTime.value
            ccd.exposureTime.value = exp
            dlg.pauseSettings()	# Freezes the setting and stream controls in window

            f = model.ProgressiveFuture()
            f.task_canceller = lambda l: True  # To allow cancelling while it's running
            f.set_running_or_notify_cancel()  # Indicate the work is starting now
            dlg.showProgress(f)

            d = []
            for i in range(10):
                left = (10 - i) * exp
                f.set_progress(end=time.time() + left)
                d.append(ccd.data.get())
                if f.cancelled():
                    # Unfreezes the setting and stream controls in window
                    dlg.resumeSettings() 
                    return

            f.set_result(None)  # Indicate it's over

            if d:
                dataio.hdf5.export(self.filename.value, d)

            dlg.Close()

