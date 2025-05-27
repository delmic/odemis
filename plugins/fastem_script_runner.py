# -*- coding: utf-8 -*-
"""
Created on 23 May 2025

@author: Nandish Patel

Gives ability to use to run Service and installation scripts under Help > Development.

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

import ctypes
import io
import logging
import os
import queue
import runpy
import sys
import threading
import traceback
from typing import List, Optional, Tuple

import wx

from odemis.gui.plugin import Plugin

DEV_SCRIPT_BASE_PATH = os.path.expanduser("~/development/fastem-calibrations/")
RELEASE_SCRIPT_BASE_PATH = "/usr/share/fastem-calibrations"
VERSION_CHOICES = ["Official Release", "Development"]
AVAILABLE_SCRIPTS = [
    ("Move galvo", "src/fastem_calibrations/tooling/move_galvo.py"),
    ("Raster galvo", "src/fastem_calibrations/tooling/raster_galvo.py"),
    ("Set correction collar", "src/fastem_calibrations/tooling/z_stack_acquisition.py"),
    (
        "Acquire image with decreased amplitude and offset",
        "src/fastem_calibrations/tooling/adjusted_offset_amp_acquisition.py",
    ),
    ("Periodic maintenance", "src/fastem_calibrations/tooling/periodic_maintenance.py"),
    (
        "Scan amplitude pre-align calibration",
        "src/fastem_calibrations/scan_amplitude_pre_align.py",
    ),
    ("Stage to multiprobe", "src/fastem_calibrations/stage_to_multiprobe.py"),
    ("Pattern calibration", "src/fastem_calibrations/pattern_calibration.py"),
    ("Pitch calibration", "src/fastem_calibrations/pitch_calibration.py"),
]


def raise_exception_in_thread(thread_id: Optional[int], exctype: Exception) -> bool:
    """
    Raises an exception in the context of the given thread ID.
    :param thread_id: The ID of the thread in which to raise the exception.
    :param exctype: The exception type to raise.
    :return: True if the exception was successfully raised, False otherwise.
    """
    if thread_id is None:
        return False
    try:
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_long(thread_id), ctypes.py_object(exctype)
        )
        if res == 0:
            return False
        elif res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), None)
            # Consider raising an error or logging more severely here
            return False  # Treat as failure for safety
        return True
    except Exception:
        return False


class WxTextCtrlOutput:
    """Custom output stream to redirect text output to a wx.TextCtrl."""

    def __init__(self, text_ctrl, style=None):
        """
        :param text_ctrl: The wx.TextCtrl to which output will be directed.
        :param style: Optional wx.TextAttr style for the text.
        """
        self.text_ctrl = text_ctrl
        self.style = style
        self.default_style = text_ctrl.GetDefaultStyle()

    def write(self, text):
        """
        Write text to the wx.TextCtrl, ensuring it is done on the main thread.
        :param text: The text to write to the TextCtrl.
        """
        if not wx.IsMainThread():
            wx.CallAfter(self._do_write, text)
            return
        self._do_write(text)

    def _do_write(self, text):
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

        self.text_ctrl.ShowPosition(
            self.text_ctrl.GetLastPosition()
        )  # Auto-scroll to end

    def _append_text_styled(self, text, style):
        """
        Append text to the TextCtrl with a specific style.
        :param text: The text to append.
        :param style: The wx.TextAttr style to apply to the text.
        """
        if not self.text_ctrl:
            return
        current_style = self.text_ctrl.GetDefaultStyle()
        self.text_ctrl.SetDefaultStyle(style)
        self.text_ctrl.AppendText(text)
        self.text_ctrl.SetDefaultStyle(current_style)  # Reset to what it was or default

        self.text_ctrl.ShowPosition(
            self.text_ctrl.GetLastPosition()
        )  # Auto-scroll to end

    def flush(self):
        """
        Flush the output stream. This is a no-op for wx.TextCtrl as it updates immediately.
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
            self.handleError(
                record
            )  # Default error handling (prints to original stderr)

    def close(self):
        # In this case, the underlying stream (WxTextCtrlOutput) is managed
        # by the ScriptConsoleFrame, so we don't close it here directly.
        # We might want to nullify the reference to help with GC if needed.
        self.wx_stream = None
        super().close()


