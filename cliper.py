import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import subprocess
import threading
import os
import sys
import json
import re
import urllib.request
import shutil
import tempfile
import time
from datetime import timedelta

# --- 프로그램 경로 및 설정 파일 경로 ---
def get_program_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

PROGRAM_DIR = get_program_dir()
CONFIG_FILE = os.path.join(PROGRAM_DIR, "cliper_config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {}

def save_config(
    save_dir,
    ext,
    accel="CPU (기본)",
    quality="최고 화질 (제한 없음)",
    video_codec="H.264 (AVC, 호환성 우선)",
):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "save_dir": save_dir,
                    "ext": ext,
                    "accel": accel,
                    "quality": quality,
                    "video_codec": video_codec,
                },
                f,
                ensure_ascii=False,
                indent=4,
            )
    except: pass

# --- 배 속에 있는 프로그램 경로를 찾는 함수 ---
def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = PROGRAM_DIR
    return os.path.join(base_path, relative_path)

# 새 배포 구조는 cliper.exe와 같은 폴더에 bin\을 둔다. yt-dlp는 이 외부 파일을
# 직접 실행·업데이트하므로 PyInstaller 내부 리소스가 갱신되는 문제가 없다.
EXTERNAL_BIN_DIR = os.path.join(PROGRAM_DIR, "bin")
EXTERNAL_YT_DLP_PATH = os.path.join(EXTERNAL_BIN_DIR, "yt-dlp.exe")
BUNDLED_YT_DLP_PATH = get_resource_path(os.path.join("bin", "yt-dlp.exe"))

# 이전 one-file 배포본 사용자를 위한 호환 경로다. 새 onedir 배포본에서는 사용되지 않는다.
LEGACY_YT_DLP_DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", PROGRAM_DIR), "ClipVideoDownloader", "bin"
)
LEGACY_MANAGED_YT_DLP_PATH = os.path.join(LEGACY_YT_DLP_DATA_DIR, "yt-dlp.exe")
YT_DLP_UPDATE_INTERVAL_SECONDS = 24 * 60 * 60

# yt-dlp는 사이트 변경에 빠르게 대응하는 nightly 채널을 일반 사용자에게 권장합니다.
# 문제가 생기면 최신 nightly로 자동 교체되어 추출 실패를 줄일 수 있습니다.
YT_DLP_UPDATE_CHANNEL = "nightly"

# 외부 bin\을 우선 사용하고, 이전 one-file 실행 파일만 내부 리소스를 fallback으로 사용한다.
def get_tool_path(filename):
    external_path = os.path.join(EXTERNAL_BIN_DIR, filename)
    if os.path.exists(external_path):
        return external_path
    return get_resource_path(os.path.join("bin", filename))


YT_DLP_PATH = get_tool_path("yt-dlp.exe")
N_M3U8_PATH = get_tool_path("N_m3u8DL-RE.exe")
FFMPEG_PATH = get_tool_path("ffmpeg.exe")


def get_yt_dlp_update_state_file(yt_dlp_path):
    return os.path.join(os.path.dirname(yt_dlp_path), "yt-dlp-update.json")


def load_yt_dlp_update_state(state_file):
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return {}


