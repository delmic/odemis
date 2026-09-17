# -*- coding: utf-8 -*-
"""
Created on 16 Sep 2026

@author: Tim Moerkerken

Copyright © 2026 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

Live-reload preview helper for layout components.

Usage — add two lines at the bottom of any component module::

    if __name__ == "__main__":
        from odemis.gui.layout.preview import run_preview
        run_preview(TDCorrelationDialogBase)

Then run the component file directly::

    python3 -m odemis.gui.layout.components.dialog_correlation_tdct

The widget is opened immediately.  Every time you save the source file the
widget is destroyed, the module is reloaded and a fresh widget is shown.
Errors during reload are logged — fix and save again without restarting.
"""

import importlib
import logging
import os
import sys
import types
from pathlib import Path
from typing import Optional

import wx

from odemis.gui.layout.util.sizers import vbox

_POLL_MS = 500
_SHOW_ALL = os.environ.get("ODEMIS_PREVIEW_SHOW_ALL", "0") == "1"


def _patch_show_all() -> None:
    """
    Monkey-patch wx visibility methods so hide requests are ignored.

    Called once at startup when ODEMIS_PREVIEW_SHOW_ALL=1 is set.
    Every widget will be visible regardless of whether layout code calls
    Show(False) or Hide().
    """
    _original_show = wx.Window.Show

    def _show_always(self, show: bool = True) -> bool:
        return _original_show(self, True)

    def _hide_never(self) -> bool:
        return _original_show(self, True)

    wx.Window.Show = _show_always
    wx.Window.Hide = _hide_never
    logging.info(
        "ODEMIS_PREVIEW_SHOW_ALL=1: all Show(False) and Hide() calls "
        "suppressed"
    )


if _SHOW_ALL:
    _patch_show_all()


def _layout_pkg_root(mod: types.ModuleType) -> Path:
    """
    Return the absolute path to the odemis.gui.layout package directory.

    :param mod: Any module inside the odemis.gui.layout package tree.
    :returns: Absolute directory path containing the layout package.
    """
    # Walk up from the module's file until we find the directory named "layout".
    path = Path(mod.__file__).resolve()
    while True:
        path = path.parent
        if path.name == "layout":
            return path
        if path == path.parent:
            raise RuntimeError("Could not find 'layout' package root")


def _collect_mtimes(pkg_root: Path) -> dict:
    """
    Return a mapping of absolute .py path to mtime for all files under pkg_root.

    :param pkg_root: Root directory to scan.
    :returns: Dict mapping file path to float mtime.
    """
    mtimes = {}
    for fpath in pkg_root.rglob("*.py"):
        # Ignore self
        if fpath == Path(__file__):
            continue
        try:
            mtimes[fpath] = fpath.stat().st_mtime
        except OSError:
            pass
    return mtimes


def _reload_layout_modules(pkg_name: str = "odemis.gui.layout") -> None:
    """
    Reload all currently-imported modules inside the layout package.

    Modules are reloaded shallowest-first (fewest dots in name) so that
    dependencies are fresh before the modules that import them are reloaded.

    :param pkg_name: Dotted name of the layout package root.
    """
    candidates = [
        (name, mod)
        for name, mod in list(sys.modules.items())
        if name == pkg_name or name.startswith(pkg_name + ".")
        if mod is not None and hasattr(mod, "__file__") and mod.__file__
    ]
    # Shallowest first = fewest dots = dependencies before dependents.
    candidates.sort(key=lambda nm: nm[0].count("."))
    for name, mod in candidates:
        try:
            importlib.reload(mod)
            logging.debug("Reloaded %s", name)
        except Exception:
            logging.exception("Failed to reload %s", name)


