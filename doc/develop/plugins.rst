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
is installed in one of these three places:

 * /usr/share/odemis/plugins/

 * /usr/local/share/odemis/plugins/
 
 * ~/.local/share/odemis/plugins/

Each ``.py`` file in these directories is loaded at GUI start as a python module
and for each subclass of Plugin present in the module, an instance is created.



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
      
      * View (on the left) + Streams (on the bottom right)
      
      * Settings (on the top right)
      
      * Progress bar (at the bottom)
      
      * Buttons (at the very bottom)

    .. py:method:: __init__(plugin, title, text="")

      Creates a window for acquisition.
      
      :param plugin: The plugin that creates that window (ie, 'self').
      
      :param title: The title of the window.
      :type title: str
      
      :param text: Informational text displayed at the top.
      :type text: str
 
    .. py:method:: addSettings(objWithVA, conf=None)
    
      Adds settings as one widget on a line for each VigilantAttribute in the object.
      
      :param objWithVA: An object that contains :py:class:`VigilantAttribute` s.
      
      :param conf: Allows to override the automatic selection of the widget.
         Among other things, it allows to force a StringVA to specify a filename with
         a file selection dialog.  See odemis.gui.conf.data for documentation.
      :type conf: dict str -> dict
    
    .. py:method:: addButton(label, callback=None, face_colour='def')
    
      Add a button at the bottom of the window. 
      The buttons are positioned in order, from right to left, and assigned increasing
      numbers starting from 0. If callback is None, pressing the button will close
      the window and the button number will be the return code of the dialog.
      
      :param label: text displayed on the button
      
      :param callback: is the function to be called 
         when the button is pressed (with the event and the dialog as arguments).
    
    .. py:method:: addStream(stream)
    
       Adds a stream to the canvas, and a stream panel to the panel box.
       It also ensure the panel box and canvas as shown.
       If this method is not called, the canvas is hidden.
       
       :param stream: Stream to be shown.
       
       :returns: The stream panel created to show the stream in the panel.
       :rtype: StreamPanel
    
    .. py:method:: showProgress(future)
    
       Shows a progress bar, based on the status of the progressive future given.
       If future is None, it will hide the progress bar.
       As long as progress is active, the buttons are disabled. 
       If future is cancellable, show a cancel button next to the progress bar.

    .. py:method:: ShowModal()
    
       Inherited from the standard wx.Dialog. It shows the window and prevents from
       accessing the rest of the GUI until the window is closed.

    .. py:method:: Destroy()
    
       Inherited from the standard wx.Dialog. Hides the window.

    .. py:attribute:: text 
    
       (wx.StaticText): the widget containing the description text. Allows to 
       change the text displayed.
       
    .. py:attribute:: canvas
 
       (MicCanvas): The canvas that is shown in the view. It allows adding overlay.

    .. py:attribute:: buttons
    
       (list of wx.Button): The buttons which were added.
       It allows enabling/disabling buttons and change label.