def save_yt_dlp_update_state(state_file, state):
    """업데이트 상태 파일을 원자적으로 기록합니다."""
    state_dir = os.path.dirname(state_file)
    try:
        os.makedirs(state_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix="yt-dlp-update-", suffix=".json", dir=state_dir
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, state_file)
    except OSError:
        try:
            if "temp_path" in locals() and os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def prepare_yt_dlp():
    """외부 bin의 yt-dlp를 우선 사용하고, 이전 one-file 배포본만 호환 처리한다."""
    global YT_DLP_PATH

    if os.path.exists(EXTERNAL_YT_DLP_PATH):
        YT_DLP_PATH = EXTERNAL_YT_DLP_PATH
        return YT_DLP_PATH

    if os.path.exists(LEGACY_MANAGED_YT_DLP_PATH):
        YT_DLP_PATH = LEGACY_MANAGED_YT_DLP_PATH
        return YT_DLP_PATH

    if not os.path.exists(BUNDLED_YT_DLP_PATH):
        raise FileNotFoundError(f"yt-dlp 실행 파일을 찾을 수 없습니다: {BUNDLED_YT_DLP_PATH}")

    try:
        os.makedirs(LEGACY_YT_DLP_DATA_DIR, exist_ok=True)
        shutil.copy2(BUNDLED_YT_DLP_PATH, LEGACY_MANAGED_YT_DLP_PATH)
        YT_DLP_PATH = LEGACY_MANAGED_YT_DLP_PATH
        write_log(f"yt-dlp 관리 파일을 준비했습니다: {LEGACY_MANAGED_YT_DLP_PATH}")
    except OSError as e:
        # 읽기 전용 환경에서도 기존 번들 파일로 다운로드 자체는 가능하게 둡니다.
        YT_DLP_PATH = BUNDLED_YT_DLP_PATH
        write_log(f"경고: yt-dlp 자동 업데이트용 파일을 만들 수 없습니다. 번들 버전을 사용합니다. ({e})")

    return YT_DLP_PATH


def update_yt_dlp_if_due():
    """하루에 한 번 최신 nightly yt-dlp를 확인한다. 실패해도 현재 버전으로 계속 진행한다."""
    yt_dlp_path = prepare_yt_dlp()
    state_file = get_yt_dlp_update_state_file(yt_dlp_path)
    state = load_yt_dlp_update_state(state_file)
    now = time.time()
    last_attempt = state.get("last_attempt", 0)
    if isinstance(last_attempt, (int, float)) and now - last_attempt < YT_DLP_UPDATE_INTERVAL_SECONDS:
        write_log("yt-dlp 업데이트 확인: 최근 24시간 내 확인 완료 (현재 버전 사용)")
        return

    write_log(f"yt-dlp 업데이트 확인 중... (공식 {YT_DLP_UPDATE_CHANNEL} 채널)")
    creation_flags = 0x08000000  # CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [yt_dlp_path, "--update-to", YT_DLP_UPDATE_CHANNEL],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
            timeout=120,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            raise RuntimeError(output or f"종료 코드 {result.returncode}")

        save_yt_dlp_update_state(
            state_file, {"last_attempt": now, "channel": YT_DLP_UPDATE_CHANNEL}
        )
        if output:
            for line in output.splitlines():
                write_log(f"[yt-dlp] {line}")
        write_log("yt-dlp 업데이트 확인 완료")
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as e:
        # 업데이트 서버/네트워크 문제는 다운로드를 막지 않는다. 실패 시 다음 다운로드 때 재시도한다.
        write_log(f"경고: yt-dlp 자동 업데이트에 실패했습니다. 현재 버전으로 계속합니다. ({e})")

def ensure_required_tools():
    missing = []
    for name, path in [
        ("yt-dlp", YT_DLP_PATH),
        ("N_m3u8DL-RE", N_M3U8_PATH),
        ("ffmpeg", FFMPEG_PATH),
    ]:
        if not os.path.exists(path):
            missing.append(f"{name}: {path}")
    if missing:
        raise FileNotFoundError("필수 실행 파일을 찾을 수 없습니다.\n" + "\n".join(missing))
