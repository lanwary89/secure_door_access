#!/usr/bin/env python3

import cv2
import os

# =========================================================
# FIX XDG_RUNTIME_DIR WARNING (for Pygame / SDL)
# =========================================================
if "XDG_RUNTIME_DIR" not in os.environ:
    os.environ["XDG_RUNTIME_DIR"] = "/tmp/runtime-pi"
    os.makedirs(os.environ["XDG_RUNTIME_DIR"], exist_ok=True)

# =========================================================
# IMPORT OTHER LIBRARIES
# =========================================================
import sqlite3
import time
import threading
import numpy as np
import RPi.GPIO as GPIO
from picamera import PiCamera
from picamera.array import PiRGBArray
import pygame
import gc
import queue
import subprocess

# =========================================================
# GPIO CONFIG
# =========================================================
RED_LED = 12
GREEN_LED = 21

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(RED_LED, GPIO.OUT)
GPIO.setup(GREEN_LED, GPIO.OUT)

GPIO.output(RED_LED, GPIO.HIGH)
GPIO.output(GREEN_LED, GPIO.LOW)

# =========================================================
# CONFIG
# =========================================================
SCREEN_W, SCREEN_H = 480, 320
CAM_W, CAM_H = 640, 480

NUM_PICTURES = 30
IMG_SIZE = 200
PHOTO_INTERVAL = 0.3
QUALITY_THRESHOLD = 80
FACE_MIN_SIZE = 130
DETECT_EVERY = 2

DB_PATH = "/home/pi/membres.db"
FACES_BASE = "/home/pi/faces"
TRAIN_SCRIPT = "/home/pi/train_model.py"
RETURN_UI = "/home/pi/screen_get_info.py"

# =========================================================
# TRAINING EVENT FLAG
# =========================================================
capture_done_event = threading.Event()

# =========================================================
# DATABASE INIT
# =========================================================
try:
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute("PRAGMA foreign_keys=ON;")
    cur = db.cursor()
except Exception as e:
    print(f"[ERROR] DB Init: {e}")
    GPIO.cleanup()
    exit(1)

