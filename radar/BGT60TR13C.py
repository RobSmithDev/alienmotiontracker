# This is a modified version of the file that came with the original DreamRF+Hat Radar Module
# All file logging has been removed and has been optimised for speed

import datetime
import os
import spidev
import logging
import time
import queue
import threading
from threading import Event
from gpiozero import DigitalInputDevice, DigitalOutputDevice

from radar.BGT60TR13C_CONST import *

RET_VAL_OK = 0
RET_VAL_ERR = -1

logging.basicConfig(level=logging.INFO, format='%(message)s')

class BGT60TR13C:
    def __init__(self, spi_bus=0, spi_dev=0,  spi_speed=10_000_000, rst_pin=12, irq_pin=25):
        self.__spi = spidev.SpiDev()
        self.__spi.open(spi_bus, spi_dev)
        self.__spi.max_speed_hz = spi_speed
        self.__spi.mode = 0
        self.__rst = DigitalOutputDevice(rst_pin)
        self.__irq = DigitalInputDevice(irq_pin, pull_up=False, bounce_time=0.001)
        self.hard_reset()
        self.__sub_frame_buffer = [] # It is used to hold incomplete FMCW frames segments, which come from SPI burst read. Once one complete frame is received, it will be cleared. It is in bytes.
        
        self.last_frame = None
        self.last_frame_ready = Event()
        self.last_frame_lock = threading.Lock()

        self.__num_samples_per_frame = 0
        self.__num_bytes_per_frame = 0
        self.__num_sampler_per_burst = 0
        self.__last_gsr_reg = 0

        # Data collection thread
        self.__data_collection_thread = None
        self.__data_collection_stop_event = threading.Event()

        self.__version = 0
        self.__seq = 0
        self._dead = False
        
    def close_all(self):
        self.__spi.close()
        self.__rst.close()
        self.__irq.close()
        

    def set_fifo_parameters(self, num_samples_per_frame, num_samples_irq, num_sampler_per_burst):
        # Note that regular Raspberry PI spidev has a limit of 4096 bytes per transfer. In addition, the frame size may be larger than buffer size. So 3 parameters are used to control it.
        # num_samples_frame: The number of samples for one FMCW frame.
        # num_samples_irq: The number of samples to trigger the interrupt.
        # num_sampler_burst: The number of samples for each SPI burst read.

        logging.info(f"num_samples_per_frame: {num_samples_per_frame}")
        logging.info(f"num_samples_irq: {num_samples_irq}")
        logging.info(f"num_sampler_per_burst: {num_sampler_per_burst}")

        self.__num_samples_per_frame = num_samples_per_frame
        self.__num_bytes_per_frame = (num_samples_per_frame>>1)*3
        self.__num_sampler_per_burst = num_sampler_per_burst
        self.__set_fifo_limit(num_samples_irq)

    def __set_reg(self, reg_addr, data):
        # Build the SPI register address and data
        temp = (reg_addr << BGT60TRXX_SPI_REGADR_POS) & BGT60TRXX_SPI_REGADR_MSK
        temp |= BGT60TRXX_SPI_WR_OP_MSK  # Set the write operation flag
        temp |= (data << BGT60TRXX_SPI_DATA_POS) & BGT60TRXX_SPI_DATA_MSK

        # Convert data to 4-byte transmission format
        tx_data = [temp >> 24 & 0xFF, temp >> 16 & 0xFF, temp >> 8 & 0xFF, temp & 0xFF]

        # Send data through SPI, and read SPI at the same time
        rx_data = self.__spi.xfer2(tx_data)
        result = (rx_data[0] << 24) | (rx_data[1] << 16) | (rx_data[2] << 8) | rx_data[3]
        self.__last_gsr_reg = rx_data[0]

        return result

    def __get_reg(self, reg_addr):
        # Build the SPI register address
        temp = (reg_addr << BGT60TRXX_SPI_REGADR_POS) & BGT60TRXX_SPI_REGADR_MSK

        # Convert data to 4-byte transmission format
        tx_data = [temp >> 24 & 0xFF, temp >> 16 & 0xFF, temp >> 8 & 0xFF, temp & 0xFF]

        # Send read command and receive data
        rx_data = self.__spi.xfer2(tx_data)
        result = (rx_data[0] << 24) | (rx_data[1] << 16) | (rx_data[2] << 8) | rx_data[3]
        self.__last_gsr_reg = rx_data[0]

        return result

    def __get_fifo_data(self, num_samples):
        fifo_data = None
        if 0 < num_samples >> 1 <= BGT60TRXX_REG_FSTAT_TR13C_FIFO_SIZE and num_samples % 2 == 0:
            tx_data = [BGT60TRXX_SPI_BURST_MODE_CMD >> 24 & 0xFF,
                       BGT60TRXX_REG_FIFO_TR13C << BGT60TRXX_SPI_BURST_MODE_SADR_POS >> 16 & 0xFF,
                       0x00, 0x00] + [0x00] * ((num_samples >> 1) * 3)
            rx_data = self.__spi.xfer2(tx_data)
            self.__last_gsr_reg = rx_data[0]
            if self.check_gsr_reg() != RET_VAL_OK:
                logging.error("GSR Error Detected")
                self._dead = True
                self.print_gsr_reg()
            else:
                fifo_data = rx_data[4:]
        else:
            logging.error("Invalid num_samples.")
        return fifo_data
        
    def is_dead(self):
        return self._dead

    def check_chip_id(self):
        chip_id = self.__get_reg(BGT60TRXX_REG_CHIP_ID)
        logging.info(f"CHIP_ID: 0x{chip_id:08X}")
        chip_id_digital = (chip_id & BGT60TRXX_REG_CHIP_ID_DIGITAL_ID_MSK) >> BGT60TRXX_REG_CHIP_ID_DIGITAL_ID_POS
        chip_id_rf = (chip_id & BGT60TRXX_REG_CHIP_ID_RF_ID_MSK) >> BGT60TRXX_REG_CHIP_ID_RF_ID_POS

        # Determine the device type based on extracted IDs
        if chip_id_digital == 3 and chip_id_rf == 3:
            logging.info("This chip is BGT60TR13C.")
            return RET_VAL_OK
        else:
            logging.info("Chip is NOT BGT60TR13C.")
            return RET_VAL_ERR

    def load_register_config_file(self, file_name):
        logging.info(f"Loading radar_config file: {file_name}")
        with open(file_name, 'r') as file:
            for line in file:
                line = line.strip()
                parts = line.split()
                if len(parts) == 3:
                    label, address_str, value_str = parts
                    address = int(address_str, 16)
                    value = int(value_str, 16)
                    if address == BGT60TRXX_REG_SFCTL:
                        if self.__spi.max_speed_hz>20_000_000:
                            value |= BGT60TRXX_REG_SFCTL_MISO_HS_READ_MSK
                        else:
                            value &= ~BGT60TRXX_REG_SFCTL_MISO_HS_READ_MSK
                    self.__set_reg(address, value)
                else:
                    raise RuntimeError(f"Line format is incorrect: {line}.")

    def start(self):
        self._dead = False
        if self.__data_collection_thread and self.__data_collection_thread.is_alive():
            self.stop()
        if self.__data_collection_thread is None or not self.__data_collection_thread.is_alive():
            self.__data_collection_stop_event.clear()
            self.__data_collection_thread = threading.Thread(target=self.__data_collection)
            self.__data_collection_thread.start()
        if self.soft_reset(BGT60TRXX_RESET_FSM) != RET_VAL_OK:
            return RET_VAL_ERR        
        self.__seq = 0
        status = self.__get_reg(BGT60TRXX_REG_MAIN)
        tmp = status | BGT60TRXX_REG_MAIN_FRAME_START_MSK
        self.__set_reg(BGT60TRXX_REG_MAIN, tmp)
        return RET_VAL_OK

    def stop(self):
        ret_temp = RET_VAL_OK
        if self.soft_reset(BGT60TRXX_RESET_FSM) != RET_VAL_OK:
            ret_temp = RET_VAL_ERR
        if self.__data_collection_thread and self.__data_collection_thread.is_alive():
            self.__data_collection_stop_event.set()
            self.__data_collection_thread.join()
        return ret_temp

    def soft_reset(self, reset_type):
        self.__sub_frame_buffer = []
        status = self.__get_reg(BGT60TRXX_REG_MAIN)
        tmp = status | reset_type
        self.__set_reg(BGT60TRXX_REG_MAIN, tmp)

        time_out_cnt = 0
        N = 10
        for i in range(N):
            time.sleep(0.01)
            status = self.__get_reg(BGT60TRXX_REG_MAIN)
            if status & reset_type == 0:
               break
            time_out_cnt += 1
        if time_out_cnt == N:
            logging.error("Soft reset timeout! Hard reset required.")
            return RET_VAL_ERR
        else:
            time.sleep(0.01)
            return RET_VAL_OK

    def hard_reset(self):
        self.__rst.on()
        time.sleep(0.01)
        self.__rst.off()
        time.sleep(0.01)
        self.__rst.on()
        time.sleep(0.01)

    def __set_fifo_limit(self, num_samples):
        if 0 < num_samples>>1 <= BGT60TRXX_REG_FSTAT_TR13C_FIFO_SIZE and num_samples % 2 == 0:
            tmp = self.__get_reg(BGT60TRXX_REG_SFCTL)
            tmp &= ~BGT60TRXX_REG_SFCTL_FIFO_CREF_MSK
            # The FIFO is 24-bit wide, which is 2 12-bit ADC samples.
            tmp |= (((num_samples >> 1) - 1) << BGT60TRXX_REG_SFCTL_FIFO_CREF_POS) & BGT60TRXX_REG_SFCTL_FIFO_CREF_MSK
            self.__set_reg(BGT60TRXX_REG_SFCTL, tmp)
            return RET_VAL_OK
        else:
            logging.error("Invalid num_samples.")
            return RET_VAL_ERR

    def print_gsr_reg(self):
        if self.__last_gsr_reg & BGT60TRXX_REG_GSR0_FOU_ERR_MSK:
            logging.info("GSR FIFO OVERFLOW/UNDERFLOW ERROR: 1")
        else:
            logging.info("GSR FIFO OVERFLOW/UNDERFLOW ERROR: 0")

        if self.__last_gsr_reg & BGT60TRXX_REG_GSR0_MISO_HS_READ_MSK:
            logging.info("GSR MISO HS: 1")
        else:
            logging.info("GSR MISO HS: 0")

        if self.__last_gsr_reg & BGT60TRXX_REG_GSR0_SPI_BURST_ERR_MSK:
            logging.info("GSR SPI BURST ERR: 1")
        else:
            logging.info("GSR SPI BURST ERR: 0")

        if self.__last_gsr_reg & BGT60TRXX_REG_GSR0_CLK_NUM_ERR_MSK:
            logging.info("GSR CLOCK NUMBER ERR: 1")
        else:
            logging.info("GSR CLOCK NUMBER ERR: 0")

    def get_frame_size(self):
        return self.__num_bytes_per_frame + 12

    def get_frame(self):
        if self.last_frame_ready.is_set():
            with self.last_frame_lock:
                self.last_frame_ready.clear()
                return self.last_frame
        return None

    def check_gsr_reg(self):
        if self.__last_gsr_reg & (BGT60TRXX_REG_GSR0_FOU_ERR_MSK | BGT60TRXX_REG_GSR0_SPI_BURST_ERR_MSK | BGT60TRXX_REG_GSR0_CLK_NUM_ERR_MSK):
            return RET_VAL_ERR
        else:
            return RET_VAL_OK
            
    def __data_collection(self):
        logging.debug("Data collection thread started.")
        try:
            while not self.__data_collection_stop_event.is_set():
                if self.__irq.value == 1:
                    fifo_data = self.__get_fifo_data(self.__num_sampler_per_burst)
                    if fifo_data:
                        self.__sub_frame_buffer.extend(fifo_data)
                        while len(self.__sub_frame_buffer) >= self.__num_bytes_per_frame:
                            frame = self.__sub_frame_buffer[:self.__num_bytes_per_frame]
                            del self.__sub_frame_buffer[:self.__num_bytes_per_frame]
    
                            full_frame = (
                                self.__version.to_bytes(4, 'little') +
                                self.__seq.to_bytes(4, 'little') +
                                len(frame).to_bytes(4, 'little') +
                                bytes(frame)
                            )
                            self.__seq += 1
                            with self.last_frame_lock:
                                self.last_frame = full_frame
                                self.last_frame_ready.set()
                else:
                    time.sleep(0.0005)  # optional slight delay
        finally:
            logging.debug("Data collection thread stopped.")            

    def __del__(self):
        self.stop()

