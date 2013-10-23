#!/bin/bash

. /etc/odemis.conf

# PYTHONPATH="../../src/odemis/gui/test:$PYTHONPATH"

export PYTHONPATH

echo $PYTHONPATH

# Code doc root
code_path="/home/rinze/dev/odemis/doc/code"

cd "$code_path"

# Remove old rst files
# rm -rf _gen/*.rst

# Build rst files (Add -f switch to force overwrite)
sphinx-apidoc -o ./_gen ../../src/

# Remove old html files
# rm -rf _build/*

# Create html
make html
