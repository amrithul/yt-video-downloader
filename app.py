# --- Required Imports ---
import json
from flask import (
    Flask, request, jsonify, Response,
    send_file, abort, after_this_request,
    send_from_directory # Required for serving static files
)
from flask_cors import CORS
import yt_dlp
import logging
import re
from urllib.parse import urlparse
import os
import tempfile # Using mkdtemp now
import shutil # Needed for manual rmtree
import glob # For finding temp file

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# Disable default static folder handling as we serve manually
app = Flask(__name__, static_folder=None)
CORS(app) # Allow cross-origin requests

# --- Constants ---
# Includes support for all discussed domains
SUPPORTED_DOMAINS = [
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com',
    'https://youtu.be/rgKf6eVtdZU?si=WH0uTSy0I9lkw3lg',
    # Add standard domains just in case parsing differs slightly
    'youtube.com', 'm.youtu.be', 'm.youtube.com'
]

# --- Helper Functions ---
def is_supported_url(url):
    """Check if the URL is from a supported domain."""
    if not url: return False
    try:
        # Ensure URL has a scheme for parsing
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        domain = urlparse(url).netloc.lower()
        # Check if the domain exactly matches or ends with a supported one (for subdomains)
        return any(domain == supported or ('.' in supported and domain.endswith('.' + supported))
                   for supported in SUPPORTED_DOMAINS)
    except Exception as e:
        logger.error(f"URL parsing/validation error for '{url}': {e}")
        return False

