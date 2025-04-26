# --- Required Imports ---
import json
from flask import (
    Flask, request, jsonify, Response,
    send_file, abort, after_this_request,
    send_from_directory
)
from flask_cors import CORS
import yt_dlp
import logging
import re
from urllib.parse import urlparse
import os
import tempfile
import shutil
import glob

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = Flask(__name__, static_folder=None)
CORS(app)

# --- Constants ---
SUPPORTED_DOMAINS = [
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com'
]

# --- YouTube Configuration ---
COOKIES_FILE = 'cookies.txt' if os.path.exists('cookies.txt') else None
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.youtube.com/'
}

# --- Helper Functions ---
def is_supported_url(url):
    """Check if the URL is from a supported domain."""
    if not url: return False
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        domain = urlparse(url).netloc.lower()
        return any(domain == supported or ('.' in supported and domain.endswith('.' + supported))
                   for supported in SUPPORTED_DOMAINS)
    except Exception as e:
        logger.error(f"URL parsing/validation error for '{url}': {e}")
        return False

def sanitize_filename(filename):
    """Sanitize the filename to remove invalid characters and limit length."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    sanitized = re.sub(r'[\s_]+', '_', sanitized)
    sanitized = sanitized.strip('_ ')
    sanitized = sanitized[:150]
    if not sanitized: return "downloaded_video"
    return sanitized

def get_ydl_options(request_type='info'):
    """Generate yt-dlp options with anti-bot measures."""
    base_options = {
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'youtube_include_dash_manifest': False,
        'http_headers': DEFAULT_HEADERS,
        'extractor_args': {
            'youtube': {
                'player_client': ['android'],
                'player_skip': ['webpage']
            }
        }
    }
    
    if request_type == 'download':
        base_options.update({
            'merge_output_format': 'mkv',
            'socket_timeout': 60,
            'retries': 3,
            'fragment_retries': 3,
            'skip_unavailable_fragments': True
        })
    else:
        base_options['skip_download'] = True
    
    if COOKIES_FILE:
        base_options['cookiefile'] = COOKIES_FILE
    
    return base_options

def extract_formats(info_dict):
    """Extracts and processes video and audio format information."""
    formats = info_dict.get('formats', [])
    video_formats = []
    audio_formats = []
    best_audio_info = None

    for f in formats:
        if not f.get('url') or not f.get('format_id') or f.get('is_live'):
            continue

        filesize = f.get('filesize') or f.get('filesize_approx')
        size_mb = f"{filesize / (1024 * 1024):.2f} MB" if filesize else "N/A"
        current_abr = f.get('abr')
        quality_note = f.get('format_note', f.get('resolution'))

        # Video Format Processing
        if f.get('vcodec') != 'none' and (f.get('resolution') or f.get('height')):
            height = int(f.get('height', 0)) if str(f.get('height', '0')).isdigit() else 0
            quality_label = f.get('format_note', f.get('resolution', 'Unknown Video'))
            
            video_formats.append({
                "quality": quality_label,
                "resolution": f.get('resolution'),
                "size": size_mb,
                "id": f['format_id'],
                "vcodec": f.get('vcodec'),
                "acodec": f.get('acodec'),
                "ext": f.get('ext', 'mp4'),
                "url": f.get('url'),
                "height": height,
                "fps": f.get('fps'),
                "protocol": f.get('protocol'),
                "filesize": filesize
            })

        # Audio Format Processing
        elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
            quality_label = f.get('format_note')
            if not quality_label and isinstance(current_abr, (int, float)):
                quality_label = f"~{current_abr:.0f}kbps"
            elif not quality_label:
                quality_label = f"Audio ({f.get('acodec', '?')})"

            audio_format_dict = {
                "quality": quality_label,
                "abr": current_abr,
                "size": size_mb,
                "filesize": filesize,
                "id": f['format_id'],
                "acodec": f.get('acodec'),
                "ext": f.get('ext', 'm4a'),
                "url": f.get('url'),
                "protocol": f.get('protocol')
            }
            audio_formats.append(audio_format_dict)

            if isinstance(current_abr, (int, float)):
                if best_audio_info is None or current_abr > best_audio_info.get('abr', -1):
                    best_audio_info = audio_format_dict
            elif best_audio_info is None:
                best_audio_info = audio_format_dict

    # Sorting
    video_formats.sort(key=lambda x: (-x['height'], -(x.get('fps') or 0)))
    audio_formats.sort(key=lambda x: -(x.get('abr') if isinstance(x.get('abr'), (int, float)) else -1))

    final_audio_summary = None
    if best_audio_info:
        final_audio_summary = {
            "quality": f"Audio Only ({best_audio_info.get('quality', 'Best Available')})",
            "size": best_audio_info.get("size", "N/A"),
            "id": best_audio_info['id'],
            "ext": best_audio_info.get('ext', 'm4a')
        }

    return video_formats, audio_formats, final_audio_summary

# --- Static File Serving ---
@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/app.js')
def serve_js():
    return send_from_directory('.', 'app.js')

@app.route('/style.css')
def serve_css():
    return send_from_directory('.', 'style.css')

# --- API Endpoints ---
@app.route('/api/get-formats', methods=['GET'])
def get_formats():
    """API endpoint to fetch video information and available formats."""
    video_url = request.args.get('url')
    if not video_url: return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400
    if not video_url.startswith(('http://', 'https://')):
        video_url = 'https://' + video_url
    if not is_supported_url(video_url):
        return jsonify({"success": False, "error": "Invalid or unsupported URL."}), 400

    logger.info(f"API: Fetching formats for URL: {video_url}")
    
    try:
        with yt_dlp.YoutubeDL(get_ydl_options('info')) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            if not info_dict:
                raise yt_dlp.utils.DownloadError("No video information extracted.")

            video_title = info_dict.get('title', 'Untitled Video')
            thumbnails = info_dict.get('thumbnails', [])
            thumbnail_url = thumbnails[-1]['url'] if thumbnails else info_dict.get('thumbnail', '')
            duration = info_dict.get('duration')
            uploader = info_dict.get('uploader', 'Unknown')
            view_count = info_dict.get('view_count')

            video_formats, audio_formats, best_audio_summary = extract_formats(info_dict)

            if not video_formats and not audio_formats:
                if info_dict.get('is_live'):
                    raise yt_dlp.utils.DownloadError("Live streams cannot be downloaded until finished.")
                raise yt_dlp.utils.DownloadError("No downloadable video or audio formats found.")

            return jsonify({
                "success": True,
                "videoTitle": video_title,
                "thumbnailUrl": thumbnail_url,
                "duration": duration,
                "uploader": uploader,
                "viewCount": view_count,
                "formats": {
                    "video": video_formats,
                    "audio": audio_formats,
                    "bestAudio": best_audio_summary
                }
            })

    except yt_dlp.utils.DownloadError as e:
        error_message = str(e)
        logger.warning(f"API: yt-dlp DownloadError for {video_url}: {error_message}")
        
        # Special handling for bot verification
        if "Sign in to confirm you're not a bot" in error_message:
            logger.info("Attempting retry with different client configuration")
            try:
                retry_options = get_ydl_options('info')
                retry_options['extractor_args']['youtube']['player_client'] = ['ios']
                with yt_dlp.YoutubeDL(retry_options) as ydl:
                    info_dict = ydl.extract_info(video_url, download=False)
                    if info_dict:
                        video_formats, audio_formats, best_audio_summary = extract_formats(info_dict)
                        return jsonify({
                            "success": True,
                            "videoTitle": info_dict.get('title', 'Untitled Video'),
                            "thumbnailUrl": info_dict.get('thumbnail', ''),
                            "duration": info_dict.get('duration'),
                            "uploader": info_dict.get('uploader', 'Unknown'),
                            "viewCount": info_dict.get('view_count'),
                            "formats": {
                                "video": video_formats,
                                "audio": audio_formats,
                                "bestAudio": best_audio_summary
                            },
                            "warning": "Used fallback method to retrieve formats"
                        })
            except Exception as retry_error:
                error_message = "YouTube requires bot verification. Cannot download automatically."
        
        # Convert other technical errors to user-friendly messages
        elif "Unsupported URL" in error_message: error_message = "Invalid or unsupported URL."
        elif "Video unavailable" in error_message: error_message = "This video is unavailable."
        elif "Private video" in error_message: error_message = "Private videos cannot be accessed."
        elif "confirm your age" in error_message: error_message = "Age-restricted video. Cannot download automatically."
        elif "Premiere" in error_message or "live event" in error_message: error_message = "Livestreams/Premieres cannot be downloaded until finished."
        elif "429" in error_message or "Too Many Requests" in error_message: error_message = "Rate limited by YouTube. Please wait and try again later."
        
        return jsonify({"success": False, "error": error_message}), 400

    except Exception as e:
        logger.exception(f"API: Unexpected server error processing URL {video_url}: {e}")
        return jsonify({"success": False, "error": "An unexpected server error occurred while fetching formats."}), 500

@app.route('/api/download', methods=['GET'])
def download_video():
    """Downloads/merges video/audio using yt-dlp with anti-bot measures."""
    video_url = request.args.get('url')
    format_id = request.args.get('format_id')
    filename_in = request.args.get('filename', 'video')

    if not video_url or not format_id:
        return jsonify({"success": False, "error": "Missing parameters"}), 400
    if not video_url.startswith(('http://', 'https://')):
        video_url = 'https://' + video_url

    logger.info(f"API: Server download request - URL: {video_url}, Format: {format_id}")

    temp_dir_path = None
    try:
        temp_dir_path = tempfile.mkdtemp()
        logger.info(f"API: Created temporary directory: {temp_dir_path}")

        # Get format info with anti-bot options
        with yt_dlp.YoutubeDL(get_ydl_options('info')) as ydl_info:
            info_dict = ydl_info.extract_info(video_url, download=False)
            formats = info_dict.get('formats', [])
            selected_format = next((f for f in formats if f.get('format_id') == format_id), None)
            if not selected_format:
                raise yt_dlp.utils.DownloadError(f"Format ID {format_id} not found.")

        needs_audio_merge = selected_format.get('vcodec') != 'none' and selected_format.get('acodec') == 'none'
        actual_ext = 'mkv' if needs_audio_merge else selected_format.get('ext', 'mp4')
        final_format_selector = f"{format_id}+bestaudio/bestaudio" if needs_audio_merge else format_id

        safe_base_filename = sanitize_filename(os.path.splitext(filename_in)[0])
        final_filename = f"{safe_base_filename}.{actual_ext}"
        temp_output_template = os.path.join(temp_dir_path, f"download_temp.%(ext)s")

        download_options = get_ydl_options('download')
        download_options.update({
            'format': final_format_selector,
            'outtmpl': temp_output_template,
            'merge_output_format': actual_ext if needs_audio_merge else None,
        })

        logger.info(f"API: Starting download with options: {download_options}")
        with yt_dlp.YoutubeDL(download_options) as ydl_down:
            ydl_down.download([video_url])

        downloaded_files = glob.glob(os.path.join(temp_dir_path, "download_temp.*"))
        if not downloaded_files:
            abort(500, description="Server error: Output file missing after download.")
        actual_temp_filepath = downloaded_files[0]

        final_filepath = os.path.join(temp_dir_path, final_filename)
        os.rename(actual_temp_filepath, final_filepath)

        response = send_file(
            final_filepath,
            as_attachment=True,
            download_name=final_filename
        )
        response.headers['X-Accel-Buffering'] = 'no'

        @after_this_request
        def cleanup(response):
            if temp_dir_path and os.path.exists(temp_dir_path):
                try:
                    shutil.rmtree(temp_dir_path)
                except Exception as e:
                    logger.error(f"API: Error during cleanup: {e}")
            return response

        return response

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "Sign in to confirm you're not a bot" in error_msg:
            error_msg = "YouTube requires bot verification. Cannot download automatically."
        logger.error(f"API: Download failed: {error_msg}")
        if temp_dir_path and os.path.exists(temp_dir_path):
            shutil.rmtree(temp_dir_path)
        return jsonify({"success": False, "error": error_msg}), 500

    except Exception as e:
        logger.exception(f"API: Critical error during download: {e}")
        if temp_dir_path and os.path.exists(temp_dir_path):
            shutil.rmtree(temp_dir_path)
        return jsonify({"success": False, "error": "Server download failed unexpectedly."}), 500

@app.route('/api/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

# --- Run the Flask App ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)