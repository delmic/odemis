#!/bin/bash
# Use the original GUI icon files to generate the icons files for all the platforms
# and needs.
# Note: you need the "imagemagick" package. The graphicsmagick package is not enough
# (as it doesn't support writing Windows ICO files)
# You also need pngcrush.

# The original icon files are:
ORIG_GUI=image/icon_gui_full.png
ORIG_VIEWER=image/icon_gui_viewer.png

WX_ICON_PATH=src/odemis/gui/img/icon/
LINUX_ICON_PATH=install/linux/usr/share/icons/hicolor/
WIN_ICON_PATH=install/windows/
DOC_ICON_PATH=doc/develop/_static/

# Calls pngcrush on the given file
icrush() {
    pngcrush -brute -rem alla "$1" "$1".opt
    mv "$1".opt "$1"
}

# Make sure they are optimised
icrush $ORIG_GUI
icrush $ORIG_VIEWER

# For wxPython GUI
cp $ORIG_GUI $WX_ICON_PATH/ico_gui_full_256.png
cp $ORIG_VIEWER $WX_ICON_PATH/ico_gui_viewer_256.png

#./src/odemis/gui/img/img2python.py


# For Linux (menu & window manager)
cp $ORIG_GUI $LINUX_ICON_PATH/256x256/apps/odemis.png
cp $ORIG_VIEWER $LINUX_ICON_PATH/256x256/apps/odemis-viewer.png
for r in 128x128 64x64 32x32; do
    # Note: -adaptive-resize makes it less blurry, but doesn't seem to help
    convert $ORIG_GUI -resize $r $LINUX_ICON_PATH/$r/apps/odemis.png
    icrush $LINUX_ICON_PATH/$r/apps/odemis.png
    convert $ORIG_VIEWER -resize $r $LINUX_ICON_PATH/$r/apps/odemis-viewer.png
    icrush $LINUX_ICON_PATH/$r/apps/odemis-viewer.png
done


# For Windows
convert -background transparent $ORIG_GUI -define icon:auto-resize=16,32,48,64,256 $WIN_ICON_PATH/odemis.ico
convert -background transparent $ORIG_VIEWER -define icon:auto-resize=16,32,48,64,256 $WIN_ICON_PATH/odemis-viewer.ico

# For the doc
convert $ORIG_GUI -bordercolor white -border 0 -resize 16x16 $DOC_ICON_PATH/odemis.ico
