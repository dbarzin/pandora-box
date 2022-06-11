#!/usr/bin/python3

import pypandora
import time
import sys

pp = pypandora.PyPandora(root_url="http://127.0.0.1:6100")

for arg in sys.argv[1:]:
    print(arg, end="", flush=True)
    print(":", end="", flush=True)

    res = pp.submit_from_disk(arg)

    while True:
        print(".", end="", flush=True)
        time.sleep(1)

        res = pp.task_status(res["taskId"])

        if res["status"] != "WAITING":
            break

    print(res["status"])
