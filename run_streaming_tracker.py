# -----------------------------------------------------------------------------
# Project: Alien Motion Tracker
# Copyright (c) 2025 RobSmithDev
#
# License: Non-Commercial Copyleft with Attribution (NCCL)
# Videos: https://www.youtube.com/playlist?list=PL18CvD80w43YAV8UG24NtwRc2Wy-i7yyd
# Build Guide: https://alien.robsmithdev.co.uk
#
# Summary:
# - Free for personal, academic, and research use.
# - Derivative works must use the same license, publish their source, and credit
#   the original author.
# - Commercial use is NOT permitted without a separate license.
#
# Full license terms: see the LICENSE file or LICENSE_SUMMARY.md in this repo.
# -----------------------------------------------------------------------------


import sys
import os
import json
import logging
import ctypes
import ctypes.util
import time
import posix_ipc
import mmap
from radar.BGT60TR13C import BGT60TR13C
from radar.helper import find_register_config_in_directory, find_setting_in_directory, calculate_frame_size

SCHED_FIFO = 1
class sched_param(ctypes.Structure):
    _fields_ = [("sched_priority", ctypes.c_int)]

def set_realtime_priority(priority: int = 20):
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    param = sched_param(priority)
    pid = 0  # 0 means "current process"
    result = libc.sched_setscheduler(pid, SCHED_FIFO, ctypes.byref(param))    
    if result != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))    
    logging.info(f"Real-time scheduling set: SCHED_FIFO, priority={priority}")
    
    
# Helps keep the radar FIFO up
set_realtime_priority(40)

# Init
bgt60tr13c = None

def initRadar(reloadMode):
    global bgt60tr13c
    bgt60tr13c = BGT60TR13C(spi_speed=50_000_000)
    bgt60tr13c.check_chip_id()
    reg_fn = find_register_config_in_directory("radar/config")
    bgt60tr13c.load_register_config_file(reg_fn)
    setting_fn = find_setting_in_directory("radar/config")
    with open(setting_fn, 'r') as file:
        setting_data = json.load(file)    
    frame_size = calculate_frame_size(setting_data)
    bgt60tr13c.set_fifo_parameters(frame_size, 4096, 2048)
    
initRadar(True)

# Shared memory
SIZE = bgt60tr13c.get_frame_size()
SHM_NAME = "radar_mem1_"+str(SIZE)
SEM_NAME = "radar_lock1_"+str(SIZE)
with open("sharedmem_meta.json", "w") as f:
    json.dump({"size": SIZE, "memname":"/"+SHM_NAME, "semname":"/"+SEM_NAME}, f)

try:
    shm = posix_ipc.SharedMemory("/"+SHM_NAME, posix_ipc.O_CREX, size=SIZE)
    creator = True
except posix_ipc.ExistentialError:
    shm = posix_ipc.SharedMemory("/"+SHM_NAME)
    creator = False
try:
    sem = posix_ipc.Semaphore("/"+SEM_NAME, posix_ipc.O_CREX, initial_value=1)
except posix_ipc.ExistentialError:
    sem = posix_ipc.Semaphore("/"+SEM_NAME)

# Memory-map the shared memory
mapfile = mmap.mmap(shm.fd, bgt60tr13c.get_frame_size())
shm.close_fd()  # no longer needed

# This need sot run as root/sudo, so we need to give permissions to the othe app!
os.chmod("/dev/shm/sem." + SEM_NAME, 0o666)  
os.chmod("/dev/shm/" + SHM_NAME, 0o666)  

bgt60tr13c.start()

try:    
    logging.info("Running...")
    # Update the shared memory with "live" data
    while True:  
        time.sleep(0.1)      
        sample_bytes = bgt60tr13c.get_frame()
        if sample_bytes:
            try:
                sem.acquire()
                mapfile.seek(0)
                mapfile.write(sample_bytes)
            finally:
                sem.release()
        else:
            if bgt60tr13c.is_dead():
                bgt60tr13c.stop()
                time.sleep(1)
                bgt60tr13c.close_all()
                print("Recovering...")
                initRadar(False)
                bgt60tr13c.start()
                print("Recovered")
                
                
except KeyboardInterrupt:
    logging.info("Stopped by user")    
finally:
    bgt60tr13c.stop()
    mapfile.close()

    if creator:
        shm.unlink()
        sem.unlink()
    logging.info("Closed.")