def build_ffmpeg_convert_cmd(temp_file, final_file, ext, cut_seek_sec, cut_duration_sec, use_gpu, video_codec):
    ext_lower = ext.lower()
    cmd = [FFMPEG_PATH, "-y"]

    if cut_seek_sec is not None:
        cmd.extend(["-ss", sec_to_time(cut_seek_sec)])

    if use_gpu and ext_lower in [".mp4", ".mkv", ".mov"]:
        cmd.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])

    cmd.extend(["-i", temp_file])

    if cut_duration_sec is not None:
        cmd.extend(["-t", sec_to_time(cut_duration_sec)])

    if ext_lower == ".mp3":
        cmd.extend(["-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", final_file])
    elif ext_lower == ".gif":
        cmd.extend(["-vf", "fps=15,scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos", "-loop", "0", final_file])
    elif ext_lower == ".webm":
        cmd.extend(["-map", "0:v:0", "-map", "0:a?", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32", "-c:a", "libopus", "-b:a", "128k", final_file])
    elif use_gpu and video_codec.startswith("AV1"):
        # 일반적인 NVIDIA GPU는 AV1 인코딩을 지원하지 않을 수 있다. 원본 AV1 스트림을
        # 그대로 복사해 사용자가 고른 코덱을 보존한다.
        cmd.extend(["-map", "0:v:0", "-map", "0:a?", "-c", "copy"])
        if ext_lower in [".mp4", ".mov"]:
            cmd.extend(["-movflags", "+faststart"])
        cmd.append(final_file)
    elif use_gpu:
        cmd.extend(["-map", "0:v:0", "-map", "0:a?", "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23", "-c:a", "copy"])
        if ext_lower in [".mp4", ".mov"]:
            cmd.extend(["-movflags", "+faststart"])
        cmd.append(final_file)
    else:
        cmd.extend(["-c", "copy", "-avoid_negative_ts", "make_zero", final_file])

    return cmd

# --- 시간 변환 함수 ---
def time_to_sec(t_str):
    parts = list(map(int, t_str.strip().split(':')))
    if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
    elif len(parts) == 2: return parts[0]*60 + parts[1]
    else: return parts[0]

def sec_to_time(sec):
    return str(timedelta(seconds=int(sec)))

# --- 다운로드 화질 설정 ---
QUALITY_OPTIONS = [
    "최고 화질 (제한 없음)",
    "2160p (4K) 이하",
    "1440p (2K) 이하",
    "1080p (FHD) 이하",
    "720p (HD) 이하",
    "480p 이하",
    "360p 이하",
]
QUALITY_MAX_HEIGHTS = {
    "최고 화질 (제한 없음)": None,
    "2160p (4K) 이하": 2160,
    "1440p (2K) 이하": 1440,
    "1080p (FHD) 이하": 1080,
    "720p (HD) 이하": 720,
    "480p 이하": 480,
    "360p 이하": 360,
}
VIDEO_CODEC_OPTIONS = [
    "H.264 (AVC, 호환성 우선)",
    "AV1 (AV01, 고효율)",
]
VIDEO_CODEC_FORMAT_FILTERS = {
    "H.264 (AVC, 호환성 우선)": "[vcodec~=avc]",
    "AV1 (AV01, 고효율)": "[vcodec~=av01]",
}

def get_quality_max_height(quality):
    return QUALITY_MAX_HEIGHTS.get(quality)


def get_yt_dlp_format_selector(max_height, video_codec):
    """선택한 코덱·최대 해상도에 맞는 비디오와 M4A 오디오를 고른다."""
    codec_filter = VIDEO_CODEC_FORMAT_FILTERS.get(video_codec)
    if codec_filter is None:
        raise ValueError(f"지원하지 않는 비디오 코덱 옵션입니다: {video_codec}")
    height_filter = "" if max_height is None else f"[height<={max_height}]"
    return (
        f"bv*{height_filter}{codec_filter}[ext=mp4]+ba[ext=m4a]"
        f"/bv*{height_filter}{codec_filter}+ba/b{height_filter}{codec_filter}"
    )


def get_representation_number(representation, *keys):
    """CHZZK 응답의 숫자 메타데이터 형식 차이를 안전하게 처리한다."""
    for key in keys:
        value = representation.get(key)
        if value is None:
            continue
        match = re.search(r"\d+", str(value))
        if match:
            return int(match.group())
    return None


def choose_chzzk_representation(representations, max_height):
    """가능하면 최대 해상도 이하에서 가장 높은 CHZZK 비디오 소스를 고른다."""
    candidates = []
    for representation in representations:
        m3u8_url = representation.get("@nvod:m3u")
        if not m3u8_url:
            continue
        height = get_representation_number(representation, "@height", "height", "Height")
        bitrate = get_representation_number(representation, "@bandwidth", "bandwidth", "bitrate") or 0
        candidates.append((m3u8_url, height, bitrate))

    if not candidates:
        return None

    known_height_candidates = [item for item in candidates if item[1] is not None]
    if max_height is not None and known_height_candidates:
        capped_candidates = [item for item in known_height_candidates if item[1] <= max_height]
        # 선택한 화질보다 낮은 소스가 없으면 가능한 가장 낮은 화질을 선택한다.
        if capped_candidates:
            candidates = capped_candidates
        else:
            return min(known_height_candidates, key=lambda item: (item[1], item[2]))[0]

    return max(candidates, key=lambda item: ((item[1] or 0), item[2]))[0]

# --- 치지직 클립 전용 HTML 스크래핑 우회 함수 ---
def extract_chzzk_clip_m3u8(clip_url, max_height=None):
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
            representations = []
            for mpd in playback.get('MPD', []):
                for period in mpd.get('Period', []):
                    for adaptation in period.get('AdaptationSet', []):
                        representations.extend(adaptation.get('Representation', []))
            m3u8_url = choose_chzzk_representation(representations, max_height)
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
    accel_raw = combo_accel.get().strip()
    quality_raw = combo_quality.get().strip()
    video_codec_raw = combo_video_codec.get().strip()

    def reset_btn():
        btn_start.config(state=tk.NORMAL, bg=ACCENT_BTN, text="🚀 미디어 다운로드 시작")
        btn_stop.config(state=tk.DISABLED, text="⏹️ 다운로드 중지")

    if not url or not save_dir or not filename or not ext_raw or not accel_raw or not quality_raw or not video_codec_raw:
        lbl_status.config(text="상태: 입력 오류 (모든 항목을 채워주세요)", fg=ACCENT_ERR)
        root.after(0, lambda: messagebox.showerror("오류", "영상 주소, 출력 형식, 저장 폴더, 파일명을 모두 입력해주세요."))
        root.after(0, reset_btn)
        return

    ext = ext_raw.split()[0]
    save_config(save_dir, ext_raw, accel_raw, quality_raw, video_codec_raw)

    # 로그 파일은 프로그램이 실행되는 위치에 생성
    log_file_path = os.path.join(PROGRAM_DIR, "clip_log.txt")
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
    temp_basename = f"cliper_temp_{os.getpid()}"
    is_mp3_output = ext.lower() == ".mp3"
    use_gpu = accel_raw.startswith("GPU")
    max_height = get_quality_max_height(quality_raw)

    try:
        lbl_status.config(text="상태: yt-dlp 업데이트 확인 중...", fg="#89B4FA")
        update_yt_dlp_if_due()
        ensure_required_tools()
        lbl_status.config(text="상태: 미디어 추출 중... (로그 확인)", fg="#89B4FA")

        if use_gpu:
            write_log("알림: GPU(CUDA) 모드가 선택되었습니다. 다운로드는 기존 방식으로 진행하고 ffmpeg 변환 단계에서 CUDA/NVENC를 사용합니다.")
            if is_mp3_output:
                write_log("알림: MP3는 오디오 변환이라 GPU 가속 대상이 아니므로 CPU로 인코딩합니다.")
            elif ext.lower() in [".gif", ".webm"]:
                write_log("알림: 선택한 출력 형식은 CUDA/NVENC 경로 대신 호환 인코딩으로 처리합니다.")
            elif video_codec_raw.startswith("AV1"):
                write_log("알림: AV1 선택 시 GPU 재인코딩 대신 원본 AV1 스트림을 유지합니다.")
        else:
            write_log("알림: CPU 모드로 처리합니다.")

        if max_height is None:
            write_log("알림: 화질 제한 없이 최고 화질로 다운로드합니다.")
        else:
            write_log(f"알림: 다운로드 화질을 {max_height}p 이하로 제한합니다.")
        write_log(f"알림: 비디오 코덱을 '{video_codec_raw}'로 선택했습니다.")
        if ext.lower() in [".webm", ".gif", ".mp3"]:
            write_log("알림: 선택한 출력 형식은 최종 변환 과정에서 비디오 코덱이 변경되거나 제거될 수 있습니다.")

        # 1단계 & 2단계 통합
        if "/clips/" in url:
            write_log("알림: 치지직 클립은 제공되는 HLS 소스를 사용하므로 코덱 선택을 보장할 수 없습니다.")
            write_log("▶ [1/4 영상 원본 주소 추출] 시작 (HTML 스크래핑 우회)")
            m3u8_url = extract_chzzk_clip_m3u8(url, max_height)
            write_log(f"클립 주소 추출 완료: {m3u8_url[:50]}...")
            write_log("▶ [1/4 영상 원본 주소 추출] 완료\n")
            
            cmd2 = [N_M3U8_PATH, m3u8_url, "--save-dir", save_dir, "--save-name", temp_basename, "--auto-select", "--thread-count", "16"] + m3u8_range_args
            run_cmd_with_log(cmd2, "2/4 영상 다운로드 중")
        elif "chzzk.naver.com" in url:
            # 치지직 VOD 및 기타 치지직 영상은 yt-dlp로 m3u8 주소만 추출 후 N_m3u8DL-RE 로 다운로드
            write_log("알림: 치지직 VOD는 제공되는 HLS 소스를 사용하므로 코덱 선택을 보장할 수 없습니다.")
            write_log("▶ [1/4 영상 원본 주소 추출] 시작 (치지직 VOD, yt-dlp 활용)")
            chzzk_format = "b" if max_height is None else f"b[height<={max_height}]/b"
            cmd1 = [YT_DLP_PATH, "--no-warnings", "-f", chzzk_format, "-g", url]
            out1 = run_cmd_with_log(cmd1, "1/4 영상 원본 주소 추출")
            m3u8_url = out1.strip().split('\n')[-1]
            
            cmd2 = [N_M3U8_PATH, m3u8_url, "--save-dir", save_dir, "--save-name", temp_basename, "--auto-select", "--thread-count", "16"] + m3u8_range_args
            run_cmd_with_log(cmd2, "2/4 영상 다운로드 중")
        else:
            write_log("▶ [1~2/4 영상 다운로드] 시작 (유튜브 등 풀영상 yt-dlp 직접 다운로드)")
            # mp4 영상 + m4a(AAC) 음성 우선 선택, 없을 경우 최고 화질로 fallback
            # --merge-output-format mp4: 항상 mp4로 mux → opus 음성 비호환 문제 방지
            cmd1 = [YT_DLP_PATH, "--no-warnings", "--ffmpeg-location", FFMPEG_PATH,
                    "-f", get_yt_dlp_format_selector(max_height, video_codec_raw),
                    "--merge-output-format", "mp4",
                    "-o", os.path.join(save_dir, f"{temp_basename}.%(ext)s")]
            if is_cut:
                cmd1.extend(["--download-sections", f"*{pad_start_sec}-{pad_end_sec}"])
            cmd1.append(url)
            run_cmd_with_log(cmd1, "1~2/4 영상 다운로드 중 (yt-dlp)")
        
        temp_files = sorted(
            os.path.join(save_dir, f)
            for f in os.listdir(save_dir)
            if f.startswith(f"{temp_basename}.") and not f.endswith('.m4a')
        )
        if not temp_files:
            raise Exception("임시 파일 다운로드에 실패했습니다.")
        temp_file = temp_files[0]
        
        cmd3 = build_ffmpeg_convert_cmd(
            temp_file, final_file, ext, cut_seek_sec, cut_duration_sec, use_gpu, video_codec_raw
        )
        run_cmd_with_log(cmd3, "3/4 영상 컷팅 및 포맷 변환")
        
        # 4단계
        write_log("▶ [4/4 임시 파일 정리] 시작")
        for f in os.listdir(save_dir):
            if f.startswith(f"{temp_basename}."):
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
            
        # 중단 및 에러 시, 쓸모없어진 임시 파일들 지워주기
        write_log("\n* 남은 임시 파일을 정리합니다...")
        for f in os.listdir(save_dir):
            if f.startswith(f"{temp_basename}."):
                try: os.remove(os.path.join(save_dir, f))
                except: pass
    finally:
        root.after(0, reset_btn)

# --- 초기 설정 로드 ---
config = load_config()

# --- 프리미엄 UI 디자인 구성 ---
root = tk.Tk()
root.title("클립 및 비디오 다운로더")
root.geometry("580x800")
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

# 다운로드 화질
tk.Label(panel_source, text="다운로드 화질", **lbl_style).grid(row=2, column=0, sticky="w", pady=(5, 0))
combo_quality = ttk.Combobox(panel_source, values=QUALITY_OPTIONS, width=24, state="readonly", font=font_main)
combo_quality.set(config.get("quality", "최고 화질 (제한 없음)"))
combo_quality.grid(row=2, column=1, columnspan=2, sticky="w", pady=(5, 0), padx=(10, 0), ipady=3)

# 비디오 압축 형식
tk.Label(panel_source, text="비디오 코덱", **lbl_style).grid(row=3, column=0, sticky="w", pady=(8, 0))
combo_video_codec = ttk.Combobox(panel_source, values=VIDEO_CODEC_OPTIONS, width=24, state="readonly", font=font_main)
combo_video_codec.set(config.get("video_codec", "H.264 (AVC, 호환성 우선)"))
combo_video_codec.grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 0), padx=(10, 0), ipady=3)

