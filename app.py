import streamlit as st
import cv2
import numpy as np
from ultralytics import YOLO
from collections import deque
import time
import os
import json
import pandas as pd

# Sahifa konfiguratsiyasi
st.set_page_config(
    page_title="Xodimlar Monitoringi Dashboardi",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Papkalarni avtomatik tekshirish va yaratish
os.makedirs("videos/kirish", exist_ok=True)
os.makedirs("videos/chiqish", exist_ok=True)
STATS_FILE = "stats.json"

# Statistikani yuklash va saqlash
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_stats(data):
    with open(STATS_FILE, "w") as f:
        json.dump(data, f, indent=4)

# Dashboard sarlavhasi
st.title("🏢 Ofis Xodimlari uchun Kirdi-Chiqdi va Vaqt Tahlili Tizimi")

# Yon panel (Sidebar)
st.sidebar.header("⚙️ Konfiguratsiya")
rtsp_url = st.sidebar.text_input("RTSP Kamera Manzili", "rtsp://admin:qwerty%4012@192.168.0.250:554/Streaming/Channels/101")
run_monitoring = st.sidebar.checkbox("🚀 Monitoringni Ishga Tushirish", value=False)

# Asosiy ekran layouti (2 ta ustun)
col_video, col_stats = st.columns([2, 1])

with col_video:
    st.subheader("📹 Jonli Kamera Oqimi (Live View)")
    video_placeholder = st.empty()

with col_stats:
    st.subheader("📊 Xodimlar Vaqt Hisoboti")
    stats_placeholder = st.empty()

st.markdown("---")
st.subheader("📁 Saqlangan Kirdi-Chiqdi Videolari (5-10 soniya)")

# Videolarni ko'rish qismi
clip_col1, clip_col2 = st.columns(2)
with clip_col1:
    st.markdown("### 📥 Kirish Videolari")
    kirish_videos = sorted(os.listdir("videos/kirish"), reverse=True)
    selected_kirish = st.selectbox("Kirish videosini tanlang", ["Tanlang..."] + kirish_videos, key="k_vid")
    if selected_kirish != "Tanlang...":
        st.video(os.path.join("videos/kirish", selected_kirish))

with clip_col2:
    st.markdown("### 📤 Chiqish Videolari")
    chiqish_videos = sorted(os.listdir("videos/chiqish"), reverse=True)
    selected_chiqish = st.selectbox("Chiqish videosini tanlang", ["Tanlang..."] + chiqish_videos, key="c_vid")
    if selected_chiqish != "Tanlang...":
        st.video(os.path.join("videos/chiqish", selected_chiqish))

# Monitoring jarayoni
if run_monitoring:
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
    
    # YOLO modelini yuklash (eng yengil va tez nano versiya)
    model = YOLO("yolov8n.pt")
    cap = cv2.VideoCapture(rtsp_url)
    
    if not cap.isOpened():
        st.error("Kameraga ulanib bo'lmadi! IP manzil, login yoki parolni tekshiring.")
    else:
        buffer_length = 100  # 10 soniyali bufer (10 FPS uchun)
        frame_buffer = deque(maxlen=buffer_length)
        
        # Siz bergan mukammal eshik koordinatalari
        LINE_START = (495, 1)
        LINE_END = (480, 300)
        line_x_threshold = 487  # Kesishish chizig'ining o'rtacha X qiymati
        
        object_positions = {}
        user_sessions = load_stats()
        
        def save_video_clip(frames, filename, width, height):
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(filename, fourcc, 10.0, (width, height))
            for f in frames:
                out.write(f)
            out.release()

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                st.warning("Kameradan video kelishi to'xtadi.")
                break
            
            # Kadr o'lchamini standartlashtirish
            frame = cv2.resize(frame, (1280, 720))
            h, w, _ = frame.shape
            frame_buffer.append(frame.copy())
            
            # YOLO tracker orqali odamlarni aniqlash va ID saqlash
            results = model.track(
                frame, 
                persist=True, 
                classes=[0], 
                tracker="bytetrack.yaml", 
                verbose=False, 
                device='cpu'
            )
            
            if results[0].boxes.id is not None:
                boxes = results[0].boxes.xyxy.cpu().numpy()
                track_ids = results[0].boxes.id.cpu().numpy()
                
                for box, track_id in zip(boxes, track_ids):
                    x1, y1, x2, y2 = map(int, box)
                    center_x = int((x1 + x2) / 2)
                    
                    uid = str(int(track_id))
                    if uid not in user_sessions:
                        user_sessions[uid] = {
                            "status": "Xonada", 
                            "total_seconds": 0, 
                            "last_enter": time.time()
                        }

                    # Ekranda ramka va ID chizish
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, f"Xodim ID: {uid}", (x1, y1 - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                    
                    if uid not in object_positions:
                        object_positions[uid] = center_x
                    else:
                        prev_x = object_positions[uid]
                        timestamp = time.strftime("%Y%m%d-%H%M%S")
                        
                        # Kirishni aniqlash (chapdan o'ngga yoki o'ngdan chapga kesishish)
                        if prev_x < line_x_threshold and center_x >= line_x_threshold:
                            filename = f"videos/kirish/xodim_{uid}_kirish_{timestamp}.mp4"
                            save_video_clip(list(frame_buffer), filename, w, h)
                            user_sessions[uid]["status"] = "Xonada"
                            user_sessions[uid]["last_enter"] = time.time()
                            save_stats(user_sessions)
                            
                        elif prev_x > line_x_threshold and center_x <= line_x_threshold:
                            filename = f"videos/chiqish/xodim_{uid}_chiqish_{timestamp}.mp4"
                            save_video_clip(list(frame_buffer), filename, w, h)
                            if user_sessions[uid]["status"] == "Xonada" and "last_enter" in user_sessions[uid]:
                                elapsed = time.time() - user_sessions[uid]["last_enter"]
                                user_sessions[uid]["total_seconds"] += elapsed
                            user_sessions[uid]["status"] = "Tashqarida"
                            save_stats(user_sessions)
                            
                        object_positions[uid] = center_x

            # Eshikning aniq qizil chizig'ini ekranga chizish
            cv2.line(frame, LINE_START, LINE_END, (0, 0, 255), 3)
            
            # Videoni Streamlit interfeysiga uzatish
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            video_placeholder.image(frame_rgb, channels="RGB", use_container_width=True)
            
            # Vaqt statistikasi jadvalini yangilash
            df_data = []
            for uid, info in user_sessions.items():
                active_secs = info["total_seconds"]
                if info["status"] == "Xonada" and "last_enter" in info:
                    active_secs += (time.time() - info["last_enter"])
                
                hours = int(active_secs // 3600)
                mins = int((active_secs % 3600) // 60)
                df_data.append({
                    "Xodim": f"Xodim {uid}", 
                    "Holati": info["status"], 
                    "Xonadagi jami vaqt": f"{hours} soat {mins} min"
                })
            
            if df_data:
                stats_placeholder.dataframe(pd.DataFrame(df_data), use_container_width=True, hide_index=True)
            
            time.sleep(0.04)  # Silliq va yengil ishlashi uchun kichik tanaffus
            
        cap.release()