from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, Response, JSONResponse
import os
import shutil
import subprocess
import uuid
import json

app = FastAPI()

UPLOAD_DIR = "uploads"
HLS_DIR = "hls"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(HLS_DIR, exist_ok=True)

ffmpeg_path = "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"
# ffprobe_path = "ffprobe"

@app.post("/upload/")
async def upload_video(file: UploadFile = File(...)):
    video_id = str(uuid.uuid4())
    input_path = os.path.join(UPLOAD_DIR, f"{video_id}_{file.filename}")
    
    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    output_dir = os.path.join(HLS_DIR, video_id)
    os.makedirs(output_dir, exist_ok=True)

    p240 = os.path.join(output_dir, "240p.m3u8")
    p360 = os.path.join(output_dir, "360p.m3u8")
    p480 = os.path.join(output_dir, "480p.m3u8")
    master = os.path.join(output_dir, "master.m3u8")

    # Check for audio stream
    try:
        probe_cmd = [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "json",
            input_path
        ]
        result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        has_audio = bool(json.loads(result.stdout).get("streams"))
    except Exception:
        has_audio = False

    def audio_map_args():
        return ["-map", "a", "-c:a", "aac"] if has_audio else []

    command = [
        ffmpeg_path,
        "-i", input_path,
        "-filter_complex",
        "[0:v]split=3[v1][v2][v3];"
        "[v1]scale=w=854:h=480[v1out];"
        "[v2]scale=w=640:h=360[v2out];"
        "[v3]scale=w=426:h=240[v3out]",

        "-map", "[v1out]", *audio_map_args(), "-c:v", "h264", "-b:v", "1400k", "-f", "hls",
        "-hls_time", "5", "-hls_playlist_type", "vod",
        "-hls_segment_filename", os.path.join(output_dir, "480p_%03d.ts"), p480,

        "-map", "[v2out]", *audio_map_args(), "-c:v", "h264", "-b:v", "800k", "-f", "hls",
        "-hls_time", "5", "-hls_playlist_type", "vod",
        "-hls_segment_filename", os.path.join(output_dir, "360p_%03d.ts"), p360,

        "-map", "[v3out]", *audio_map_args(), "-c:v", "h264", "-b:v", "400k", "-f", "hls",
        "-hls_time", "5", "-hls_playlist_type", "vod",
        "-hls_segment_filename", os.path.join(output_dir, "240p_%03d.ts"), p240
    ]

    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"FFmpeg error: {e.stderr}")

    with open(master, "w") as f:
        f.write("#EXTM3U\n")
        f.write("#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=426x240\n240p.m3u8\n")
        f.write("#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n360p.m3u8\n")
        f.write("#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480\n480p.m3u8\n")

    return {
        "video_id": video_id,
        "master_url": f"/hls/{video_id}/master.m3u8"
    }

from fastapi import Request

@app.get("/videos")
def list_hls_videos(request: Request):
    try:
        video_ids = [d for d in os.listdir(HLS_DIR) if os.path.isdir(os.path.join(HLS_DIR, d))]
        videos = []
        base_url = str(request.url).replace(str(request.url.path), "")
        
        for video_id in video_ids:
            master_path = os.path.join(HLS_DIR, video_id, "master.m3u8")
            if os.path.exists(master_path):
                videos.append({
                    "video_id": video_id,
                    "master_url": f"{base_url}/hls/{video_id}/master.m3u8"
                })
        return JSONResponse(content=videos)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/hls/{video_id}/{filename}")
def serve_hls(video_id: str, filename: str):
    file_path = os.path.join(HLS_DIR, video_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    if filename.endswith(".m3u8"):
        return Response(content=open(file_path, "rb").read(), media_type="application/vnd.apple.mpegurl")
    elif filename.endswith(".ts"):
        return Response(content=open(file_path, "rb").read(), media_type="video/MP2T")
    else:
        return FileResponse(file_path)
