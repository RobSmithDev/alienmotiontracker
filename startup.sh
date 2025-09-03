#!/bin/bash

# Wait until the system is ready
sleep 5

cd /home/alien/luma-env/

# Hopefully one of these will work
amixer -D pulse sset Master 100%
amixer cset numid=3 70%
amixer -c 3 cset numid=4 65535

echo Starting Tracker
# Start run_streaming_tracker.py as root in background
sudo ./bin/python3 run_streaming_tracker.py &

# Optional: wait a moment to ensure tracker is running
sleep 2

echo Starting Display
# Start lcd2.py (normal user unless it also needs sudo)
./bin/python3 main.py

