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

import os
import math
import threading
import random
import time
import RPi.GPIO as GPIO
import atexit
import datetime
from luma.core.interface.serial import i2c, spi
from luma.core.render import canvas
from luma.lcd.device import ili9341

from PIL import ImageDraw, ImageFont, Image
from PIL import ImageFilter

from alienaudio import AlienAudio 
from positions import PositionStore
from i2cboard import I2CDevices

#from luma.emulator.device import pygame
from radarmonitor import RadarMonitor


class AlienDisplay:
    def __init__(self):
        self.serial = spi(port=0, device=1, gpio_DC=23, gpio_RST=24,transfer_size=4096,bus_speed_hz=50000000)
        self.device = ili9341(self.serial, rotate=0, persist=True)
        
        #self.device = pygame(width=320, height=240, rotate=0, mode='RGB', scale=1, frame_rate=20)
        
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        # Screen OFF to start with
        GPIO.setup(20, GPIO.OUT, initial=GPIO.LOW)      
        
        self.displaySplashScreen()                        
        self.LineColor = (191, 171, 151)
        self.PanelColor = (0, 130, 255)
        self.TextColor = (255, 50, 50)
        self.AlienPlotColor = (0, 255, 175)
        self.Center = (160, 160)
        self.FadeDuration = 3               
        self.pastTriggerPoint = False
        self.debug = None
        # Uncomment the following for some detection debug on the display
        #self.debug = []
        self.lastTrigger = False        
        self.triggerPressedCounter = 0
        
        self.radar = RadarMonitor()
                       
        # Setup the trigger button
        GPIO.setup(21, GPIO.IN, pull_up_down=GPIO.PUD_UP)     
                
        # the /2 is purely to reduce the resolution so overlapping data can be removed
        self.aliens = PositionStore(self.Center[0]/2, self.Center[1]/2)
        
        self.i2cDev = I2CDevices()
        self.audio = AlienAudio()
                
        try:
            self.font_big = ImageFont.truetype("FreeSansBold.ttf", 33)
            self.font_small = ImageFont.truetype("FreeSansBold.ttf", 12)
            self.font_med = ImageFont.truetype("FreeSansBold.ttf", 20)
        except:
            self.font_big = ImageFont.load_default()
            self.font_small = ImageFont.load_default()
            self.font_med = ImageFont.load_default()
        
        # Used to draw the radar pulse
        self.pulseImage = Image.new("RGBA", (320,160), (0,0,0,0)) 
        self.pulseDraw = ImageDraw.Draw(self.pulseImage)
        
        # A surface to draw the aliens to
        self.alienDrawing = Image.new("RGBA", (320,200), (0,0,0,0))
        self.alienDrawingDraw = ImageDraw.Draw(self.alienDrawing)
        
        # Pre-render fixed parts of the animation
        self.bottomGraphic = self.createBottomSection()
        self.backgroundGradient = self.createBackground()
        
        # Where a lot is built up before having a soft blur applied
        self.glowImage = Image.new("RGBA", (320,200), (0,0,0,0))
        self.glowDraw = ImageDraw.Draw(self.glowImage)
        
        self.rotationAngle = 0      
        self.shutdownCounter = 0 
        self.shutdownTimer = 0
        
        # Enables data logging for later playback
        self.dataLog = False        

        # set to the file name to replay a capture
        self.replayFile = None
        #self.replayFile = "datalog_20250827_203808_125.bin"
        
                
        # Logging, for replay
        self.logFile = None
        self.logStart = time.time()
        self._laststrLog = ""
        self.currentLine = None
        self.nextLine = None
        self.replayFileDetections = []
        
        if self.dataLog:
            self.replayFile = None
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            filename_with_timestamp = "datalog_" + f"{timestamp}.bin"
            self.logFile = open(filename_with_timestamp, "w")
            
        if self.replayFile is not None:
            self.replayFile = open(self.replayFile, "r")     
            print(self.replayFile)       


    def closeLogging(self):
        if self.logFile is not None:
            self.logFile.close()
            self.logFile = None

    # Fader in and out the splash screen. You *could* do this with PWM, but this looked better
    def displaySplashScreen(self, fadeout = False):            
        # --- Fonts ---
        try:
            font_big = ImageFont.truetype("FreeSansBold.ttf", 25)
            font_small = ImageFont.truetype("FreeSansBold.ttf", 16)
        except:
            font_big = ImageFont.load_default()
            font_small = ImageFont.load_default()
        title = "WEYLAND-YUTANI CORP"
        subtitle = "\"BUILDING BETTER WORLDS\""
            
        for loop in range(0,31):
            if fadeout:
                scale = 1 - (loop / 30.0)
            else:
                scale = loop / 30.0

            with canvas(self.device) as draw:    
                # Yellow V
                draw.polygon([(5,84), (49,84), (98,133), (147,84), (174,84), (223, 133), (272, 84), (315, 84), (315, 90), (242, 163), (208, 163), (160, 115), (112, 163), (78, 163), (5,90)], fill=(int(255*scale), int(204*scale), 0))
                # Gray triangles
                c = int(170 * scale)
                draw.polygon([(74, 84), (122, 84), (122, 90), (98, 114), (74, 90)], fill=(c, c, c))
                draw.polygon([(199, 84), (247, 84), (247, 90), (223, 114), (199, 90)], fill=(c, c, c))
                draw.polygon([(136, 163), (136, 157), (160, 133), (184, 157), (184, 163)], fill=(c, c, c))
                title_bbox = draw.textbbox((0, 0), title, font=font_big)
                subtitle_bbox = draw.textbbox((0, 0), subtitle, font=font_small)
                c = int(255 * scale)
                draw.text(((self.device.width - (title_bbox[2] - title_bbox[0])) / 2, 55), title, font=font_big, fill=(c,c,c))
                draw.text(((self.device.width - (subtitle_bbox[2] - subtitle_bbox[0])) / 2, 170), subtitle, font=font_small, fill=(c,c,c))
                
            time.sleep(0.001)
            # For the first loop first draw)
            if loop == 0:
                if fadeout:
                    # First time through in fadeout, wait for 3 seconds
                    time.sleep(3)                
                else:
                    # First time through in fade in, turn on the display!            
                    GPIO.output(20, GPIO.HIGH)                    
        # done? if fade out, turn off the display
        if fadeout:
            GPIO.output(20, GPIO.LOW)

    # Creates the bottom blue area with the text on, ready to be pasted onto screen
    def createBottomSection(self):
        tmp = Image.new("RGBA", (320,40), (0,0,0,0)) 
        draw = ImageDraw.Draw(tmp)        
        draw.polygon([(0,40), (0,1),(105, 0), (112, 5), (125,32), (195,32), (208,5), (215,0), (320,1), (320,40) ], fill=self.PanelColor)
        
        alpha = tmp.split()[3]        # Alpha channel
        color = tmp.convert("RGB")    # Color only
        color = color.filter(ImageFilter.BoxBlur(2))
        
        # Merge back 
        img = Image.merge("RGBA", (*color.split(), alpha))
        draw = ImageDraw.Draw(img)
        
        # Render static text
        draw.text((9, 8), "F.E.M.S. 0.", font=self.font_small, fill="white")                
        draw.circle((157,16), 2, fill=self.TextColor)
        draw.circle((163,16), 2, fill=self.TextColor)
        draw.text((178, 14), "m", font=self.font_med, fill=self.TextColor)                            
        return img
        
    # Creates the background gradient image behind the display
    def createBackground(self): 
        tmp = Image.new("RGBA", (320,200), (0,0,0,0)) 
        draw = ImageDraw.Draw(tmp)
        draw.rectangle((0, 0, tmp.width, tmp.height), fill="black")
        for x in range(140,1,-1):
            s = (150-x) / 255
            draw.circle((160,160), x, fill=(round(self.PanelColor[0]*s),round(self.PanelColor[1]*s),round(self.PanelColor[2]*s)))
        return tmp
    
    # Colour lerping
    @staticmethod
    def _lerp_color(color1, color2, t):
        return tuple(round(c1 + (c2 - c1) * t) for c1, c2 in zip(color1, color2))    

    # Lerp between three colours
    def _get_three_color_gradient(self, s, colorA, colorB, colorC):
        s = max(0, min(1, s))
        if s < 0.5:
            t = s / 0.5
            return self._lerp_color(colorA, colorB, t)
        else:
            t = (s - 0.5) / 0.5
            return self._lerp_color(colorB, colorC, t)   

    # Render the radar "pulse" - time will be between 0 and 0.4 onto the pulseImage image
    def renderPulse(self, time):
        radius = round(time*550)    
        self.pulseDraw.rectangle([0, 0, 320, 200], fill=(0,0,0,0))
        if radius>=5:
            innerRadius = radius - round(radius / 4)
            for x in range(innerRadius, radius):
                s = ((x - innerRadius) / (radius - innerRadius))
                if x>160: s = s * (1-((x - 160)/100))
                col = self._get_three_color_gradient(s, (0,0,0,0), (0, 153, 255, 255), (255,255,255, 255))
                self.pulseDraw.arc([self.Center[0]-x, self.Center[1]-x, self.Center[0]+x, self.Center[1]+x],start=180, end=360, fill=col, width=1)

    # Draw all of the aliens on the alienDrawing image. The input is between 0 and 0.4 for the animation.
    def renderAliens(self, nowTime, timepos):
        # First we clear our image by drawing the background to it        
        self.alienDrawingDraw.rectangle((0, 0, self.alienDrawing.width, self.alienDrawing.height), fill=(0,0,0,0))   
        alienList = self.aliens.get_positions()
        nearest = 20
        
        # First draw the ones over a second old, fading out
        for alien in alienList:
            brightness = nowTime - alien.timestamp
            if brightness>1:
                brightness = max(0, 1-((brightness-1) / (self.FadeDuration-1)))
                # convert distance to match LCD scale
                distance = 3 + (alien.distance / 15) * 200
                pos = ( (math.cos(alien.angle + self.rotationAngle) * distance) + 160, (math.sin(alien.angle + self.rotationAngle) * distance) + 160 ) 
                self.alienDrawingDraw.circle(pos,13,(round(self.AlienPlotColor[0]*brightness), round(self.AlienPlotColor[1]*brightness), round(self.AlienPlotColor[2]*brightness), round(255*brightness))) 
        
        # Next draw the "active" ones
        if timepos < -0.1:
            radius = 13
        else:
            radius = 18 - round(math.sin((timepos/1.2) * math.pi) * 6)     

        for alien in alienList:
            brightness = nowTime - alien.timestamp
            if brightness<=1:
                nearest = min(nearest, alien.distance)
                distance = 3 + (alien.distance / 15) * 200
                pos = ( (math.cos(alien.angle + self.rotationAngle) * distance) + 160, (math.sin(alien.angle + self.rotationAngle) * distance) + 160 ) 
                self.alienDrawingDraw.circle(pos,radius,self.AlienPlotColor)            
        return nearest, self.alienDrawing.filter(ImageFilter.BoxBlur(7))

    # Render the entire screen
    def internalRedrawImage(self, nowTime, animationPos, triggerPressed):
        # The "radar" animation lasts about 0.4 sec - We can optimize what we draw for this
        self.glowImage.paste(self.backgroundGradient, (0,0), self.backgroundGradient)             
        if animationPos<0.4:
            self.renderPulse(animationPos)
            self.glowImage.alpha_composite(self.pulseImage)
            self.pastTriggerPoint = False
        else:
            if not self.pastTriggerPoint:
                self.pastTriggerPoint = True
                self.aliens.remove_old_positions(self.FadeDuration);
                self.triggerPressedCounter = self.triggerPressedCounter + 1
                if triggerPressed and self.triggerPressedCounter>2:                
                    self.addAliens()
                else:
                    self.purgeAliens()
                
        self.glowDraw.arc([self.Center[0]-70, self.Center[1]-70, self.Center[0]+70, self.Center[1]+70],start=180, end=360, fill=self.LineColor, width=3)
        self.glowDraw.arc([self.Center[0]-140, self.Center[1]-140, self.Center[0]+140, self.Center[1]+140],start=180, end=360, fill=self.LineColor, width=3)    
        
        # Draw all the segments
        for r in range(0, 4):       
            angle = math.degrees(self.rotationAngle) + 180 + r*90
            angler = self.rotationAngle + (math.pi + r*math.pi/2)
            # Outer and inner rings        
            self.glowDraw.arc([self.Center[0]-128, self.Center[1]-128, self.Center[0]+128, self.Center[1]+128],start=angle-23, end=angle+23, fill=self.LineColor, width=3)
            angle = angle + 45
            self.glowDraw.arc([self.Center[0]-82, self.Center[1]-82, self.Center[0]+82, self.Center[1]+82],start=angle-23, end=angle+23, fill=self.LineColor, width=3)
            # Lines from the inner ring to the outer
            c = math.cos(angler)
            s = math.sin(angler)                
            pos = (self.Center[0]+c*55, self.Center[1]+s*55)
            self.glowDraw.line([pos, (self.Center[0]+c*148, self.Center[1]+s*148)], fill=self.LineColor, width=3)
            self.glowDraw.circle(pos, 3, self.LineColor)        
            angler = angler + math.pi/4
            c = math.cos(angler)
            s = math.sin(angler)                
            pos = (self.Center[0]+c*70, self.Center[1]+s*70)
            self.glowDraw.line([pos, (self.Center[0]+c*148, self.Center[1]+s*148)], fill=self.LineColor, width=3)        

            # Join the ARCS for the inner and outer lines
            angler = math.radians(angle-22)
            c = math.cos(angler)
            s = math.sin(angler)                
            self.glowDraw.line([(self.Center[0]+c*70, self.Center[1]+s*70), (self.Center[0]+c*81, self.Center[1]+s*81)], fill=self.LineColor, width=3)
            self.glowDraw.line([(self.Center[0]+c*124, self.Center[1]+s*124), (self.Center[0]+c*145, self.Center[1]+s*145)], fill=self.LineColor, width=3)
            angler = angler + math.pi/4
            c = math.cos(angler)
            s = math.sin(angler)                
            self.glowDraw.line([(self.Center[0]+c*70, self.Center[1]+s*70), (self.Center[0]+c*81, self.Center[1]+s*81)], fill=self.LineColor, width=3)
            self.glowDraw.line([(self.Center[0]+c*124, self.Center[1]+s*124), (self.Center[0]+c*145, self.Center[1]+s*145)], fill=self.LineColor, width=3)   

        # Alien animation is also 0.4
        nearest, alienimg = self.renderAliens(nowTime, animationPos-0.4)
        self.glowImage.alpha_composite(alienimg)

        bluredImage = self.glowImage.filter(ImageFilter.BoxBlur(1))            
        blurDraw = ImageDraw.Draw(bluredImage)
        
        self.audio.set_pitch(1.0+min(15-nearest,15)/18.0)
        self.audio.set_alien_detected(nearest < 15)
        meters = math.floor(nearest)
        cm = math.floor((nearest - meters)*100)    
        meterText = f"{meters:02d}"
        cmText = f"{cm:02d}"
                
        # Distance Text         
        blurDraw.text((120, 161), meterText[0], font=self.font_big, fill=self.TextColor)
        blurDraw.text((136, 161), meterText[1], font=self.font_big, fill=self.TextColor)
        blurDraw.text((168, 161), cmText[0], font=self.font_med, fill=self.TextColor)
        blurDraw.text((179, 161), cmText[1], font=self.font_med, fill=self.TextColor)                        
        
        bluredImage.paste(self.bottomGraphic, (0,160), self.bottomGraphic)   
        
        # Draw the temperature here
        tempI2C, tempCPU = self.i2cDev.getTemperature()
        #tempF = round((tempI2C * 9/5) + 32)        
        txt = f"CX. {tempI2C:02.0f}/{tempCPU:05.2f}"
        blurDraw.text((230, 168), txt, font=self.font_small, fill="white")
        t = math.degrees(-self.rotationAngle)
        if t<0:
            t = t + 360.0      
        txt = f"{t:06.2f}"
        blurDraw.text((68, 168), txt, font=self.font_small, fill="white")        
        return bluredImage
        
    # just used to purge whatever is detected while the trigger isn't pressed
    def purgeAliens(self):
        if self.radar:
            self.radar.getDetections()        
        
    # While the trigger IS pressed
    def addAliens(self):
        if self.radar:
            if self.replayFile:
                locations = self.replayFileDetections
                self.replayFileDetections = []
            else:
                locations = self.radar.getDetections()
                
            if self.debug is not None:                 
                self.debug = locations
                
            if self.logFile is not None:
                t = time.time() - self.logStart
                for (distance, angle) in locations:
                    prefix = f"A{distance:8.4f},{angle:8.4f}\n"
                    self.logFile.write(prefix)                
                
            self.aliens.addAliens(self.rotationAngle, locations)
    
    # To shutdown the pi, you turn the volume t0 0 and slide the sensitivity slider back and forth
    def monitorShutdown(self, sense, volume):
        if volume>0.05:
            self.shutdownCounter = 0
        else:            
            # mute, or almost mute
            if self.shutdownCounter & 1:
                if sense<0.1:
                    self.shutdownCounter = self.shutdownCounter + 1
                    self.shutdownTimer = time.time()
            else:
                if sense>0.9:                    
                    self.shutdownCounter = self.shutdownCounter + 1
                    self.shutdownTimer = time.time()
                    
            if time.time() - self.shutdownTimer>=2:
                self.shutdownCounter = 0
                
    def fetchReplayLine(self):
        t = time.time() - self.logStart
        if self.currentLine is not None:
            if t>self.currentLine[0]:                
                self.currentLine = None
            
        if self.nextLine == None:
            # populate self.replayFileDetections = [] prefix = f"A{distance:8.4f},{angle:8.4f}\n"
            line = self.replayFile.readline()
            while line:
                line = line.strip()
                if line[0] == "A":
                    line = line.lstrip("A").strip()
                    parts = line.split(",")
                    distance = float(parts[0])
                    angle = float(parts[1])
                    self.replayFileDetections.append([distance, angle])
                elif line[0] == "I":
                    line = line.lstrip("I").strip()
                    parts = line.split(",")
                    timef = float(parts[0])
                    rotationAngle = float(parts[1])
                    sense = float(parts[2])
                    volume = float(parts[3])
                    trigger = int(parts[4])
                    self.nextLine = [timef, rotationAngle, sense, volume, trigger]
                    break
                line = self.replayFile.readline()        
            
        if self.currentLine is None:
            self.currentLine = self.nextLine
            self.nextLine = None
        print(self.currentLine)
                
                
    # Actually render the frame to the LCD display
    def renderFrame(self):
        sense, volume = self.i2cDev.getPots()
        triggerPressed = GPIO.input(21) == GPIO.LOW
        
        if self.replayFile:
            self.fetchReplayLine()        
            if self.currentLine:
                sense = self.currentLine[2]
                volume = self.currentLine[3]
                self.rotationAngle = self.currentLine[1]
                triggerPressed = self.currentLine[4]
        
        if triggerPressed and not self.lastTrigger:
            self.aliens.reset()
            self.triggerPressedCounter = 0            
        self.lastTrigger = triggerPressed
        
        self.audio.enable_audio(triggerPressed)
        self.audio.set_volume(volume)
        self.radar.setSensitivity(sense)
        self.radar.pushUpdate()
        self.monitorShutdown(sense, volume)
        
        if not triggerPressed:
            self.aliens.remove_old_positions(self.FadeDuration)
            self.purgeAliens()
                    
        if self.replayFile is None:
            gx, gy, gz = self.i2cDev.read_gyro()
            self.rotationAngle = self.i2cDev.get_bearing()            
            self.radar.receive_gyro(gx, gy, gz, self.rotationAngle)
            
        animationPos = self.audio.getSoundTime();
        
        if self.logFile is not None:
            t = time.time() - self.logStart
            prefix = f",{self.rotationAngle:6.3f},{sense:5.2f},{volume:5.2f},"
            if triggerPressed:
                prefix += "1\n"
            else:
                prefix += "0\n"
            if prefix != self._laststrLog:
                self._laststrLog = prefix                
                prefix = f"I{t:8.4f}" + prefix
                self.logFile.write(prefix)
        
        with canvas(self.device) as draw:    
            screen = self.internalRedrawImage(time.time(), animationPos, triggerPressed)
            draw._image.paste(screen, (0,0), screen)   
            draw.rectangle((0, 200, self.device.width, self.device.height), fill=self.PanelColor)
            
            if self.shutdownCounter>4:
                y = 10
                draw.line([(0,241-y),(320,241-y)], fill=(100, 200, 255)) 
                for y in range(2, self.shutdownCounter):
                    draw.line([(0,241-y),(320,241-y)], fill=(0,65,128)) 
                if self.shutdownCounter>10:             
                    print("Shutdown triggered")
                    self.displaySplashScreen(True)
                    self.closeLogging()
                    # If you leave these lines in then luma resets the display (white) on exit grr
                    atexit.unregister(self.device.cleanup)
                    self.device.cleanup = lambda: None
                    if not self.debug:
                        os.system("sudo shutdown -h now")
                    exit()
                          
            if self.debug is not None:
                y = 10
                for (distance, angle) in self.debug:
                    if distance < 11:
                        txt = f"{distance:05.2f}m  {angle:04.1f} deg"
                        draw.text((11, y+1), txt, font=self.font_small, fill="black")        
                        draw.text((10, y), txt, font=self.font_small, fill="white")        
                        y = y + 15
    def __del__(self):
        self.closeLogging()
                               
    
display = AlienDisplay()

try:
    while True:
        display.renderFrame()
            
            
except KeyboardInterrupt:
    print("Game Over Man, Game Over!")
