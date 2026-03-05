#!/usr/bin/env python3
import time
import threading
import subprocess
import sqlite3
from evdev import InputDevice, ecodes
from PIL import Image, ImageDraw, ImageFont
import os
import RPi.GPIO as GPIO
import datetime

# =========================================================
# GPIO CONFIG
# =========================================================
RED_LED   = 12
GREEN_LED = 21
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(RED_LED, GPIO.OUT)
GPIO.setup(GREEN_LED, GPIO.OUT)

# =========================================================
# HARDWARE CONFIG
# =========================================================
FBDEV = "/dev/fb0"
TOUCH_DEVICE = "/dev/input/touchscreen"

SCREEN_W = 480
SCREEN_H = 320
PRESSURE_MIN = 20
TMP_IMG = "/tmp/ui.png"

CALIB = {
    'xmin': 710,
    'xmax': 3497,
    'ymin': 445,
    'ymax': 3667
}

# =========================================================
# STATES
# =========================================================
STATE_HOME = 1
STATE_ADMIN = 2
STATE_LASTNAME = 3
STATE_FIRSTNAME = 4
STATE_NEWCODE = 5

state = STATE_HOME
buttons = []

admin_code = ""
first_name = ""
last_name = ""
new_member_code = ""

# =========================================================
# GLOBAL RUNNING FLAG
# =========================================================
running = True

# =========================================================
# COLORS
# =========================================================
PRIMARY = (0,120,220)
BUTTON = (80,80,80)
BUTTON_DELETE = (150,60,60)
BUTTON_ENTER = (0,130,80)
TEXT_BG = (255,255,255)

# =========================================================
# DATABASE
# =========================================================
DB_PATH = "/home/pi/membres.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            code_membre TEXT UNIQUE NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_member(fn, ln, code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO members (first_name, last_name, code_membre) VALUES (?,?,?)",
            (fn, ln, code)
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"[DB ERROR] {e}")
    finally:
        conn.close()

def generate_new_code():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT code_membre FROM members ORDER BY id DESC LIMIT 1")
    r = c.fetchone()
    conn.close()
    return "1000" if not r else str(int(r[0])+1).zfill(4)

init_db()

# =========================================================
# UTILITIES
# =========================================================
def clear_fb():
    try:
        with open(FBDEV,"wb") as f:
            f.write(b"\x00"*(SCREEN_W*SCREEN_H*4))
    except:
        pass

def map_touch(v, vmin, vmax, outmax, flip=False):
    v = max(vmin, min(v, vmax))
    if flip:
        return int((vmax - v) * outmax / (vmax - vmin))
    else:
        return int((v - vmin) * outmax / (vmax - vmin))

def round_rect(d,x,y,w,h,r,c):
    d.rectangle((x+r,y,x+w-r,y+h),fill=c)
    d.rectangle((x,y+r,x+w,y+h-r),fill=c)
    d.pieslice((x,y,x+2*r,y+2*r),180,270,fill=c)
    d.pieslice((x+w-2*r,y,x+w,y+2*r),270,360,fill=c)
    d.pieslice((x,y+h-2*r,x+2*r,y+h),90,180,fill=c)
    d.pieslice((x+w-2*r,y+h-2*r,x+w,y+h),0,90,fill=c)

def font(size,bold=False):
    try:
        name="DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        return ImageFont.truetype(
            f"/usr/share/fonts/truetype/dejavu/{name}",size)
    except:
        return ImageFont.load_default()

# =========================================================
# BACKGROUND
# =========================================================
try:
    BG = Image.open("/home/pi/TEO.jpg").resize((SCREEN_W,SCREEN_H))
except:
    BG = Image.new("RGB",(SCREEN_W,SCREEN_H),(20,20,20))

try:
    COMEBACK_IMG = Image.open("/home/pi/comback.png").resize((50,50))
except:
    COMEBACK_IMG = Image.new("RGB",(50,50),(255,0,0))