def run_preview(cls: type, **kwargs) -> None:
    """
    Open a live-reload preview window for a wx widget class.

    Watches all .py files under the odemis.gui.layout package for changes.
    On any save the current widget is destroyed, all layout modules are
    reloaded (shallowest first so dependencies are refreshed before their
    dependents), and a fresh widget is shown.

    For wx.Dialog and wx.Frame subclasses the widget is shown directly with
    parent=None.  For wx.Panel (and other wx.Window) subclasses a host frame
    is created first and the widget is parented to it.

    Any keyword arguments are forwarded to the widget constructor after
    parent.

    :param cls: The wx widget class to preview.
    :param kwargs: Optional keyword arguments forwarded to the constructor.
    """
    # Force the root logger to INFO and ensure at least one handler is present
    # with a readable format.  logging.basicConfig() is a no-op when handlers
    # already exist (e.g. auto-created by odemis import warnings), so we set
    # the level and formatter explicitly.
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    fmt = logging.Formatter("%(levelname)-8s %(name)s: %(message)s")
    for handler in root.handlers:
        handler.setLevel(logging.INFO)
        handler.setFormatter(fmt)

    mod: types.ModuleType = sys.modules[cls.__module__]
    cls_name: str = cls.__name__
    pkg_root: Path = _layout_pkg_root(mod)

    app = wx.App(False)

    class _Host(wx.Frame):
        """
        Invisible 1×1 frame that owns the timer and the previewed widget.
        """

        def __init__(self) -> None:
            super().__init__(None, size=(1, 1))
            self.Hide()
            self._container: Optional[wx.Window] = None
            self._mtimes: dict = _collect_mtimes(pkg_root)
            self._init_size = None
            self._init_position = None
            self._open()
            self._timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._poll, self._timer)
            self._timer.Start(_POLL_MS)

        # ------------------------------------------------------------------
        # Widget lifecycle
        # ------------------------------------------------------------------

        def _open(self) -> None:
            """
            Instantiate and show the widget.  Errors are logged.

            For wx.Dialog and wx.Frame subclasses the widget is created with
            parent=None and shown directly.  For wx.Panel (and other
            wx.Window) subclasses a host frame is created first and the
            widget is parented to it; the host frame acts as the container
            that is tracked, sized, and destroyed on reload.
            """
            nonlocal mod
            try:
                klass = getattr(mod, cls_name)
                if issubclass(klass, (wx.Dialog, wx.Frame)):
                    container = klass(None, **kwargs)
                else:
                    host_frame = wx.Frame(None, title=cls_name)
                    widget = klass(host_frame, **kwargs)
                    with vbox() as sizer:
                        sizer.Add(widget, proportion=1, flag=wx.EXPAND)
                    host_frame.SetSizer(sizer)
                    host_frame.Layout()
                    container = host_frame
                if self._init_position is None:
                    display = wx.Display(0)
                    container.SetPosition(display.Geometry.GetTopLeft())
                    container.SetSize(*display.Geometry.Size)
                container.Show()
                self._container = container
                logging.info("Showing %s — watching %s", cls_name, pkg_root)
            except Exception:
                logging.exception("Failed to create %s — fix the error and save again", cls_name)

        def _close_widget(self) -> None:
            """
            Destroy the current container without triggering app exit.
            """
            if self._container is None:
                return
            try:
                self._container.Unbind(wx.EVT_CLOSE)
                self._container.Destroy()
            except Exception:
                pass
            self._container = None

        # ------------------------------------------------------------------
        # Event handlers
        # ------------------------------------------------------------------

        def _on_close(self, event: wx.CloseEvent) -> None:
            """
            Exit when the user closes the widget.

            :param event: Close event from the previewed container.
            """
            event.Skip()
            self._timer.Stop()
            wx.CallAfter(wx.GetApp().ExitMainLoop)

        def _poll(self, _event: wx.TimerEvent) -> None:
            """
            Check all layout .py files for changes and reload if any changed.

            :param _event: Timer event (unused).
            """
            current = _collect_mtimes(pkg_root)
            changed = [
                f for f, mtime in current.items()
                if mtime > self._mtimes.get(f, 0)
            ]
            if not changed:
                return
            self._mtimes = current
            for f in changed:
                logging.info("Change detected — %s", f.relative_to(pkg_root))
            self._reload()

        def _reload(self) -> None:
            """
            Destroy the widget, reload all layout modules, show a fresh widget.
            """
            nonlocal mod
            # Store window position and size
            try:
                self._init_size = self._container.GetSize()
                self._init_position = self._container.GetPosition()
            except:
                # If we cannot render due to an error, just not save anything and use the previous value next time
                pass
            self._close_widget()
            _reload_layout_modules()
            # _reload_layout_modules only covers odemis.gui.layout.* modules.
            # When the component file runs as __main__ it is excluded from that
            # pass, so changes made directly in the component file would be
            # silently ignored.  Reload it here by exec-ing the source into the
            # existing module namespace with __name__ temporarily overridden so
            # the `if __name__ == "__main__"` guard does not re-fire.
            layout_mod_names = {
                name for name in sys.modules
                if name.startswith("odemis.gui.layout")
            }
            if mod.__name__ not in layout_mod_names:
                try:
                    source = Path(mod.__file__).read_text(encoding="utf-8")
                    code = compile(source, mod.__file__, "exec")
                    ns = vars(mod)
                    original_name = ns.get("__name__")
                    ns["__name__"] = "__preview_reload__"
                    try:
                        exec(code, ns)  # noqa: S102
                        logging.debug("Reloaded %s", mod.__file__)
                    finally:
                        ns["__name__"] = original_name
                except Exception:
                    logging.exception("Failed to reload %s", mod.__file__)
            # Re-bind mod to the freshly reloaded module object.
            mod = sys.modules[mod.__name__]
            self._open()

            try:
                self._container.SetSize(self._init_size)
                self._container.SetPosition(self._init_position)
            except:
                pass

    host = _Host()
    app.SetTopWindow(host)
    app.MainLoop()