# =========================================================
# GET LAST MEMBER
# =========================================================
try:
    cur.execute("SELECT id, first_name, last_name FROM members ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("[ERROR] No member found in DB")
        db.close()
        GPIO.cleanup()
        exit(1)

    member_id, fn, ln = row
    USER = f"{fn}_{ln}".upper().replace(" ", "_")
    FACE_DIR = f"{FACES_BASE}/{USER}"
    os.makedirs(FACE_DIR, exist_ok=True)
except Exception as e:
    print(f"[ERROR] Loading member: {e}")
    db.close()
    GPIO.cleanup()
    exit(1)

# =========================================================
# AUTO-RESET OLD PHOTOS
# =========================================================
try:
    for f in os.listdir(FACE_DIR):
        os.remove(os.path.join(FACE_DIR, f))
    cur.execute("DELETE FROM member_pictures WHERE member_id=?", (member_id,))
    db.commit()
except Exception as e:
    print(f"[ERROR] Reset photos: {e}")

START_INDEX = 0
captured_count_global = 0
print(f"[INFO] Starting capture from index 0")

# =========================================================
# SAVE IMAGE TO DB
# =========================================================
def save_picture(path):
    try:
        cur.execute(
            "INSERT INTO member_pictures (member_id, image_path) VALUES (?,?)",
            (member_id, path)
        )
        db.commit()
    except Exception as e:
        print(f"[ERROR] Save picture: {e}")

# =========================================================
# CAMERA INIT
# =========================================================
cam = PiCamera()
cam.resolution = (CAM_W, CAM_H)
cam.framerate = 30
raw_capture = PiRGBArray(cam, size=(CAM_W, CAM_H))
time.sleep(1)

# =========================================================
# FACE DETECTOR
# =========================================================
face_cascade = cv2.CascadeClassifier(
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
)

def blur_score(img):
    return cv2.Laplacian(img, cv2.CV_64F).var()

def is_good_face(face, w, h):
    if w < FACE_MIN_SIZE or h < FACE_MIN_SIZE:
        return False
    if blur_score(face) < QUALITY_THRESHOLD:
        return False
    return True

# =========================================================
# PYGAME INIT
# =========================================================
pygame.init()
screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
pygame.display.set_caption("Face Capture")

blink_event = threading.Event()

def blink_green():
    while not blink_event.is_set():
        GPIO.output(GREEN_LED, GPIO.HIGH)
        time.sleep(0.6)
        GPIO.output(GREEN_LED, GPIO.LOW)
        time.sleep(0.6)

frame_queue = queue.Queue(maxsize=2)
faces_result = []
lock = threading.Lock()

def camera_thread():
    for f in cam.capture_continuous(raw_capture, format="bgr", use_video_port=True):
        if capture_done_event.is_set():
            break
        frame = cv2.flip(f.array, 1)
        if frame_queue.full():
            try:
                frame_queue.get_nowait()
            except:
                pass
        frame_queue.put(frame)
        raw_capture.truncate(0)

def ai_thread():
    global faces_result, captured_count_global
    frame_id = 0
    last_photo = time.time() - PHOTO_INTERVAL
    count = START_INDEX

    while not capture_done_event.is_set():

        if frame_queue.empty():
            time.sleep(0.002)
            continue

        frame = frame_queue.get()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces_detected = []

        if frame_id % DETECT_EVERY == 0:
            small_gray = cv2.resize(gray, (CAM_W//2, CAM_H//2))
            detected = face_cascade.detectMultiScale(
                small_gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(FACE_MIN_SIZE//2, FACE_MIN_SIZE//2)
            )
            faces_detected = [(x*2, y*2, w*2, h*2) for (x, y, w, h) in detected]

        tmp_result = []

        for (x, y, w, h) in faces_detected:
            face = frame[y:y+h, x:x+w]
            if not is_good_face(face, w, h):
                continue

            tmp_result.append((x, y, w, h, True))

            if time.time() - last_photo > PHOTO_INTERVAL and count < NUM_PICTURES:

                if count == 0:
                    GPIO.output(RED_LED, GPIO.LOW)
                    threading.Thread(target=blink_green, daemon=True).start()

                face_resized = cv2.resize(face, (IMG_SIZE, IMG_SIZE))
                img_path = f"{FACE_DIR}/{count}.jpg"
                cv2.imwrite(img_path, face_resized)
                save_picture(img_path)

                count += 1
                last_photo = time.time()

                with lock:
                    captured_count_global = count

        with lock:
            faces_result = tmp_result

        frame_id += 1
        del gray
        gc.collect()

        if count >= NUM_PICTURES:
            capture_done_event.set()
            break

def main_display():
    global captured_count_global

    while not capture_done_event.is_set():

        if frame_queue.empty():
            continue

        frame = frame_queue.get()

        with lock:
            faces = faces_result.copy()
            hud_count = captured_count_global

        # ===== Draw face rectangles (Green/Red) =====
        for (x, y, w, h, valid) in faces:
            color = (0, 255, 0) if valid else (0, 0, 255)
            cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)

        # ===== Draw center guide frame (Yellow/Green) =====
        cx, cy = SCREEN_W // 2, SCREEN_H // 2
        s = 120
        guide_color = (0, 255, 0) if faces else (0, 255, 255)
        cv2.rectangle(frame, (cx-s, cy-s), (cx+s, cy+s), guide_color, 3)

        # ===== Draw counter =====
        cv2.putText(frame,
                    f"Captured: {hud_count}/{NUM_PICTURES}",
                    (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_surface = pygame.surfarray.make_surface(frame_rgb.swapaxes(0,1))
        screen.blit(frame_surface, (0,0))
        pygame.display.flip()


# =========================================================
# START THREADS
# =========================================================
cam_thread = threading.Thread(target=camera_thread)
ai_worker = threading.Thread(target=ai_thread)

cam_thread.start()
ai_worker.start()

main_display()

# =========================================================
# CLEANUP BEFORE TRAINING
# =========================================================
blink_event.set()
GPIO.output(GREEN_LED, GPIO.LOW)
GPIO.output(RED_LED, GPIO.HIGH)

cam.close()
pygame.quit()
gc.collect()

print("[INFO] Starting training...")

# =========================================================
# AUTO-TRAIN
# =========================================================
try:
    subprocess.run(["python3", TRAIN_SCRIPT])
except Exception as e:
    print(f"[ERROR] Auto-train: {e}")

# =========================================================
# RETURN UI
# =========================================================
try:
    subprocess.run(["python3", RETURN_UI])
except Exception as e:
    print(f"[ERROR] Return UI: {e}")

db.close()
GPIO.cleanup()