# =========================================================
# SCREENS
# =========================================================
def screen_home():
    buttons.clear()
    img=BG.copy()
    d=ImageDraw.Draw(img)

    bw,bh=280,50
    bx=(SCREEN_W-bw)//2
    by=SCREEN_H-bh-20
    round_rect(d,bx,by,bw,bh,15,PRIMARY)
    f=font(18,True)
    t="Ajouter un Nouveau\nMembre"
    for i,l in enumerate(t.split("\n")):
        w,h=d.textsize(l,font=f)
        d.text((bx+(bw-w)//2,by+5+i*22),l,"white",f)
    buttons.append(("ADD",bx,by,bw,bh))

    # Comback button top-left
    img.paste(COMEBACK_IMG,(10,10))
    buttons.append(("COMEBACK",10,10,50,50))

    return img

def screen_code(title,val,show=False):
    buttons.clear()
    img=Image.new("RGB",(SCREEN_W,SCREEN_H),TEXT_BG)
    d=ImageDraw.Draw(img)
    d.text((SCREEN_W//2-100,8),title,"black",font=font(18,True))
    round_rect(d,40,50,SCREEN_W-80,36,10,(220,220,220))
    txt="".join(val) if show else "*"*len(val)
    d.text((50,56),txt,"black",font=font(20,True))

    keys=["1","2","3","4","5","6","7","8","9","<","0","OK"]
    s=10
    kw=(SCREEN_W-4*s)//3
    kh=48
    sy=100
    for i,k in enumerate(keys):
        x=s+(i%3)*(kw+s)
        y=sy+(i//3)*(kh+s)
        col=BUTTON_ENTER if k=="OK" else BUTTON_DELETE if k=="<" else BUTTON
        round_rect(d,x,y,kw,kh,10,col)
        d.text((x+kw//2-8,y+kh//2-10),k,"white",font(18,True))
        buttons.append((k,x,y,kw,kh))
    return img

def keyboard(txt,title):
    buttons.clear()
    img=Image.new("RGB",(SCREEN_W,SCREEN_H),TEXT_BG)
    d=ImageDraw.Draw(img)
    d.text((SCREEN_W//2-120,6),title,"black",font(18,True))
    round_rect(d,30,40,SCREEN_W-60,36,10,(220,220,220))
    d.text((40,46),txt,"black",font(18))
    layout=["azertyuiop","qsdfghjkl","wxcvbnm"]
    sy=90
    sp=6
    kh=36
    for r,row in enumerate(layout):
        kw=(SCREEN_W-40-(len(row)-1)*sp)//len(row)
        sx=(SCREEN_W-(kw*len(row)+(len(row)-1)*sp))//2
        for c,ch in enumerate(row):
            x=sx+c*(kw+sp)
            y=sy+r*(kh+sp)
            round_rect(d,x,y,kw,kh,8,BUTTON)
            d.text((x+kw//2-6,y+kh//2-8),ch,"white",font(16))
            buttons.append((ch,x,y,kw,kh))
    y=sy+3*(kh+sp)
    round_rect(d,10,y,70,kh,8,BUTTON_DELETE)
    d.text((28,y+8),"DEL","white",font(14))
    buttons.append(("DEL",10,y,70,kh))
    round_rect(d,90,y,SCREEN_W-180,kh,8,BUTTON)
    d.text((SCREEN_W//2-20,y+8),"space","white",font(14))
    buttons.append(("SPACE",90,y,SCREEN_W-180,kh))
    round_rect(d,SCREEN_W-80,y,70,kh,8,BUTTON_ENTER)
    d.text((SCREEN_W-68,y+8),"ENTER","white",font(14))
    buttons.append(("ENTER",SCREEN_W-80,y,70,kh))
    return img

# =========================================================
# RENDER
# =========================================================
def render():
    if state==STATE_HOME:
        img=screen_home()
    elif state==STATE_ADMIN:
        img=screen_code("Code Admin",list(admin_code))
    elif state==STATE_LASTNAME:
        img=keyboard(last_name,"Nom du membre")
    elif state==STATE_FIRSTNAME:
        img=keyboard(first_name,"Prenom du membre")
    elif state==STATE_NEWCODE:
        img=screen_code("Code Membre",list(new_member_code),True)
    img.save(TMP_IMG)
    subprocess.run(["fbi","-T","1","-d",FBDEV,"-a","-noverbose",TMP_IMG],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

# =========================================================
# TOUCH HANDLER
# =========================================================
def handle_touch(x,y):
    global state,admin_code,first_name,last_name,new_member_code,running
    for lab,bx,by,bw,bh in buttons:
        if bx<=x<=bx+bw and by<=y<=by+bh:
            if lab=="COMEBACK":
                running=False
                clear_fb()
                GPIO.cleanup()
                os.execv("/usr/bin/python3",["python3","/home/pi/recognition.py"])
            elif state==STATE_HOME and lab=="ADD":
                state=STATE_ADMIN
                admin_code=""
            elif state==STATE_ADMIN:
                if lab=="OK" and admin_code=="1982": state=STATE_LASTNAME
                elif lab=="<": admin_code=admin_code[:-1]
                elif lab.isdigit(): admin_code+=lab
            elif state==STATE_LASTNAME:
                if lab=="ENTER": state=STATE_FIRSTNAME
                elif lab=="DEL": last_name=last_name[:-1]
                elif lab=="SPACE": last_name+=" "
                else: last_name+=lab
            elif state==STATE_FIRSTNAME:
                if lab=="ENTER":
                    new_member_code=generate_new_code()
                    state=STATE_NEWCODE
                elif lab=="DEL": first_name=first_name[:-1]
                elif lab=="SPACE": first_name+=" "
                else: first_name+=lab
            elif state==STATE_NEWCODE:
                if lab=="OK":
                    GPIO.output(RED_LED, GPIO.LOW)
                    save_member(first_name,last_name,new_member_code)
                    subprocess.run(["python3","/home/pi/take_pic_face.py"])
                    subprocess.run(["python3","/home/pi/train_model.py"])
                    GPIO.output(RED_LED, GPIO.HIGH)
                    GPIO.output(GREEN_LED, GPIO.LOW)
                    clear_fb()
                    running=False
                    time.sleep(0.5)
                    subprocess.run(["killall","-9","fbi"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    clear_fb()
                    GPIO.output(RED_LED, GPIO.LOW)
                    GPIO.output(GREEN_LED, GPIO.LOW)
                    GPIO.cleanup()
                    time.sleep(1)
                    os.execv("/usr/bin/python3",["python3","/home/pi/recognition.py"])
                elif lab=="<": new_member_code=new_member_code[:-1]
                elif lab.isdigit(): new_member_code+=lab
            render()

# =========================================================
# TOUCH LOOP
# =========================================================
def touch_loop():
    global running
    dev = InputDevice(TOUCH_DEVICE)
    x=y=p=0
    touching=False
    for e in dev.read_loop():
        if not running: break
        if e.type==ecodes.EV_ABS:
            if e.code==ecodes.ABS_X: x=e.value
            elif e.code==ecodes.ABS_Y: y=e.value
            elif e.code==ecodes.ABS_PRESSURE: p=e.value
        elif e.type==ecodes.EV_KEY and e.code==ecodes.BTN_TOUCH:
            if e.value==0 and touching and p>PRESSURE_MIN:
                sx=map_touch(x,CALIB["xmin"],CALIB["xmax"],SCREEN_W,flip=True)
                sy=map_touch(y,CALIB["ymin"],CALIB["ymax"],SCREEN_H,flip=True)
                handle_touch(sx,sy)
            touching=e.value==1

# =========================================================
# MAIN
# =========================================================
def main():
    global running
    clear_fb()
    GPIO.output(RED_LED,GPIO.HIGH)
    GPIO.output(GREEN_LED,GPIO.LOW)
    render()
    t=threading.Thread(target=touch_loop,daemon=True)
    t.start()
    while running:
        time.sleep(0.5)
    print("Script stopped cleanly.")

# =========================================================
if __name__=="__main__":
    main()
