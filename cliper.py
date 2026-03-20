import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
import threading
import os
import sys
import json
import re
import urllib.request
from datetime import timedelta

# --- 설정 파일 경로 ---
CONFIG_FILE = "cliper_config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {}

def save_config(save_dir, ext):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"save_dir": save_dir, "ext": ext}, f, ensure_ascii=False, indent=4)
    except: pass

# --- 배 속에 있는 프로그램 경로를 찾는 함수 ---
def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

YT_DLP_PATH = get_resource_path(os.path.join("bin", "yt-dlp.exe"))
N_M3U8_PATH = get_resource_path(os.path.join("bin", "N_m3u8DL-RE.exe"))
FFMPEG_PATH = get_resource_path(os.path.join("bin", "ffmpeg.exe"))

# --- 시간 변환 함수 ---
def time_to_sec(t_str):
    parts = list(map(int, t_str.strip().split(':')))
    if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
    elif len(parts) == 2: return parts[0]*60 + parts[1]
    else: return parts[0]

def sec_to_time(sec):
    return str(timedelta(seconds=int(sec)))

# --- 치지직 클립 전용 HTML 스크래핑 우회 함수 ---
def extract_chzzk_clip_m3u8(clip_url):
    """최신 API를 사용하여 치지직 클립의 m3u8 주소를 추출합니다."""
    clip_uid = clip_url.split('/')[-1].split('?')[0]
    
    # 1. 클립 상세 정보(videoId, inKey) 추출
    play_info_url = f"https://api.chzzk.naver.com/service/v1/play-info/clip/{clip_uid}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    
    try:
        req = urllib.request.Request(play_info_url, headers=headers)
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode('utf-8'))
            if data.get('code') != 200:
                raise Exception(f"API 오류: {data.get('message')}")
            video_id = data['content']['videoId']
    except Exception as e:
        raise Exception(f"클립 메타데이터 추출 실패: {e}")

    # 2. Videohub API를 통해 HLS(m3u8) 주소 추출
    # yt-dlp와 유사하게 고품질 m3u8을 제공하는 VideoHub 패널 파라미터 사용
    params = {
        'seedType': 'SPECIFIC',
        'serviceType': 'CHZZK',
        'seedMediaId': video_id,
        'mediaType': 'VOD',
        'panelType': 'sdk_chzzk',
        'adAllowed': 'Y',
        'deviceType': 'html5_mo'
    }
    query = '&'.join([f'{k}={v}' for k, v in params.items()])
    hub_url = f"https://api-videohub.naver.com/shortformhub/feeds/v8/card?{query}"
    
    try:
        req2 = urllib.request.Request(hub_url, headers=headers)
        with urllib.request.urlopen(req2) as res2:
            data_hub = json.loads(res2.read().decode('utf-8'))
            playback = data_hub.get('card', {}).get('content', {}).get('vod', {}).get('playback', {})
            for mpd in playback.get('MPD', []):
                for period in mpd.get('Period', []):
                    for adaptation in period.get('AdaptationSet', []):
                        for representation in adaptation.get('Representation', []):
                            m3u8_url = representation.get('@nvod:m3u')
                            if m3u8_url:
                                return m3u8_url
    except Exception as e:
        raise Exception(f"재생 주소(m3u8) 추출 실패: {e}")
        
    raise Exception("재생 가능한 영상 소스가 없습니다. (이미 삭제되었거나 비공개 상태일 수 있습니다.)")

# --- 색상 및 UI 테마 설정 (Catppuccin 모티브) ---
BG_MAIN = "#1E1E2E"        # 배경
BG_PANEL = "#181825"       # 패널 배경 (더 어두운 테마)
FG_TEXT = "#CDD6F4"        # 일반 텍스트
FG_DIM = "#A6ADC8"         # 보조 텍스트
ACCENT_ERR = "#F38BA8"     # 에러 (레드)
ACCENT_OK = "#A6E3A1"      # 성공 (그린)
ACCENT_BTN = "#CBA6F7"     # 주 버튼 (퍼플/핑크)
BTN_TEXT = "#11111B"       # 주 버튼 텍스트
ENTRY_BG = "#313244"       # 입력창 배경
ENTRY_FG = "#CDD6F4"       # 입력창 텍스트

