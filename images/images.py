#!/usr/bin/python3

# display all images and reset

import os
import time

format = "1020x600"
#format = "1920x1080"

images = ["pandora-box1.png", "pandora-box2.png", "pandora-box3.png", "pandora-box4.png"]

for image in images :
	os.system("convert -resize %s -background black -gravity center -extent %s %s bgra:/dev/fb0" % (format,format,image))
	time.sleep(1)

os.system("reset")