def sanitize_filename(filename):
    """Sanitize the filename to remove invalid characters and limit length."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename) # Remove invalid characters
    sanitized = re.sub(r'[\s_]+', '_', sanitized) # Replace whitespace/multiple underscores with single underscore
    sanitized = sanitized.strip('_ ') # Remove leading/trailing whitespace/underscores
    sanitized = sanitized[:150] # Limit filename length
    if not sanitized: return "downloaded_video" # Provide default if empty
    return sanitized

# --- Format Extraction Function (Robust Version) ---
def extract_formats(info_dict):
    """
    Extracts, processes, and sorts video and audio format information
    from the dictionary provided by yt-dlp.
    """
    formats = info_dict.get('formats', [])
    video_formats = []
    audio_formats = []
    best_audio_info = None # To track the best audio-only stream found

    for f in formats:
        # Skip if essential info is missing or if it's a live stream (cannot download directly)
        if not f.get('url') or not f.get('format_id') or f.get('is_live'):
            continue

        # Get file size information
        filesize = f.get('filesize') or f.get('filesize_approx')
        # Format size into human-readable string (e.g., "123.45 MB")
        size_mb = f"{filesize / (1024 * 1024):.2f} MB" if filesize else "N/A"

        current_abr = f.get('abr') # Average audio bitrate (can be None)
        # Use specific format note (e.g., '1080p') or resolution as quality label
        quality_note = f.get('format_note', f.get('resolution'))

        # --- Video Format Processing ---
        # Check if it has video codec and resolution/height information
        if f.get('vcodec') != 'none' and (f.get('resolution') or f.get('height')):
            height = 0 # Initialize height
            try: height = int(f.get('height', 0)) # Get numeric height for sorting
            except (ValueError, TypeError): pass # Ignore if height is not a number

            # Determine the display quality label
            quality_label = f.get('format_note', f.get('resolution', 'Unknown Video'))

            # Append relevant video format details to the list
            video_formats.append({
                "quality": quality_label,           # e.g., "1080p", "1920x1080"
                "resolution": f.get('resolution'),  # e.g., "1920x1080"
                "size": size_mb,                    # e.g., "123.45 MB"
                "id": f['format_id'],               # Internal ID (e.g., "299")
                "vcodec": f.get('vcodec'),          # e.g., "vp09", "avc1.640028"
                "acodec": f.get('acodec'),          # Audio codec (e.g., "opus", "mp4a.40.2", "none")
                "ext": f.get('ext', 'mp4'),         # File extension (e.g., "mp4", "webm")
                "url": f.get('url'),                # Direct URL (might expire)
                "height": height,                   # Numeric height for sorting
                "fps": f.get('fps'),                # Frames per second (e.g., 60)
                "protocol": f.get('protocol'),      # Network protocol (e.g., "https", "m3u8")
                "filesize": filesize                # Raw filesize in bytes (optional)
            })

        # --- Audio Format Processing ---
        # Check if it has audio codec but *no* video codec
        elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
             # Determine display quality label for audio
             quality_label = f.get('format_note') # Prefer specific note if available
             # If no note, use bitrate if available
             if not quality_label and isinstance(current_abr, (int, float)):
                 quality_label = f"~{current_abr:.0f}kbps"
             # Fallback to just mentioning the codec
             elif not quality_label:
                 quality_label = f"Audio ({f.get('acodec', '?')})"

             # Create dictionary for this audio format
             audio_format_dict = {
                "quality": quality_label,           # e.g., "~128kbps", "Audio (opus)"
                "abr": current_abr,                 # Average bitrate (numeric, can be None)
                "size": size_mb,                    # e.g., "10.50 MB"
                "filesize": filesize,               # Raw filesize in bytes (optional)
                "id": f['format_id'],               # Internal ID (e.g., "140")
                "acodec": f.get('acodec'),          # e.g., "opus", "mp4a.40.2"
                "ext": f.get('ext', 'm4a'),         # File extension (e.g., "m4a", "webm", "opus")
                "url": f.get('url'),                # Direct URL (might expire)
                "protocol": f.get('protocol')       # Network protocol
             }
             audio_formats.append(audio_format_dict)

             # --- Logic to find the best quality audio-only stream ---
             if isinstance(current_abr, (int, float)):
                best_abr_so_far = -1
                if best_audio_info and isinstance(best_audio_info.get('abr'), (int, float)): best_abr_so_far = best_audio_info.get('abr', -1)
                if current_abr > best_abr_so_far: best_audio_info = audio_format_dict
             elif best_audio_info is None: best_audio_info = audio_format_dict
             # --- End of best audio logic ---

    # --- Sorting ---
    video_formats.sort(key=lambda x: (-x['height'], -(x.get('fps') or 0)));
    audio_formats.sort(key=lambda x: -(x.get('abr') if isinstance(x.get('abr'), (int, float)) else -1))

    # --- Prepare Best Audio Summary ---
    final_audio_summary = None
    if best_audio_info:
        final_audio_summary = {
            "quality": f"Audio Only ({best_audio_info.get('quality', 'Best Available')})",
            "size": best_audio_info.get("size", "N/A"),
            "id": best_audio_info['id'],
            "ext": best_audio_info.get('ext', 'm4a')
        }
    return video_formats, audio_formats, final_audio_summary


# --- Static File Serving Routes ---
@app.route('/')
def serve_index(): logger.info("Serving index.html"); return send_from_directory('.', 'index.html')
@app.route('/app.js')
def serve_js(): logger.info("Serving app.js"); return send_from_directory('.', 'app.js')
@app.route('/style.css')
def serve_css(): logger.info("Serving style.css"); return send_from_directory('.', 'style.css')

# --- API Endpoints ---
@app.route('/api/get-formats', methods=['GET'])
def get_formats():
    video_url = request.args.get('url')
    if not video_url: return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400
    if not video_url.startswith(('http://', 'https://')): video_url = 'https://' + video_url
    if not is_supported_url(video_url): return jsonify({"success": False, "error": "Invalid or unsupported URL."}), 400

    logger.info(f"API: Fetching formats for URL: {video_url}")

    # --- Updated yt-dlp options for get-formats (Bot Bypass Attempts) ---
    ydl_opts = {
        'noplaylist': True, 'quiet': True, 'no_warnings': True, 'skip_download': True,
        'extract_flat': False, 'youtube_include_dash_manifest': False,
        # Mimic Browser/Mobile Headers & Client
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'extractor_args': {
            'youtube': { 'player_client': ['android'], 'player_skip': ['webpage', 'configs'] }
        },
        'referer': 'https://www.youtube.com/',  # Example Referer
        # 'cookiefile': 'cookies.txt', # Requires secure handling if implemented
    }
    # --- End of updated options ---

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            if not info_dict: raise yt_dlp.utils.DownloadError("No video information extracted.")
            video_title = info_dict.get('title', 'Untitled Video'); thumbnails = info_dict.get('thumbnails', []); thumbnail_url = thumbnails[-1]['url'] if thumbnails else info_dict.get('thumbnail', ''); duration = info_dict.get('duration'); uploader = info_dict.get('uploader', 'Unknown'); view_count = info_dict.get('view_count')
            video_formats, audio_formats, best_audio_summary = extract_formats(info_dict)
            if not video_formats and not audio_formats:
                if info_dict.get('is_live'): raise yt_dlp.utils.DownloadError("Live streams cannot be downloaded yet.")
                raise yt_dlp.utils.DownloadError("No downloadable formats found.")
            return jsonify({ "success": True, "videoTitle": video_title, "thumbnailUrl": thumbnail_url, "duration": duration, "uploader": uploader, "viewCount": view_count, "formats": { "video": video_formats, "audio": audio_formats, "bestAudio": best_audio_summary } })
    except yt_dlp.utils.DownloadError as e:
        error_message = str(e); logger.warning(f"API: yt-dlp DownloadError for {video_url}: {error_message}")
        # --- Updated Error Handling for Bot Detection ---
        if "Sign in to confirm" in error_message or "confirm your age" in error_message:
             error_message = "YouTube requires login/verification for this video. Cannot download automatically via server. Try locally with cookies."
        # --- End of Update ---
        elif "Unsupported URL" in error_message: error_message = "Invalid or unsupported URL."
        elif "Video unavailable" in error_message: error_message = "This video is unavailable."
        elif "Private video" in error_message: error_message = "Private videos cannot be accessed."
        elif "Premiere" in error_message or "live event" in error_message: error_message = "Livestreams/Premieres not ready."
        elif "429" in error_message: error_message = "Rate limited. Please wait and try again."
        else: error_message = "Could not fetch video data (may be region locked, deleted, bot detection, etc)."
        return jsonify({"success": False, "error": error_message}), 400
    except Exception as e:
        logger.exception(f"API: Unexpected server error processing URL {video_url}: {e}")
        return jsonify({"success": False, "error": "An unexpected server error occurred while fetching formats."}), 500


@app.route('/api/download', methods=['GET'])
def download_video():
    """Downloads/merges video/audio using yt-dlp on the server, renames, sends file, then cleans up temp dir."""
    video_url = request.args.get('url')
    format_id = request.args.get('format_id')
    filename_in = request.args.get('filename', 'video')
    if not video_url or not format_id: return jsonify({"success": False, "error": "Missing parameters"}), 400
    if not video_url.startswith(('http://', 'https://')): video_url = 'https://' + video_url
    logger.info(f"API: Server download request - URL: {video_url}, Format: {format_id}, Base Filename: '{filename_in}'")
    temp_dir_path = None
    try:
        temp_dir_path = tempfile.mkdtemp(); logger.info(f"API: Created temporary directory: {temp_dir_path}")
        # --- Step 1: Get format info ---
        info_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        selected_format_info = None; needs_audio_merge = False; actual_ext = 'mp4'; final_format_selector = format_id
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
             info_dict = ydl_info.extract_info(video_url, download=False)
             formats = info_dict.get('formats', []);
             for f in formats:
                 if f.get('format_id') == format_id: selected_format_info = f; break
        if not selected_format_info: raise yt_dlp.utils.DownloadError(f"Format ID {format_id} not found.")
        actual_ext = selected_format_info.get('ext', 'mp4')
        if selected_format_info.get('vcodec') != 'none' and selected_format_info.get('acodec') == 'none':
            needs_audio_merge = True; final_format_selector = f"{format_id}+bestaudio/bestaudio"; actual_ext = 'mkv'
            logger.info(f"API: Format {format_id} needs merge. Target ext: .{actual_ext}.")
        else: logger.info(f"API: Format {format_id} direct. Original ext: .{actual_ext}.")

        # --- Step 2: Prepare names & options ---
        safe_base_filename = sanitize_filename(os.path.splitext(filename_in)[0])
        final_filename = f"{safe_base_filename}.{actual_ext}"
        temp_output_template = os.path.join(temp_dir_path, f"download_temp.%(ext)s")

        # --- ** Updated yt-dlp options for download (Bot Bypass Attempts) ** ---
        ydl_opts_download = {
            'format': final_format_selector,
            'outtmpl': temp_output_template,
            'noplaylist': True,
            # 'quiet': True, # Keep commented for debugging merge
            'no_warnings': True,
            'merge_output_format': actual_ext if needs_audio_merge else None,
            'socket_timeout': 60,
            # Mimic Browser/Mobile Headers & Client
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
            },
            'extractor_args': {
                'youtube': { 'player_client': ['android'], 'player_skip': ['webpage', 'configs'] }
            },
            'referer': 'https://www.youtube.com/', 
            # 'cookiefile': 'cookies.txt', # Keep commented out
            # 'ffmpeg_location': '/path/to/ffmpeg', # Optional
        }
        # --- ** End of updated options ** ---

        # --- Step 3: Execute Download ---
        logger.info(f"API: Starting yt-dlp process for {final_filename}..."); logger.info(f"API: yt-dlp options: {json.dumps(ydl_opts_download)}")
        try:
            with yt_dlp.YoutubeDL(ydl_opts_download) as ydl_down: ydl_down.download([video_url])
            logger.info(f"API: yt-dlp process finished successfully to temp dir.")
        except Exception as download_exc:
            logger.error(f"API: yt-dlp download/merge failed: {download_exc}")
            if needs_audio_merge and ('ffmpeg' in str(download_exc).lower()): abort(500, description="Download failed: FFmpeg error/missing.")
            elif "Sign in to confirm" in str(download_exc): abort(400, description="YouTube requires bot verification during download. Try locally with cookies.") # Check here too
            else: abort(500, description=f"Download failed: {download_exc}")

        # --- Step 4: Find temp file ---
        search_pattern = os.path.join(temp_dir_path, "download_temp.*"); downloaded_files = glob.glob(search_pattern)
        if not downloaded_files: abort(500, description="Output file missing after download.")
        actual_temp_filepath = downloaded_files[0]; logger.info(f"API: Found temp file: {actual_temp_filepath}")

        # --- Step 5: Rename temp file ---
        final_filepath = os.path.join(temp_dir_path, final_filename)
        try:
            os.rename(actual_temp_filepath, final_filepath); logger.info(f"API: Renamed temp file to: {final_filepath}")
        except OSError as rename_err: abort(500, description=f"Failed to rename temp file: {rename_err}")

        # --- Step 6: Prepare Response and Schedule Cleanup ---
        logger.info(f"API: Preparing to send file: {final_filename}")
        if not os.path.exists(final_filepath): abort(500, description="Final file disappeared before sending.")
        response = send_file( final_filepath, as_attachment=True, download_name=final_filename ); response.headers['X-Accel-Buffering'] = 'no'

        @after_this_request
        def cleanup(response):
            tdir = temp_dir_path
            try:
                if tdir and os.path.exists(tdir): logger.info(f"API: Deferred cleanup: Removing temp dir {tdir}"); shutil.rmtree(tdir); logger.info(f"API: Deferred cleanup: Successfully removed {tdir}")
                else: logger.info(f"API: Deferred cleanup: Temp dir '{tdir}' already gone or invalid.")
            except Exception as e: logger.error(f"API: Error during deferred cleanup of {tdir}: {e}")
            return response
        return response

    except Exception as e:
        logger.exception(f"API: Critical error during download process for {video_url}: {e}")
        if temp_dir_path and os.path.exists(temp_dir_path):
             try: logger.warning(f"API: Cleaning up temp dir {temp_dir_path} due to critical error: {e}"); shutil.rmtree(temp_dir_path)
             except Exception as cleanup_err: logger.error(f"API: Error cleaning up temp dir after critical error: {cleanup_err}")
        if isinstance(e, yt_dlp.utils.DownloadError): return jsonify({"success": False, "error": f"Download preparation failed: {e}"}), 500
        elif hasattr(e, 'description') and hasattr(e, 'code') and isinstance(e.code, int): return jsonify({"success": False, "error": e.description}), e.code
        else: return jsonify({"success": False, "error": "Server download failed unexpectedly."}), 500

# --- Health Check ---
@app.route('/api/health')
def health_check():
    """Simple health check endpoint."""
    return jsonify({"status": "healthy"}), 200

# --- Run App ---
if __name__ == '__main__':
    # Use port defined by environment variable PORT (used by Render), default to 5000 locally
    port = int(os.environ.get('PORT', 5000))
    # Set debug=False for production deployment on Render
    app.run(debug=False, host='0.0.0.0', port=port)
