
import os

from wx.tools.img2py import img2py

os.unlink("data.py")

first = True

for _, _, files in os.walk('.'):
    for f in files:
        if f.endswith('.png'):
            img2py(f, "data.py", append=not first, catalog=True)
            first = False
