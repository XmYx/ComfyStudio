import os
import random
import tempfile

import cv2

def extract_frame(video_path):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        # QMessageBox.warning(self, "Error", "Cannot open video file.")
        return (False, "Cannot open video file.")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count - 1)
    ret, frame = cap.read()
    if not ret:
        # QMessageBox.warning(self, "Error", "Failed to read last frame.")
        cap.release()
        return (False, "Failed to read last frame.")
    temp_dir = tempfile.gettempdir()
    frame_filename = os.path.join(temp_dir, f"extracted_frame_{random.randint(0, 999999)}.png")
    cv2.imwrite(frame_filename, frame)
    cap.release()
    return (True, frame_filename)