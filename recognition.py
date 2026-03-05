#!/usr/bin/env python3
import cv2, time, pickle, RPi.GPIO as GPIO
from picamera import PiCamera
from picamera.array import PiRGBArray
import threading, queue, pygame, numpy as np, sqlite3, os, smbus2
from evdev import InputDevice, ecodes, list_devices
import sys, subprocess, select
import requests

# ================= ESP32 CONFIG =================
ESP32_IP = "192.168.100.9"
ESP32_TOKEN = "PI_OK_2026"
ESP32_URL = f"http://{ESP32_IP}/relay/on?token={ESP32_TOKEN}"

# ================= GPIO =================
LED_RED, LED_GREEN, RELAY, BUZZER = 12, 21, 16, 20
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(LED_RED, GPIO.OUT, initial=GPIO.HIGH)
GPIO.setup(LED_GREEN, GPIO.OUT, initial=GPIO.LOW)
GPIO.setup(RELAY, GPIO.OUT, initial=GPIO.HIGH)
GPIO.setup(BUZZER, GPIO.OUT, initial=GPIO.LOW)

# ================= CONFIG =================
SCREEN_W, SCREEN_H = 480, 320
CAM_W, CAM_H = 640, 480
IMG_SIZE = 200
CONFIDENCE_THRESHOLD = 60
UNKNOWN_CHECK_DELAY = 8
NO_FACE_TIMEOUT = 60
UNKNOWN_MAX_REPEATS = 15
VALIDATION_TIME = 1.0

MODEL_DIR = "/home/pi/models"
CASCADE_PATH = "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
DB_PATH = "/home/pi/membres.db"
IMG_ACCEPT = "/home/pi/validation.png"
IMG_REFUSE = "/home/pi/refuse.png"

# ================= LOAD MODEL =================
recognizer = cv2.face.LBPHFaceRecognizer_create()
if os.path.exists(f"{MODEL_DIR}/lbph_model.yml"):
    recognizer.read(f"{MODEL_DIR}/lbph_model.yml")
labels = {}
if os.path.exists(f"{MODEL_DIR}/labels.pkl"):
    with open(f"{MODEL_DIR}/labels.pkl","rb") as f:
        labels = pickle.load(f)
id_label = {v:k for k,v in labels.items()}
face_cascade = cv2.CascadeClassifier(CASCADE_PATH)

# ================= DATABASE =================
def log_access(code, name, status):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS history_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_membre TEXT,
                name TEXT,
                status TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")
        cur.execute("INSERT INTO history_access (code_membre,name,status) VALUES (?,?,?)",
                    (code,name,status))
        conn.commit()
    except Exception as e:
        print("[DB ERROR]", e)
    finally:
        conn.close()

# ================= CAMERA =================
camera = PiCamera()
camera.resolution = (CAM_W, CAM_H)
camera.framerate = 30
raw = PiRGBArray(camera, size=(CAM_W, CAM_H))
time.sleep(1)

# ================= PYGAME =================
pygame.init()
screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
pygame.display.set_caption("Face Recognition")
pygame.mouse.set_visible(False)
display_surface = pygame.Surface((SCREEN_W, SCREEN_H))

# ================= THREAD QUEUES =================
frame_queue = queue.Queue(maxsize=1)
result_queue = queue.Queue(maxsize=1)

# ================= FACE DETECTION THREAD =================
def face_detection_thread():
    while True:
        if frame_queue.empty():
            time.sleep(0.001)
            continue
        frame = frame_queue.get()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(80,80))
        results = []
        for (x,y,w,h) in faces:
            face_crop = cv2.resize(gray[y:y+h, x:x+w], (IMG_SIZE, IMG_SIZE))
            label, confidence = (None, None)
            if labels: label, confidence = recognizer.predict(face_crop)
            results.append((x,y,w,h,label,confidence))
        if not result_queue.full():
            result_queue.put(results)
        frame_queue.task_done()

threading.Thread(target=face_detection_thread, daemon=True).start()

# ================= KEYPAD =================
I2C_ADDR = 0x20
bus = smbus2.SMBus(1)
KEYS=[['1','4','7','*'],['2','5','8','0'],['3','6','9','#']]
ROWS=[0x10,0x20,0x40]
COLS=[0x01,0x02,0x04,0x08]

def scan_keypad():
    for r,row_bit in enumerate(ROWS):
        bus.write_byte(I2C_ADDR, 0xFF & ~row_bit)
        time.sleep(0.001)
        val = bus.read_byte(I2C_ADDR)
        for c,col_bit in enumerate(COLS):
            if not (val & col_bit):
                return KEYS[r][c]
    return None

# ================= TOUCHSCREEN =================
touch_count, last_touch_time = 0, 0
TOUCH_MAX_INTERVAL = 0.8
dev = None
for path in list_devices():
    device = InputDevice(path)
    if "touch" in device.name.lower():
        dev = device
        print("Touchscreen detected:", dev.path, device.name)
        break
if not dev:
    print("⚠️ Touchscreen not found")

# ================= CLEAN EXIT =================
def clean_exit(next_script=None):
    try: 
        if dev: dev.close()
    except: pass
    camera.close()
    GPIO.cleanup()
    pygame.quit()
    if next_script:
        subprocess.Popen(["python3", next_script])
    sys.exit(0)

