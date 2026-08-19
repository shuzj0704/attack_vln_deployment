#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Capture a single RGB frame from the Intel RealSense D435i and save it locally.
"""

import os
import sys
import time
from datetime import datetime

try:
    import pyrealsense2 as rs
    import numpy as np
    import cv2
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}")
    print("Please install: pyrealsense2, numpy, opencv-python")
    sys.exit(1)

# Save beside this script unless D435I_SAVE_DIR is configured.
SAVE_DIR = os.environ.get("D435I_SAVE_DIR", os.path.dirname(os.path.abspath(__file__)))
os.makedirs(SAVE_DIR, exist_ok=True)

def capture_rgb():
    # Configure RealSense pipeline: color stream only
    pipeline = rs.pipeline()
    config = rs.config()

    # Try to enable the first available RealSense device
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

    print("[INFO] Starting RealSense pipeline...")
    try:
        pipeline.start(config)
    except RuntimeError as e:
        print(f"[ERROR] Failed to start RealSense pipeline: {e}")
        print("[HINT] Check if D435i is connected via 'lsusb | grep RealSense'")
        sys.exit(1)

    # Wait a few frames for auto-exposure to stabilize
    print("[INFO] Warming up camera...")
    for _ in range(30):
        pipeline.wait_for_frames()

    # Capture frame
    frames = pipeline.wait_for_frames()
    color_frame = frames.get_color_frame()
    if not color_frame:
        print("[ERROR] No color frame received")
        pipeline.stop()
        sys.exit(1)

    # Convert to numpy array (BGR format from RealSense)
    color_image = np.asanyarray(color_frame.get_data())

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    filename = f"d435i_rgb_{timestamp}.png"
    filepath = os.path.join(SAVE_DIR, filename)

    # Save image
    success = cv2.imwrite(filepath, color_image)
    if not success:
        print(f"[ERROR] Failed to write image to {filepath}")
        pipeline.stop()
        sys.exit(1)

    print(f"[OK] Saved RGB image: {filepath}")
    print(f"     Resolution: {color_image.shape[1]}x{color_image.shape[0]}")

    pipeline.stop()

if __name__ == "__main__":
    try:
        capture_rgb()
    except KeyboardInterrupt:
        print("\n[INFO] Cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        sys.exit(1)
