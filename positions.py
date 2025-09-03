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


import time
from dataclasses import dataclass
from math import sin, cos, pi
from typing import Dict, Tuple
from pprint import pprint

@dataclass
class Position:
    distance: float
    angle: float        # in radians
    timestamp: float    # when it was added or last updated
    posX: int           # where it would appear in screen if the bearing was 0, for fast detection of duplicates
    posY: int

class PositionStore:
    # width and height are radius's
    def __init__(self, width, height):
        self.positions: Dict[Tuple[int, int], Position] = {}  # keyed by (posX, posY)
        self.width = round(width)
        self.height = round(height)

    def addAliens(self, currentBearing, locations):
        now = time.time()
        if not locations:
            return
                
        #for (distance, angle, velocity), score in zip(locations, scores):
        for (distance, angle) in locations:
            if distance <= 11.0:    # reject anything after 11m its out the range of the display and is a little noisy at this distance anyway
                # angle is in degrees, convert to radians and compensate for the current rotation 
                realTheta = (angle * pi / 180.0) - currentBearing - (3.14159/2)
                posX = round(self.width * cos(realTheta) * distance)
                posY = round(self.width * sin(realTheta) * distance)
                key = (posX, posY)
                if key in self.positions:
                    self.positions[key].timestamp = now
                else:
                    self.positions[key] = Position(distance, realTheta, now, posX, posY)
   
    def reset(self):
        self.positions.clear()
   
    def remove_old_positions(self, timeout: float = 5.0):
        now = time.time()
        self.positions = {
            k: v for k, v in self.positions.items()
            if now - v.timestamp < timeout
        }

    def get_positions(self):
        return [p for p in self.positions.values()]