# 2. 출력 및 저장 패널
panel_save = create_panel(main_frame, "출력 및 저장")

tk.Label(panel_save, text="출력 형식", **lbl_style).grid(row=0, column=0, sticky="w", pady=(0, 10))
combo_ext = ttk.Combobox(panel_save, values=[".mp4 (기본)", ".mkv (고화질)", ".webm", ".mov", ".gif (움짤)", ".mp3 (소리만)"], width=18, state="readonly", font=font_main)
combo_ext.set(config.get("ext", ".mp4 (기본)"))
combo_ext.grid(row=0, column=1, sticky="w", pady=(0, 10), padx=(10, 0), ipady=3)

tk.Label(panel_save, text="처리 장치", **lbl_style).grid(row=1, column=0, sticky="w", pady=(0, 10))
combo_accel = ttk.Combobox(panel_save, values=["CPU (기본)", "GPU CUDA (NVIDIA)"], width=18, state="readonly", font=font_main)
combo_accel.set(config.get("accel", "CPU (기본)"))
combo_accel.grid(row=1, column=1, sticky="w", pady=(0, 10), padx=(10, 0), ipady=3)

tk.Label(panel_save, text="저장 폴더", **lbl_style).grid(row=2, column=0, sticky="w", pady=(0, 10))
entry_dir = tk.Entry(panel_save, width=32, **entry_style)
entry_dir.insert(0, config.get("save_dir", ""))
entry_dir.grid(row=2, column=1, pady=(0, 10), padx=(10, 5), sticky="w", ipady=5)

btn_browse = tk.Button(panel_save, text="찾기", command=select_directory, bg=ENTRY_BG, fg=FG_TEXT, activebackground=BG_PANEL, activeforeground=FG_TEXT, relief=tk.FLAT, font=font_main, bd=0, highlightbackground=BG_PANEL, highlightthickness=1)
btn_browse.grid(row=2, column=2, sticky="w", pady=(0, 10), ipady=3, ipadx=8)

tk.Label(panel_save, text="파일 이름", **lbl_style).grid(row=3, column=0, sticky="w")
entry_filename = tk.Entry(panel_save, width=44, **entry_style)
entry_filename.grid(row=3, column=1, columnspan=2, sticky="w", ipady=5, padx=(10, 0))

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
