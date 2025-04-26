# --- Required Imports ---
import json
from flask import (
    Flask, request, jsonify, Response,
    send_file, abort, after_this_request # Ensure all needed Flask components are imported
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
app = Flask(__name__)
CORS(app) # Allow requests from frontend

# --- Constants ---
# Includes support for youtube.com/watch?v=... and youtu.be/... domains
# Also includes previously added domains
SUPPORTED_DOMAINS = [
    'youtube.com', 'www.youtube.com', 'm.youtube.com',
    'youtu.be', 'youtube-nocookie.com', 'www.youtube-nocookie.com',
    'https://youtu.be/rgKf6eVtdZU?si=WH0uTSy0I9lkw3lg', # Added previously
    # Standard YouTube Domains
    'youtube.com',
    'm.youtu.be',
    'm.youtube.com'
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
        # Exact match or subdomain check
        return any(domain == supported or domain.endswith('.' + supported)
                   for supported in SUPPORTED_DOMAINS)
    except Exception as e:
        logger.error(f"URL parsing/validation error for '{url}': {e}")
        return False

def sanitize_filename(filename):
    """Sanitize the filename to remove invalid characters and limit length."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename) # Remove invalid chars
    sanitized = re.sub(r'[\s_]+', '_', sanitized) # Replace whitespace/multiple underscores
    sanitized = sanitized.strip('_ ') # Remove leading/trailing whitespace/underscores
    sanitized = sanitized[:150] # Limit length
    if not sanitized: return "downloaded_video" # Provide default if empty
    return sanitized

# --- Format Extraction Function (Robust Version) ---
def extract_formats(info_dict):
    """Extracts, processes, and sorts video and audio format information."""
    formats = info_dict.get('formats', [])
    video_formats = []; audio_formats = []; best_audio_info = None
    for f in formats:
        # Essential checks
        if not f.get('url') or not f.get('format_id'): continue
        # Skip live streams for direct download options
        if f.get('is_live'): continue

        filesize = f.get('filesize') or f.get('filesize_approx')
        size_mb = f"{filesize / (1024 * 1024):.2f} MB" if filesize else "N/A"
        current_abr = f.get('abr'); quality_note = f.get('format_note', f.get('resolution'))

        # Video Format Processing
        if f.get('vcodec') != 'none' and (f.get('resolution') or f.get('height')):
            height = 0;
            try: height = int(f.get('height', 0)); 
            except: pass
            quality_label = f.get('format_note', f.get('resolution', 'Unknown Video'))
            video_formats.append({
                "quality": quality_label, "resolution": f.get('resolution'), "size": size_mb,
                "id": f['format_id'], "vcodec": f.get('vcodec'), "acodec": f.get('acodec'),
                "ext": f.get('ext', 'mp4'), "url": f.get('url'), "height": height,
                "fps": f.get('fps'), "protocol": f.get('protocol'), "filesize": filesize # Pass raw filesize if needed
            })
        # Audio Format Processing
        elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
             quality_label = f.get('format_note');
             if not quality_label and isinstance(current_abr, (int, float)): quality_label = f"~{current_abr:.0f}kbps"
             elif not quality_label: quality_label = f"Audio ({f.get('acodec', '?')})"
             audio_format_dict = {
                "quality": quality_label, "abr": current_abr, "size": size_mb,
                "filesize": filesize, "id": f['format_id'], "acodec": f.get('acodec'),
                "ext": f.get('ext', 'm4a'), "url": f.get('url'), "protocol": f.get('protocol')
             }
             audio_formats.append(audio_format_dict)
             # Logic to find best audio
             if isinstance(current_abr, (int, float)):
                best_abr_so_far = -1
                if best_audio_info and isinstance(best_audio_info.get('abr'), (int, float)): best_abr_so_far = best_audio_info.get('abr', -1)
                if current_abr > best_abr_so_far: best_audio_info = audio_format_dict
             elif best_audio_info is None: best_audio_info = audio_format_dict
    # Sorting
    video_formats.sort(key=lambda x: (-x['height'], -(x.get('fps') or 0)));
    audio_formats.sort(key=lambda x: -(x.get('abr') if isinstance(x.get('abr'), (int, float)) else -1))
    # Best Audio Summary
    final_audio_summary = None
    if best_audio_info: final_audio_summary = { "quality": f"Audio Only ({best_audio_info.get('quality', 'Best')})", "size": best_audio_info.get("size", "N/A"), "id": best_audio_info['id'], "ext": best_audio_info.get('ext', 'm4a') }
    return video_formats, audio_formats, final_audio_summary

# --- API Endpoints ---
@app.route('/api/get-formats', methods=['GET'])
def get_formats():
    """API endpoint to fetch video information and available formats."""
    video_url = request.args.get('url')
    if not video_url: return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400
    if not video_url.startswith(('http://', 'https://')): video_url = 'https://' + video_url
    # Use the updated validation logic
    if not is_supported_url(video_url): return jsonify({"success": False, "error": "Invalid or unsupported URL."}), 400

    logger.info(f"Fetching formats for URL: {video_url}")
    ydl_opts = { 'noplaylist': True, 'quiet': True, 'no_warnings': True, 'skip_download': True, 'extract_flat': False, 'youtube_include_dash_manifest': False }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            if not info_dict: raise yt_dlp.utils.DownloadError("No video information extracted.")

            video_title = info_dict.get('title', 'Untitled Video')
            thumbnails = info_dict.get('thumbnails', [])
            thumbnail_url = thumbnails[-1]['url'] if thumbnails else info_dict.get('thumbnail', '') # Get best quality thumb
            duration = info_dict.get('duration'); uploader = info_dict.get('uploader', 'Unknown'); view_count = info_dict.get('view_count')

            video_formats, audio_formats, best_audio_summary = extract_formats(info_dict)

            # Check if *any* downloadable formats were found after extraction
            if not video_formats and not audio_formats:
                # Check if it was perhaps a live stream that hasn't finished processing
                if info_dict.get('is_live'): raise yt_dlp.utils.DownloadError("Live streams cannot be downloaded until finished.")
                # Otherwise, truly no formats available
                raise yt_dlp.utils.DownloadError("No downloadable video or audio formats found.")

            return jsonify({
                "success": True, "videoTitle": video_title, "thumbnailUrl": thumbnail_url,
                "duration": duration, "uploader": uploader, "viewCount": view_count,
                "formats": { "video": video_formats, "audio": audio_formats, "bestAudio": best_audio_summary }
            })

    except yt_dlp.utils.DownloadError as e:
        error_message = str(e); logger.warning(f"yt-dlp DownloadError for {video_url}: {error_message}")
        # Convert common errors to user-friendly messages
        if "Unsupported URL" in error_message: error_message = "Invalid or unsupported URL."
        elif "Video unavailable" in error_message: error_message = "This video is unavailable."
        elif "Private video" in error_message: error_message = "Private videos cannot be accessed."
        elif "confirm your age" in error_message: error_message = "Age-restricted video."
        elif "Premiere" in error_message or "live event" in error_message: error_message = "Livestreams/Premieres not ready."
        elif "429" in error_message or "Too Many Requests" in error_message: error_message = "Rate limited by YouTube. Please wait and try again later."
        else: error_message = "Could not fetch video data (may be region locked, deleted, etc)."
        return jsonify({"success": False, "error": error_message}), 400 # Use 400 for client-side type errors

    except Exception as e:
        logger.exception(f"Unexpected server error processing URL {video_url}: {e}")
        return jsonify({"success": False, "error": "An unexpected server error occurred."}), 500


# --- ** Final /api/download Route (Server-Side Merge + Deferred Cleanup) ** ---
@app.route('/api/download', methods=['GET'])
def download_video():
    """Downloads/merges video/audio using yt-dlp on the server, renames, sends file, then cleans up temp dir."""
    video_url = request.args.get('url')
    format_id = request.args.get('format_id')
    filename_in = request.args.get('filename', 'video') # Use filename suggested by frontend

    if not video_url or not format_id: return jsonify({"success": False, "error": "Missing parameters"}), 400
    if not video_url.startswith(('http://', 'https://')): video_url = 'https://' + video_url

    logger.info(f"Server download request - URL: {video_url}, Format: {format_id}, Base Filename: '{filename_in}'")

    # Create temp dir manually, store its path
    temp_dir_path = None # Initialize to None
    try:
        temp_dir_path = tempfile.mkdtemp()
        logger.info(f"Created temporary directory: {temp_dir_path}")

        # --- Step 1: Get info to determine format details ---
        # Use minimal options just to get format info quickly
        info_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        selected_format_info = None; needs_audio_merge = False
        actual_ext = 'mp4'; final_format_selector = format_id

        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
             info_dict = ydl_info.extract_info(video_url, download=False)
             formats = info_dict.get('formats', [])
             for f in formats:
                 if f.get('format_id') == format_id: selected_format_info = f; break
        if not selected_format_info: raise yt_dlp.utils.DownloadError(f"Format ID {format_id} not found for URL.")

        # Determine actual extension and if merge is needed
        actual_ext = selected_format_info.get('ext', 'mp4') # Get real extension
        # Check if selected format is video-only (has video, lacks audio)
        if selected_format_info.get('vcodec') != 'none' and selected_format_info.get('acodec') == 'none':
            needs_audio_merge = True
            # Format selector tells yt-dlp to get specific video + best audio
            final_format_selector = f"{format_id}+bestaudio/bestaudio"
            # Use MKV container for merged output - more compatible than MP4 with various codecs
            actual_ext = 'mkv'
            logger.info(f"Format {format_id} is video-only. Will attempt merge with best audio into .{actual_ext}.")
        else:
             logger.info(f"Format {format_id} has audio or is audio-only. Direct download of .{actual_ext}.")

        # --- Step 2: Prepare final filename AND temporary output template ---
        # Sanitize the base filename requested by frontend
        safe_base_filename = sanitize_filename(os.path.splitext(filename_in)[0])
        # Construct final filename with correct extension (mkv if merged, original otherwise)
        final_filename = f"{safe_base_filename}.{actual_ext}"
        # Use a generic name template for yt-dlp within the temp dir
        # yt-dlp will replace %(ext)s with the correct extension (mp4, mkv, m4a etc.)
        temp_output_template = os.path.join(temp_dir_path, f"download_temp.%(ext)s")

        # Define yt-dlp options for the actual download/merge
        ydl_opts_download = {
            'format': final_format_selector,
            'outtmpl': temp_output_template, # Output to temp name/path
            'noplaylist': True,
            # 'quiet': True, # Keep commented out for debugging merge issues
            'no_warnings': True,
             # Specify merge container only if merging
            'merge_output_format': actual_ext if needs_audio_merge else None,
             # Increase timeout (optional, helpful for slow connections)
            'socket_timeout': 60, # 60 seconds
             # Optional: Specify path if ffmpeg isn't in system PATH
             # 'ffmpeg_location': '/path/to/ffmpeg_executable',
             # Add progress hook if wanting server-side progress (more complex)
             # 'progress_hooks': [my_progress_hook],
        }

        # --- Step 3: Execute Download (and Merge if needed) ---
        logger.info(f"Starting yt-dlp process for {final_filename}...")
        logger.info(f"yt-dlp options: {json.dumps(ydl_opts_download)}") # Log options for debug
        try:
            with yt_dlp.YoutubeDL(ydl_opts_download) as ydl_down:
                ydl_down.download([video_url]) # Pass URL as a list
            logger.info(f"yt-dlp process finished successfully to temp dir.")
        except Exception as download_exc:
            # Log the full error from yt-dlp/ffmpeg
            logger.error(f"yt-dlp download/merge failed: {download_exc}")
            # Provide specific feedback if ffmpeg seems missing
            if needs_audio_merge and ('ffmpeg' in str(download_exc).lower() or 'ffprobe' in str(download_exc).lower()):
                 abort(500, description="Download failed: FFmpeg utility may be missing or not found on the server. It's required for merging audio/video.")
            else: # Generic failure during download
                 abort(500, description=f"Download failed during server processing: {download_exc}")

        # --- Step 4: Find the actual downloaded temp file ---
        # Use glob to find the file since the extension was added by yt-dlp
        search_pattern = os.path.join(temp_dir_path, "download_temp.*")
        downloaded_files = glob.glob(search_pattern)
        if not downloaded_files:
             logger.error(f"Could not find downloaded file matching pattern '{search_pattern}' in temp dir: {temp_dir_path}")
             abort(500, description="Server error: Download seemed to succeed but the output file is missing.")
        actual_temp_filepath = downloaded_files[0] # Should be only one file
        logger.info(f"Found temporary file: {actual_temp_filepath}")

        # --- Step 5: Rename temp file to the desired final filename ---
        final_filepath = os.path.join(temp_dir_path, final_filename)
        try:
            os.rename(actual_temp_filepath, final_filepath)
            logger.info(f"Renamed temp file to final path: {final_filepath}")
        except OSError as rename_err:
             logger.error(f"Failed to rename temp file from '{actual_temp_filepath}' to '{final_filepath}': {rename_err}")
             abort(500, description=f"Server error: Failed to prepare file after download: {rename_err}")


        # --- Step 6: Prepare Response and Schedule Cleanup ---
        logger.info(f"Preparing to send file: {final_filename}")
        # Ensure file exists before sending
        if not os.path.exists(final_filepath):
             logger.error(f"Final file path does not exist before sending: {final_filepath}")
             abort(500, description="Server error: Final file path lost before sending.")

        # Prepare the file response using send_file
        response = send_file(
            final_filepath,
            as_attachment=True, # Treat as download
            download_name=final_filename # Set filename for browser
            # Flask will attempt to set mimetype automatically based on extension
        )
        response.headers['X-Accel-Buffering'] = 'no' # Helps prevent buffering issues with reverse proxies like Nginx

        # Use Flask's after_this_request to schedule cleanup *after* file is sent
        @after_this_request
        def cleanup(response):
            # Use the temp_dir_path captured in the outer scope
            tdir = temp_dir_path
            try:
                if tdir and os.path.exists(tdir):
                    logger.info(f"Deferred cleanup: Removing temp dir {tdir}")
                    shutil.rmtree(tdir)
                    logger.info(f"Deferred cleanup: Successfully removed {tdir}")
                else:
                     logger.info(f"Deferred cleanup: Temp dir '{tdir}' already gone or invalid.")
            except Exception as e:
                logger.error(f"Error during deferred cleanup of {tdir}: {e}")
            return response # Important: Must return the response object

        return response # Return the response object; cleanup runs after it's sent

    except Exception as e:
        # General error handling for issues before response is prepared
        logger.exception(f"Critical error during download process for {video_url}: {e}")
        # Ensure cleanup happens even if error occurs before scheduling deferred cleanup
        if temp_dir_path and os.path.exists(temp_dir_path): # Check if var exists and path is valid
             try:
                 logger.warning(f"Cleaning up temp dir {temp_dir_path} due to critical error: {e}")
                 shutil.rmtree(temp_dir_path)
             except Exception as cleanup_err:
                 logger.error(f"Error cleaning up temp dir after critical error: {cleanup_err}")
        # Return appropriate error response (check if abort was called first)
        if isinstance(e, yt_dlp.utils.DownloadError):
             return jsonify({"success": False, "error": f"Download preparation failed: {e}"}), 500
        elif hasattr(e, 'description') and hasattr(e, 'code') and isinstance(e.code, int): # Check if it's likely from abort()
             return jsonify({"success": False, "error": e.description}), e.code # Pass abort code/desc
        else:
             return jsonify({"success": False, "error": "Server download failed unexpectedly."}), 500

# --- End of Final /api/download Route ---


@app.route('/api/health')
def health_check():
    """Simple health check endpoint."""
    return jsonify({"status": "healthy"}), 200

# --- Run the Flask App ---
if __name__ == '__main__':
    # Set debug=False for production deployment
    app.run(debug=True, host='0.0.0.0', port=5000)