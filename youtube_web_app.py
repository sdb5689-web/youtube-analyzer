# ================================================================
# 🎬 YouTube 분석 웹앱 v1.0 (Streamlit)
#
# 설치: pip install streamlit requests youtube-transcript-api
#       openpyxl gspread google-auth
# 실행: streamlit run youtube_web_app.py
# ================================================================
import streamlit as st
import requests, json, re, time, os, io
from collections import Counter, defaultdict
from datetime import datetime

# ─── 선택적 임포트 ────────────────────────────────────────────
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    HAS_XLSX = True
except ImportError:
    HAS_XLSX = False

try:
    import gspread
    from google.oauth2.service_account import Credentials
    HAS_GSHEET = True
except ImportError:
    HAS_GSHEET = False

# ── Whisper (OpenAI STT) ──────────────────────
try:
    import openai as _openai_lib
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

# ── yt-dlp (오디오 다운로드) ──────────────────
try:
    import yt_dlp as _yt_dlp_lib
    HAS_YTDLP = True
except ImportError:
    HAS_YTDLP = False

# ================================================================
# 페이지 설정
# ================================================================
st.set_page_config(
    page_title="🎬 YouTube 분석 도구",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── 커스텀 CSS ───────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1B3A6B 0%, #0D2347 100%);
        padding: 20px 30px;
        border-radius: 12px;
        color: white;
        margin-bottom: 20px;
    }
    .main-header h1 { margin: 0; font-size: 2rem; }
    .main-header p  { margin: 5px 0 0 0; opacity: 0.9; }

    .metric-card {
        background: #f8f9fa;
        border: 1px solid #dee2e6;
        border-radius: 10px;
        padding: 15px;
        text-align: center;
    }
    .metric-card .value { font-size: 1.8rem; font-weight: bold; color: #1B3A6B; }
    .metric-card .label { font-size: 0.85rem; color: #666; margin-top: 4px; }

    .video-card {
        background: white;
        border: 1px solid #e0e0e0;
        border-radius: 12px;
        padding: 18px;
        margin-bottom: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.07);
        transition: box-shadow 0.2s;
    }
    .video-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.13); }
    .video-title { font-size: 1.05rem; font-weight: bold; color: #1a1a1a; }
    .video-meta  { color: #666; font-size: 0.87rem; margin-top: 6px; }
    .badge-hot   { background:#1B3A6B; color:white; padding:2px 8px; border-radius:4px; font-size:0.78rem; }
    .badge-good  { background:#FF8C00; color:white; padding:2px 8px; border-radius:4px; font-size:0.78rem; }
    .badge-new   { background:#4CAF50; color:white; padding:2px 8px; border-radius:4px; font-size:0.78rem; }
    .badge-norm  { background:#9E9E9E; color:white; padding:2px 8px; border-radius:4px; font-size:0.78rem; }

    .stat-row { display:flex; gap:16px; flex-wrap:wrap; margin-top:8px; }
    .stat-item{ background:#f0f2f6; border-radius:6px; padding:4px 12px; font-size:0.85rem; }

    .keyword-tag {
        display:inline-block; background:#e3f2fd; color:#1565c0;
        border-radius:20px; padding:3px 12px; margin:3px;
        font-size:0.82rem; border:1px solid #bbdefb;
    }
    .section-title {
        font-size:1.1rem; font-weight:bold;
        border-left:4px solid #1B3A6B;
        padding-left:10px; margin:16px 0 10px 0;
    }
    .transcript-box {
        background:#fafafa; border:1px solid #e0e0e0;
        border-radius:8px; padding:12px; font-size:0.82rem;
        max-height:200px; overflow-y:auto; line-height:1.6;
    }
    div[data-testid="stExpander"] { border-radius:10px; border:1px solid #e0e0e0; }
    .stTabs [data-baseweb="tab"] { font-size:0.95rem; }
    .stButton button { border-radius:8px; }
</style>
""", unsafe_allow_html=True)

# ================================================================
# 유틸 함수
# ================================================================
STOPWORDS = set([
    "이","그","저","것","수","을","를","이","가","은","는","에","의","도","로","으로",
    "와","과","한","하는","있는","없는","하고","하면","그리고","하지만","때문에",
    "통해","위해","대한","같은","더","또한","이런","저런","그런","어떤","모든",
    "the","and","is","in","to","of","a","an","that","it","for","on","are","with",
    "이것","저것","그것","이렇게","그렇게","저렇게","때","곳","중","후","전","내","외",
    "씩","만","까지","부터","에서","들","할","될","있","없","않","못","안","다",
])

def fmt(n):
    try: n = int(n)
    except: return "0"
    if n >= 1_000_000_000: return f"{n/1e9:.1f}B"
    if n >= 1_000_000:     return f"{n/1e6:.1f}M"
    if n >= 1_000:         return f"{n/1e3:.0f}K"
    return str(n)

def parse_duration(dur_raw):
    m = re.findall(r'(\d+)([HMS])', dur_raw)
    d = {k: int(v) for v, k in m}
    h, mi, s = d.get('H',0), d.get('M',0), d.get('S',0)
    return f"{h}:{mi:02d}:{s:02d}" if h > 0 else f"{mi}:{s:02d}"

def extract_keywords(text, top_n=15):
    if not text or "자막 없음" in text or len(text) < 50:
        return []
    cleaned = re.sub(r'[^\w\s가-힣]', ' ', text.lower())
    words = cleaned.split()
    filtered = [
        w for w in words
        if len(w) >= 2 and not w.isdigit() and w not in STOPWORDS
        and not re.match(r'^\d+$', w)
    ]
    counter = Counter(filtered)
    return [word for word, _ in counter.most_common(top_n)]

def is_valid_transcript(tr: str) -> bool:
    """실제 사용 가능한 대본인지 판단 (자막 없음/오류 문자열 제외)"""
    if not tr or len(tr) < 20:
        return False
    BAD = ("자막 없음", "youtube-transcript-api 미설치",
           "[Whisper 오류]", "미설치", "다운로드 실패")
    return not any(tr.startswith(b) or b in tr[:40] for b in BAD)

def summarize_text(text, max_chars=300):
    if not text or "자막 없음" in text or len(text) < 30:
        return "(요약 없음)"
    cleaned = re.sub(r'\s+', ' ', text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    cut = cleaned[:max_chars]
    for end_char in ['다.', '요.', '죠.', '네.', '.', '!', '?']:
        idx = cut.rfind(end_char)
        if idx > max_chars // 2:
            return cut[:idx+1] + "..."
    return cut + "..."

def build_channel_stats(all_videos):
    ch = defaultdict(lambda: {
        "videos": [], "totalView": 0, "totalLike": 0,
        "totalComment": 0, "subscriber": "비공개"
    })
    for v in all_videos:
        cn = v["channelTitle"]
        ch[cn]["videos"].append(v)
        ch[cn]["totalView"]    += v["viewCount"]
        ch[cn]["totalLike"]    += v["likeCount"]
        ch[cn]["totalComment"] += v["commentCount"]
        ch[cn]["subscriber"]    = v.get("subscriberLabel", "비공개")
    stats = []
    for name, data in ch.items():
        cnt = len(data["videos"])
        stats.append({
            "channel":      name,
            "videoCount":   cnt,
            "subscriber":   data["subscriber"],
            "totalView":    data["totalView"],
            "avgView":      data["totalView"] // cnt if cnt else 0,
            "totalLike":    data["totalLike"],
            "avgLike":      data["totalLike"] // cnt if cnt else 0,
            "totalComment": data["totalComment"],
            "avgComment":   data["totalComment"] // cnt if cnt else 0,
            "videos":       data["videos"],
        })
    stats.sort(key=lambda x: x["totalView"], reverse=True)
    return stats

def get_badge(rank, view_count):
    if rank == 1: return "🥇"
    if rank == 2: return "🥈"
    if rank == 3: return "🥉"
    if view_count >= 1_000_000: return "🔥"
    if view_count >= 100_000:   return "⭐"
    return "▶"

# ================================================================
# YouTube API 함수
# ================================================================
def search_youtube(api_key, keyword, max_r, order_api):
    video_ids = []
    token = None
    while len(video_ids) < max_r:
        params = {
            "key": api_key, "q": keyword,
            "part": "id", "type": "video",
            "maxResults": min(50, max_r - len(video_ids)),
            "order": order_api,
            "regionCode": "KR", "relevanceLanguage": "ko"
        }
        if token: params["pageToken"] = token
        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params=params, timeout=10
            )
            d = r.json()
        except Exception as e:
            return None, f"인터넷 연결 오류: {e}"
        if "error" in d:
            return None, f"API 오류 [{d['error']['code']}]: {d['error']['message']}"
        for item in d.get("items", []):
            video_ids.append(item["id"]["videoId"])
        token = d.get("nextPageToken")
        if not token: break
    return video_ids, None

def fetch_video_details(api_key, video_ids):
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = ",".join(video_ids[i:i+50])
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "key": api_key, "id": batch,
                "part": "snippet,statistics,contentDetails"
            },
            timeout=10
        )
        for item in r.json().get("items", []):
            sn = item.get("snippet", {})
            st = item.get("statistics", {})
            cd = item.get("contentDetails", {})
            vc = int(st.get("viewCount",   "0") if st.get("viewCount",   "0").isdigit() else 0)
            lc = int(st.get("likeCount",   "0") if st.get("likeCount",   "0").isdigit() else 0)
            cc = int(st.get("commentCount","0") if st.get("commentCount","0").isdigit() else 0)
            videos.append({
                "videoId":         item["id"],
                "channelId":       sn.get("channelId", ""),
                "title":           sn.get("title", ""),
                "channelTitle":    sn.get("channelTitle", ""),
                "description":     sn.get("description", ""),
                "publishedAt":     sn.get("publishedAt", "")[:10],
                "tags":            sn.get("tags", []),
                "thumbnail":       (sn.get("thumbnails",{}).get("maxres") or
                                    sn.get("thumbnails",{}).get("high") or {}).get("url",""),
                "duration":        parse_duration(cd.get("duration","PT0S")),
                "viewCount":       vc, "viewLabel":    fmt(vc)+"회",
                "likeCount":       lc, "likeLabel":    fmt(lc),
                "commentCount":    cc, "commentLabel": fmt(cc),
                "url":             f"https://www.youtube.com/watch?v={item['id']}",
                "subscriberLabel": "비공개",
                "transcript":      "",
                "keywords":        [],
                "summary":         "",
            })
    return videos

def fetch_subscribers(api_key, videos):
    cache = {}
    ch_ids = list(set(v["channelId"] for v in videos if v["channelId"]))
    for i in range(0, len(ch_ids), 50):
        batch = ",".join(ch_ids[i:i+50])
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"key": api_key, "id": batch, "part": "statistics"},
            timeout=10
        )
        for item in r.json().get("items", []):
            sub = item.get("statistics", {}).get("subscriberCount", "0")
            cache[item["id"]] = fmt(int(sub)) + "명" if sub.isdigit() else "비공개"
    for v in videos:
        v["subscriberLabel"] = cache.get(v["channelId"], "비공개")
    return videos

def get_transcript(video_id):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        for lang in ['ko', 'en']:
            try:
                segs = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
                return " ".join(
                    s.get('text','') if isinstance(s, dict) else s.text
                    for s in segs
                )
            except: pass
        try:
            tlist = YouTubeTranscriptApi.list_transcripts(video_id)
            t = None
            for lang in ['ko','en']:
                try: t = tlist.find_transcript([lang]); break
                except: pass
            if t is None:
                for x in tlist: t = x; break
            if t:
                texts = []
                for seg in t.fetch():
                    if isinstance(seg, dict):   texts.append(seg.get('text',''))
                    elif hasattr(seg, 'text'):  texts.append(seg.text)
                    else:                       texts.append(str(seg))
                return " ".join(texts)
        except: pass
        return "자막 없음"
    except ImportError:
        return "youtube-transcript-api 미설치"
    except Exception as e:
        return f"자막 없음 ({str(e)[:50]})"


# ================================================================
# Whisper STT : 오디오 다운로드 → OpenAI Whisper API 변환
# ================================================================
def whisper_transcribe(video_id: str, openai_api_key: str) -> str:
    """
    yt-dlp 로 오디오를 다운로드한 뒤 OpenAI Whisper API 로 텍스트 변환.
    성공 시 변환된 텍스트, 실패 시 오류 메시지 반환.
    """
    import os, tempfile, subprocess, sys

    if not openai_api_key:
        return "[Whisper 오류] OpenAI API 키가 없습니다."

    # yt-dlp 설치 확인
    try:
        import yt_dlp
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp", "-q"])
            import yt_dlp
        except Exception as e:
            return f"[Whisper 오류] yt-dlp 설치 실패: {e}"

    # openai 설치 확인
    try:
        import openai
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
            import openai
        except Exception as e:
            return f"[Whisper 오류] openai 설치 실패: {e}"

    url = f"https://www.youtube.com/watch?v={video_id}"
    tmp_dir = tempfile.mkdtemp()

    # OpenAI Whisper API 가 지원하는 확장자 (ffmpeg 없이 직접 전송 가능)
    SUPPORTED_EXTS = ('.m4a', '.webm', '.mp4', '.mp3', '.mpeg',
                      '.mpga', '.wav', '.ogg', '.opus')

    try:
        import requests as _req

        # ── Step 1: yt-dlp 로 오디오 스트림 URL 추출 (다운로드 없음) ──
        ydl_opts_info = {
            "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        audio_url = None
        audio_ext = "m4a"
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                audio_url = info.get("url")
                audio_ext = info.get("ext", "m4a")

        if not audio_url:
            return "[Whisper 오류] 오디오 스트림 URL 추출 실패"

        # ── Step 2: requests 로 직접 다운로드 ──
        audio_path = os.path.join(tmp_dir, f"audio.{audio_ext}")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.youtube.com/",
        }
        total_bytes = 0
        MAX_BYTES = 25 * 1024 * 1024  # 25MB
        with _req.get(audio_url, headers=headers, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(audio_path, "wb") as fp:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        total_bytes += len(chunk)
                        if total_bytes > MAX_BYTES:
                            return (f"[Whisper 오류] 파일 크기 초과 "
                                    f"(25MB 이상). 짧은 영상만 지원합니다.")
                        fp.write(chunk)

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
            return "[Whisper 오류] 오디오 다운로드 실패 (파일 크기 0)"

        # ── Step 3: OpenAI Whisper API 호출 ──
        openai.api_key = openai_api_key
        with open(audio_path, "rb") as f:
            response = openai.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="text",
            )

        result_text = response if isinstance(response, str) else getattr(response, 'text', str(response))
        return result_text if result_text else "[Whisper] 변환 결과 없음"

    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        if "Private video" in err or "members-only" in err:
            return "[Whisper 오류] 비공개/멤버십 영상은 다운로드 불가"
        return f"[Whisper 오류] 다운로드 실패: {err[:80]}"
    except Exception as e:
        return f"[Whisper 오류] {str(e)[:100]}"
    finally:
        # 임시 파일 정리
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def get_transcript_with_whisper(video_id: str,
                                 openai_api_key: str = "") -> str:
    """
    1차: youtube-transcript-api 로 자막 시도
    2차: 자막 없으면 Whisper API 로 변환
    """
    result = get_transcript(video_id)
    # 자막을 가져오지 못한 경우 Whisper 시도
    if (not result or
        result.startswith("자막 없음") or
        result.startswith("youtube-transcript") or
        result.startswith("[Whisper")):
        if openai_api_key:
            whisper_result = whisper_transcribe(video_id, openai_api_key)
            if whisper_result and not whisper_result.startswith("[Whisper 오류]"):
                return "[🎙️ Whisper 변환]\n" + whisper_result
            else:
                return f"자막 없음 / {whisper_result}"
        else:
            return result
    return result

# ================================================================
# 엑셀 저장 (바이트 반환)
# ================================================================
def save_xlsx_bytes(all_results_by_keyword, channel_stats):
    if not HAS_XLSX:
        return None
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    RED   = "1B3A6B"
    DRED  = "0D2347"
    WHITE = "FFFFFF"
    LGRAY = "F5F5F5"
    DGRAY = "D0D0D0"

    def style_header(ws, headers, row=1):
        fill = PatternFill("solid", fgColor=RED)
        font = Font(bold=True, color=WHITE, size=10)
        for col_idx, h in enumerate(headers, 1):
            c = ws.cell(row=row, column=col_idx, value=h)
            c.fill = fill; c.font = font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = Border(
                bottom=Side(style="thin", color=DRED),
                right=Side(style="thin", color=DRED)
            )

    def style_cell(ws, row, col, value, fill=None, bold=False, align="left"):
        c = ws.cell(row=row, column=col, value=value)
        if fill:
            c.fill = PatternFill("solid", fgColor=fill)
        c.font = Font(bold=bold, size=9)
        c.alignment = Alignment(horizontal=align, vertical="top", wrap_text=True)
        c.border = Border(
            bottom=Side(style="thin", color=DGRAY),
            right=Side(style="thin", color=DGRAY)
        )

    # ── 시트1: 영상 목록 ─────────────────────────────────────
    ws1 = wb.create_sheet("📋 영상 목록")
    headers1 = ["검색어","순위","제목","채널","구독자","조회수","좋아요","댓글",
                "재생시간","업로드일","태그","핵심키워드","요약","URL"]
    style_header(ws1, headers1)
    widths1 = [12,5,40,20,10,10,8,8,10,12,30,25,50,45]
    for i, w in enumerate(widths1, 1):
        ws1.column_dimensions[get_column_letter(i)].width = w
    ws1.row_dimensions[1].height = 28

    row = 2
    for kw, videos in all_results_by_keyword.items():
        for rank_idx, v in enumerate(videos, 1):
            fill_c = LGRAY if row % 2 == 0 else None
            data = [
                kw, rank_idx, v["title"], v["channelTitle"],
                v["subscriberLabel"], v["viewLabel"], v["likeLabel"], v["commentLabel"],
                v["duration"], v["publishedAt"],
                " | ".join(v["tags"][:10]) if v["tags"] else "",
                " · ".join(v.get("keywords", [])[:8]),
                v.get("summary", ""),
                v["url"]
            ]
            for col_idx, val in enumerate(data, 1):
                style_cell(ws1, row, col_idx, val, fill=fill_c)
            ws1.row_dimensions[row].height = 18
            row += 1

    # ── 시트2: 채널 통계 ─────────────────────────────────────
    ws2 = wb.create_sheet("📊 채널 통계")
    headers2 = ["채널명","구독자","영상수","총조회수","평균조회수","총좋아요","평균좋아요","총댓글","대표영상"]
    style_header(ws2, headers2)
    widths2 = [25,10,7,12,12,12,10,10,40]
    for i, w in enumerate(widths2, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.row_dimensions[1].height = 28

    row = 2
    for cs in channel_stats:
        rep = cs["videos"][0]["url"] if cs["videos"] else ""
        data = [
            cs["channel"], cs["subscriber"], cs["videoCount"],
            fmt(cs["totalView"])+"회", fmt(cs["avgView"])+"회",
            fmt(cs["totalLike"]), fmt(cs["avgLike"]),
            fmt(cs["totalComment"]), rep
        ]
        fill_c = LGRAY if row % 2 == 0 else None
        for col_idx, val in enumerate(data, 1):
            style_cell(ws2, row, col_idx, val, fill=fill_c, align="center" if col_idx in [2,3,4,5,6,7,8] else "left")
        ws2.row_dimensions[row].height = 18
        row += 1

    # ── 시트3: 키워드 요약 ───────────────────────────────────
    ws3 = wb.create_sheet("🔑 키워드 요약")
    headers3 = ["검색어","영상수","평균조회수","최고조회수","공통키워드 TOP5"]
    style_header(ws3, headers3)
    for i, w in enumerate([18,7,12,12,50], 1):
        ws3.column_dimensions[get_column_letter(i)].width = w
    ws3.row_dimensions[1].height = 28

    row = 2
    for kw, videos in all_results_by_keyword.items():
        all_kws = []
        for v in videos:
            all_kws.extend(v.get("keywords", []))
        top5 = " · ".join([w for w, _ in Counter(all_kws).most_common(5)])
        avg_v = sum(v["viewCount"] for v in videos) // len(videos) if videos else 0
        max_v = max((v["viewCount"] for v in videos), default=0)
        data = [kw, len(videos), fmt(avg_v)+"회", fmt(max_v)+"회", top5]
        fill_c = LGRAY if row % 2 == 0 else None
        for col_idx, val in enumerate(data, 1):
            style_cell(ws3, row, col_idx, val, fill=fill_c)
        ws3.row_dimensions[row].height = 18
        row += 1

    # ── 시트4: 대본 전문 ─────────────────────────────────────
    ws4 = wb.create_sheet("📜 대본 전문")
    headers4 = ["제목","채널","URL","대본 전문"]
    style_header(ws4, headers4)
    for i, w in enumerate([40,20,45,120], 1):
        ws4.column_dimensions[get_column_letter(i)].width = w
    ws4.row_dimensions[1].height = 28

    row = 2
    for videos in all_results_by_keyword.values():
        for v in videos:
            if v.get("transcript") and "자막 없음" not in v["transcript"] and len(v["transcript"]) > 20:
                data = [v["title"], v["channelTitle"], v["url"], v["transcript"]]
                fill_c = LGRAY if row % 2 == 0 else None
                for col_idx, val in enumerate(data, 1):
                    style_cell(ws4, row, col_idx, val, fill=fill_c)
                ws4.row_dimensions[row].height = 80
                row += 1

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()

# ================================================================
# 텍스트 빌드
# ================================================================
def build_txt(all_results_by_keyword, channel_stats, sort_label):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["="*68,
             f"  📊 YouTube 분석 결과  |  생성: {ts}",
             f"  정렬: {sort_label}",
             "="*68]
    for kw, videos in all_results_by_keyword.items():
        lines += [f"\n{'━'*68}",
                  f"  🔍 검색어: [{kw}]  ({len(videos)}개 영상)",
                  f"{'━'*68}"]
        for v in videos:
            lines += [
                f"\n{'═'*68}",
                f"  {v.get('badge','▶')}  #{v.get('rank',0)}위  |  {v['title']}",
                f"{'═'*68}",
                f"🔗 URL       : {v['url']}",
                f"📺 채널      : {v['channelTitle']}  (구독자 {v['subscriberLabel']})",
                f"⏱️  길이     : {v['duration']}",
                f"📅 업로드    : {v['publishedAt']}",
                "",
                f"📈 통계",
                f"   👁️  조회수 : {v['viewLabel']} ({v['viewCount']:,}회)",
                f"   👍 좋아요 : {v['likeLabel']} ({v['likeCount']:,}개)",
                f"   💬 댓글수 : {v['commentLabel']} ({v['commentCount']:,}개)",
                "",
                f"📝 영상 설명:",
                "─"*68,
                v['description'] if v['description'] else "(없음)",
                "",
                f"🏷️  태그 ({len(v['tags'])}개):",
                "  " + " | ".join(v['tags'][:20]) if v['tags'] else "  (없음)",
                "",
                f"🔑 핵심 키워드:",
                "  " + " · ".join(v.get('keywords', [])) if v.get('keywords') else "  (추출 불가)",
                "",
                f"📋 요약:",
                "  " + v.get('summary', '(없음)'),
                "",
                f"📜 대본 전문:",
                "─"*68,
                v['transcript'] if v['transcript'] else "자막 없음",
                f"\n{'─'*68}",
            ]
    lines += [f"\n{'━'*68}",
              f"  📊 채널별 통계  (총 {len(channel_stats)}개 채널)",
              f"{'━'*68}",
              f"  {'채널명':<22} {'구독자':>8} {'영상수':>6} {'총조회수':>10} {'평균조회수':>10} {'평균좋아요':>10}",
              "  " + "─"*65]
    for cs in channel_stats:
        lines.append(
            f"  {cs['channel']:<22} {cs['subscriber']:>8} "
            f"{cs['videoCount']:>6} {fmt(cs['totalView'])+' 회':>10} "
            f"{fmt(cs['avgView'])+' 회':>10} {fmt(cs['avgLike']):>10}"
        )
    lines += ["", "="*68]
    return "\n".join(lines)

# ================================================================
# JSON 빌드
# ================================================================
def build_json(all_results_by_keyword, channel_stats):
    output = {"keywords": {}, "channel_stats": [], "generated_at": datetime.now().isoformat()}
    for kw, videos in all_results_by_keyword.items():
        output["keywords"][kw] = [{
            k: v[k] for k in ["videoId","title","channelTitle","url","publishedAt",
                               "viewCount","likeCount","commentCount","duration",
                               "subscriberLabel","tags","keywords","summary","transcript"]
        } for v in videos]
    output["channel_stats"] = [{
        k: cs[k] for k in ["channel","subscriber","videoCount","totalView","avgView","totalLike","avgLike"]
    } for cs in channel_stats]
    return json.dumps(output, ensure_ascii=False, indent=2)

# ================================================================
# Google Sheets 업로드
# ================================================================
def upload_to_gsheet(all_results_by_keyword, channel_stats, sort_label,
                     credentials_dict=None, spreadsheet_name=None,
                     share_email=None, existing_id=None):
    if not HAS_GSHEET:
        return False, "gspread/google-auth 라이브러리가 설치되지 않았습니다.\n`pip install gspread google-auth` 를 실행해주세요."
    if not credentials_dict:
        return False, "credentials.json 파일이 없습니다."

    # ✅ gspread v6 호환 스코프
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    try:
        # ✅ gspread v6 권장 방식: service_account_from_dict (가장 안정적)
        try:
            gc = gspread.service_account_from_dict(credentials_dict, scopes=SCOPES)
        except AttributeError:
            # gspread v5 이하 fallback
            creds = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
            try:
                gc = gspread.Client(auth=creds)
            except Exception:
                gc = gspread.authorize(creds)
    except Exception as e:
        err_hint = str(e)
        return False, (
            f"Google 인증 실패: {err_hint}\n\n"
            "확인사항:\n"
            "1️⃣ credentials.json 이 서비스 계정(Service Account) 파일인지 확인\n"
            "2️⃣ Google Cloud Console → API 라이브러리에서 'Google Sheets API' 활성화 확인\n"
            "3️⃣ 'Google Drive API' 도 활성화 확인\n"
            "4️⃣ 서비스 계정의 이메일을 스프레드시트 편집자로 공유했는지 확인"
        )

    try:
        if existing_id:
            eid = existing_id.strip()
            if "spreadsheets/d/" in eid:
                eid = eid.split("spreadsheets/d/")[1].split("/")[0]
            sh = gc.open_by_key(eid)
            sheet_url = f"https://docs.google.com/spreadsheets/d/{sh.id}"
        else:
            if not spreadsheet_name:
                slug = "_".join(list(all_results_by_keyword.keys())[:2])[:30]
                ts   = datetime.now().strftime("%m%d_%H%M")
                spreadsheet_name = f"YouTube분석_{slug}_{ts}"
            sh = gc.create(spreadsheet_name)
            sheet_url = f"https://docs.google.com/spreadsheets/d/{sh.id}"
            if share_email:
                sh.share(share_email, perm_type='user', role='writer')
            else:
                sh.share('', perm_type='anyone', role='reader')
    except gspread.exceptions.APIError as e:
        msg = str(e)
        if "quota" in msg.lower() or "403" in msg:
            return False, (
                "❌ Google Drive 저장 용량 초과 (403 오류)\n\n"
                "해결 방법:\n"
                "1️⃣  drive.google.com 에서 불필요한 파일 삭제 후 재시도\n"
                "2️⃣  또는 기존 스프레드시트 ID를 아래 입력란에 넣어주세요"
            )
        return False, f"스프레드시트 생성 오류: {msg}"
    except Exception as e:
        return False, f"스프레드시트 생성 오류: {e}"

    # ── 시트 작성 헬퍼 ─────────────────────────────────────────
    def safe_write(ws, rows_data):
        """시트에 데이터 쓰기 — gspread v5/v6 완전 호환"""
        if not rows_data:
            return
        safe = [[str(c) if c is not None else "" for c in row]
                for row in rows_data]
        ws.clear()
        written = False
        # 방법 1: gspread v5 방식 (range_name, values)
        try:
            ws.update("A1", safe)
            written = True
        except Exception:
            pass
        # 방법 2: gspread v6 키워드 방식
        if not written:
            try:
                ws.update(range_name="A1", values=safe)
                written = True
            except Exception:
                pass
        # 방법 3: batch_update 방식
        if not written:
            try:
                ws.batch_update([{"range": "A1", "values": safe}])
                written = True
            except Exception:
                pass
        # 방법 4: 최후 수단 append
        if not written:
            for i in range(0, len(safe), 100):
                ws.append_rows(safe[i:i+100], value_input_option="RAW")

    def get_or_create_ws(title, rows=500, cols=20):
        """시트 생성/조회 (이모지 이름 실패 시 plain 이름 fallback)"""
        current_titles = [ws.title for ws in sh.worksheets()]
        def _try(t):
            if t in current_titles:
                ws = sh.worksheet(t)
                ws.clear()
                return ws
            return sh.add_worksheet(title=t, rows=rows, cols=cols)
        try:
            return _try(title)
        except Exception:
            import re as _re
            plain = _re.sub(r'[^\w\s가-힣]', '', title).strip() or f"Sheet{len(current_titles)}"
            return _try(plain)

    # ── 시트1: 영상 목록 ───────────────────────────────────────
    try:
        ws1   = get_or_create_ws("영상 목록", rows=1000, cols=15)
        h1    = ["검색어","순위","제목","채널","구독자","조회수","좋아요","댓글",
                 "재생시간","업로드일","태그","핵심키워드","요약","URL"]
        rows1 = [h1]
        for kw, videos in all_results_by_keyword.items():
            for rank_idx, v in enumerate(videos, 1):
                rows1.append([
                    kw, rank_idx, v["title"], v["channelTitle"],
                    v["subscriberLabel"], v["viewLabel"], v["likeLabel"], v["commentLabel"],
                    v["duration"], v["publishedAt"],
                    " | ".join(v["tags"][:10]) if v["tags"] else "",
                    " · ".join(v.get("keywords", [])[:8]),
                    v.get("summary", ""),
                    v["url"]
                ])
        safe_write(ws1, rows1)
    except Exception as e:
        return False, f"❌ 시트1(영상 목록) 쓰기 오류: {e}"

    # ── 시트2: 채널 통계 ───────────────────────────────────────
    try:
        ws2   = get_or_create_ws("채널 통계", rows=200, cols=10)
        h2    = ["채널명","구독자","영상수","총조회수","평균조회수","총좋아요","평균좋아요","총댓글","대표영상"]
        rows2 = [h2]
        for cs in channel_stats:
            rep = cs["videos"][0]["url"] if cs["videos"] else ""
            rows2.append([
                cs["channel"], cs["subscriber"], cs["videoCount"],
                fmt(cs["totalView"])+"회", fmt(cs["avgView"])+"회",
                fmt(cs["totalLike"]), fmt(cs["avgLike"]),
                fmt(cs["totalComment"]), rep
            ])
        safe_write(ws2, rows2)
    except Exception as e:
        return False, f"❌ 시트2(채널 통계) 쓰기 오류: {e}"

    # ── 시트3: 키워드 요약 ─────────────────────────────────────
    try:
        ws3   = get_or_create_ws("키워드 요약", rows=100, cols=6)
        h3    = ["검색어","영상수","평균조회수","최고조회수","공통키워드 TOP5"]
        rows3 = [h3]
        for kw, videos in all_results_by_keyword.items():
            all_kws = []
            for v in videos:
                all_kws.extend(v.get("keywords", []))
            top5  = " · ".join([w for w, _ in Counter(all_kws).most_common(5)])
            avg_v = sum(v["viewCount"] for v in videos) // len(videos) if videos else 0
            max_v = max((v["viewCount"] for v in videos), default=0)
            rows3.append([kw, len(videos), fmt(avg_v)+"회", fmt(max_v)+"회", top5])
        safe_write(ws3, rows3)
    except Exception as e:
        return False, f"❌ 시트3(키워드 요약) 쓰기 오류: {e}"

    # ── 시트4: 대본 전문 ───────────────────────────────────────
    try:
        ws4   = get_or_create_ws("대본 전문", rows=500, cols=5)
        h4    = ["제목","채널","URL","대본 출처","대본 전문"]
        rows4 = [h4]
        MAX_CELL = 49000
        for kw, videos in all_results_by_keyword.items():
            for v in videos:
                tr = v.get("transcript", "")
                if not is_valid_transcript(tr):
                    continue
                whisper_flag = "🎙️ Whisper" if tr.startswith("[🎙️") else "📝 자막"
                if len(tr) > MAX_CELL:
                    tr = tr[:MAX_CELL] + f"\n\n[⚠️ {MAX_CELL}자 초과로 잘림. 원본: {len(v['transcript'])}자]"
                rows4.append([v["title"], v["channelTitle"], v["url"], whisper_flag, tr])
        # 대본은 크므로 50행씩 나눠서 update
        ws4.clear()
        for i in range(0, len(rows4), 50):
            chunk = rows4[i:i+50]
            safe_rows = [[str(c) if c is not None else "" for c in row]
                         for row in chunk]
            start_cell = f"A{i + 1}"
            try:
                ws4.update(start_cell, safe_rows)
            except Exception:
                try:
                    ws4.update(range_name=start_cell, values=safe_rows)
                except Exception:
                    ws4.append_rows(safe_rows, value_input_option="RAW")
    except Exception as e:
        return False, f"❌ 시트4(대본 전문) 쓰기 오류: {e}"

    # ── 기본 Sheet1 제거 ───────────────────────────────────────
    for ws in sh.worksheets():
        if ws.title in ("Sheet1", "시트1"):
            try:
                sh.del_worksheet(ws)
            except Exception:
                pass

    return True, sheet_url

# ================================================================
# 메인 UI
# ================================================================
def main():
    # ── 헤더 ─────────────────────────────────────────────────
    st.markdown("""
    <div class="main-header">
        <h1>🎬 YouTube 분석 도구</h1>
        <p>YouTube 동영상을 검색·분석하고 엑셀 / 텍스트 / Google Sheets로 내보낼 수 있습니다</p>
    </div>
    """, unsafe_allow_html=True)

# ================================================================
    # secrets.toml 에서 설정값 한꺼번에 로드 (없으면 빈값)
    # ================================================================
    def _secret(key, default=""):
        try:
            v = st.secrets.get(key, None)
            if v is None or str(v).strip() == "" or str(v) == "None":
                return default
            return str(v).strip()
        except Exception:
            return default

    _s_api_key    = _secret("YOUTUBE_API_KEY")
    _s_keywords   = _secret("DEFAULT_KEYWORDS")
    _s_max_count  = _secret("DEFAULT_MAX_COUNT", "20")
    _s_sort       = _secret("DEFAULT_SORT", "조회수순")
    _s_email      = _secret("GSHEET_SHARE_EMAIL")
    _s_existing   = _secret("GSHEET_EXISTING_ID")
    _s_openai_key = _secret("OPENAI_API_KEY")

    # ✅ Streamlit Cloud Secrets의 gcp_service_account 자동 로드
    _s_gcp_creds = None
    try:
        _gcp = st.secrets.get("gcp_service_account", {})
        if _gcp and _gcp.get("type") == "service_account":
            _s_gcp_creds = dict(_gcp)
    except Exception:
        _s_gcp_creds = None

    # ================================================================
    # 클립보드 복사 헬퍼 함수 (JS 기반)
    # ================================================================
    def copy_button(copy_text: str, btn_label: str, btn_key: str):
        """텍스트를 클립보드에 복사하는 버튼을 렌더링"""
        import base64
        encoded = base64.b64encode(copy_text.encode("utf-8")).decode("utf-8")
        html = f"""
<div style="margin-top:8px;">
  <button onclick="
    (function(){{
      var text = atob('{encoded}');
      navigator.clipboard.writeText(text).then(function(){{
        var btn = document.getElementById('cpbtn_{btn_key}');
        var orig = btn.innerText;
        btn.innerText = '✅ 복사됨!';
        btn.style.background = '#4CAF50';
        setTimeout(function(){{ btn.innerText = orig; btn.style.background = '#1a73e8'; }}, 2000);
      }}).catch(function(){{
        var ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        var btn = document.getElementById('cpbtn_{btn_key}');
        btn.innerText = '✅ 복사됨!';
        btn.style.background = '#4CAF50';
        setTimeout(function(){{ btn.innerText = '{btn_label}'; btn.style.background = '#1a73e8'; }}, 2000);
      }});
    }})();
  "
  id="cpbtn_{btn_key}"
  style="background:#1a73e8;color:white;border:none;border-radius:8px;
         padding:7px 18px;cursor:pointer;font-size:0.88rem;font-weight:600;
         width:100%;transition:background 0.2s;">
    {btn_label}
  </button>
</div>
"""
        st.markdown(html, unsafe_allow_html=True)

    # ================================================================
    # 사이드바
    # ================================================================
    with st.sidebar:
        st.markdown("## ⚙️ 검색 설정")

        # API 키: secrets.toml 에서 자동 로드
        api_key = st.text_input(
            "🔑 YouTube API 키",
            value=_s_api_key,
            type="password",
            placeholder="AIzaSy...",
            help="Google Cloud Console에서 발급한 YouTube Data API v3 키를 입력하세요."
        )
        if _s_api_key:
            st.caption("✅ secrets.toml 에서 API 키 자동 로드됨")

        st.markdown("---")
        st.markdown("### 🔍 검색 옵션")

        keywords_input = st.text_area(
            "검색 키워드 (쉼표로 여러 개 입력)",
            value=_s_keywords,
            placeholder="예: 비타민D 효능, 50대 영양제",
            height=80
        )

        _max_default = int(_s_max_count) if _s_max_count.isdigit() else 20
        _max_default = max(5, min(50, _max_default))
        max_count = st.slider(
            "키워드당 최대 검색 수",
            min_value=5, max_value=50, value=_max_default, step=5
        )

        _sort_options = ["조회수순","최신순","관련성순","평점순"]
        _sort_idx = _sort_options.index(_s_sort) if _s_sort in _sort_options else 0
        sort_option = st.selectbox(
            "정렬 방식",
            options=_sort_options,
            index=_sort_idx
        )
        SORT_MAP = {"조회수순":"viewCount","최신순":"date","관련성순":"relevance","평점순":"rating"}
        order_api = SORT_MAP[sort_option]

        fetch_transcript = st.checkbox(
            "📜 자막(대본) 가져오기",
            value=True,
            help="체크 시 각 영상의 자막을 가져와 키워드 추출과 요약을 수행합니다. 영상이 많으면 시간이 걸립니다."
        )

        # ── Whisper STT 설정 ──────────────────────────────────
        use_whisper = False
        openai_api_key_input = ""
        if fetch_transcript:
            use_whisper = st.checkbox(
                "🎙️ 자막 없는 영상은 Whisper로 변환",
                value=False,
                help="자막이 없는 영상에 대해 OpenAI Whisper API로 음성을 텍스트로 변환합니다.\n"
                     "OpenAI API 키가 필요하며, 영상당 약 $0.006/분 비용이 발생합니다.\n"
                     "25분 이하 영상 권장."
            )
            if use_whisper:
                # Secrets에 키가 있으면 입력창 숨기고 자동 사용
                if _s_openai_key:
                    openai_api_key_input = _s_openai_key
                    st.caption("✅ Secrets에서 OpenAI API Key 자동 로드됨")
                    st.caption("💡 비용: ~$0.006/분 · 25분 이하 영상 권장")
                else:
                    openai_api_key_input = st.text_input(
                        "🔑 OpenAI API Key",
                        value="",
                        type="password",
                        placeholder="sk-...",
                        help="https://platform.openai.com/api-keys 에서 발급\n"
                             "또는 Streamlit Secrets에 OPENAI_API_KEY 추가"
                    )
                    if not openai_api_key_input:
                        st.caption("⚠️ API 키를 입력하거나 Secrets에 OPENAI_API_KEY를 추가하세요.")
                    else:
                        st.caption("✅ Whisper API 키 설정됨")
                    st.caption("💡 비용: ~$0.006/분 · 25분 이하 영상 권장")

        st.markdown("---")
        search_btn = st.button("🚀 검색 시작", use_container_width=True, type="primary")

        st.markdown("---")
        st.markdown("### 📊 Google Sheets 설정")

        use_gsheet = st.checkbox(
            "Google Sheets 자동 업로드",
            value=False,
            disabled=not HAS_GSHEET
        )
        if not HAS_GSHEET:
            st.caption("⚠️ `pip install gspread google-auth` 필요")

        # ── credentials.json 자동 탐색 ─────────────────────────
        import os as _os
        _cred_search_paths = [
            _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "credentials.json"),
            _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".streamlit", "credentials.json"),
            _os.path.join(_os.getcwd(), "credentials.json"),
            _os.path.join(_os.getcwd(), ".streamlit", "credentials.json"),
        ]
        _auto_creds_path = None
        for _p in _cred_search_paths:
            if _os.path.exists(_p):
                _auto_creds_path = _p
                break

        credentials_file = None
        share_email      = _s_email
        existing_id      = _s_existing

        if use_gsheet:
            # ── 우선순위: ①Secrets gcp_service_account → ②로컬 파일 → ③수동 업로드
            _all_auto = bool((_s_gcp_creds or _auto_creds_path) and _s_existing)

            if _s_gcp_creds:
                # ✅ Streamlit Cloud Secrets에서 자동 로드됨
                st.success("✅ Secrets에서 Google 인증 자동 로드 완료")
                if _s_email:
                    st.caption(f"📧 공유 이메일: {_s_email}")
                if _s_existing:
                    _eid_display = _s_existing[:20] + "..." if len(_s_existing) > 20 else _s_existing
                    st.caption(f"📊 시트 ID: {_eid_display}")
                _show_manual = st.checkbox("⚙️ 설정 수동 변경", value=False, key="gsheet_manual")
            elif _all_auto:
                # ✅ 로컬 파일에서 자동 로드됨
                st.success("✅ 모든 설정 자동 로드 완료")
                st.caption(f"🔑 credentials: {_os.path.basename(_auto_creds_path)}")
                if _s_email:
                    st.caption(f"📧 공유 이메일: {_s_email}")
                _eid_display = _s_existing[:20] + "..." if len(_s_existing) > 20 else _s_existing
                st.caption(f"📊 시트 ID: {_eid_display}")
                _show_manual = st.checkbox("⚙️ 설정 수동 변경", value=False, key="gsheet_manual")
            else:
                _show_manual = True

            if _show_manual if not _s_gcp_creds else st.session_state.get("gsheet_manual", False):
                # credentials.json
                if _s_gcp_creds:
                    st.info("✅ Secrets(gcp_service_account)에서 인증 정보 로드됨")
                elif _auto_creds_path:
                    st.info(f"✅ credentials.json 자동 감지: {_os.path.basename(_auto_creds_path)}")
                    if st.checkbox("다른 파일로 교체", value=False, key="replace_creds"):
                        credentials_file = st.file_uploader(
                            "credentials.json 업로드",
                            type=["json"],
                            help="Google Cloud 서비스 계정 JSON 키 파일"
                        )
                else:
                    st.warning("⚠️ credentials.json 없음\n\nStreamlit Cloud Secrets에 [gcp_service_account] 섹션을 추가하거나 파일을 업로드하세요")
                    credentials_file = st.file_uploader(
                        "credentials.json 업로드",
                        type=["json"],
                        help="Google Cloud 서비스 계정 JSON 키 파일"
                    )

                # 이메일
                share_email = st.text_input(
                    "📧 공유할 이메일",
                    value=_s_email,
                    placeholder="yourname@gmail.com",
                    help="secrets.toml의 GSHEET_SHARE_EMAIL에 저장하면 자동 입력됩니다."
                )

                # 스프레드시트 ID
                existing_id = st.text_input(
                    "📊 스프레드시트 ID",
                    value=_s_existing,
                    placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
                    help="secrets.toml의 GSHEET_EXISTING_ID에 저장하면 자동 입력됩니다."
                )
                if not _s_existing:
                    st.caption("💡 스프레드시트 URL에서 /d/ 뒤의 문자열을 secrets.toml의 GSHEET_EXISTING_ID 에 저장하면 다음부터 자동 입력됩니다!")

    # ================================================================
    # 검색 실행
    # ================================================================
    if search_btn:
        if not api_key:
            st.error("❌ YouTube API 키를 입력해주세요.")
            st.stop()
        if not keywords_input.strip():
            st.error("❌ 검색 키워드를 입력해주세요.")
            st.stop()

        keywords = [kw.strip() for kw in keywords_input.replace("，",",").split(",") if kw.strip()]


        # credentials.json 로드: ①Secrets gcp → ②수동 업로드 → ③자동 탐색 순서
        creds_dict = None
        if use_gsheet:
            # ✅ 우선순위 1: Streamlit Cloud Secrets gcp_service_account
            if _s_gcp_creds:
                creds_dict = _s_gcp_creds
            elif credentials_file:
                try:
                    creds_dict = json.load(credentials_file)
                except:
                    st.error("❌ 업로드한 credentials.json 파일을 읽을 수 없습니다.")
                    st.stop()
            elif _auto_creds_path:
                try:
                    with open(_auto_creds_path, "r", encoding="utf-8") as _f:
                        creds_dict = json.load(_f)
                except Exception as _e:
                    st.error(f"❌ credentials.json 자동 로드 실패: {_e}")
                    st.stop()
            else:
                st.error("❌ credentials.json 없음\n\n해결 방법:\n1. Streamlit Cloud → Settings → Secrets에 [gcp_service_account] 추가\n2. 또는 파일 직접 업로드")
                st.stop()

        # 진행 상태
        progress_bar = st.progress(0)
        status_text  = st.empty()

        all_results   = {}
        all_videos_flat = []
        total_steps   = len(keywords)

        for ki, kw in enumerate(keywords):
            status_text.info(f"🔍 [{kw}] 검색 중... ({ki+1}/{total_steps})")

            # 1) 검색
            video_ids, err = search_youtube(api_key, kw, max_count, order_api)
            if err:
                st.error(f"❌ 오류: {err}")
                st.stop()
            if not video_ids:
                st.warning(f"⚠️ [{kw}] 검색 결과가 없습니다.")
                all_results[kw] = []
                progress_bar.progress((ki+1)/total_steps)
                continue

            # 2) 상세 정보
            status_text.info(f"📊 [{kw}] 영상 상세 정보 수집 중...")
            videos = fetch_video_details(api_key, video_ids)

            # 3) 구독자
            status_text.info(f"👥 [{kw}] 구독자 수 수집 중...")
            videos = fetch_subscribers(api_key, videos)

            # 4) 자막 (+ Whisper 폴백)
            if fetch_transcript:
                for vi, v in enumerate(videos):
                    # 상태 메시지
                    whisper_note = " 🎙️Whisper 대기중" if use_whisper and openai_api_key_input else ""
                    status_text.info(f"📜 [{kw}] 자막 수집 중... ({vi+1}/{len(videos)}) - {v['title'][:30]}...{whisper_note}")

                    if use_whisper and openai_api_key_input:
                        raw = get_transcript(v["videoId"])
                        # 자막 없으면 Whisper 시도
                        if (not raw or
                            raw.startswith("자막 없음") or
                            raw.startswith("youtube-transcript")):
                            status_text.info(
                                f"🎙️ [{kw}] Whisper 변환 중... ({vi+1}/{len(videos)}) "
                                f"- {v['title'][:25]}... (수 분 소요될 수 있습니다)"
                            )
                            raw = whisper_transcribe(v["videoId"], openai_api_key_input)
                            if raw and not raw.startswith("[Whisper 오류]"):
                                v["transcript"] = f"[🎙️ Whisper 변환]\n{raw}"
                            else:
                                # 오류 내용을 화면에 표시
                                err_msg = raw if raw else "[Whisper 오류] 알 수 없는 오류"
                                st.warning(f"🎙️ Whisper 변환 실패: {v['title'][:30]}\n→ {err_msg}")
                                v["transcript"] = "자막 없음 (Whisper 실패)"
                        else:
                            v["transcript"] = raw
                    else:
                        v["transcript"] = get_transcript(v["videoId"])

                    v["keywords"] = extract_keywords(
                        v["transcript"] + " " + v["description"] + " " + " ".join(v["tags"])
                    )
                    v["summary"]  = summarize_text(
                        v["transcript"] if len(v.get("transcript","")) > 100 else v["description"]
                    )

            # 5) 배지 & 순위
            for rank_i, v in enumerate(videos, 1):
                v["rank"]  = rank_i
                v["badge"] = get_badge(rank_i, v["viewCount"])

            all_results[kw] = videos
            all_videos_flat.extend(videos)
            progress_bar.progress((ki+1)/total_steps)

        status_text.success("✅ 분석 완료!")
        progress_bar.progress(1.0)

        # 채널 통계
        channel_stats = build_channel_stats(all_videos_flat)

        # ── 세션에 저장 ─────────────────────────────────────
        st.session_state["results"]       = all_results
        st.session_state["channel_stats"] = channel_stats
        st.session_state["sort_label"]    = sort_option
        st.session_state["creds_dict"]    = creds_dict
        st.session_state["share_email"]   = share_email
        st.session_state["existing_id"]   = existing_id
        st.session_state["use_gsheet"]    = use_gsheet

    # ================================================================
    # 결과 표시
    # ================================================================
    if "results" not in st.session_state or not st.session_state["results"]:
        if not search_btn:
            st.info("👈 왼쪽 사이드바에서 API 키와 검색 키워드를 입력하고 **검색 시작** 버튼을 눌러주세요.")
            with st.expander("📖 사용 방법 & 준비 사항"):
                st.markdown("""
**① 필수 라이브러리 설치**
```bash
pip install streamlit requests youtube-transcript-api openpyxl gspread google-auth
```

**② YouTube API 키 준비**
1. [Google Cloud Console](https://console.cloud.google.com) 접속
2. YouTube Data API v3 활성화
3. 사용자 인증정보 → API 키 생성

**③ Google Sheets 업로드 (선택)**
1. Google Sheets API + Google Drive API 활성화
2. 서비스 계정 생성 → JSON 키 다운로드
3. 사이드바에서 credentials.json 업로드

**④ 앱 실행**
```bash
streamlit run youtube_web_app.py
```
                """)
        st.stop()

    all_results   = st.session_state["results"]
    channel_stats = st.session_state["channel_stats"]
    sort_label    = st.session_state["sort_label"]

    # ── 요약 통계 ─────────────────────────────────────────────
    total_videos   = sum(len(v) for v in all_results.values())
    total_channels = len(channel_stats)
    total_views    = sum(v["viewCount"] for vs in all_results.values() for v in vs)
    has_transcript = sum(1 for vs in all_results.values()
                         for v in vs if is_valid_transcript(v.get("transcript", "")))

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f'<div class="metric-card"><div class="value">{total_videos}</div><div class="label">🎬 분석 영상</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="metric-card"><div class="value">{total_channels}</div><div class="label">📺 채널 수</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="metric-card"><div class="value">{fmt(total_views)}</div><div class="label">👁️ 총 조회수</div></div>', unsafe_allow_html=True)
    with c4:
        st.markdown(f'<div class="metric-card"><div class="value">{has_transcript}</div><div class="label">📜 자막 있음</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 다운로드 & 구글 시트 버튼 ────────────────────────────
    st.markdown("### 💾 결과 내보내기")
    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)

    now_str  = datetime.now().strftime("%Y%m%d_%H%M")
    slug_kw  = list(all_results.keys())[0][:12].replace(" ","_")
    base_name = f"youtube_{slug_kw}_{now_str}"

    txt_data  = build_txt(all_results, channel_stats, sort_label).encode("utf-8")
    json_data = build_json(all_results, channel_stats).encode("utf-8")
    xlsx_data = save_xlsx_bytes(all_results, channel_stats) if HAS_XLSX else None

    with btn_col1:
        st.download_button(
            label="📄 TXT 다운로드",
            data=txt_data,
            file_name=f"{base_name}.txt",
            mime="text/plain",
            use_container_width=True
        )
    with btn_col2:
        st.download_button(
            label="🔢 JSON 다운로드",
            data=json_data,
            file_name=f"{base_name}.json",
            mime="application/json",
            use_container_width=True
        )
    with btn_col3:
        if xlsx_data:
            st.download_button(
                label="📊 Excel 다운로드",
                data=xlsx_data,
                file_name=f"{base_name}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        else:
            st.button("📊 Excel (openpyxl 미설치)", disabled=True, use_container_width=True)
    with btn_col4:
        # creds_dict 가 세션에 있거나, 자동탐색 파일이 있으면 버튼 활성화
        import os as _os2
        _cred_paths2 = [
            _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), "credentials.json"),
            _os2.path.join(_os2.path.dirname(_os2.path.abspath(__file__)), ".streamlit", "credentials.json"),
            _os2.path.join(_os2.getcwd(), "credentials.json"),
            _os2.path.join(_os2.getcwd(), ".streamlit", "credentials.json"),
        ]
        _found_cred2 = next((p for p in _cred_paths2 if _os2.path.exists(p)), None)
        # ✅ Secrets gcp_service_account 도 업로드 가능 조건에 포함
        _can_upload = HAS_GSHEET and (
            st.session_state.get("creds_dict") or _found_cred2 or _s_gcp_creds
        )

        if _can_upload:
            if st.button("☁️ Google Sheets 업로드", use_container_width=True, type="primary"):
                # creds_dict: 세션 우선, 없으면 파일에서 로드
                _cd = st.session_state.get("creds_dict")
                # ✅ Secrets gcp_service_account 우선 사용
                if not _cd and _s_gcp_creds:
                    _cd = _s_gcp_creds
                if not _cd and _found_cred2:
                    try:
                        with open(_found_cred2, "r", encoding="utf-8") as _ff:
                            _cd = json.load(_ff)
                    except Exception as _e:
                        st.error(f"❌ credentials.json 로드 실패: {_e}")
                        st.stop()

                _email2   = st.session_state.get("share_email") or _s_email or None
                _eid2     = st.session_state.get("existing_id") or _s_existing or None

                if not _cd:
                    st.error("❌ credentials.json 파일이 없습니다. 앱 폴더에 넣어주세요.")
                else:
                    # 업로드 전 데이터 확인
                    _total_rows = sum(len(v) for v in all_results.values())
                    st.info(f"📊 업로드할 영상 수: {_total_rows}개, 키워드: {list(all_results.keys())}")
                    with st.spinner("📊 구글 스프레드시트 업로드 중..."):
                        ok, result = upload_to_gsheet(
                            all_results, channel_stats, sort_label,
                            credentials_dict=_cd,
                            share_email=_email2,
                            existing_id=_eid2
                        )
                    if ok:
                        st.success("✅ 업로드 완료!")
                        st.markdown(f"🔗 [스프레드시트 열기]({result})")
                        st.session_state["gsheet_url"] = result
                    else:
                        st.error(result)
        elif not HAS_GSHEET:
            st.button("☁️ Google Sheets (라이브러리 미설치)", disabled=True, use_container_width=True)
            st.caption("pip install gspread google-auth")
        else:
            st.button("☁️ Google Sheets (credentials 없음)", disabled=True, use_container_width=True)
            st.caption("앱 폴더에 credentials.json을 넣어주세요")

    if st.session_state.get("gsheet_url"):
        st.info(f"📊 마지막 업로드된 시트: {st.session_state['gsheet_url']}")

    st.markdown("---")

    # ── 탭 구성 ───────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "🎬 영상 목록", "📊 채널 통계", "🔑 키워드 분석", "📜 대본 전문"
    ])

    # ── 탭1: 영상 목록 ───────────────────────────────────────
    with tab1:
        for kw, videos in all_results.items():
            st.markdown(f"### 🔍 검색어: `{kw}`  &nbsp; ({len(videos)}개 영상 · {sort_label})")
            if not videos:
                st.warning("검색 결과가 없습니다.")
                continue

            for v in videos:
                badge_html = {
                    "🥇":"<span class='badge-hot'>🥇 1위</span>",
                    "🥈":"<span class='badge-good'>🥈 2위</span>",
                    "🥉":"<span class='badge-good'>🥉 3위</span>",
                    "🔥":"<span class='badge-hot'>🔥 인기</span>",
                    "⭐":"<span class='badge-new'>⭐ 우수</span>",
                }.get(v.get("badge","▶"), f"<span class='badge-norm'>#{v.get('rank',0)}위</span>")

                with st.expander(f"{v.get('badge','▶')} #{v.get('rank',0)}위  {v['title']}", expanded=(v.get('rank',0)<=3)):
                    col_img, col_info = st.columns([1, 3])

                    with col_img:
                        if v.get("thumbnail"):
                            st.image(v["thumbnail"], use_container_width=True)
                        st.markdown(f"[▶ YouTube에서 보기]({v['url']})")

                        # ── 📋 전체 정보 복사 (st.code 방식 — Streamlit Cloud 호환) ──
                        _sep  = "=" * 60
                        _sep2 = "-" * 60
                        _transcript_text = v.get('transcript', '')
                        _has_transcript  = is_valid_transcript(_transcript_text)
                        _copy_text = (
f"""{_sep}
■ 제목
{v['title']}

■ 채널명
{v['channelTitle']}

■ URL
{v['url']}
{_sep2}
■ 핵심 키워드
""" +
(' '.join(f'#{k}' for k in v.get('keywords', [])) if v.get('keywords') else '(키워드 없음)') +
f"""
{_sep2}
■ 요약
""" +
(v.get('summary') or '(요약 없음)') +
f"""
{_sep2}
■ 태그 ({len(v.get('tags', []))}개)
""" +
(','.join(f'#{t}' for t in v.get('tags', [])) if v.get('tags') else '(태그 없음)') +
f"""
{_sep2}
■ 영상 설명
""" +
(v.get('description') or '(설명 없음)') +
f"""
{_sep}
■ 대본 전문 {'✅' if _has_transcript else '❌ (자막 없음)'}
{_sep}
""" +
(_transcript_text if _has_transcript else '이 영상에는 자막이 없습니다.') +
f"""
{_sep}
"""
                        )
                        # 토글 버튼으로 펼치기/접기
                        with st.expander("📋 전체 정보 복사 (대본 포함) — 우측 상단 □ 아이콘으로 복사"):
                            st.code(_copy_text, language=None)

                    with col_info:
                        st.markdown(f"""
<div>
{badge_html}
<span class='video-title'> {v['title']}</span><br>
<span class='video-meta'>
📺 {v['channelTitle']} &nbsp;|&nbsp; 구독자 {v['subscriberLabel']} &nbsp;|&nbsp;
⏱ {v['duration']} &nbsp;|&nbsp; 📅 {v['publishedAt']}
</span>
</div>
<div class='stat-row'>
  <span class='stat-item'>👁️ 조회수 {v['viewLabel']}</span>
  <span class='stat-item'>👍 좋아요 {v['likeLabel']}</span>
  <span class='stat-item'>💬 댓글 {v['commentLabel']}</span>
</div>
""", unsafe_allow_html=True)

                        if v.get("keywords"):
                            st.markdown("<div class='section-title'>🔑 핵심 키워드</div>", unsafe_allow_html=True)
                            kw_html = "".join(f"<span class='keyword-tag'>{k}</span>" for k in v["keywords"][:12])
                            st.markdown(kw_html, unsafe_allow_html=True)

                        if v.get("summary") and v["summary"] != "(요약 없음)":
                            st.markdown("<div class='section-title'>📋 요약</div>", unsafe_allow_html=True)
                            st.markdown(f"> {v['summary']}")

                        if v.get("tags"):
                            with st.expander(f"🏷️ 태그 ({len(v['tags'])}개)"):
                                st.write("  |  ".join(v["tags"][:20]))

                        if v.get("description"):
                            with st.expander("📝 영상 설명"):
                                st.text(v["description"][:800] + ("..." if len(v["description"])>800 else ""))

    # ── 탭2: 채널 통계 ──────────────────────────────────────
    with tab2:
        st.markdown(f"### 📊 채널별 통계 (총 {len(channel_stats)}개 채널)")
        import pandas as pd
        df_ch = pd.DataFrame([{
            "채널명":     cs["channel"],
            "구독자":     cs["subscriber"],
            "영상수":     cs["videoCount"],
            "총조회수":   fmt(cs["totalView"])+"회",
            "평균조회수": fmt(cs["avgView"])+"회",
            "총좋아요":   fmt(cs["totalLike"]),
            "평균좋아요": fmt(cs["avgLike"]),
            "총댓글":     fmt(cs["totalComment"]),
        } for cs in channel_stats])
        st.dataframe(df_ch, use_container_width=True, hide_index=True)

        st.markdown("### 🏆 채널별 상세")
        for cs in channel_stats[:10]:
            with st.expander(f"📺 {cs['channel']} | 구독자 {cs['subscriber']} | 영상 {cs['videoCount']}개"):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("총조회수",   fmt(cs["totalView"])+"회")
                c2.metric("평균조회수", fmt(cs["avgView"])+"회")
                c3.metric("총좋아요",   fmt(cs["totalLike"]))
                c4.metric("평균댓글",   fmt(cs["avgComment"]))

                st.markdown("**소속 영상:**")
                for v in cs["videos"][:5]:
                    st.markdown(f"- [{v['title']}]({v['url']}) — 조회수 {v['viewLabel']}")

    # ── 탭3: 키워드 분석 ────────────────────────────────────
    with tab3:
        st.markdown("### 🔑 검색어별 키워드 분석")
        for kw, videos in all_results.items():
            with st.expander(f"🔍 [{kw}]  ({len(videos)}개 영상)", expanded=True):
                all_kws = []
                for v in videos:
                    all_kws.extend(v.get("keywords", []))
                if all_kws:
                    counter = Counter(all_kws)
                    top20   = counter.most_common(20)

                    c1, c2 = st.columns([2,1])
                    with c1:
                        st.markdown("**🔝 상위 20개 키워드**")
                        kw_html = "".join(
                            f"<span class='keyword-tag' style='font-size:{0.8+count*0.03:.2f}rem'>{word}({count})</span>"
                            for word, count in top20
                        )
                        st.markdown(kw_html, unsafe_allow_html=True)
                    with c2:
                        st.markdown("**📈 키워드 빈도 TOP 10**")
                        import pandas as pd
                        df_kw = pd.DataFrame(top20[:10], columns=["키워드","빈도"])
                        st.dataframe(df_kw, hide_index=True, use_container_width=True)
                else:
                    st.info("키워드를 추출하려면 자막 가져오기를 체크하고 재검색하세요.")

                avg_v = sum(v["viewCount"] for v in videos) // len(videos) if videos else 0
                max_v = max((v["viewCount"] for v in videos), default=0)
                c1, c2, c3 = st.columns(3)
                c1.metric("영상 수", f"{len(videos)}개")
                c2.metric("평균 조회수", fmt(avg_v)+"회")
                c3.metric("최고 조회수", fmt(max_v)+"회")

    # ── 탭4: 대본 전문 ──────────────────────────────────────
    with tab4:
        st.markdown("### 📜 영상 대본 전문")
        transcript_videos = [
            v for vs in all_results.values()
            for v in vs
            if is_valid_transcript(v.get("transcript", ""))
        ]
        if not transcript_videos:
            st.info("자막이 있는 영상이 없습니다. '자막(대본) 가져오기'를 체크하고 재검색하세요.")
        else:
            st.caption(f"자막 있는 영상: {len(transcript_videos)}개")
            for v in transcript_videos:
                with st.expander(f"📺 {v['title']} — {v['channelTitle']}"):
                    st.markdown(f"🔗 [{v['url']}]({v['url']})")
                    st.markdown(f"**길이:** {v['duration']} | **조회수:** {v['viewLabel']}")
                    st.markdown("---")
                    st.markdown(f'<div class="transcript-box">{v["transcript"]}</div>', unsafe_allow_html=True)
                    st.download_button(
                        label="📄 이 영상 대본 TXT 다운로드",
                        data=v["transcript"].encode("utf-8"),
                        file_name=f"transcript_{v['videoId']}.txt",
                        mime="text/plain",
                        key=f"dl_{v['videoId']}"
                    )


if __name__ == "__main__":
    main()
