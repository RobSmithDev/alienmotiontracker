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

import json
import os
import sys
import posix_ipc
import mmap
import numpy as np
import time

from radar.helper import parse_radar_cfg, find_setting_in_directory, parse_full_frame, read_uint12, split_samples


class RadarDev:
    def __init__(self, setting_fn):
        self.shm = None
        self.sem = None
        self.mapfile = None
        self.SIZE = 0
        self.SHM_NAME = ""
        self.SEM_NAME = ""
        self.lastSeq = -1

        with open(setting_fn, 'r') as file:
            setting = json.load(file)
        with open("sharedmem_meta.json", 'r') as file:
            tmp = json.load(file)
            self.SIZE = tmp["size"]
            self.SHM_NAME = tmp["memname"]
            self.SEM_NAME = tmp["semname"]
            
        self.cfg = parse_radar_cfg(setting)
        shm = posix_ipc.SharedMemory(self.SHM_NAME)
        self.sem = posix_ipc.Semaphore(self.SEM_NAME)
        self.mapfile = mmap.mmap(shm.fd, self.SIZE)
        shm.close_fd()
        
    def get_next_frame(self):
        try:
            self.sem.acquire()
            self.mapfile.seek(0)
            raw_data = self.mapfile.read(self.SIZE)
        finally:            
            self.sem.release()
        raw_data = list(raw_data)
        (version, seq, data_len, raw_data) = parse_full_frame(raw_data)

        # Prevent re-processing the same data
        if (self.lastSeq == seq):
            return None
        self.lastSeq = seq
        
        adc_data = read_uint12(raw_data)
        adc_data_split = split_samples(adc_data, 1, self.cfg["num_chirps_per_frame"], self.cfg["num_samples_per_chirp"], self.cfg["num_antennas"])
        adc_data_split = np.transpose(adc_data_split[0,:,:,:], (2, 0, 1))
        return adc_data_split

