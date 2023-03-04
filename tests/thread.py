#!/usr/bin/python3

import queue
import threading
import time

exitFlag = False


class myThread (threading.Thread):
    def __init__(self, id, q):
        threading.Thread.__init__(self)
        self.id = id
        self.q = q

    def run(self):
        print(f"Thread-{self.id} Starting ")
        self.process_data()
        print(f"Thread-{self.id} Done.")

    def process_data(self):
        while not exitFlag:
            queueLock.acquire()
            if not workQueue.empty():
                data = self.q.get()
                queueLock.release()
                print(f"Thread-{self.id} processing {data}")
            else:
                queueLock.release()
            time.sleep(1)


maxThread = 2
workList = ["One", "Two", "Three", "Four", "Five", "Six", 'Seven']
queueLock = threading.Lock()
workQueue = queue.Queue()
threads = []

# Create new threads
for i in range(maxThread):
    thread = myThread(i, workQueue)
    thread.start()
    threads.append(thread)

# Fill the queue
queueLock.acquire()
for word in workList:
    workQueue.put(word)
queueLock.release()

# Wait for queue to empty
while not workQueue.empty():
    pass

# Notify threads it's time to exit
exitFlag = True

# Wait for all threads to complete
for t in threads:
    t.join()

print("Exiting Main Thread")