# ================= TOUCH + KEYPAD =================
def check_touch_and_keypad():
    global touch_count, last_touch_time
    now = time.time()
    # touchscreen
    if dev:
        r, _, _ = select.select([dev], [], [], 0)
        if dev in r:
            try:
                for event in dev.read():
                    if event.type == ecodes.EV_KEY and event.value == 1:
                        if now - last_touch_time > TOUCH_MAX_INTERVAL:
                            touch_count = 1
                        else:
                            touch_count += 1
                        last_touch_time = now
                        if touch_count >= 3:
                            clean_exit("/home/pi/screen_get_info.py")
            except: pass
    # keypad
    key = scan_keypad()
    if key == '*':
        clean_exit("/home/pi/keypad.py")

# ================= MAIN LOOP =================
unknown_counter = 0
last_face_time = time.time()
face_action_timer = 0
face_state = "idle"
stream = camera.capture_continuous(raw, format="bgr", use_video_port=True)

for frame_pi in stream:
    frame = cv2.flip(frame_pi.array, 1)
    raw.truncate(0)
    check_touch_and_keypad()
    if not frame_queue.full(): frame_queue.put(frame.copy())
    faces_detected = result_queue.get() if not result_queue.empty() else []
    now = time.time()
    face_handled = False

    # ===== HANDLE KNOWN =====
    for (x,y,w,h,label,confidence) in faces_detected:
        if label is not None and confidence < CONFIDENCE_THRESHOLD:
            if face_state != "known":
                recognized_name = id_label[label].replace("_"," ")
                # Show acceptance image
                accept_img = pygame.image.load(IMG_ACCEPT)
                accept_img = pygame.transform.scale(accept_img, (SCREEN_W, SCREEN_H))
                display_surface.blit(accept_img, (0,0))
                font = pygame.font.SysFont("dejavusans", 28)
                text_surf = font.render(recognized_name.upper(), True, (255,255,255))
                display_surface.blit(text_surf, ((SCREEN_W-text_surf.get_width())//2, 10))
                screen.blit(display_surface, (0,0)); pygame.display.flip()
                # LEDs & Relay
                GPIO.output(LED_RED, GPIO.HIGH); GPIO.output(LED_GREEN, GPIO.HIGH)
                GPIO.output(RELAY, GPIO.LOW)
                # Log database
                log_access(None, recognized_name, "known")
                # Trigger ESP32
                try:
                    requests.get(ESP32_URL, timeout=1)
                    print(f"✅ ESP32 triggered for {recognized_name}")
                except Exception as e:
                    print("[ESP32 ERROR]", e)
                face_action_timer = now
                face_state = "known"
                unknown_counter = 0
            face_handled = True
            break

    # ===== HANDLE UNKNOWN =====
    if not face_handled and faces_detected:
        if face_state != "unknown":
            face_action_timer = now
            face_state = "unknown"
        elif now - face_action_timer >= UNKNOWN_CHECK_DELAY:
            refuse_img = pygame.image.load(IMG_REFUSE)
            refuse_img = pygame.transform.scale(refuse_img, (SCREEN_W, SCREEN_H))
            display_surface.blit(refuse_img, (0,0))
            font = pygame.font.SysFont("dejavusans", 28)
            text_surf = font.render("INCONNU", True, (255,255,255))
            display_surface.blit(text_surf, ((SCREEN_W-text_surf.get_width())//2, 10))
            screen.blit(display_surface, (0,0)); pygame.display.flip()
            GPIO.output(LED_RED, GPIO.HIGH); GPIO.output(LED_GREEN, GPIO.LOW)
            GPIO.output(BUZZER, GPIO.HIGH)
            time.sleep(3); GPIO.output(BUZZER, GPIO.LOW)
            unknown_counter += 1; face_state = "idle"; face_action_timer = now
            if unknown_counter >= UNKNOWN_MAX_REPEATS: unknown_counter = 0
            last_face_time = now

    # ===== RESET KNOWN =====
    if face_state == "known" and now - face_action_timer >= VALIDATION_TIME:
        GPIO.output(LED_GREEN, GPIO.LOW); GPIO.output(RELAY, GPIO.HIGH)
        face_state = "idle"; last_face_time = now

    # ===== DISPLAY LIVE VIDEO =====
    display_frame = cv2.resize(frame, (SCREEN_W, SCREEN_H))
    cx, cy = SCREEN_W//2, SCREEN_H//2; box_size = 200
    x1, y1 = cx-box_size//2, cy-box_size//2
    x2, y2 = cx+box_size//2, cy+box_size//2
    cv2.rectangle(display_frame, (x1,y1), (x2,y2), (0,255,255), 2)
    screen_surf = pygame.surfarray.make_surface(cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB).swapaxes(0,1))
    screen.blit(screen_surf, (0,0)); pygame.display.flip()

    # ===== NO FACE TIMEOUT =====
    if now - last_face_time > NO_FACE_TIMEOUT:
        print("⚠️ No face detected 60s, returning main.py")
        clean_exit("/home/pi/main.py")

    # ===== PYGAME EVENTS =====
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            clean_exit()
