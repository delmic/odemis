#-*- coding: utf-8 -*-

"""
.. codeauthor:: Rinze de Laat <delaat@delmic.com>

Copyright © 2014 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
import unittest

import wx

import odemis.gui.test as test
from odemis.gui.test import gui_loop


test.goto_manual()


class GridPanelTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcgrid_frame
    # test.set_log_level(logging.DEBUG)

    def test_view_change(self):

        # test.set_sleep_time(100)

        sizer = self.frame.grid_panel.GetSizer()
        sizer.SetEmptyCellSize((0, 0))

        def show(pos=None):

            positions = [(0, 0), (0, 1), (1, 0), (1, 1)]

            for apos in positions:
                row, col = apos
                item = sizer.FindItemAtPosition(apos)

                if pos and apos != pos:
                    if item:
                        win = item.GetWindow()
                        win.Hide()

                    if sizer.IsRowGrowable(row) and pos[0] != row:
                        logging.debug("rem grow row %s", row)
                        sizer.RemoveGrowableRow(row)
                    if sizer.IsColGrowable(col) and pos[1] != col:
                        logging.debug("rem grow col %s", col)
                        sizer.RemoveGrowableCol(col)

                else:
                    if item:
                        win = item.GetWindow()
                        win.Show()

                    # Needed to update the number of rows and columns that the sizer sees
                    sizer.Layout()

                    if not sizer.IsRowGrowable(row):
                        logging.debug("add grow row %s", row)
                        sizer.AddGrowableRow(row)
                    if not sizer.IsColGrowable(col):
                        logging.debug("add grow col %s", col)
                        sizer.AddGrowableCol(col)

            sizer.Layout()

            full_size = self.frame.GetSize()[0]

            sizes = [([400], [400]), ([400], [0, 400]), ([0, 400], [400]), ([0, 400], [0, 400])]
            # self.assertEquals()

            if pos:
                pos_sizes = dict(zip(positions, sizes))
                rows, cols = pos_sizes[pos]
                self.assertEquals(sizer.RowHeights, rows)
                self.assertEquals(sizer.ColWidths, cols)

            else:
                half_size = full_size // 2
                self.assertEquals(sizer.RowHeights, [half_size, half_size])
                self.assertEquals(sizer.ColWidths, [half_size, half_size])

        logging.debug((1, 1))
        show((1, 1))
        gui_loop()

        logging.debug(None)
        show(None)
        gui_loop()

        logging.debug((0, 1))
        show((0, 1))
        gui_loop()

        logging.debug(None)
        show(None)
        gui_loop()

        logging.debug((1, 0))
        show((1, 0))
        gui_loop()

        logging.debug((0, 0))
        show((0, 0))
        gui_loop()

        logging.debug((1, 1))
        show((1, 1))
        gui_loop()

        logging.debug((1, 1))
        show(None)
        gui_loop()

    def test_view_switch(self):

        sizer = self.frame.grid_panel.GetSizer()
        sizer.SetEmptyCellSize((0, 0))

        test.set_log_level(logging.DEBUG)
        test.set_sleep_time(1000)

        def position_at(win, pos=None):
            if pos:
                if sizer.CheckForIntersectionPos(pos, (1, 1)):
                    current_item = sizer.FindItemAtPosition(pos)
                    current_win = current_item.GetWindow()
                    sizer.Detach(current_win)

                    item = sizer.FindItem(win)

                    if item:
                        old_pos = item.GetPos()
                        logging.debug("moving %s to %s", win.__class__.__name__, pos)
                        sizer.SetItemPosition(win, pos)
                        sizer.Add(current_win, old_pos, flag=wx.EXPAND)
                    else:
                        logging.debug("adding %s at %s", win.__class__.__name__, pos)
                        sizer.Add(win, pos, flag=wx.EXPAND)

                else:
                    item = sizer.FindItem(win)

                    if item:
                        logging.debug("moving %s to %s", win.__class__.__name__, pos)
                        sizer.SetItemPosition(win, pos)
                    else:
                        logging.debug("adding %s at %s", win.__class__.__name__, pos)
                        sizer.Add(win, pos, flag=wx.EXPAND)

                win.Show()
            else:
                win.Hide()
                sizer.Detach(win)

            # Hide empty rows and columns

            # A list of position/span pairs: top and bottom row, left and right column
            spans = [((0, 0), (1, 2)), ((1, 0), (1, 2)), ((0, 0), (2, 1)), ((0, 1), (2, 1))]

            for pos, span in spans:
                is_row = span[0] == 1
                row, col = pos

                if not sizer.CheckForIntersectionPos(pos, span):
                    if is_row:
                        if sizer.IsRowGrowable(row):
                            logging.debug("rem grow row %s", row)
                            sizer.RemoveGrowableRow(row)
                    else:

                        if sizer.IsColGrowable(col):
                            logging.debug("rem grow col %s", col)
                            sizer.RemoveGrowableCol(col)
                else:
                    if is_row:
                        if not sizer.IsRowGrowable(row):
                            logging.debug("add grow row %s", row)
                            sizer.AddGrowableRow(row)
                    else:
                        if not sizer.IsColGrowable(col):
                            logging.debug("add grow col %s", col)
                            sizer.AddGrowableCol(col)

            sizer.Layout()

        position_at(self.frame.red, (0, 1))
        gui_loop()

        position_at(self.frame.red, (1, 1))
        gui_loop()

        position_at(self.frame.blue)
        gui_loop()

        position_at(self.frame.purple)
        gui_loop()


if __name__ == "__main__":
    unittest.main()
