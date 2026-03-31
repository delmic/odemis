# -*- coding: utf-8 -*-
"""
Created on April 1st, 2026 by the Odemis team.

Copyright © 2026 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import base64
import datetime
import logging
import random
from typing import List

import wx


# Jokes stored in a slightly encoded form. Nothing serious, just a bit of fun.
THOUGHTS = [
    b"UHJvIFRpcDogSWYgeW91ciBzYW1wbGUgc3RhcnRzICJjaGFyZ2luZywiIGp1c3QgdGVsbCBpdCB0"
    b"aGF0IHRoZSBiaWxsIGlzIG9uIHRoZSBkZXBhcnRtZW50LiBJdCB3b24ndCBoZWxwIHRoZSBpbWFn"
    b"ZSwgYnV0IGl0IG1pZ2h0IG1ha2UgeW91IGZlZWwgYmV0dGVyLg==",
    b"V2hhdCB0byBkbyB3aXRoIGJhZCBncmF0aW5ncz8gQnJpbmcgdGhlbSB0byBwcmlzbS4=",
    b"V2hhdCB0byBkbyB3aXRoIGEgY2hhcmdlZCBwYXJ0aWNsZSB0aGF0J3MgYWN0aW5nIHVwPyBLZWVw"
    b"IGFuIGlvbiBpdC4=",
    b"V2h5IGFyZSBtaWNyb3Njb3Bpc3RzIGJhZCBhdCByZWxhdGlvbnNoaXBzPyBUaGV5IGFsd2F5cyB6"
    b"b29tIGluIG9uIHRoZSBmbGF3cy4=",
    b"V2h5IGRvIGJpb2xvZ2lzdHMgbG92ZSBtaWNyb3Njb3Blcz8gQmVjYXVzZSB0aGV5IGVuam95IHRo"
    b"ZSBsaXR0bGUgdGhpbmdzIGluIGxpZmUu",
    b"V2h5IGRpZCB0aGUgcGhvdG9uIGdldCBhcnJlc3RlZD8gRm9yIGludGVyZmVyZW5jZS4=",
    b"V2hhdCBkaWQgdGhlIG9wdGljYWwgbWljcm9zY29wZSBzYXkgdG8gdGhlIGVsZWN0cm9uIG1pY3Jv"
    b"c2NvcGU/ICJTdG9wIGJlaW5nIHNvIG5lZ2F0aXZlLiI=",
    b"V2h5IHNob3VsZG4ndCB5b3UgdHJ1c3QgYW4gYXRvbSB1bmRlciBhIG1pY3Jvc2NvcGU/IEJlY2F1"
    b"c2UgdGhleSBtYWtlIHVwIGV2ZXJ5dGhpbmcu",
    b"V2hhdCdzIGEgbWljcm9zY29wZSdzIGZhdm9yaXRlIHNwb3J0PyBab29tLWJhLg==",
    b"VGhlIHNhbXBsZSBzYWlkLCAiU3RvcCBsb29raW5nIGF0IG1lIGxpa2UgdGhhdC4iIFRoZSBtaWNy"
    b"b3Njb3BlIHJlcGxpZWQsICJJIGxpdGVyYWxseSBjYW4ndCBkbyBhbnl0aGluZyBlbHNlLiI=",
    b"V2hhdCBpcyBhbiBpbWFnZSBwcm9jZXNzb3IncyBmYXZvcml0ZSBtb3ZpZT8gVGhlIEJhY2tncm91"
    b"bmQgU3VidHJhY3Rpb24u",
    b"V2h5IHdhcyB0aGUgcGl4ZWwgZmVlbGluZyBsb25lbHk/IEl0IHdhc24ndCBwYXJ0IG9mIGEgUmVn"
    b"aW9uIG9mIEludGVyZXN0Lg==",
    b"V2hhdCB0byBkbyB3aXRoIGEgcGFydHkgaW4gYSB2YWN1dW0gY2hhbWJlcj8gQWRkIHNvbWUgYXRt"
    b"b3NwaGVyZS4=",
    b"V2hhdCBkaWQgb25lIGZsdW9yb3Bob3JlIHNheSB0byB0aGUgb3RoZXI/ICJTdG9wIGJsZWFjaGlu"
    b"Zywgd2UncmUgc3VwcG9zZWQgdG8gZ2xvdyB1bmRlciBwcmVzc3VyZS4i",
]


def _decode_jokes() -> List[str]:
    """Decode and return all jokes as plain strings.

    :returns: list of joke strings
    """
    return [base64.b64decode(j).decode("utf-8") for j in THOUGHTS]


def show_important_thought_dialog(parent: wx.Window) -> None:
    """Show the joke dialog if today is April 1st.

    This function is a no-op on any other day of the year.
    Keeps showing a new joke each time the user clicks "Another one".

    :param parent: parent wx window for the dialog
    """
    today = datetime.date.today()
    if today.month != 4 or today.day != 1:
        return

    logging.debug("It's April Fools' Day, showing joke dialog")
    jokes = _decode_jokes()
    random.shuffle(jokes)
    index = 0

    while True:
        msg = "Today is a special day, the opportunity to share this important thought:\n\n" + jokes[index]
        dlg = wx.MessageDialog(
            parent,
            msg,
            caption="1 April Thought",
            style=wx.YES_NO | wx.ICON_INFORMATION,
        )
        dlg.SetYesNoLabels("Another one", "Let's use the microscope now")
        answer = dlg.ShowModal()
        dlg.Destroy()

        if answer != wx.ID_YES:
            break

        index += 1
        if index >= len(jokes):
            random.shuffle(jokes)
            index = 0
