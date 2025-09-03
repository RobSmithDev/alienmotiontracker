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


import threading,os
from multiprocessing import Process, Queue
from radar.radar_dev import RadarDev
from radar.signal_proc import SigProc
from radar.helper import find_setting_in_directory
import time
#import numpy as np
from multiprocessing import Process, SimpleQueue
from queue import Empty
from threadpoolctl import threadpool_limits


# this runs in a seperate process
def radarCalc(queueFromProcess: Queue, queueToProcess: Queue):
    # Initialize device using the config
    config = find_setting_in_directory("radar/config")
    radar = RadarDev(config)
    # Initialize signal processing
    sig_proc = SigProc(radar.cfg)
    threshold = 0.5
    giro = [0, 0]

    while True:        
        # Consume everything coming in
        try:
            threshold, gx, gy = queueFromProcess.get(timeout = 0.01)
            giro = [max(abs(gx),giro[0]),max(abs(gy),giro[1])]
            while True:
                threshold, gx, gy = queueFromProcess.get(block=False)
                giro = [max(abs(gx),giro[0]),max(abs(gy),giro[1])]                
        except Empty:
            pass
                        
        # Now run the signal processing
        frame = radar.get_next_frame()
        if frame is None:
            time.sleep(0.05)
        else:
            locations = sig_proc.update_with_sensitivity(frame, s=threshold, vx_mps=giro[0], vy_mps=giro[1])
            queueToProcess.put(locations)
            
        giro[0] = giro[0] / 2
        giro[1] = giro[1] / 2
        

class RadarMonitor:
    def __init__(self):        
        self.threshold = 0.5        
        self._lock = threading.Lock()
        self.giro = [0, 0]    
    
        self.processorQueueToProcess = Queue(maxsize=40)
        self.processorQueueFromProcess = Queue(maxsize=4)        
        self.processor = Process(target=radarCalc, args=(self.processorQueueToProcess,self.processorQueueFromProcess,), daemon=True)

        self.processor.start()        
        
    def receive_gyro(self, x, y, z, yaw):
        self.giro = [abs(x),abs(y)]
        #print(float((np.hypot(self.giro[0], self.giro[1]))))
                
    # Return all detections since last call
    def getDetections(self):
        ret = []
        try:
            while True:
                ret.extend(self.processorQueueFromProcess.get(block=False))
        except Empty:
            pass
                    
        return ret
            
    def setSensitivity(self, sense):
        # If you put the slider in the other way around, remove the 1.0- 
        self.threshold = 0.5 if 0.48 < sense < 0.52 else sense
            
    def pushUpdate(self):        
        try:
            self.processorQueueToProcess.put([self.threshold, self.giro[0], self.giro[1]], block=False)
        except Exception:
            pass
   
                
                
