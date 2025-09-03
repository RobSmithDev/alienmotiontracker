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


import numpy as np
import sounddevice as sd
import threading
import time
import math
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')

class AlienAudio:
    def __init__(self, sample_rate=44100, block_size=2048, latency='high'):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.latency = latency
        self.fade_len = int(0.02 * self.sample_rate)
        self.alien_detected = False
        self.stop_flag = False
        self.pitch = 1.0
        self.nextPitch = 1.0
        self.soundStartTime = time.time()+10
        self.volume = 1.0
                                       
        # Harmonic arrays with normalized data 
        self.detect_freqs = np.array([
            1766.35696973, 1762.24597601, 1760.87564477, 1764.98663849,
            1771.8382947 , 1767.72730098, 1773.20862594, 1763.61630725,
            1751.28332608, 1770.46796346, 1759.50531353, 1749.91299484,
            1748.5426636 , 1747.17233236, 1752.65365732, 1774.57895718,
            1769.09763222, 1758.13498229, 1756.76465105, 1782.80094463
        ])

        # Normalize magnitudes to a range similar to previous arrays
        raw_mags = np.array([
            11387433.21564729, 11251761.81773782, 10791236.77074591,
             9212377.17809407,  8555763.3926356 ,  8124808.82997575,
             7265085.86845172,  6685030.79921617,  5299590.92291964,
             4863383.53289835,  4586625.44790851,  4204881.13930477,
             3085012.69929448,  2998470.77182872,  2953987.37021473,
             2869361.03519348,  2683358.94931159,  2542413.6981486 ,
             2488879.71308884,  1844043.82511854
        ])
        self.detect_mags = (raw_mags / np.max(raw_mags)) * 2000.0
        self.phase_delay = -0.18
        
        self.detect_phases = np.array([
            -0.66001164,  2.71119907, -1.76161053,  0.94210066,  2.0280763 ,
            -2.02097905,  0.0996094 ,  1.60454328,  1.47130112, -2.61978794,
            -0.23729353,  2.5260381 ,  2.88273616, -2.63610954,  0.39293432,
            -1.81419211, -2.46583779,  0.01123537,  0.59935837, -2.97000326
        ]) + 2 * np.pi * self.detect_freqs * self.phase_delay                            
                                       
        self.play_sounds = False
        self.detect_duration = 0.73 #/2
        self.samples_detect = math.ceil(int(self.detect_duration * self.sample_rate)/block_size)*block_size
        self.fade_window = self.make_fade_window(self.samples_detect)

        # Embedded click sound
        raw_click = np.array([
            0.00690199, 0.01833958, 0.04929994, 0.09505029, 0.13133504, 0.16288700,
            0.22559653, 0.35673437, 0.55570893, 0.75350030, 0.83612700, 0.70400316,
            0.37152435, -0.04338395, -0.36481956, -0.52297377, -0.60185368, -0.70439755,
            -0.83040820, -0.92605009, -0.93952357, -0.85333336, -0.71131322, -0.56920784,
            -0.45970108, -0.37743548, -0.31060525, -0.25137530, -0.19897077, -0.15356651,
            -0.11514457, -0.08222120, -0.05400429, -0.03002109, -0.01058637, 0.00434139,
            0.01491683, 0.02163064, 0.02510650, 0.02590352, 0.02445240, 0.02104514,
            0.01583888, 0.00887124, 0.00010382, -0.01026885, -0.02117259, -0.03203240,
            -0.04265293, -0.05297751
        ], dtype=np.float32)
        samples_idle = math.ceil(int(self.detect_duration * self.sample_rate)/block_size)*block_size        # must be divisible by block_size    
        idlepadded = np.zeros(samples_idle, dtype=np.float32)
        idlepadded[:len(raw_click)] = raw_click * 30.0
        fade_window = self.make_fade_window(samples_idle)
        idlepadded *= fade_window
        self.loop_idle = idlepadded        
        # The silent noise is needed by some USB audio devices. They suspend when you play silence and take a while to wake up. This "noise" keeps it awake
        self.silentNoise = (np.random.rand(block_size).astype(np.float32) * 2 - 1) * 0.3e-3
        self.loop_detect =  self.generate_detection_loop()
        self.current_loop = self.loop_idle
        self.samples_current = len(self.loop_idle)
        self.samples_played = self.samples_current*2
        self.wasPlaying = False
        
        self.next_current_loop = []
        self.next_samples_current = 0
        
        self.remake = 0
        
        # Needed for service
        sd.default.device = (None, 'pulse')
        sd.default.samplerate = 44100
        sd.default.channels = 2
        
        self.thread = threading.Thread(target=self.start_stream, daemon=True)
        self.thread.start()
        
    def make_fade_window(self, length):
        padded_len = int(math.ceil(length / 1024) * 1024)
        fade_window = np.ones(padded_len, dtype=np.float32)
        fade_window[:self.fade_len] = np.linspace(0, 1, self.fade_len)
        fade_window[-self.fade_len:] = np.linspace(1, 0, self.fade_len)
        return fade_window[:length]        
        
    def enable_audio(self, enable):
        self.play_sounds = enable        
        
    # return how long its been since the sound last triggered
    def getSoundTime(self):
        return (time.time() - self.soundStartTime) * 1.2

    def generate_detection_loop(self):
        # Time vector
        t = np.linspace(0, self.detect_duration, self.samples_detect, endpoint=False)
        # Broadcasted computation of all harmonics at once
        freqs = self.detect_freqs[:, np.newaxis] * self.nextPitch
        phases = self.detect_phases[:, np.newaxis]
        mags = self.detect_mags[:, np.newaxis]
        signal = np.sum(mags * np.cos(2 * np.pi * freqs * t + phases), axis=0)
        # Normalize
        signal /= np.max(np.abs(signal))        
        # Fade window 
        signal *= self.fade_window        
        signal = signal.astype(np.float32)
        if len(self.loop_idle)>0:
            signal += self.loop_idle[:len(signal)]
        return signal

    def set_pitch(self, pitch: float):
        """Set pitch multiplier (e.g., 1.0 = normal, >1.0 = higher pitch)."""
        self.pitch = max(1.0, pitch)
        if not self.alien_detected:
            self.nextPitch = self.pitch

    def set_alien_detected(self, detected: bool):
        """Enable or disable alien detection mode (True = detection tone, False = idle click)."""
        self.alien_detected = detected
        
    def set_volume(self, volume):
        self.volume = volume                

    def audio_callback(self, outdata, frames, audiotime, status):        
        end_position = self.samples_played + frames
        data = self.current_loop[self.samples_played:end_position]
        self.samples_played = self.samples_played + frames        
        
        # At the half-way point, make the next sound
        if self.remake == 0:
            if self.samples_played >= self.samples_current/2:
                self.remake = 1
                
        if len(data)>0:
            outdata[:, 0] = (data * self.volume) + self.silentNoise
        else:
            if self.play_sounds:
                if self.wasPlaying:
                    if self.remake == 2:
                        self.current_loop = self.next_current_loop
                        self.samples_current = self.next_samples_current
                        self.remake = 0
                else:
                    self.wasPlaying = True
                    self.nextPitch = self.pitch
                    self.current_loop = self.loop_idle
                    self.samples_current = len(self.loop_idle)
                    self.remake = 0
                self.soundStartTime = time.time()                        
                outdata[:, 0] = (self.current_loop[0:frames] * self.volume) + self.silentNoise
                self.samples_played = frames 
            else:
                outdata[:, 0] = self.silentNoise
                self.nextPitch = self.pitch
                self.wasPlaying = False

    def start_stream(self):
        with sd.OutputStream(channels=1, callback=self.audio_callback,
                             samplerate=self.sample_rate, blocksize=self.block_size,
                             latency=self.latency, dtype='float32'):
            while not self.stop_flag:
                if self.remake == 1:                     
                    if self.alien_detected:                
                        self.nextPitch = (self.nextPitch * 0.8) + (self.pitch * 0.2)
                        self.next_current_loop = self.generate_detection_loop()
                        self.next_samples_current = len(self.loop_detect)
                    else:
                        self.nextPitch = self.pitch
                        self.next_current_loop = self.loop_idle
                        self.next_samples_current = len(self.loop_idle)
                    self.remake = 2                    
                sd.sleep(200)

    def stop(self):
        self.stop_flag = True
        
