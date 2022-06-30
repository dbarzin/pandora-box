#!/usr/bin/python3

# display all images and reset

import os
import time

size = "1024x600"
#size = "1920x1080"

images = ["pandora-box1.png", "pandora-box2.png", "pandora-box3.png", "pandora-box4.png"]

for image in images :
	os.system("convert -resize %s -background black -gravity center -extent %s %s bgra:/dev/fb0" % (size, size, image))
	time.sleep(1)

os.system("reset")


