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

import smbus
import time
import math
import threading
from collections import deque
from gpiozero import CPUTemperature

class I2CDevices:
    MPU6050 = 0x68
    ADS1115 = 0x48
    REG_CONVERSION = 0x00
    REG_CONFIG     = 0x01
    WHO_AM_I       = 0x75

    def __init__(self, bus_id=1):
        self.bus = smbus.SMBus(bus_id)
        self.temp = 38.04
        self.cpuTemp = 40
        self.gx = self.gy = self.gz = 0.0
        self.lastTempRead = time.time() - 5
        self.GYRO_SENSITIVITY = 131.0      # LSB/deg/s
        self.DEG_TO_RAD = math.pi / 180.0
        self.gyro_bias = [0.0, 0.0, 0.0]
        self._latest_raw = {0: 0, 1: 0}
        self._initMPU6050()

        # --- gravity vector tilt-comp filter ---
        self.g_hat = [0.0, 0.0, 1.0]       # filtered gravity unit vector (body frame)
        self.beta = 0.05                   # accel blend per update (tune 0.02 x 0.1)
        self.g_min = 0.8 * 16384.0         # accept accel magnitude ~0.8g..1.2g
        self.g_max = 1.2 * 16384.0

        self.prev_time = time.time()
        self.cpu = CPUTemperature()
        self.running = True
        self.thread = threading.Thread(target=self._update_loop, daemon=True)
        self.thread.start()

    def _initMPU6050(self, smooth_window=10):
        self.bus.write_byte_data(self.MPU6050, 0x6B, 0)  # Wake up MPU-6050
        time.sleep(0.01)                                 # settle
        self._calibrate_gyro()
        self.bearing = 0.0
        self.bearing_history = deque(maxlen=smooth_window)

    def _read_raw_data(self, addr):
        high = self.bus.read_byte_data(self.MPU6050, addr)
        low  = self.bus.read_byte_data(self.MPU6050, addr + 1)
        value = (high << 8) | low
        if value > 32767:
            value -= 65536
        return value
        
    # ---- ADS1115 helpers ----
    def _write_u16(self, reg, value):
        self.bus.write_i2c_block_data(self.ADS1115, reg, [(value >> 8) & 0xFF, value & 0xFF])

    def _read_u16(self, reg):
        b = self.bus.read_i2c_block_data(self.ADS1115, reg, 2)
        return (b[0] << 8) | b[1]

    def _read_s16(self, reg):
        v = self._read_u16(reg)
        return v - 65536 if v & 0x8000 else v

    # Sample from the A2D
    def _sample_channel(self, ch, timeout=0.01):
        mux = 0x4 + ch  # single-ended AINx vs GND
        config = ((1 << 15) | (mux << 12) | (1 << 9) | (1 << 8) | (7 << 5) | 0x03)
        self._write_u16(self.REG_CONFIG, config)
        t0 = time.time()
        while True:
            cfg = self._read_u16(self.REG_CONFIG)
            if cfg & (1 << 15): break
            if time.time() - t0 > timeout: break
            time.sleep(0.001)
        raw = self._read_s16(self.REG_CONVERSION)
        self._latest_raw[ch] = max(min(raw / 26000, 1), 0)

    # --- Accelerometer & gravity filter (replaces Euler pitch/roll path) ---
    def _read_accel(self):
        ax = float(self._read_raw_data(0x3B))
        ay = float(self._read_raw_data(0x3D))
        az = float(self._read_raw_data(0x3F))
        return ax, ay, az

    def _update_gravity(self, ax, ay, az):
        mag = math.sqrt(ax*ax + ay*ay + az*az)
        if self.g_min <= mag <= self.g_max and mag > 0.0:
            ux, uy, uz = ax/mag, ay/mag, az/mag
            gx, gy, gz = self.g_hat
            # complementary blend then renormalize
            gx = (1.0 - self.beta)*gx + self.beta*ux
            gy = (1.0 - self.beta)*gy + self.beta*uy
            gz = (1.0 - self.beta)*gz + self.beta*uz
            n = math.sqrt(gx*gx + gy*gy + gz*gz) or 1.0
            self.g_hat = [gx/n, gy/n, gz/n]

    # public access to corrected readings
    def read_gyro(self):
        return self.gx, self.gy, self.gz

    # Adjust depending on the orientation of the sensor. I have it vertically mounted
    def remap_gyro(self, gx, gy, gz):
        return gx, gy, gz

    def _read_gyro(self):
        # Convert to rad/s then remap to body frame
        gx = (self._read_raw_data(0x43) / self.GYRO_SENSITIVITY - self.gyro_bias[0]) * self.DEG_TO_RAD
        gy = (self._read_raw_data(0x45) / self.GYRO_SENSITIVITY - self.gyro_bias[1]) * self.DEG_TO_RAD
        gz = (self._read_raw_data(0x47) / self.GYRO_SENSITIVITY - self.gyro_bias[2]) * self.DEG_TO_RAD
        return self.remap_gyro(gx, gy, gz)

    def _calibrate_gyro(self):
        print("Calibrating gyro... keep the sensor still")
        samples = []
        for _ in range(300):
            try:
                gx = self._read_raw_data(0x43) / self.GYRO_SENSITIVITY
                gy = self._read_raw_data(0x45) / self.GYRO_SENSITIVITY
                gz = self._read_raw_data(0x47) / self.GYRO_SENSITIVITY
                samples.append((gx, gy, gz))
                time.sleep(0.01)
            except OSError:
                time.sleep(0.01)
        print("Finished with", len(samples), "samples")
        avg_x = sum(s[0] for s in samples) / len(samples)
        avg_y = sum(s[1] for s in samples) / len(samples)
        avg_z = sum(s[2] for s in samples) / len(samples)
        self.gyro_bias = [avg_x, avg_y, avg_z]
        print(f"Gyro bias: {self.gyro_bias}")

    def _update_loop(self):
        while self.running:
            try:
                loop_start = time.time()

                # 1) Update gravity estimate from accelerometer
                ax, ay, az = self._read_accel()
                self._update_gravity(ax, ay, az)

                # 2) Read gyro (remapped) in rad/s
                self.gx, self.gy, self.gz = self._read_gyro()

                # 3) Yaw rate = projection of ? onto gravity (in body frame)
                ux, uy, uz = self.g_hat
                yaw_rate = self.gx*ux + self.gy*uy + self.gz*uz  # rad/s

                # 4) Integrate bearing
                now = time.time()
                dt = now - self.prev_time
                # Clamp dt to avoid spikes if the thread was preempted
                if dt > 0.1: dt = 0.1
                self.prev_time = now
                self.bearing = (self.bearing + yaw_rate * dt) % (2 * math.pi)
                self.bearing_history.append(self.bearing)

                # 5) Housekeeping (temps, ADC)
                t = now
                if t - self.lastTempRead >= 0.2:
                    self.temp = (self._read_temperature() * 0.5) + (self.temp * 0.5)
                    self.cpuTemp = self.cpu.temperature
                    self.lastTempRead = t
                    self._sample_channel(0)
                    self._sample_channel(1)


                # Single sleep to control loop rate 
                elapsed = time.time() - loop_start
                delay = max(0.02, 0.01 - elapsed)
                time.sleep(delay)

            except OSError as e:
                print(e)
                time.sleep(0.01)

    def _read_temperature(self):
        temp_raw = self._read_raw_data(0x41)
        return (temp_raw / 340.0) + 36.53

    # Return the value of the variable resistors
    def getPots(self):
        return self._latest_raw[0], self._latest_raw[1]

    def getTemperature(self):
        return self.temp, self.cpuTemp

    def get_bearing(self):
        if not self.bearing_history:
            return self.bearing
        sin_sum = sum(math.sin(b) for b in self.bearing_history)
        cos_sum = sum(math.cos(b) for b in self.bearing_history)
        avg_angle = math.atan2(sin_sum, cos_sum)
        if avg_angle < 0:
            avg_angle += 2 * math.pi
        return avg_angle

    def stop(self):
        self.running = False
        self.thread.join()