class WxTextCtrlInput:
    """Custom input stream to redirect input() calls to a wx.TextCtrl."""

    def __init__(
        self,
        input_ctrl: wx.TextCtrl,
        output_ctrl: wx.TextCtrl,
        input_prompt_label: wx.StaticText,
    ):
        """
        :param input_ctrl: The wx.TextCtrl to which input will be directed.
        :param output_ctrl: The wx.TextCtrl to echo the input prompt.
        :param input_prompt_label: The wx.StaticText label to show the input prompt.
        """
        self.input_ctrl = input_ctrl
        self.output_ctrl = output_ctrl
        self.input_prompt_label = input_prompt_label
        self.input_queue = queue.Queue(maxsize=1)
        self.is_closed = False
        self._prompt_active = False  # To manage prompt display

    def _activate_prompt(self, prompt_text: str = ""):
        """
        Activate the input prompt and enable the input control.
        :param prompt_text: Optional text to display as the prompt.
        """
        if not self.input_ctrl:
            return
        self.input_ctrl.Enable(True)
        self.input_ctrl.SetFocus()
        self.input_prompt_label.SetLabel(prompt_text if prompt_text else "Input: ")
        self.input_prompt_label.Parent.Layout()
        self._prompt_active = True

    def _deactivate_prompt(self):
        """Deactivate the input prompt and disable the input control."""
        if not self.input_ctrl:
            return
        self.input_ctrl.Enable(False)
        self.input_prompt_label.SetLabel("")
        self._prompt_active = False

    def readline(self):
        """Read a line from the input stream, blocking until input is available."""
        if self.is_closed:
            raise EOFError("Console input stream closed.")

        # Get the prompt string from input() if any.
        # input() prints its prompt to stdout. We don't capture it here directly
        # but our WxTextCtrlOutput will display it.
        # We just need to enable the input field.
        wx.CallAfter(self._activate_prompt)

        try:
            line = self.input_queue.get(block=True, timeout=None)  # Wait indefinitely
            if line is None:  # Sentinel for closing
                self.is_closed = True  # Ensure it's marked closed
                raise EOFError(
                    "Console input stream explicitly closed during readline."
                )
            # Echo the input. The newline is already part of 'line' from on_input_enter
            if self.output_ctrl:  # Echo input to output
                wx.CallAfter(self.output_ctrl.AppendText, line)
            return line
        except (
            queue.Empty
        ):  # Should not happen with timeout=None unless queue is closed
            self.is_closed = True
            raise EOFError("Console input stream timed out or closed.")
        finally:
            if self._prompt_active:  # Only deactivate if we activated it
                wx.CallAfter(self._deactivate_prompt)

    def read(self, size=-1):
        """
        Read a specified number of characters from the input stream.
        :param size: Number of characters to read. If -1, read until EOF.
        :return: The read characters as a string.
        """
        return self.readline()

    def fileno(self):
        """
        Return the file descriptor for the input stream.
        This is not applicable for wx.TextCtrl, so we raise an error.
        :raises io.UnsupportedOperation: Always raised since wx.TextCtrl does not have a fileno.
        """
        raise io.UnsupportedOperation("fileno")

    def isatty(self):
        """Check if the input stream is a TTY (terminal)."""
        return True

    def close(self):
        """
        Close the input stream, signaling that no more input will be provided.
        This will cause readline() to raise EOFError on the next call.
        """
        if not self.is_closed:
            self.is_closed = True
            self.input_queue.put(None)  # Signal readline to unblock and raise EOFError

    def provide_input(self, text: str):
        """
        Provide input to the console. This method is called when the user types in the input field.
        :param text: The text to provide as input.
        """
        if not self.is_closed:
            try:
                self.input_queue.put_nowait(text)
            except queue.Full:
                # This can happen if provide_input is called rapidly before readline consumes.
                # Or if readline is already unblocked by a close() signal.
                if self.output_ctrl:
                    wx.CallAfter(
                        self.output_ctrl.AppendText,
                        "\n[Input ignored: console closing or busy]\n",
                    )


