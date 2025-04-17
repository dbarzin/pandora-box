#!/usr/bin/bash
# sudo apt install pylint black python3-autopep8

black -l 90 ./pandora-box.py

autopep8 ./pandora-box.py --in-place --aggressive --aggressive

pylint ./pandora-box.py