# --- UI 이벤트 함수 ---
def select_directory():
    dir_path = filedialog.askdirectory(initialdir=entry_dir.get())
    if dir_path:
        entry_dir.delete(0, tk.END)
        entry_dir.insert(0, dir_path)

def toggle_cut():
    state = tk.NORMAL if var_cut.get() else tk.DISABLED
    bg_color = ENTRY_BG if var_cut.get() else BG_PANEL
    entry_start.config(state=state, bg=bg_color)
    entry_end.config(state=state, bg=bg_color)

# --- 실시간 로그 기록 함수 ---
log_file_path = ""

def write_log(msg):
    if log_file_path:
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except: pass

    def update_ui():
        txt_log.config(state=tk.NORMAL)
        txt_log.insert(tk.END, msg + "\n")
        txt_log.see(tk.END)
        txt_log.config(state=tk.DISABLED)
        root.update_idletasks() # UI 강제 업데이트
    root.after(0, update_ui)

# --- 메인 작업 프로세스 상태 관리 ---
current_process = None
is_cancelled = False

def cancel_process():
    global is_cancelled, current_process
    if current_process and current_process.poll() is None:
        is_cancelled = True
        btn_stop.config(state=tk.DISABLED, text="중지하는 중...")
        write_log("\n[알림] 사용자가 다운로드를 강제 중단했습니다. 프로세스를 종료합니다...")
        try:
            current_process.kill()
        except:
            pass

def run_cmd_with_log(cmd_list, step_name):
    global current_process, is_cancelled
    write_log(f"\n▶ [{step_name}] 시작")
    output_lines = []
    
    # Windows에서 콘솔 창을 띄우지 않기 위한 플래그
    creation_flags = 0x08000000 
    
    current_process = subprocess.Popen(
        cmd_list,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding='utf-8',
        errors='replace',
        creationflags=creation_flags
    )
    
    for line in current_process.stdout:
        if is_cancelled:
            break
        clean_line = line.strip()
        if clean_line:
            write_log(clean_line)
            output_lines.append(clean_line)
            
    current_process.wait()
    
    if is_cancelled:
        raise Exception("사용자에 의해 작업이 취소되었습니다.")
        
    if current_process.returncode != 0:
        raise Exception(f"[{step_name}] 실패 (에러코드: {current_process.returncode})")
        
    write_log(f"▶ [{step_name}] 완료\n")
    return "\n".join(output_lines)

# --- 메인 작업 프로세스 ---
def process_clip():
    global is_cancelled
    is_cancelled = False
    
    btn_start.config(state=tk.DISABLED, bg=BG_PANEL, text="⏳ 작업 진행 중...")
    btn_stop.config(state=tk.NORMAL)
    lbl_status.config(text="상태: 미디어 추출 중... (로그 확인)", fg="#89B4FA") # 파란색 계열
    
    txt_log.config(state=tk.NORMAL)
    txt_log.delete(1.0, tk.END)
    txt_log.config(state=tk.DISABLED)
    
    thread = threading.Thread(target=run_commands)
    thread.start()