class ScriptConsoleFrame(wx.Frame):
    """A wx.Frame that serves as a console for running Python scripts with input/output redirection."""

    def __init__(
        self,
        parent: wx.Window,
        title: str,
        script_path: str,
        script_globals: Optional[dict] = None,
    ):
        """
        :param parent: The parent wx.Window for this frame.
        :param title: The title of the console window.
        :param script_path: The path to the Python script to run.
        :param script_globals: Optional dictionary of global variables to pass to the script.
        """
        super().__init__(parent, title=title, size=(800, 600))
        self.script_path = script_path
        self.script_globals = script_globals if script_globals else {}
        self.script_thread = None
        self.is_script_running = False
        self._stop_event = threading.Event()  # For signaling the script thread to stop
        self.force_stop_timer = None  # Timer for escalating to force stop

        self.panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        self.output_ctrl = wx.TextCtrl(
            self.panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2
        )
        font = wx.Font(
            10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL
        )
        self.output_ctrl.SetFont(font)
        vbox.Add(self.output_ctrl, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)

        # Input Area and Stop Button hbox
        controls_hbox = wx.BoxSizer(wx.HORIZONTAL)

        # Input Prompt and TextCtrl
        input_sub_hbox = wx.BoxSizer(wx.HORIZONTAL)
        self.input_prompt_label = wx.StaticText(self.panel, label="")
        self.input_prompt_label.SetFont(font)
        input_sub_hbox.Add(
            self.input_prompt_label,
            flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT,
            border=5,
        )

        self.input_ctrl = wx.TextCtrl(self.panel, style=wx.TE_PROCESS_ENTER)
        self.input_ctrl.SetFont(font)
        self.input_ctrl.Enable(False)
        self.input_ctrl.Bind(wx.EVT_TEXT_ENTER, self.on_input_enter)
        input_sub_hbox.Add(self.input_ctrl, proportion=1, flag=wx.EXPAND)

        controls_hbox.Add(
            input_sub_hbox, proportion=1, flag=wx.EXPAND | wx.RIGHT, border=10
        )

        # Stop Button
        self.stop_button = wx.Button(self.panel, label="Stop Script (Ctrl+C)")
        self.stop_button.Bind(wx.EVT_BUTTON, self.on_stop_button_clicked)
        self.stop_button.Enable(False)  # Enabled when script is running
        controls_hbox.Add(
            self.stop_button,
            flag=wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT,
            border=5,
        )

        vbox.Add(
            controls_hbox, flag=wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, border=5
        )
        # --- End Input Area and Stop Button HBox ---

        self.panel.SetSizer(vbox)
        self.Layout()
        self.Bind(wx.EVT_CLOSE, self.on_close_window)

        self.original_stdin = sys.stdin
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr

        self.stdin_redirect = WxTextCtrlInput(
            self.input_ctrl, self.output_ctrl, self.input_prompt_label
        )
        self.stdout_redirect = WxTextCtrlOutput(self.output_ctrl)
        self.stderr_redirect = WxTextCtrlOutput(
            self.output_ctrl, style=wx.TextAttr(wx.RED)
        )
        self.log_handler = None

        self.start_script_execution()
        self.Show()

    def on_input_enter(self, event):
        """Handle the Enter key press in the input control."""
        if self.is_script_running and not self.stdin_redirect.is_closed:
            command = self.input_ctrl.GetValue() + "\n"
            self.input_ctrl.Clear()
            self.stdin_redirect.provide_input(command)
        event.Skip()

    def start_script_execution(self):
        """Start the script execution in a separate thread."""
        if self.is_script_running:
            return
        self.is_script_running = True
        self.stop_button.Enable(True)  # Enable stop button
        self._stop_event.clear()
        self.output_ctrl.AppendText(f"--- Running script: {self.script_path} ---\n")
        self.script_thread = threading.Thread(target=self._execute_script_target)
        self.script_thread.daemon = True
        self.script_thread.start()

    def _execute_script_target(self):
        """The target function for the script execution thread."""
        _old_stdin, _old_stdout, _old_stderr = sys.stdin, sys.stdout, sys.stderr
        _old_argv = list(sys.argv)
        sys.stdin = self.stdin_redirect
        sys.stdout = self.stdout_redirect
        sys.stderr = self.stderr_redirect
        sys.argv = [self.script_path]

        script_logger = logging.getLogger()
        if not script_logger.hasHandlers() or not any(
            isinstance(h, WxLogHandler) for h in script_logger.handlers
        ):
            script_logger.setLevel(logging.DEBUG)

        self.log_handler = WxLogHandler(self.stdout_redirect)
        self.log_handler.setLevel(logging.DEBUG)
        script_logger.addHandler(self.log_handler)

        merged_globals = {
            "__name__": "__main__",
            "console_should_stop": self._stop_event.is_set,
        }
        if self.script_globals:
            merged_globals.update(self.script_globals)

        try:
            # The core idea for Ctrl+C is to raise KeyboardInterrupt in the script's thread.
            # However, directly injecting exceptions into other threads is tricky and
            # platform-dependent (e.g., using ctypes and PyThreadState_SetAsyncExc).
            # The `_stop_event` and `stdin_redirect.close()` are more cooperative.
            # If a script is truly stuck in a C extension without releasing the GIL,
            # those cooperative methods might not work immediately.

            # For now, we rely on the cooperative stop via _stop_event and EOFError on input.
            runpy.run_path(
                self.script_path, init_globals=merged_globals, run_name="__main__"
            )

            if not self._stop_event.is_set():
                wx.CallAfter(
                    self._append_output_safe, "\n--- Script execution finished ---\n"
                )
        except KeyboardInterrupt:  # This would be the ideal if we could inject it
            wx.CallAfter(
                self._append_output_safe,
                "\n--- Script interrupted (KeyboardInterrupt) ---\n",
            )
        except EOFError:
            wx.CallAfter(
                self._append_output_safe,
                "\n--- Script execution interrupted (input closed or EOF) ---\n",
            )
        except SystemExit as e:
            wx.CallAfter(
                self._append_output_safe,
                f"\n--- Script exited with code: {e.code} ---\n",
            )
        except Exception:
            tb_str = traceback.format_exc()
            wx.CallAfter(
                self._append_output_safe, f"\n--- Script Error ---\n{tb_str}\n"
            )
        finally:
            if self.log_handler:
                script_logger.removeHandler(self.log_handler)
                self.log_handler.close()
                self.log_handler = None

            sys.stdin = _old_stdin
            sys.stdout = _old_stdout
            sys.stderr = _old_stderr
            sys.argv = _old_argv
            wx.CallAfter(self._script_execution_completed)

    def _append_output_safe(self, text):
        """Safely append text to output_ctrl, checking if it still exists."""
        if self.output_ctrl:  # Check if the widget still exists
            self.output_ctrl.AppendText(text)

    def _script_execution_completed(self):
        """Called when the script execution is completed or interrupted."""
        self.is_script_running = False
        if self.stop_button:
            self.stop_button.Enable(False)
        if self.input_ctrl:
            self.input_ctrl.Enable(False)
        if self.input_prompt_label:
            self.input_prompt_label.SetLabel("")

    def _attempt_graceful_stop(self):
        """Initiates the stop sequence for the script."""
        if (
            self.is_script_running
            and self.script_thread
            and self.script_thread.is_alive()
        ):
            self._stop_event.set()  # Signal the script to stop (if it checks)
            if self.stdin_redirect:
                self.stdin_redirect.close()  # This will cause input() to raise EOFError
            return True
        return False

    def on_stop_button_clicked(self, event):
        """Handle the stop button click event."""
        if not self.is_script_running:
            return

        # Disable button immediately to prevent multiple clicks during stop attempt
        if self.stop_button:
            self.stop_button.Enable(False)

        wx.CallAfter(
            self._append_output_safe,
            "\n--- Stop requested. Attempting graceful stop... Please wait for 5 seconds. ---\n",
        )

        if self._attempt_graceful_stop():
            # If graceful stop was initiated, start a timer.
            # If the script doesn't stop cooperatively within N seconds, then try forceful.
            if self.force_stop_timer is not None and self.force_stop_timer.IsRunning():
                self.force_stop_timer.Stop()  # Should not happen if logic is correct

            timer_id = wx.NewIdRef()
            self.force_stop_timer = wx.Timer(self, timer_id)
            self.Bind(wx.EVT_TIMER, self.on_force_stop_timeout, id=timer_id)
            self.force_stop_timer.StartOnce(5000)  # 5 seconds timeout
        else:
            # Script wasn't running or thread not alive, _attempt_graceful_stop returned False.
            if (
                self.stop_button and not self.is_script_running
            ):  # Check self.is_script_running again
                self.stop_button.Enable(
                    False
                )  # Keep it disabled if script truly stopped
            elif self.stop_button and self.is_script_running:
                self.stop_button.Enable(True)  # Re-enable if script is still running

    def on_force_stop_timeout(self, event):
        """Called if graceful stop doesn't work within the timeout."""
        if (
            self.is_script_running
            and self.script_thread
            and self.script_thread.is_alive()
        ):
            wx.CallAfter(
                self._append_output_safe,
                "\n--- Graceful stop timed out. Attempting forceful interruption (KeyboardInterrupt)... ---\n",
            )
            try:
                thread_id = (
                    self.script_thread.ident
                )  # Get the integer thread identifier
                if thread_id is not None:
                    if raise_exception_in_thread(thread_id, KeyboardInterrupt):
                        wx.CallAfter(
                            self._append_output_safe,
                            "--- KeyboardInterrupt signal sent to script thread. ---\n",
                        )
                    else:
                        wx.CallAfter(
                            self._append_output_safe,
                            "--- Failed to send KeyboardInterrupt (thread might have exited). ---\n",
                        )
                else:
                    wx.CallAfter(
                        self._append_output_safe,
                        "--- Could not get script thread ID for forceful stop. ---\n",
                    )
            except Exception as e:
                wx.CallAfter(
                    self._append_output_safe,
                    f"\n--- Error during forceful interruption: {e} ---\n",
                )

    def on_close_window(self, event):
        """Handle the window close event."""
        if self._attempt_graceful_stop():
            wx.CallAfter(
                self._append_output_safe,
                "\n--- Console closing. Graceful stop initiated. ---\n",
            )

        if self.log_handler:
            root_logger = logging.getLogger()
            if self.log_handler in root_logger.handlers:
                root_logger.removeHandler(self.log_handler)
            self.log_handler.close()
            self.log_handler = None

        if self.stdin_redirect:
            self.stdin_redirect.close()
            self.stdin_redirect.input_ctrl = None
            self.stdin_redirect.output_ctrl = None
        if self.stdout_redirect:
            self.stdout_redirect.text_ctrl = None
        if self.stderr_redirect:
            self.stderr_redirect.text_ctrl = None

        self.Destroy()
        event.Skip()


