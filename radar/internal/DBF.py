# ===========================================================================
# Copyright (C) 2022 Infineon Technologies AG
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
# ===========================================================================
#
# Modifications: Rewritten for speed, (c) 2025 RobSmithDev


import numpy as np


class DBF:
    def __init__(self, num_antennas: int, num_beams: int = 27, max_angle_degrees: float = 45, d_by_lambda: float = 0.5):
        angle_vector = np.radians(np.linspace(-max_angle_degrees, max_angle_degrees, num_beams))
        weights = np.empty((num_antennas, num_beams), dtype=np.complex64)
        for iBeam, angle in enumerate(angle_vector):
            # e^{j 2p i d/? sin?}
            weights[:, iBeam] = np.exp(1j * 2 * np.pi * d_by_lambda * np.sin(angle) * np.arange(num_antennas, dtype=np.float32))
        self.weights = weights[::-1, :]  # (A,B), reversed as before        

    def run(self, range_doppler):
        R, D, A = range_doppler.shape
        W = self.weights.astype(np.complex64, copy=False)        # (A,B)
        RD = np.ascontiguousarray(range_doppler.reshape(R*D, A)) # (R*D, A)
        out = RD @ W                                             # (R*D, B)  [BLAS-accelerated]
        return out.reshape(R, D, W.shape[1])