def run_commands():
    global log_file_path, is_cancelled
    
    url = entry_url.get().strip()
    is_cut = var_cut.get()
    start_str = entry_start.get().strip()
    end_str = entry_end.get().strip()
    save_dir = entry_dir.get().strip()
    filename = entry_filename.get().strip()
    ext_raw = combo_ext.get().strip()

    def reset_btn():
        btn_start.config(state=tk.NORMAL, bg=ACCENT_BTN, text="🚀 미디어 다운로드 시작")
        btn_stop.config(state=tk.DISABLED, text="⏹️ 다운로드 중지")

    if not url or not save_dir or not filename or not ext_raw:
        lbl_status.config(text="상태: 입력 오류 (모든 항목을 채워주세요)", fg=ACCENT_ERR)
        root.after(0, lambda: messagebox.showerror("오류", "영상 주소, 출력 형식, 저장 폴더, 파일명을 모두 입력해주세요."))
        root.after(0, reset_btn)
        return

    ext = ext_raw.split()[0]
    save_config(save_dir, ext_raw)

    # 로그 파일은 프로그램이 실행되는 위치에 생성
    program_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    log_file_path = os.path.join(program_dir, "clip_log.txt")
    write_log("=== 영상/클립 추출 작업 시작 ===")

    m3u8_range_args = []
    cut_seek_sec = None   # 입력 탐색 시작 지점
    cut_duration_sec = None  # 자를 구간 길이

    if is_cut:
        try:
            start_sec = time_to_sec(start_str)
            end_sec = time_to_sec(end_str)
        except Exception:
            lbl_status.config(text="상태: 시간 형식 오류", fg=ACCENT_ERR)
            write_log("오류: 시간 형식이 잘못되었습니다.")
            root.after(0, reset_btn)
            return

        pad_start_sec = max(0, start_sec - 15)
        pad_end_sec = end_sec + 15
        rel_start_sec = start_sec - pad_start_sec
        rel_end_sec = end_sec - pad_start_sec
        
        cut_seek_sec = rel_start_sec
        cut_duration_sec = rel_end_sec - rel_start_sec
        m3u8_range_args = ["--custom-range", f"{sec_to_time(pad_start_sec)}-{sec_to_time(pad_end_sec)}"]
    else:
        write_log("알림: '구간 자르기'가 비활성화되어 풀영상을 다운로드합니다.")

    final_file = os.path.join(save_dir, f"{filename}{ext}")

    try:
        # 1단계 & 2단계 통합
        if "/clips/" in url:
            write_log("▶ [1/4 영상 원본 주소 추출] 시작 (HTML 스크래핑 우회)")
            m3u8_url = extract_chzzk_clip_m3u8(url)
            write_log(f"클립 주소 추출 완료: {m3u8_url[:50]}...")
            write_log("▶ [1/4 영상 원본 주소 추출] 완료\n")
            
            cmd2 = [N_M3U8_PATH, m3u8_url, "--save-dir", save_dir, "--save-name", "temp_clip", "--auto-select", "--thread-count", "16"] + m3u8_range_args
            run_cmd_with_log(cmd2, "2/4 영상 다운로드 중")
        elif "chzzk.naver.com" in url:
            # 치지직 VOD 및 기타 치지직 영상은 yt-dlp로 m3u8 주소만 추출 후 N_m3u8DL-RE 로 다운로드
            write_log("▶ [1/4 영상 원본 주소 추출] 시작 (치지직 VOD, yt-dlp 활용)")
            cmd1 = [YT_DLP_PATH, "--no-warnings", "-f", "b", "-g", url]
            out1 = run_cmd_with_log(cmd1, "1/4 영상 원본 주소 추출")
            m3u8_url = out1.strip().split('\n')[-1]
            
            cmd2 = [N_M3U8_PATH, m3u8_url, "--save-dir", save_dir, "--save-name", "temp_clip", "--auto-select", "--thread-count", "16"] + m3u8_range_args
            run_cmd_with_log(cmd2, "2/4 영상 다운로드 중")
        else:
            write_log("▶ [1~2/4 영상 다운로드] 시작 (유튜브 등 풀영상 yt-dlp 직접 다운로드)")
            # mp4 영상 + m4a(AAC) 음성 우선 선택, 없을 경우 최고 화질로 fallback
            # --merge-output-format mp4: 항상 mp4로 mux → opus 음성 비호환 문제 방지
            cmd1 = [YT_DLP_PATH, "--no-warnings", "--ffmpeg-location", FFMPEG_PATH,
                    "-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b",
                    "--merge-output-format", "mp4",
                    "-o", os.path.join(save_dir, "temp_clip.%(ext)s")]
            if is_cut:
                cmd1.extend(["--download-sections", f"*{pad_start_sec}-{pad_end_sec}"])
            cmd1.append(url)
            run_cmd_with_log(cmd1, "1~2/4 영상 다운로드 중 (yt-dlp)")
        
        temp_files = [os.path.join(save_dir, f) for f in os.listdir(save_dir) if f.startswith("temp_clip.") and not f.endswith('.m4a')]
        if not temp_files:
            raise Exception("임시 파일 다운로드에 실패했습니다.")
        temp_file = temp_files[0]
        
        # 3단계: 입력 탐색(Input Seeking)으로 키프레임에서 정확하게 잘라냄
        # -ss를 -i 앞에 두면 ffmpeg가 바로 해당 키프레임으로 이동 후 스트림 복사 → 재인코딩 없이 즉시 완료
        if cut_seek_sec is not None:
            cmd3 = [FFMPEG_PATH, "-y",
                    "-ss", sec_to_time(cut_seek_sec), "-i", temp_file,
                    "-t",  sec_to_time(cut_duration_sec),
                    "-c", "copy", "-avoid_negative_ts", "make_zero", final_file]
        else:
            cmd3 = [FFMPEG_PATH, "-y", "-i", temp_file, "-c", "copy", "-avoid_negative_ts", "make_zero", final_file]
        run_cmd_with_log(cmd3, "3/4 영상 컷팅 및 포맷 변환")
        
        # 4단계
        write_log("▶ [4/4 임시 파일 정리] 시작")
        for f in os.listdir(save_dir):
            if f.startswith("temp_clip."):
                try: 
                    os.remove(os.path.join(save_dir, f))
                    write_log(f"삭제 완료: {f}")
                except Exception as e: 
                    write_log(f"삭제 실패: {f} ({e})")
        write_log("▶ [4/4 임시 파일 정리] 완료\n")
            
        lbl_status.config(text="상태: 완료!", fg=ACCENT_OK)
        write_log("=== 모든 작업이 성공적으로 완료되었습니다! ===")
        root.after(0, lambda: messagebox.showinfo("완료", f"생성이 완료되었습니다!\n확인 경로: {final_file}"))
        
    except Exception as e:
        if is_cancelled:
            lbl_status.config(text="상태: 다운로드 중단됨", fg="#F9E2AF") # 노란색/경고색
        else:
            lbl_status.config(text="상태: 오류 발생 (로그 확인)", fg=ACCENT_ERR)
            write_log(f"\n!!! 작업 중 치명적 오류 발생 !!!\n{str(e)}")
            root.after(0, lambda msg=str(e): messagebox.showerror("오류", f"작업 중 오류가 발생했습니다.\n로그 창을 확인해주세요.\n\n요약: {msg}"))
            
        # 중단 및 에러 시, 쓸모없어진 temp_clip 임시 파일들 지워주기
        write_log("\n* 남은 임시 파일을 정리합니다...")
        for f in os.listdir(save_dir):
            if f.startswith("temp_clip."):
                try: os.remove(os.path.join(save_dir, f))
                except: pass
    finally:
        root.after(0, reset_btn)

