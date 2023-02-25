#!/usr/bin/python3

def waitMouseClick():
    print("wait mouse click")
    mouse = open("/dev/input/mice", "rb")
    down = False
    while True:
        buf = mouse.read(3)
        if ((buf[0] & 0x1) == 1):
            down = True
        if (((buf[0] & 0x1) == 0) and down):
            break
    mouse.close()


def loop(state):
    print('loop ' + state)
    match state:
        case "START":
            waitMouseClick()
            return "STEP1"
        case "STEP1":
            waitMouseClick()
            return "STEP2"
        case "STEP2":
            waitMouseClick()
            return "STOP"
        case _:
            print("Unknwn state " + state)
            return "STOP"
    print("end loop")


if __name__ == "__main__":
    # The client code.
    state = "START"
    while (state != "STOP"):
        state = loop(state)
