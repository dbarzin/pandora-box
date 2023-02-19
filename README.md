Pandora-box
============

Pandora-Box is a USB scaning station based on [Pandora](https://github.com/pandora-analysis).
It runs on Ubuntu 22.04 server LTS

It is based on recycled ThinkCenter and an integrated HDMI touch screen.

[<img src="images/box1.jpg" width="400">](images/box1.jpg)
[<img src="images/box2.jpg" width="400">](images/box2.jpg)
[<img src="images/box3.jpg" width="400">](images/box3.jpg)
[<img src="images/box4.jpg" width="400">](images/box4.jpg)


## Interface

It has a graphic user interface :

[<img src="images/key1.png" width="400">](images/key1.png)
[<img src="images/wait1.png" width="400">](images/wait1.png)
[<img src="images/ok.png" width="400">](images/ok.png)
[<img src="images/bad.png" width="400">](images/bad.png)

and a text user interface for advanced users :

[<img src="images/pandora-curses.png" width="400">](images/pandora-curses.png)

## Roadmap

If you want to contribute, we have a [roadmap](ROADMAP.md).

## Installation

The [installation and configuration procedure](INSTALL.md) is documented.

## License

Pandora-box is an open source software distributed under [GPL](https://www.gnu.org/licenses/licenses.html).

# Troubleshooting

Update Pandora

    poetry run update --yes
	
	
Check Pandora listening on port 6100

    sudo lsof -i -P -n | grep LISTEN

Result should contains 

    ...
    gunicorn: 1034         pandora    5u  IPv4  27043      0t0  TCP *:6100 (LISTEN)
    ...

Test submit a file to Panra with command line

    poetry run pandora --url http://127.0.0.1:6100 -f <<file_name>>
	
Look a the Pandora logs files

    tail -500f /var/log/pandora_message.log
    tail -500f /var/log/pandora_error.log

Look a the Pandora-box logs files

    tail -500f /var/log/pandora-box.log