# --- 초기 설정 로드 ---
config = load_config()

# --- 프리미엄 UI 디자인 구성 ---
root = tk.Tk()
root.title("클립 및 비디오 다운로더")
root.geometry("580x680") 
root.resizable(False, False)
root.configure(bg=BG_MAIN)

# 스타일 객체 설정
style = ttk.Style()
style.theme_use('clam')
style.configure("TCombobox", fieldbackground=ENTRY_BG, background=BG_PANEL, foreground=ENTRY_FG, borderwidth=0, arrowcolor=ACCENT_BTN)
style.map("TCombobox", fieldbackground=[('readonly', ENTRY_BG)], selectbackground=[('readonly', ACCENT_BTN)], selectforeground=[('readonly', BTN_TEXT)])

# 폰트
font_title = ("맑은 고딕", 16, "bold")
font_main = ("맑은 고딕", 10)
font_bold = ("맑은 고딕", 10, "bold")
font_log = ("Consolas", 9)

# 타이틀 바
frame_title = tk.Frame(root, bg=BG_MAIN)
frame_title.pack(fill=tk.X, pady=(20, 10))
tk.Label(frame_title, text="🎥 클립 및 비디오 다운로더", font=font_title, bg=BG_MAIN, fg=ACCENT_BTN).pack()
tk.Label(frame_title, text="유튜브 / 치지직 / 기타등등 고화질 추출기", font=("맑은 고딕", 9), bg=BG_MAIN, fg=FG_DIM).pack(pady=(2, 0))

