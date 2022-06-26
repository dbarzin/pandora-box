#!/usr/bin/python3

import os
import time

os.system("convert -resize 1920x1080 -background black -gravity center -extent 1920x1080 image1.png bgra:/dev/fb0")
time.sleep(1)
os.system("convert -resize 1920x1080 -background black -gravity center -extent 1920x1080 image2.png bgra:/dev/fb0")
time.sleep(1)
os.system("convert -resize 1920x1080 -background black -gravity center -extent 1920x1080 image1.png bgra:/dev/fb0")
time.sleep(1)
os.system("convert -resize 1920x1080 -background black -gravity center -extent 1920x1080 image2.png bgra:/dev/fb0")
time.sleep(1)

