# Alien Style Motion Tracker
[![License: NCCL](https://img.shields.io/badge/License-NCCL--Non--Commercial--Attribution-blue.svg)](./licence.txt)

I decided it was time to build a fully working M314 Motion Tracker. I've always loved the Aliens films and franchise, and this has been something I've wanted to build for over 10 years.
Unlike other replicas, this one not only makes the sounds and has a display, but it *actually tracks motion* up to about 11ish meters.
This project uses the DreamRF+Radar Hat for the Raspberry Pi, powered by the BGT60TR13C from Infineon.

My hope is that the community effort will improve this project even further, and I'm excited to see where this goes.

## Video Journey
The 3-part video series showing how this was designed can be found [on my YouTube Channel](https://www.youtube.com/playlist?list=PL18CvD80w43YAV8UG24NtwRc2Wy-i7yyd)

## Rough Requirements
 - Raspberry Pi 4b
 - DreamRF+Hat
 - Python 3.1
 - Luma.Core [pull request 278](https://github.com/rm-hull/luma.core/pull/278) 
 - Other Hardware from the Build Guide
 
## Running
 - Start run_streaming_tracker.py as sudo
 - Start main.py
or
 - Run the startup.sh bash script
    - Assumes the code is in /home/alien/luma-env/
    - Has a local Python environment 

## Installation and Build Guide
The full build guide is located at:
[alien.robsmithdev.co.uk](https://alien.robsmithdev.co.uk)

This project is licensed under the **Non-Commercial Copyleft with Attribution License (NCCL)**.  

Certain third-party components are provided under their own licenses  
(e.g., BSD 3-Clause by Infineon). See [`NOTICE`](./NOTICE) and  
[`THIRD_PARTY_LICENSES`](./THIRD_PARTY_LICENSES/) for details.  

- [📄 View License Summary](./licence_summary.md)  
- [📘 View Full License](./licence.txt)  

## License
This project is licensed under the Non-Commercial Copyleft with Attribution License (NCCL).
Certain third-party components are provided under their own licenses (e.g., BSD 3-Clause by Infineon)
see [`NOTICE`](./NOTICE) and [`THIRD_PARTY_LICENSES`](./THIRD_PARTY_LICENSES/) for details.
 - [View License Summary](./licence_summary.md)
 - [View Full License](./licence.txt)
 
 
 