# 메인 프레임
main_frame = tk.Frame(root, bg=BG_MAIN)
main_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=5)

def create_panel(parent, text):
    panel = tk.LabelFrame(parent, text=f" {text} ", font=font_bold, bg=BG_MAIN, fg=ACCENT_BTN, bd=1, relief=tk.SOLID, padx=15, pady=15)
    panel.pack(fill=tk.X, pady=(0, 15))
    return panel

# 1. 다운로드 소스 패널
panel_source = create_panel(main_frame, "다운로드 설정")

lbl_style = {"bg": BG_MAIN, "fg": FG_TEXT, "font": font_bold}
entry_style = {"bg": ENTRY_BG, "fg": ENTRY_FG, "insertbackground": ACCENT_BTN, "relief": tk.FLAT, "font": font_main, "highlightthickness": 1, "highlightbackground": BG_PANEL, "highlightcolor": ACCENT_BTN}

# URL
tk.Label(panel_source, text="영상 URL", **lbl_style).grid(row=0, column=0, sticky="w", pady=(0, 10))
entry_url = tk.Entry(panel_source, width=44, **entry_style)
entry_url.grid(row=0, column=1, columnspan=2, sticky="w", pady=(0, 10), ipady=5, padx=(10, 0))

# 구간 자르기
tk.Label(panel_source, text="구간 자르기", **lbl_style).grid(row=1, column=0, sticky="w", pady=(0, 5))
frame_time = tk.Frame(panel_source, bg=BG_MAIN)
frame_time.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 5), padx=(10, 0))

var_cut = tk.BooleanVar(value=False)
chk_cut = tk.Checkbutton(frame_time, text="활성화", variable=var_cut, command=toggle_cut, bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_PANEL, activebackground=BG_MAIN, activeforeground=FG_TEXT, font=font_main)
chk_cut.pack(side=tk.LEFT, padx=(0, 10))

entry_start = tk.Entry(frame_time, width=8, state=tk.DISABLED, disabledbackground=BG_PANEL, **entry_style)
entry_start.pack(side=tk.LEFT)
tk.Label(frame_time, text=" ~ ", bg=BG_MAIN, fg=FG_TEXT).pack(side=tk.LEFT, padx=3)
entry_end = tk.Entry(frame_time, width=8, state=tk.DISABLED, disabledbackground=BG_PANEL, **entry_style)
entry_end.pack(side=tk.LEFT)
tk.Label(frame_time, text=" (예: 00:00:03 ~ 00:00:12)", bg=BG_MAIN, fg=FG_DIM, font=("맑은 고딕", 9)).pack(side=tk.LEFT, padx=(8,0))

# 2. 출력 및 저장 패널
panel_save = create_panel(main_frame, "출력 및 저장")

tk.Label(panel_save, text="출력 형식", **lbl_style).grid(row=0, column=0, sticky="w", pady=(0, 10))
combo_ext = ttk.Combobox(panel_save, values=[".mp4 (기본)", ".mkv (고화질)", ".webm", ".mov", ".gif (움짤)", ".mp3 (소리만)"], width=18, state="readonly", font=font_main)
combo_ext.set(config.get("ext", ".mp4 (기본)"))
combo_ext.grid(row=0, column=1, sticky="w", pady=(0, 10), padx=(10, 0), ipady=3)

tk.Label(panel_save, text="저장 폴더", **lbl_style).grid(row=1, column=0, sticky="w", pady=(0, 10))
entry_dir = tk.Entry(panel_save, width=32, **entry_style)
entry_dir.insert(0, config.get("save_dir", ""))
entry_dir.grid(row=1, column=1, pady=(0, 10), padx=(10, 5), sticky="w", ipady=5)

