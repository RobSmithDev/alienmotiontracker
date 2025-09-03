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
#
# There is literally nothing left from the original version of this file!

import os
import numpy as np
from scipy.ndimage import maximum_filter
from radar.internal.DBF import DBF
from radar.internal.doppler import DopplerAlgo
from scipy.ndimage import maximum_filter as _maxf
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED

class SigProc:
    """
    Processing for BGT60TR13C frames:
      - Builds range–Doppler–angle cube via per-antenna Doppler + DBF
      - Finds ALL moving peaks per beam with adaptive thresholding + NMS
      - Provides a simple 'update' that returns flat detections across beams:
            (distance_m, angle_deg, strength, doppler_sign)
    """

    def __init__(self, radar_config: dict):
        # ---- keep a shallow copy of config ----
        self.radar_config = dict(radar_config)

        # ---- canonical numeric config ----        
        self.num_samples_per_chirp = self.radar_config.get("num_samples_per_chirp") 
        self.num_chirps_per_frame = self.radar_config.get("num_chirps_per_frame")

        # RX antenna count: explicit 'num_antennas' or derived from rx_mask bits set.
        self.num_rx_antennas = self.radar_config.get("num_antennas")

        # Bandwidth: explicit or derived from start/end frequency.
        self.bandwidth = self.radar_config.get("bandwidth")

        # Beam/angle params (can be overridden in config)
        self.num_beams = 50 # 70 is the optimal, but based on screen size, we cant see to much resolution even at 10 meters. 50 will process faster
        self.max_angle_deg = 50
        self.dead_zone = 0.95
                
        # ---- derived bins ----
        c = 3e8
        # Max FFTable range (half-spectrum usable)
        self.max_range_m = c / (2.0 * float(self.bandwidth)) * (float(self.num_samples_per_chirp) / 2.0)
        
        # constants
        self.fc = 0.5*(int(self.radar_config.get("start_frequency_Hz")) + int(self.radar_config.get("end_frequency_Hz")))
        self.lambda_m = c / self.fc            # ~ 0.00496 m
        self.prf_hz = self.radar_config.get("chirp_rate")

        self.range_bin = np.linspace(0.0, float(self.max_range_m), int(self.num_samples_per_chirp), endpoint=False)
        self.angle_bin = np.linspace(-float(self.max_angle_deg), float(self.max_angle_deg), int(self.num_beams), endpoint=True)

        # ---- helpers ----
        # If your DopplerAlgo/DBF constructors take different args, align as needed.
        self.doppler = DopplerAlgo(radar_config=self.radar_config, num_ant=self.num_rx_antennas, mti_alpha=0.6)
        self.dbf = DBF(self.num_rx_antennas, num_beams=int(self.num_beams), max_angle_degrees=float(self.max_angle_deg))
        
        self.beam_eq = np.ones(self.num_beams, dtype=float)   # multiplicative gain per beam
        self._beam_baseline = np.zeros(self.num_beams, dtype=float)
        self._beam_baseline_ready = False
        
        self._init_rd_buffers()

    # -------------------------------------------------------------------------
    # Core: build (range, doppler, beam) cube and (range, beam) energy
    # -------------------------------------------------------------------------
    def _init_rd_buffers(self):
        R = int(self.num_samples_per_chirp)
        D = int(2 * self.num_chirps_per_frame)
        A = int(self.num_rx_antennas)
        # persistent buffers
        self._rd_spectrum = np.empty((R, D, A), dtype=np.complex64)  # filled each frame
        self._W = self.dbf.weights.astype(np.complex64, copy=False)  # (A,B)
            
    def _range_angle_cube(self, frame, max_workers=None):
        R = int(self.num_samples_per_chirp)
        D = int(2 * self.num_chirps_per_frame)
        A = int(self.num_rx_antennas)

        if max_workers is None:
            max_workers = min(A, os.cpu_count() or 1)

        def work(i_ant):
            mat = frame[i_ant, :, :]
            self._rd_spectrum[:, :, i_ant] = self.doppler.compute_doppler_map(mat, i_ant)

        # run antenna maps in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(work, i) for i in range(A)]
            wait(futs, return_when=ALL_COMPLETED)

        RD_flat = np.ascontiguousarray(self._rd_spectrum.reshape(R * D, A))
        rd_flat = RD_flat @ self._W
        rd_beam_formed = rd_flat.reshape(R, D, self._W.shape[1])

        x = rd_beam_formed
        beam_range_energy = np.sqrt((x.real*x.real + x.imag*x.imag).sum(axis=1)).astype(np.float32) / np.sqrt(self._W.shape[1])
        return rd_beam_formed, beam_range_energy                    

    def _range_gain_vector(self, start_m=7.0, slope_db_per_m=1.0, max_boost_db=9.0):
        """
        Make a 1D gain vector g[k] for each range bin:
          gain starts at start_m, grows linearly at slope_db_per_m, capped at max_boost_db.
          Returned in linear (magnitude) units, not dB.
        """
        r = self.range_bin.astype(float)
        gain_db = (r - float(start_m)) * float(slope_db_per_m)
        gain_db[r < float(start_m)] = 0.0
        gain_db = np.clip(gain_db, 0.0, float(max_boost_db))
        g = 10.0 ** (gain_db / 20.0)
        return g  # shape (R,)

    def _update_beam_equalization(self, bre, alpha=0.05, min_range_m=1.0):
        """
        Update per-beam baseline from current (range x beam) energy map 'bre'.
        Uses an exponential moving average of median energy over ranges >= min_range_m.
        """
        k0 = int(np.searchsorted(self.range_bin, float(min_range_m)))
        if k0 >= bre.shape[0]:
            return
        # robust per-beam statistic
        med = np.median(bre[k0:, :], axis=0)  # shape (B,)
        if not self._beam_baseline_ready and np.any(med > 0):
            self._beam_baseline = med.astype(float)
            self._beam_baseline_ready = True
        else:
            self._beam_baseline = (1.0 - alpha) * self._beam_baseline + alpha * med

        # convert baseline to equalization gains normalized to median beam
        ref = np.median(self._beam_baseline[self._beam_baseline > 0]) if np.any(self._beam_baseline > 0) else 1.0
        eps = 1e-6
        gains = ref / np.maximum(self._beam_baseline, eps)
        # clamp to avoid crazy boosts at extreme edges
        gains = np.clip(gains, 0.5, 2.0)  # allow at most 2x boost, 0.5x cut
        self.beam_eq = gains

    def update_handheld_adaptive(
        self,
        frame,
        # detection baseline
        threshold=0.05,
        top_k=3,
        min_range_hard=1.0,
        base_range_tol_m=0.20,
        step_per_bucket_m=0.50,
        angle_bucket_deg=10.0,
        max_range_tol_m=2.0,
        mode="nms",
        # IMU inputs (sensor-frame)
        vx_mps=None,
        vy_mps=None,
        speed_mps=None,          # if provided, overrides sqrt(vx^2+vy^2)
        # behavior when fast
        fast_speed_mps=0.6,      # tune: 0.4..0.8 typical
        slow_speed_mps=0.4,      # hysteresis lower bound        
        doppler_bin_tol_normal=1,
        doppler_bin_tol_fast=2,  # wider notch when fast
        require_consecutive_fast=True,
         # far boost tuned for ~9 m
        start_m=6.5,
        slope_db_per_m=1.2,
        max_boost_db=12.0,
        thr_relax_factor=0.9, 
        # SLOW MOVER RESCUE knobs
        slow_rescue_enable=True,
        slow_rescue_exclude_bins=1,  # exclude this many bins on each side of notch center
        slow_rescue_relax=0.90       # keep if outside-notch energy > relax * thr_curve
    ):
        """
        Adaptive handheld handling with hysteresis.
        - If speed <= slow_speed_mps: normal handheld rejection.
        - If speed >= fast_speed_mps:
            when_fast == "hold"   -> return []
            when_fast == "filter" -> widen notch, add persistence.
        Returns flat list of (distance_m, angle_deg) in SENSOR frame.
        """
        # 0) motion estimate
        if speed_mps is None:
            if vx_mps is not None and vy_mps is not None:
                speed = float((np.hypot(vx_mps, vy_mps)))
            else:
                speed = 0.0
        else:
            speed = float(speed_mps)
                    
        # hysteresis state
        state = getattr(self, "_motion_state", "slow")
        if state == "slow" and speed >= float(fast_speed_mps):
            state = "fast"
        elif state == "fast" and speed <= float(slow_speed_mps):
            state = "slow"
        self._motion_state = state
        
        # 1) Build cube and energy map once
        rd_beams, bre = self._range_angle_cube(frame)

        
        self._update_beam_equalization(bre, alpha=0.01, min_range_m=1.0)
        bre *= self.beam_eq[None, :]
        
        range_resolution = self.range_bin[1] - self.range_bin[0]
        range_clear_idx = int(self.dead_zone / range_resolution)
        if range_clear_idx > 0:
            bre[:range_clear_idx, :] = 0.0
        R, D, B = rd_beams.shape
                
        dop_avg = np.mean(np.abs(rd_beams), axis=(0, 2))
        j0 = int(np.argmax(dop_avg))

        # 2) baseline per-range percentile + NMS + optional relax for far bins
        thr_curve = np.percentile(bre, 95, axis=1)
        thr_curve = np.maximum(thr_curve, threshold)
        if float(thr_relax_factor) != 1.0:
            far_mask = (self.range_bin >= float(start_m))
            thr_curve = thr_curve.copy()
            thr_curve[far_mask] *= float(thr_relax_factor)  # e.g., 0.9 to relax 10%

        # 3) apply range gain AFTER computing threshold
        g = self._range_gain_vector(start_m=start_m,
                                    slope_db_per_m=slope_db_per_m,
                                    max_boost_db=max_boost_db)  # (R,)
        bre *= g[:, None]

        # 4) NMS and initial peaks from boosted map vs un-boosted threshold
        local_max = _maxf(bre, size=3)
        peak_mask = (bre == local_max) & (bre > thr_curve[:, None])

        # 5) collect per-beam peaks with observed doppler
        peaks_per_beam = []
        for b in range(B):
            r_idx = np.where(peak_mask[:, b])[0]
            r_idx = np.sort(r_idx)[::-1]  # far -> near
            beam_peaks = []
            for k in r_idx:
                dop_slice = rd_beams[k, :, b]
                j_obs = int(np.argmax(np.abs(dop_slice)))
                beam_peaks.append({
                    "k": int(k),  # needed for rescue
                    "range_m": float(self.range_bin[k]),
                    "energy":  float(bre[k, b]),
                    "doppler_idx": j_obs
                })
            peaks_per_beam.append(beam_peaks)

        # 6) IMU-based ego-doppler rejection with SLOW MOVER RESCUE
        have_dyn = (vx_mps is not None and vy_mps is not None and self.prf_hz is not None and self.lambda_m is not None)
        if have_dyn:
            vx, vy = float(vx_mps), float(vy_mps)
            theta = np.deg2rad(self.angle_bin)
            vr = vx * np.cos(theta) + vy * np.sin(theta)    # (B,)
            fd_pred = 2.0 * vr / float(self.lambda_m)              # Hz
            df_bin = float(self.prf_hz) / float(D)                 # Hz/bin
            j_pred = np.round(fd_pred / df_bin).astype(int) + j0

            tol = int(doppler_bin_tol_fast if state == "fast" else doppler_bin_tol_normal)

            for b in range(B):
                kept = []
                jp = int(j_pred[b])
                for p in peaks_per_beam[b]:
                    if abs(int(p["doppler_idx"]) - jp) > tol:
                        kept.append(p)
                    else:
                        # SLOW MOVER RESCUE: keep if energy outside notch still clears threshold
                        if slow_rescue_enable:
                            kidx = int(p["k"])
                            vec = rd_beams[kidx, :, b]
                            # indices to exclude around the notch center
                            ex = int(max(0, slow_rescue_exclude_bins))
                            j_lo = max(0, jp - ex)
                            j_hi = min(D, jp + ex + 1)
                            if j_lo <= 0 and j_hi >= D:
                                # notch covers all, cannot rescue
                                pass
                            else:
                                if j_lo < j_hi:
                                    outside = np.concatenate((vec[:j_lo], vec[j_hi:]))
                                else:
                                    outside = vec
                                # L2 outside notch, scale like bre (include far-boost)
                                e_out = float(np.linalg.norm(outside)) * float(g[kidx]) / np.sqrt(self.num_beams)
                                if e_out > float(slow_rescue_relax) * float(thr_curve[kidx]):
                                    # update energy to the outside energy for consistency
                                    p["energy"] = max(p["energy"], e_out)
                                    kept.append(p)                        
                peaks_per_beam[b] = kept

        # 5) optional persistence only when fast
        if state == "fast" and require_consecutive_fast:
            prev = getattr(self, "_prev_fast_peaks", None)
            # store per-beam ranges from previous frame
            def to_ranges(beams):
                arr = []
                for beam in beams:
                    arr.append(np.array([d["range_m"] for d in beam], dtype=float))
                return arr
            if prev is not None and len(prev) == len(peaks_per_beam):
                new_beams = []
                for b in range(B):
                    prev_r = prev[b]
                    cur = []
                    if prev_r.size > 0:
                        for p in peaks_per_beam[b]:
                            if np.any(np.abs(prev_r - p["range_m"]) <= 0.20):
                                cur.append(p)
                    # if no previous info, keep nothing in fast mode to be strict
                    peaks_per_beam[b] = cur
            self._prev_fast_peaks = to_ranges(peaks_per_beam)
        else:
            self._prev_fast_peaks = None
            
        # 6) ultra-near reject, per-beam top_k, then angle-scaled compaction
        angles_deg = self.angle_bin
        flat = []
        for b, beam_peaks in enumerate(peaks_per_beam):
            beam_peaks = [p for p in beam_peaks if p["range_m"] >= float(min_range_hard)]
            if top_k is not None and len(beam_peaks) > top_k:
                beam_peaks = sorted(beam_peaks, key=lambda q: q["energy"], reverse=True)[:top_k]
            for p in beam_peaks:
                flat.append((p["range_m"], float(angles_deg[b]), p["energy"]))        
        
        if not flat:
            return []

        flat.sort(key=lambda x: (x[0], -x[2]))

        def tol_for_angle(ang_deg):
            buckets = int(abs(ang_deg) // float(angle_bucket_deg))
            tol = float(base_range_tol_m) + buckets * float(step_per_bucket_m)
            if max_range_tol_m is not None:
                tol = min(tol, float(max_range_tol_m))            
            return tol

        groups = []
        current = [flat[0]]
        for r, ang, E in flat[1:]:
            tol = max(tol_for_angle(ang), tol_for_angle(current[-1][1]))
            if abs(r - current[-1][0]) <= tol:
                current.append((r, ang, E))
            else:
                groups.append(current)
                current = [(r, ang, E)]
        groups.append(current)

        out = []
        if mode == "cluster":
            for g in groups:
                Esum = sum(E for _, _, E in g) + 1e-9
                ang_c = sum(ang * E for _, ang, E in g) / Esum
                r_rep = max(g, key=lambda t: t[2])[0]
                out.append((float(r_rep), float(ang_c)))
        else:
            for g in groups:
                r_rep, ang_rep, _ = max(g, key=lambda t: t[2])
                out.append((float(r_rep), float(ang_rep)))
        
        return out                     
            
    def update_with_sensitivity(
        self,
        frame,
        s,                              # sensitivity control in [0,1], 0.5 is neutral        
        vx_mps=None,
        vy_mps=None    
    ):
        """
        Sensitivity wrapper. Maps s in [0,1] to detector knobs and calls the
        current baseline (update) or handheld version (update_handheld_adaptive).
        Returns flat list of (distance_m, angle_deg) in SENSOR frame.
        """

        # 0) clamp and derive a signed control around center
        s = float(max(0.0, min(1.0, s)))
        x = s - 0.5  # -0.5..+0.5, where + is "more sensitive"
        
        # increase the sensativity range even more on the + side.
        if x>0:
            x = x * 1.5

        # 1) Map control to knobs (gentle ranges)
        # Threshold baseline (percentile-based inside detect_all_moving_peaks_per_beam)
        # We pass a base floor; keep range modest to avoid harming long range.
        base_threshold = 0.05 + (-0.02)*x  # 0.03..0.07

        # Per-beam cap
        if abs(x) < 0.05:
            top_k = 3
        elif x > 0:
            top_k = 4  # allow one more hit when more sensitive
        else:
            top_k = 2  # be a bit stricter when less sensitive

        # Ultra-near hard cut
        min_range_hard = 1.0 + (-0.3)*x    # 0.85..1.15 m

        # Angle-scaled compaction
        base_range_tol_m = 0.20 + (0.10)*x     # 0.15..0.25 m
        step_per_bucket_m = 0.50 + (0.20)*x    # 0.40..0.60 m
        angle_bucket_deg = 10.0                # keep stable
        max_range_tol_m = 2.0                  # safety cap
        mode = "nms"

        # Handheld notch tolerances (only used in adaptive path)
        doppler_bin_tol_normal = 1 if x >= 0 else 2
        doppler_bin_tol_fast = 2 if x >= 0 else 3

        # Fast/slow thresholds for state machine
        #fast_speed_mps = 0.6 + (-0.1)*x   # 0.55..0.65
        #slow_speed_mps = 0.4 + (-0.1)*x   # 0.35..0.45

        if x>0:
            fast_speed_mps=0.1     # enter fast mode at or above this speed
            slow_speed_mps=0.07    # leave fast mode at or below this speed   
        else:
            fast_speed_mps=0.2     # enter fast mode at or above this speed
            slow_speed_mps=0.07    # leave fast mode at or below this speed   
        
        return self.update_handheld_adaptive(
            frame,
            threshold=base_threshold,
            top_k=top_k,
            min_range_hard=min_range_hard,
            base_range_tol_m=base_range_tol_m,
            step_per_bucket_m=step_per_bucket_m,
            angle_bucket_deg=angle_bucket_deg,
            max_range_tol_m=max_range_tol_m,
            mode=mode,
            vx_mps=vx_mps,
            vy_mps=vy_mps,
            fast_speed_mps=fast_speed_mps,
            slow_speed_mps=slow_speed_mps,
            doppler_bin_tol_normal=doppler_bin_tol_normal,
            doppler_bin_tol_fast=doppler_bin_tol_fast,
            require_consecutive_fast=True
        )       

