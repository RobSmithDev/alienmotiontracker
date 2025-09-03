# This file belongs to the original demo samples that come with the DreamRF+Hat Radar

import os
import re
import numba
import numpy as np

def read_uint12(data_chunk):
    data = np.frombuffer(bytes(data_chunk), dtype=np.uint8)
    fst_uint8, mid_uint8, lst_uint8 = np.reshape(data, (data.shape[0] // 3, 3)).astype(np.uint16).T
    fst_uint12 = (fst_uint8 << 4) + (mid_uint8 >> 4)
    snd_uint12 = ((mid_uint8 % 16) << 8) + lst_uint8
    return np.reshape(np.concatenate((fst_uint12[:, None], snd_uint12[:, None]), axis=1), 2 * fst_uint12.shape[0]).astype(np.float32)

def split_samples(uint16_chunk, num_frames, num_chirps, num_samples, num_rx_antennas):
    return uint16_chunk.reshape((num_frames, num_chirps, num_samples, num_rx_antennas))

def find_register_config_in_directory(directory):
    # Define the filename pattern: start, date section, and end
    pattern = r"BGT60TR13C_export_registers_\d{8}-\d{6}\.txt"

    # Search for matching files
    matched_files = [f for f in os.listdir(directory) if re.match(pattern, f)]

    # Check the number of matched files
    if len(matched_files) == 0:
        raise FileNotFoundError("No matching radar radar_config file found.")
    elif len(matched_files) > 1:
        raise RuntimeError("Multiple matching radar radar_config files found.")

    # Return the path if only one file is found
    file_path = os.path.join(directory, matched_files[0])
    return file_path

def find_setting_in_directory(directory):
    # Define the filename pattern for settings files
    pattern = r"BGT60TR13C_settings_\d{8}-\d{6}\.json"

    # Search for matching settings files
    matched_files = [f for f in os.listdir(directory) if re.match(pattern, f)]

    # Check the number of matched settings files
    if len(matched_files) == 0:
        raise FileNotFoundError("No matching settings file found.")
    elif len(matched_files) > 1:
        raise RuntimeError("Multiple matching settings files found.")

    # Return the path if only one file is found
    file_path = os.path.join(directory, matched_files[0])
    return file_path

def calculate_frame_size(setting_data):
    """
    Calculate the number of samples per frame based on the provided settings data.

    Parameters:
        setting_data (dict): Parsed JSON data containing configuration settings.

    Returns:
        int: Total number of samples per frame.
    """
    # Retrieve the relevant "frame" sequence (second sequence in JSON)
    frame_sequence = setting_data["sequence"][0]["sequence"]

    total_samples_per_frame = 0
    for frame in frame_sequence:
        if frame["type"] == "loop":
            num_repetitions = frame["num_repetitions"]
            chirp_sequence = frame["sequence"]

            # Calculate samples for each chirp in the chirp sequence
            chirp_samples = 0
            for chirp in chirp_sequence:
                if chirp["type"] == "chirp":
                    num_samples = chirp["num_samples"]
                    rx_mask = chirp["rx_mask"]

                    # Count the number of active antennas (number of 1s in rx_mask)
                    num_antennas = bin(rx_mask).count("1")

                    # Total samples per chirp is num_samples * num_antennas
                    chirp_samples += num_samples * num_antennas

            # Multiply by the number of repetitions for the frame level
            total_samples_per_frame += chirp_samples * num_repetitions

    return total_samples_per_frame

# numba implementation of squared abs
@numba.vectorize([numba.float32(numba.complex64)])
def abs2_numba_complex64(x):
    return x.real**2 + x.imag**2

# numba implementation of squared abs
@numba.vectorize([numba.float64(numba.complex128)])
def abs2_numba_complex128(x):
    return x.real**2 + x.imag**2

def parse_radar_cfg(setting):
    frame_sequence = setting["sequence"][0]["sequence"]
    frame_rate = 1 / setting["sequence"][0]["repetition_time_s"]
    frame = frame_sequence[0]

    # Extract frame and chirp parameters
    if frame["type"] == "loop":
        num_chirps_per_frame = frame["num_repetitions"]
        chirp_rate = 1 / frame["repetition_time_s"]
        chirp = frame["sequence"][0]
        if chirp["type"] == "chirp":
            num_samples_per_chirp = chirp["num_samples"]
            rx_mask = chirp["rx_mask"]
            sample_rate = chirp["sample_rate_Hz"]
            bandwidth = chirp["end_frequency_Hz"] - chirp["start_frequency_Hz"]
            min_freq = chirp["start_frequency_Hz"]
            max_freq = chirp["end_frequency_Hz"]
            num_antennas = bin(rx_mask).count("1")
            num_azimuth_antennas = bin(rx_mask & 0x05).count("1")
            num_elevation_antennas = bin(rx_mask & 0x06).count("1")
        else:
            raise ValueError("Invalid chirp type in settings.")
    else:
        raise ValueError("Invalid frame type in settings.")
    radar_param = {"frame_rate": frame_rate,
                   "chirp_rate": chirp_rate,
                   "num_chirps_per_frame": num_chirps_per_frame,
                   "num_samples_per_chirp": num_samples_per_chirp,
                   "sample_rate": sample_rate,
                   "bandwidth": bandwidth,
                   "start_frequency_Hz": min_freq,
                   "end_frequency_Hz": max_freq,
                   "rx_mask": rx_mask,
                   "num_antennas": num_antennas,
                   "num_azimuth_antennas": num_azimuth_antennas,
                   "num_elevation_antennas": num_elevation_antennas}
    return radar_param

def parse_full_frame(full_frame):
    frame = None
    version = int.from_bytes(full_frame[0:4], byteorder='little', signed=False)
    if version == 0:
        seq = int.from_bytes(full_frame[4:8], byteorder='little', signed=False)
        data_len = int.from_bytes(full_frame[8:12], byteorder='little', signed=False)
        if len(full_frame)-12 == data_len:
            frame = (version, seq, data_len, full_frame[12:])
    return frame