btn_browse = tk.Button(panel_save, text="찾기", command=select_directory, bg=ENTRY_BG, fg=FG_TEXT, activebackground=BG_PANEL, activeforeground=FG_TEXT, relief=tk.FLAT, font=font_main, bd=0, highlightbackground=BG_PANEL, highlightthickness=1)
btn_browse.grid(row=1, column=2, sticky="w", pady=(0, 10), ipady=3, ipadx=8)

tk.Label(panel_save, text="파일 이름", **lbl_style).grid(row=2, column=0, sticky="w")
entry_filename = tk.Entry(panel_save, width=44, **entry_style)
entry_filename.grid(row=2, column=1, columnspan=2, sticky="w", ipady=5, padx=(10, 0))

# 3. Action 버튼 메뉴
frame_action = tk.Frame(main_frame, bg=BG_MAIN)
frame_action.pack(fill=tk.X, pady=(10, 10))

lbl_status = tk.Label(frame_action, text="상태: 대기 중", fg=FG_DIM, bg=BG_MAIN, font=font_bold)
lbl_status.pack(pady=(0, 10))

# 마우스 호버 이펙트 (시작 버튼)
def on_enter_btn(e):
    if btn_start['state'] == tk.NORMAL:
        btn_start['bg'] = "#DDB6F6" # hover color
def on_leave_btn(e):
    if btn_start['state'] == tk.NORMAL:
        btn_start['bg'] = ACCENT_BTN

# 버튼 컨테이너 (시작/중지 나란히 배치)
frame_buttons = tk.Frame(frame_action, bg=BG_MAIN)
frame_buttons.pack(fill=tk.X)

btn_start = tk.Button(frame_buttons, text="🚀 미디어 다운로드 시작", command=process_clip, bg=ACCENT_BTN, fg=BTN_TEXT, activebackground="#DDB6F6", activeforeground=BTN_TEXT, relief=tk.FLAT, font=("맑은 고딕", 12, "bold"), pady=12, cursor="hand2")
btn_start.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
btn_start.bind("<Enter>", on_enter_btn)
btn_start.bind("<Leave>", on_leave_btn)

# 마우스 호버 이펙트 (중지 버튼)
def on_enter_stop(e):
    if btn_stop['state'] == tk.NORMAL:
        btn_stop['bg'] = "#F5A1B8" # 좀 더 밝은 레드
def on_leave_stop(e):
    if btn_stop['state'] == tk.NORMAL:
        btn_stop['bg'] = ENTRY_BG

btn_stop = tk.Button(frame_buttons, text="⏹️ 다운로드 중지", command=cancel_process, bg=ENTRY_BG, fg=FG_TEXT, activebackground="#F5A1B8", activeforeground="#11111B", relief=tk.FLAT, font=("맑은 고딕", 12, "bold"), pady=12, state=tk.DISABLED, cursor="hand2")
btn_stop.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5, 0))
btn_stop.bind("<Enter>", on_enter_stop)
btn_stop.bind("<Leave>", on_leave_stop)

# 4. 실시간 로그 창
frame_log = tk.Frame(root, bg=BG_MAIN)
frame_log.pack(fill=tk.BOTH, expand=True, padx=30, pady=(0, 25))

tk.Label(frame_log, text="📜 작업 로그", font=("맑은 고딕", 9, "bold"), bg=BG_MAIN, fg=FG_DIM).pack(anchor="w", pady=(0, 5))

log_container = tk.Frame(frame_log, bg=BG_PANEL, bd=1, relief=tk.SOLID)
log_container.pack(fill=tk.BOTH, expand=True)

scrollbar = tk.Scrollbar(log_container, bg=BG_PANEL, troughcolor=BG_MAIN, activebackground=ACCENT_BTN, width=12)
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

txt_log = tk.Text(log_container, height=8, bg=ENTRY_BG, fg=ACCENT_OK, font=font_log, yscrollcommand=scrollbar.set, state=tk.DISABLED, relief=tk.FLAT, padx=10, pady=10, bd=0)
txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
scrollbar.config(command=txt_log.yview)

if __name__ == "__main__":
    root.mainloop()