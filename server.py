"""
VaultDL - Backend Server
Run: pip install flask flask-cors yt-dlp && python server.py
"""

import os
import threading
import uuid
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# In-memory progress tracker
progress_store = {}

# ── Common yt-dlp options to bypass 403 ──────────────────────────────
COMMON_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    
    # 👇 Agar aapne cookies.txt file upload ki hai, toh niche wali line ke aage se '#' hata dein
    # 'cookiefile': 'cookies.txt', 
    
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-us,en;q=0.5',
        'Sec-Fetch-Mode': 'navigate',
    },
    'extractor_args': {
        'youtube': {
            # 👇 Naye player clients jo 403 Forbidden ko bypass karte hain
            'player_client': ['web_embedded', 'tv', 'web'],
        }
    },
}


@app.route('/api/info', methods=['GET'])
def get_info():
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    try:
        opts = {**COMMON_OPTS, 'skip_download': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen = set()

        # Video formats
        for f in info.get('formats', []):
            if f.get('vcodec') != 'none' and f.get('height'):
                height = f.get('height')
                label = f"{height}p"
                if label not in seen:
                    seen.add(label)
                    formats.append({
                        'format_id': f['format_id'],
                        'label': label,
                        'type': 'video',
                        'ext': f.get('ext', 'mp4'),
                        'filesize': f.get('filesize') or f.get('filesize_approx'),
                        'fps': f.get('fps'),
                    })

        # Sort video by quality descending
        formats = sorted(formats, key=lambda x: int(x['label'].replace('p', '')), reverse=True)

        # Add Best Video option at top
        formats.insert(0, {
            'format_id': 'bestvideo+bestaudio/best',
            'label': 'Best Quality',
            'type': 'video',
            'ext': 'mp4'
        })

        # Audio formats
        audio_formats = [
            {'format_id': 'bestaudio/best', 'label': '320 kbps (Best)', 'type': 'audio', 'ext': 'mp3'},
            {'format_id': 'bestaudio[abr<=192]', 'label': '192 kbps', 'type': 'audio', 'ext': 'mp3'},
            {'format_id': 'bestaudio[abr<=128]', 'label': '128 kbps', 'type': 'audio', 'ext': 'mp3'},
        ]

        return jsonify({
            'title': info.get('title', 'Unknown Title'),
            'thumbnail': info.get('thumbnail', ''),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader', ''),
            'platform': info.get('extractor_key', 'Unknown'),
            'view_count': info.get('view_count', 0),
            'formats': formats,
            'audio_formats': audio_formats,
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.get_json()
    url = data.get('url', '').strip()
    format_id = data.get('format_id', 'bestvideo+bestaudio/best')
    media_type = data.get('type', 'video')

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    task_id = str(uuid.uuid4())
    progress_store[task_id] = {'status': 'starting', 'percent': 0, 'filename': None, 'error': None}

    def do_download():
        output_template = os.path.join(DOWNLOAD_DIR, f"{task_id}_%(title)s.%(ext)s")

        def progress_hook(d):
            if d['status'] == 'downloading':
                pct_str = d.get('_percent_str', '0%').strip().replace('%', '')
                try:
                    pct = float(pct_str)
                except Exception:
                    pct = 0
                progress_store[task_id]['status'] = 'downloading'
                progress_store[task_id]['percent'] = pct
            elif d['status'] == 'finished':
                progress_store[task_id]['status'] = 'processing'
                progress_store[task_id]['percent'] = 99

        if media_type == 'audio':
            ydl_opts = {
                **COMMON_OPTS,
                'format': format_id,
                'outtmpl': output_template,
                'progress_hooks': [progress_hook],
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '320',
                }],
            }
        else:
            ydl_opts = {
                **COMMON_OPTS,
                'format': format_id,
                'outtmpl': output_template,
                'progress_hooks': [progress_hook],
                'merge_output_format': 'mp4',
            }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Find the downloaded file
            files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(task_id)]
            if files:
                progress_store[task_id]['status'] = 'done'
                progress_store[task_id]['percent'] = 100
                progress_store[task_id]['filename'] = files[0]
            else:
                progress_store[task_id]['status'] = 'error'
                progress_store[task_id]['error'] = 'File not found after download'
        except Exception as e:
            progress_store[task_id]['status'] = 'error'
            progress_store[task_id]['error'] = str(e)

    thread = threading.Thread(target=do_download, daemon=True)
    thread.start()

    return jsonify({'task_id': task_id})


@app.route('/api/progress/<task_id>', methods=['GET'])
def get_progress(task_id):
    info = progress_store.get(task_id, {'status': 'unknown', 'percent': 0})
    return jsonify(info)


@app.route('/api/file/<task_id>', methods=['GET'])
def download_file(task_id):
    info = progress_store.get(task_id)
    if not info or not info.get('filename'):
        return jsonify({'error': 'File not ready'}), 404

    filepath = os.path.join(DOWNLOAD_DIR, info['filename'])
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found on disk'}), 404

    clean_name = info['filename'].replace(f"{task_id}_", "", 1)
    return send_file(filepath, as_attachment=True, download_name=clean_name)


@app.route('/api/cleanup/<task_id>', methods=['DELETE'])
def cleanup(task_id):
    info = progress_store.get(task_id)
    if info and info.get('filename'):
        filepath = os.path.join(DOWNLOAD_DIR, info['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
    progress_store.pop(task_id, None)
    return jsonify({'ok': True})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 VaultDL Server running on port {port}")
    print("📁 Downloads folder:", DOWNLOAD_DIR)
    app.run(debug=False, host='0.0.0.0', port=port)