class ScriptSelectorDialog(wx.Dialog):
    """Dialog for selecting a script to run, with options for development or official release versions."""

    def __init__(
        self,
        parent: wx.Window,
        title: str,
        available_scripts: List[Tuple[str, str]],
        dev_base: str,
        release_base: str,
    ):
        super().__init__(
            parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )

        self.available_scripts = available_scripts
        self.dev_base = dev_base
        self.release_base = release_base

        self.selected_script_path = None  # To store the selected script path

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
            choices=VERSION_CHOICES,
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

        _, relative_path = self.available_scripts[script_idx]
        version_idx = self.version_radio_box.GetSelection()  # 0 for Release, 1 for Dev

        base_path = self.release_base if version_idx == 0 else self.dev_base
        chosen_path = os.path.join(base_path, relative_path)

        if not os.path.exists(chosen_path) or not os.path.isfile(chosen_path):
            version_str = VERSION_CHOICES[version_idx]
            wx.MessageBox(
                f"The selected script was not found at the expected location for the '{version_str}' version:\n\n{chosen_path}",
                "File Not Found",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            self.selected_script_path = None
            return

        self.selected_script_path = chosen_path
        self.EndModal(wx.ID_OK)

    def get_selected_script_path(self):
        return self.selected_script_path


class ScriptRunnerPlugin(Plugin):  # Your plugin class
    name = "Script Runner with Console"
    __version__ = "1.0"
    __author__ = "Nandish Patel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        self._main_app = main_app
        self._microscope = microscope

        self.addMenu(
            "Help/Development/Service and installation scripts",
            self._on_run_script_menu,
        )

    def _on_run_script_menu(self):
        """Menu callback for running a service or installation script."""
        parent_frame = (
            self._main_app.GetTopWindow()
            if hasattr(self._main_app, "GetTopWindow")
            else None
        )

        # Create and show the custom script selector dialog
        AVAILABLE_SCRIPTS.sort(key=lambda x: x[0])
        dialog = ScriptSelectorDialog(
            parent_frame,
            "Select Script to Run",
            AVAILABLE_SCRIPTS,
            DEV_SCRIPT_BASE_PATH,
            RELEASE_SCRIPT_BASE_PATH,
        )

        if dialog.ShowModal() == wx.ID_OK:
            script_path = dialog.get_selected_script_path()
            if script_path:
                script_name = os.path.basename(script_path)
                logging.info(f"User selected script to run: {script_path}")

                console_frame_title = f"Console: {script_name}"
                ScriptConsoleFrame(
                    parent_frame,
                    console_frame_title,
                    script_path,
                )
            else:
                logging.warning(
                    "Script selection dialog returned OK, but no script path was resolved."
                )
        else:
            logging.debug("Script selection dialog was cancelled by the user.")

        dialog.Destroy()
