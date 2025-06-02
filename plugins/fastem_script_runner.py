# -*- coding: utf-8 -*-
"""
Created on 23 May 2025

@author: Nandish Patel

Gives the ability to run service and installation scripts under Help > Development.

Copyright © 2025 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import logging
import os
import subprocess
import sys
import threading
from typing import IO, Any, Callable, List, Optional, Tuple, Union

import wx

from odemis.gui.plugin import Plugin

# VERSION_BASE_PATH
# Dictionary mapping user-selectable version to their actual base paths
# and potentially other version-specific configurations.
VERSION_BASE_PATH = {
    "Official Release": "/usr/lib/python3/dist-packages/fastem_calibrations",
    "Development": os.path.expanduser("~/development/fastem-calibrations/src")
}
# AVAILABLE_SCRIPTS
# A list of tuples defining the scripts available to the user through the plugin.
# Each tuple should be in the format: (display_name, script_name)
#
# - display_name (str): The user-friendly name that will appear in the selection UI.
# - script_name (str):
#   This is the name of the Python script file (e.g., "my_script.py").
#   The `find_script_path` function will recursively search for this filename
#   within the selected version's base path. The first match found during the
#   recursive search will be used. Ensure script filenames are unique enough
#   within their respective base paths if multiple scripts with the same name
#   exist in different subdirectories, as the search order is not guaranteed
#   beyond finding the first one.
AVAILABLE_SCRIPTS = [
    ("Acquire image with decreased amplitude and offset", "adjusted_offset_amp_acquisition.py"),
    ("Move galvo", "move_galvo.py"),
    ("Pattern calibration", "pattern_calibration.py"),
    ("Periodic maintenance", "periodic_maintenance.py"),
    ("Pitch calibration", "pitch_calibration.py"),
    ("Raster galvo", "raster_galvo.py"),
    ("Scan amplitude pre-align calibration", "scan_amplitude_pre_align.py"),
    ("Set correction collar", "z_stack_acquisition.py"),
    ("Stage to multiprobe", "stage_to_multiprobe.py"),
]


def find_script_path(version: str, script_name: str, version_base_path: dict) -> Tuple[str, str]:
    """
    Finds the complete path to a script by searching within the base path
    (and its subdirectories) for the given version.

    :param version: The key for the version (e.g., "Official Release", "Development").
    :param script_name: filename (e.g., "move_galvo.py").
    :param version_base_path: A dictionary mapping version names to their base paths.

    :returns: A tuple containing the base path and the absolute path to the script file.

    :raises:
        ValueError: If the version or script_name is not valid.
        FileNotFoundError: If the base_path for the version doesn't exist,
                           or if the script file cannot be found within the base path.
    """
    if version not in version_base_path:
        raise ValueError(
            f"Version '{version}' not found. "
            f"Available versions: {list(version_base_path.keys())}"
        )

    base_path = version_base_path[version]

    # Check if the base path itself exists and is a directory
    if not os.path.isdir(base_path):
        raise FileNotFoundError(
            f"Base path for version '{version}' does not exist or is not a directory: {base_path}"
        )

    # Walk through the directory tree starting from base_path
    for dirpath, _, filenames_in_dir in os.walk(base_path):
        if script_name in filenames_in_dir:
            # File found, construct the full path
            found_path = os.path.join(dirpath, script_name)
            return base_path, os.path.abspath(found_path) # Return absolute path

    # If the loop completes, the file was not found in base_path or its subdirectories
    raise FileNotFoundError(
        f"Script file '{script_name}'"
        f"not found within '{base_path}' (and its subdirectories) for version '{version}'."
    )


class WxTextCtrlOutput:
    """Custom output stream to redirect text output to a wx.TextCtrl."""

    def __init__(self, text_ctrl: wx.TextCtrl, style: Optional[wx.TextAttr] = None):
        """
        :param text_ctrl: The wx.TextCtrl to which output will be directed.
        :param style: Optional wx.TextAttr style for the text.
        """
        self.text_ctrl = text_ctrl
        self.style = style
        self.default_style = text_ctrl.GetDefaultStyle()

    def write(self, text: str):
        """
        Write text to the wx.TextCtrl, ensuring it is done on the main thread.
        :param text: The text to write to the TextCtrl.
        """
        if not wx.IsMainThread():
            wx.CallAfter(self._do_write, text)
            return
        self._do_write(text)

    def _do_write(self, text: str):
        """
        Internal method to write text to the TextCtrl.
        :param text: The text to write to the TextCtrl.
        """
        if not self.text_ctrl:
            return
        if self.style:
            self._append_text_styled(text, self.style)
        else:
            self.text_ctrl.AppendText(text)

        # Auto-scroll to end
        self.text_ctrl.ShowPosition(self.text_ctrl.GetLastPosition())

    def _append_text_styled(self, text: str, style: wx.TextAttr):
        """
        Append text to the TextCtrl with a specific style.
        :param text: The text to append.
        :param style: The wx.TextAttr style to apply to the text.
        """
        if not self.text_ctrl:
            return
        start_pos = self.text_ctrl.GetLastPosition()
        self.text_ctrl.AppendText(text)
        end_pos = self.text_ctrl.GetLastPosition()
        self.text_ctrl.SetStyle(start_pos, end_pos, style)

    def flush(self):
        """
        Flush the output stream, has no effect for wx.TextCtrl as it updates instantly.
        """
        pass  # wx.TextCtrl updates immediately


class WxLogHandler(logging.Handler):
    """Custom logging handler to redirect log messages to a wx.TextCtrl."""

    def __init__(self, wx_text_ctrl_output_stream: WxTextCtrlOutput):
        super().__init__()
        self.wx_stream = wx_text_ctrl_output_stream
        # You can set a default formatter here if you like
        # Or allow it to be set externally
        self.setFormatter(
            logging.Formatter("%(asctime)s (%(module)s) %(levelname)s: %(message)s")
        )

    def emit(self, record):
        # The `record` object has all the log information
        # `self.format(record)` will turn it into a string based on the handler's formatter
        try:
            msg = self.format(record) + "\n"  # Add a newline like a typical console log
            self.wx_stream.write(msg)
        except Exception:
            # Default error handling (prints to original stderr)
            self.handleError(record)

    def close(self):
        # In this case, the underlying stream (WxTextCtrlOutput) is managed
        # by the ScriptConsoleFrame, so we don't close it here directly.
        # We might want to nullify the reference to help with GC if needed.
        self.wx_stream = None
        super().close()


class ScriptConsoleFrame(wx.Frame):
    TIMER_ID_FORCE_KILL = wx.NewIdRef()  # For escalating to SIGKILL

    def __init__(
        self,
        parent: wx.Window,
        title: str,
        script_path: str,
        script_args: Optional[list] = None,
        script_env: Optional[dict] = None
    ):
        """
        :param parent: The parent wx.Window for this frame.
        :param title: The title of the console window.
        :param script_path: The path to the Python script to run.
        :param script_args: Optional list of arguments to pass to the script.
        :param script_env: Optional dictionary of environment variables to set for the script.
        """
        super().__init__(parent, title=title, size=(800, 600))

        self.script_path = script_path
        self.script_args = script_args if script_args else []
        self.script_env = script_env  # If None, inherits environment

        self.process = None  # subprocess.Popen instance
        self.is_script_running = False
        self.io_threads = []  # To store stdout/stderr reading threads

        self.force_kill_timer = None

        # Create the main panel
        self.panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        # Output TextCtrl
        self.output_ctrl = wx.TextCtrl(self.panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        font = wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.output_ctrl.SetFont(font)
        vbox.Add(self.output_ctrl, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)

        # Input TextCtrl
        controls_hbox = wx.BoxSizer(wx.HORIZONTAL)
        self.input_ctrl = wx.TextCtrl(self.panel, style=wx.TE_PROCESS_ENTER)
        self.input_ctrl.SetFont(font)
        self.input_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_input_enter)
        controls_hbox.Add(self.input_ctrl, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=10)

        # Stop Button
        self.stop_button = wx.Button(self.panel, label="Stop Script")
        self.stop_button.Bind(wx.EVT_BUTTON, self.on_stop_button_clicked)
        controls_hbox.Add(self.stop_button, flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, border=5)
        vbox.Add(controls_hbox, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=5)

        self.panel.SetSizer(vbox)
        self.Layout()
        self.Bind(wx.EVT_CLOSE, self.on_close_window)
        self.Bind(wx.EVT_TIMER, self.on_force_kill_timeout, id=self.TIMER_ID_FORCE_KILL)

        # Redirector for GUI output (fed by pipe-reading threads)
        self.gui_stdout_writer = WxTextCtrlOutput(self.output_ctrl)
        self.gui_stderr_writer = WxTextCtrlOutput(self.output_ctrl, style=wx.TextAttr(wx.RED))

        self.script_logger = logging.getLogger()
        self.log_handler = None

        self.start_script_execution()
        self.Show()

    def _pipe_reader_target(self, pipe: Union[int, IO[Any]], writer_func: Callable, stream_name: str = ""):
        try:
            for line_count, line in enumerate(iter(pipe.readline, '')):
                if not self.is_script_running:
                    break
                wx.CallAfter(writer_func, line)
        except ValueError:
            # This can happen if the pipe is closed (e.g., by the process exiting)
            # while readline() is active or if the TextCtrl is gone.
            pass  # Normal on process termination
        except Exception as e:
            if self.is_script_running:
                wx.CallAfter(writer_func, f"\n--- Error in pipe reader for {stream_name}: {e} ---\n")
        finally:
            if hasattr(pipe, 'close') and not pipe.closed:
                try:
                    pipe.close()
                except Exception as e_close:
                    logging.debug(f"Pipe reader {stream_name}: Error closing pipe: {e_close}")
            logging.debug(f"Pipe reader {stream_name}: Exiting thread.")

    def _append_output_safe(self, text: str):
        """Safely append text to output_ctrl, checking if it still exists."""
        if self.output_ctrl:
            self.output_ctrl.AppendText(text)

    def start_script_execution(self):
        if self.is_script_running:
            return

        if not self.script_logger.hasHandlers() or not any(
            isinstance(h, WxLogHandler) for h in self.script_logger.handlers
        ):
            self.script_logger.setLevel(logging.DEBUG)

        self.log_handler = WxLogHandler(self.gui_stdout_writer)
        self.log_handler.setLevel(logging.DEBUG)
        self.script_logger.addHandler(self.log_handler)
        command = [sys.executable, '-u', self.script_path] + self.script_args
        self.output_ctrl.AppendText(f"--- Running script: {' '.join(command)} ---\n")

        try:
            script_full_env = os.environ.copy()
            if self.script_env:
                script_full_env.update(self.script_env)
            script_full_env["PYTHONUNBUFFERED"] = "1"  # Force unbuffered output from Python script

            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0,
                env=script_full_env,
            )
            self.is_script_running = True

            # Start threads to read stdout and stderr
            self.io_threads = []
            t_stdout = threading.Thread(target=self._pipe_reader_target,
                                       args=(self.process.stdout, self.gui_stdout_writer.write, "stdout"),
                                       daemon=True)
            t_stderr = threading.Thread(target=self._pipe_reader_target,
                                       args=(self.process.stderr, self.gui_stderr_writer.write, "stderr"),
                                       daemon=True)
            self.io_threads.extend([t_stdout, t_stderr])
            t_stdout.start()
            t_stderr.start()

            # A thread to monitor if the process has exited
            t_monitor = threading.Thread(target=self._monitor_process, daemon=True)
            self.io_threads.append(t_monitor)
            t_monitor.start()
        except Exception as e:
            self.output_ctrl.AppendText(f"\n--- Error starting script process: {e} ---\n")
            self._script_execution_completed(returncode=-1) # Indicate error

    def _monitor_process(self):
        if self.process:
            return_code = self.process.wait()  # Blocks until process terminates
            wx.CallAfter(self._script_execution_completed, return_code)

    def _script_execution_completed(self, returncode: Optional[int] = None):
        if self.force_kill_timer and self.force_kill_timer.IsRunning():
            self.force_kill_timer.Stop()

        # Wait briefly for I/O threads to finish reading any remaining output
        for t in self.io_threads:
            if t.is_alive():
                t.join(timeout=0.1)

        self.stop_button.Enable(False)
        self.input_ctrl.Enable(False)
        if self.log_handler:
            self.script_logger.removeHandler(self.log_handler)
            self.log_handler.close()
            self.log_handler = None
        self.io_threads = []
        self.process = None
        self.force_kill_timer = None
        self.is_script_running = False

        if returncode is not None:
            wx.CallAfter(self._append_output_safe, f"\n--- Script process exited with code: {returncode} ---\n")

    def on_input_enter(self, event):
        if self.process and self.is_script_running and self.process.stdin and not self.process.stdin.closed:
            command_to_send = self.input_ctrl.GetValue() + os.linesep # Ensure correct OS line ending
            self.input_ctrl.Clear()
            try:
                self.gui_stdout_writer.write(f"[You typed]: {command_to_send}") # Echo to GUI
                self.process.stdin.write(command_to_send)
                self.process.stdin.flush()
            except (OSError, ValueError, BrokenPipeError) as e:
                self._append_output_safe(f"\n--- Error writing to script input (pipe likely closed): {e} ---\n")
        event.Skip()

    def on_stop_button_clicked(self, event):
        if not self.is_script_running or not self.process:
            return

        self.stop_button.Enable(False)  # Disable to prevent multiple clicks

        wx.CallAfter(self._append_output_safe, "\n--- Stop requested. Sending SIGTERM to script... ---\n")

        try:
            self.process.terminate()
        except Exception as e:
            wx.CallAfter(self._append_output_safe, f"\n--- Error sending SIGTERM: {e} ---\n")
            self.stop_button.Enable(True)  # Re-enable if terminate failed
            return  # Don't start timer if terminate itself failed

        # Start a timer. If SIGTERM doesn't stop it, escalate to SIGKILL.
        if self.force_kill_timer and self.force_kill_timer.IsRunning():
            self.force_kill_timer.Stop()
        self.force_kill_timer = wx.Timer(self, self.TIMER_ID_FORCE_KILL)
        self.Bind(wx.EVT_TIMER, self.on_force_kill_timeout, id=self.TIMER_ID_FORCE_KILL)
        self.force_kill_timer.StartOnce(5000) # 5 seconds timeout for SIGKILL

    def on_force_kill_timeout(self, event):
        self.force_kill_timer = None

        if self.is_script_running and self.process and self.process.poll() is None:
            wx.CallAfter(self._append_output_safe, "\n--- SIGTERM timed out. Sending SIGKILL to script... ---\n")
            try:
                self.process.kill() # Sends SIGKILL on POSIX, TerminateProcess on Windows
            except Exception as e:
                wx.CallAfter(self._append_output_safe, f"\n--- Error sending SIGKILL: {e} ---\n")

    def on_close_window(self, event):
        if self.force_kill_timer and self.force_kill_timer.IsRunning():
            self.force_kill_timer.Stop()
            self.force_kill_timer = None

        if self.is_script_running and self.process:
            logging.debug("Window closed. Attempting to terminate script process.")
            try:
                self.process.terminate()
            except ProcessLookupError:  # Process already gone
                pass
            except Exception:
                logging.exception("Error terminating script on close.")

        self.Destroy()
        event.Skip()


class ScriptSelectorDialog(wx.Dialog):
    """Dialog for selecting a script to run, with options for development or official release versions."""

    def __init__(
        self,
        parent: wx.Window,
        title: str,
        available_scripts: List[Tuple[str, str]],
        version_base_path: dict,
    ):
        super().__init__(
            parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )

        self.available_scripts = available_scripts
        self.version_base_path = {}
        for version, base_path in version_base_path.items():
            if os.path.isdir(base_path):
                self.version_base_path[version] = base_path
            else:
                logging.debug(
                    f"Version base path '{base_path}' for '{version}' does not exist or is not a directory."
                )

        self.selected_script_path = None
        self.script_env = {}

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Script Selection
        script_names = [name for name, _ in self.available_scripts]
        script_box = wx.StaticBox(panel, label="Select Script:")
        script_box_sizer = wx.StaticBoxSizer(script_box, wx.VERTICAL)
        self.script_choice = wx.Choice(panel, choices=script_names)
        if script_names:
            self.script_choice.SetSelection(0)
        script_box_sizer.Add(self.script_choice, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(script_box_sizer, 0, wx.EXPAND | wx.ALL, 10)

        # Version Selection
        version_box = wx.StaticBox(panel, label="Select Version:")
        version_box_sizer = wx.StaticBoxSizer(version_box, wx.VERTICAL)
        self.version_radio_box = wx.RadioBox(
            panel,
            choices=list(self.version_base_path.keys()),
            majorDimension=1,  # 1 column
            style=wx.RA_SPECIFY_COLS,
        )
        self.version_radio_box.SetSelection(0)  # Default to Official Release
        version_box_sizer.Add(self.version_radio_box, 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(version_box_sizer, 0, wx.EXPAND | wx.ALL, 10)

        # Buttons
        btn_sizer = wx.StdDialogButtonSizer()
        ok_btn = wx.Button(panel, wx.ID_OK)
        ok_btn.SetDefault()
        cancel_btn = wx.Button(panel, wx.ID_CANCEL)
        btn_sizer.AddButton(ok_btn)
        btn_sizer.AddButton(cancel_btn)
        btn_sizer.Realize()
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        panel.SetSizer(main_sizer)
        main_sizer.Fit(self)  # Fit dialog to contents
        self.SetMinSize(self.GetSize())  # Prevent shrinking too much
        self.CentreOnParent()

        ok_btn.Bind(wx.EVT_BUTTON, self.on_ok)

    def on_ok(self, event):
        """Handle the OK button click event."""
        script_idx = self.script_choice.GetSelection()
        if script_idx == wx.NOT_FOUND:
            wx.MessageBox(
                "Please select a script.",
                "Selection Error",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        _, script_name = self.available_scripts[script_idx]
        version_idx = self.version_radio_box.GetSelection()
        version_str = self.version_radio_box.GetString(version_idx)

        try:
            base_path, chosen_path = find_script_path(version_str, script_name, self.version_base_path)
        except (ValueError, FileNotFoundError) as e:
            wx.MessageBox(
                str(e),
                "Error Finding Script",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        try:
            if version_str == "Development":
                # Normalize the base_path to avoid issues with trailing slashes, etc.
                normalized_base_path = os.path.abspath(os.path.normpath(base_path))

                # Get the current PYTHONPATH from the environment Odemis is running in
                current_pythonpath = os.environ.get("PYTHONPATH", "")
                # Prepend the new base_path
                self.script_env["PYTHONPATH"] = normalized_base_path + os.pathsep + current_pythonpath
                logging.debug(
                    f"For Development version, prepended '{normalized_base_path}'. New PYTHONPATH: {self.script_env['PYTHONPATH']}"
                )
        except Exception as e:
            wx.MessageBox(
                f"Error setting up PYTHONPATH: {e}",
                "Environment Setup Error",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return

        self.selected_script_path = chosen_path
        self.EndModal(wx.ID_OK)

    def get_script_path(self):
        return self.selected_script_path

    def get_script_env(self):
        return self.script_env


class ScriptRunnerPlugin(Plugin):
    name = "FAST-EM maintenance script runner with console"
    __version__ = "1.0"
    __author__ = "Nandish Patel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        self._main_app = main_app
        if microscope is None:
            return

        self.addMenu(
            "Help/Development/Service and installation scripts",
            self._on_run_script_menu,
        )

    def _on_run_script_menu(self):
        """Menu callback for running a service or installation script."""
        # Create and show the custom script selector dialog
        dialog = ScriptSelectorDialog(
            self._main_app.main_frame,
            "Select Script to Run",
            AVAILABLE_SCRIPTS,
            VERSION_BASE_PATH,
        )

        if dialog.ShowModal() == wx.ID_OK:
            script_path = dialog.get_script_path()
            script_env = dialog.get_script_env()
            if script_path:
                script_name = os.path.basename(script_path)
                logging.info(f"User selected script to run: {script_path}")

                console_frame_title = f"Console: {script_name}"
                ScriptConsoleFrame(
                    self._main_app.main_frame,
                    console_frame_title,
                    script_path,
                    script_env=script_env
                )
            else:
                logging.warning(
                    "Script selection dialog returned OK, but no script path was resolved."
                )
        else:
            logging.debug("Script selection dialog was cancelled by the user.")

        dialog.Destroy()
