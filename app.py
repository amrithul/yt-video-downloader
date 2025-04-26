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
# The first argument is the import name, usually __name__
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
             # Check if current audio bitrate is valid number
             if isinstance(current_abr, (int, float)):
                best_abr_so_far = -1 # Initialize best bitrate found so far
                # Check if we already have a best candidate and its bitrate is valid
                if best_audio_info and isinstance(best_audio_info.get('abr'), (int, float)):
                    best_abr_so_far = best_audio_info.get('abr', -1) # Get its bitrate
                # If current format's bitrate is better, update the best candidate
                if current_abr > best_abr_so_far:
                    best_audio_info = audio_format_dict
             # If current format has no valid bitrate, but we haven't found *any* audio yet,
             # take this one as the initial best (better than nothing).
             elif best_audio_info is None:
                best_audio_info = audio_format_dict
             # --- End of best audio logic ---

    # --- Sorting ---
    # Sort video formats: Highest resolution first, then highest FPS
    video_formats.sort(key=lambda x: (-x['height'], -(x.get('fps') or 0)))
    # Sort audio formats: Highest bitrate first (treat None as lowest)
    audio_formats.sort(key=lambda x: -(x.get('abr') if isinstance(x.get('abr'), (int, float)) else -1))

    # --- Prepare Best Audio Summary (for display purposes) ---
    final_audio_summary = None
    if best_audio_info:
        # Create a simplified dictionary for the best audio-only option
        final_audio_summary = {
            "quality": f"Audio Only ({best_audio_info.get('quality', 'Best Available')})",
            "size": best_audio_info.get("size", "N/A"),
            "id": best_audio_info['id'],
            "ext": best_audio_info.get('ext', 'm4a') # Use the actual best audio extension
        }

    # Return the processed lists and the summary object
    return video_formats, audio_formats, final_audio_summary


# --- ** Static File Serving Routes ** ---

# Serve index.html for the root URL ('/')
@app.route('/')
def serve_index():
    # '.' represents the current working directory (should be /app in Docker)
    logger.info("Serving index.html")
    # send_from_directory securely serves files from the specified directory
    return send_from_directory('.', 'index.html')

# Serve app.js when requested via <script src="/app.js">
@app.route('/app.js')
def serve_js():
    logger.info("Serving app.js")
    return send_from_directory('.', 'app.js')

# Serve style.css when requested via <link href="/style.css">
@app.route('/style.css')
def serve_css():
    logger.info("Serving style.css")
    return send_from_directory('.', 'style.css')

# --- End of Static File Routes ---


# --- API Endpoints ---
@app.route('/api/get-formats', methods=['GET'])
def get_formats():
    """API endpoint to fetch video information and available formats."""
    video_url = request.args.get('url')
    # Validate input URL
    if not video_url: return jsonify({"success": False, "error": "Missing 'url' parameter"}), 400
    if not video_url.startswith(('http://', 'https://')): video_url = 'https://' + video_url
    if not is_supported_url(video_url): return jsonify({"success": False, "error": "Invalid or unsupported URL."}), 400

    logger.info(f"API: Fetching formats for URL: {video_url}")
    # Options for yt-dlp to only extract information
    ydl_opts = {
        'noplaylist': True,       # Don't process playlist items if a playlist URL is given
        'quiet': True,            # Suppress yt-dlp console output
        'no_warnings': True,      # Suppress yt-dlp warnings
        'skip_download': True,    # Only extract metadata, don't download files
        'extract_flat': False,    # Get full format details, not just a flat list
        'youtube_include_dash_manifest': False # Avoids some potential issues
    }
    try:
        # Use yt-dlp context manager
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False) # Extract info
            if not info_dict: raise yt_dlp.utils.DownloadError("No video information extracted.")

            # Extract basic video metadata
            video_title = info_dict.get('title', 'Untitled Video')
            thumbnails = info_dict.get('thumbnails', [])
            # Try to get the highest resolution thumbnail
            thumbnail_url = thumbnails[-1]['url'] if thumbnails else info_dict.get('thumbnail', '')
            duration = info_dict.get('duration'); uploader = info_dict.get('uploader', 'Unknown'); view_count = info_dict.get('view_count')

            # Process the extracted formats using our helper function
            video_formats, audio_formats, best_audio_summary = extract_formats(info_dict)

            # Check if any downloadable formats were found
            if not video_formats and not audio_formats:
                if info_dict.get('is_live'): raise yt_dlp.utils.DownloadError("Live streams cannot be downloaded until finished.")
                raise yt_dlp.utils.DownloadError("No downloadable video or audio formats found for this video.")

            # Return successful response with processed data
            return jsonify({
                "success": True, "videoTitle": video_title, "thumbnailUrl": thumbnail_url,
                "duration": duration, "uploader": uploader, "viewCount": view_count,
                "formats": { "video": video_formats, "audio": audio_formats, "bestAudio": best_audio_summary }
            })

    except yt_dlp.utils.DownloadError as e:
        # Handle known download errors from yt-dlp
        error_message = str(e); logger.warning(f"API: yt-dlp DownloadError for {video_url}: {error_message}")
        # Convert common technical errors to user-friendly messages
        if "Unsupported URL" in error_message: error_message = "Invalid or unsupported URL."
        elif "Video unavailable" in error_message: error_message = "This video is unavailable."
        elif "Private video" in error_message: error_message = "Private videos cannot be accessed."
        elif "confirm your age" in error_message: error_message = "Age-restricted video. Cannot download automatically."
        elif "Premiere" in error_message or "live event" in error_message: error_message = "Livestreams/Premieres cannot be downloaded until finished."
        elif "429" in error_message or "Too Many Requests" in error_message: error_message = "Rate limited by YouTube. Please wait and try again later."
        elif "Sign in to confirm you're not a bot" in error_message: error_message = "YouTube requires bot verification. Cannot download automatically."
        else: error_message = "Could not fetch video data (may be region locked, deleted, etc)."
        return jsonify({"success": False, "error": error_message}), 400 # Use 400 for client-side type errors

    except Exception as e:
        # Catch any other unexpected errors during processing
        logger.exception(f"API: Unexpected server error processing URL {video_url}: {e}")
        return jsonify({"success": False, "error": "An unexpected server error occurred while fetching formats."}), 500


@app.route('/api/download', methods=['GET'])
def download_video():
    """
    Downloads/merges video/audio using yt-dlp on the server to a temporary file,
    renames it, sends the file to the client, and then cleans up the temporary directory.
    Requires ffmpeg to be installed on the server for merging.
    """
    video_url = request.args.get('url')
    format_id = request.args.get('format_id')
    filename_in = request.args.get('filename', 'video') # Use filename suggested by frontend

    # --- Input Validation ---
    if not video_url or not format_id: return jsonify({"success": False, "error": "Missing parameters"}), 400
    if not video_url.startswith(('http://', 'https://')): video_url = 'https://' + video_url
    # No need to call is_supported_url again, assume valid if get_formats worked

    logger.info(f"API: Server download request - URL: {video_url}, Format: {format_id}, Base Filename: '{filename_in}'")

    temp_dir_path = None # Initialize to None to ensure cleanup check works
    try:
        # --- Create Temporary Directory ---
        temp_dir_path = tempfile.mkdtemp()
        logger.info(f"API: Created temporary directory: {temp_dir_path}")

        # --- Step 1: Get format info again (minimal call) to confirm details ---
        info_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True}
        selected_format_info = None; needs_audio_merge = False
        actual_ext = 'mp4'; final_format_selector = format_id
        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
             info_dict = ydl_info.extract_info(video_url, download=False)
             formats = info_dict.get('formats', [])
             for f in formats:
                 if f.get('format_id') == format_id: selected_format_info = f; break
        # Ensure the selected format was actually found in the info
        if not selected_format_info: raise yt_dlp.utils.DownloadError(f"Format ID {format_id} not found for URL on download request.")

        # Determine actual extension and if merge is needed based on retrieved info
        actual_ext = selected_format_info.get('ext', 'mp4') # Get real extension
        if selected_format_info.get('vcodec') != 'none' and selected_format_info.get('acodec') == 'none':
            needs_audio_merge = True
            # Format selector tells yt-dlp to get specific video + best audio
            final_format_selector = f"{format_id}+bestaudio/bestaudio"
            # Use MKV container for merged output - more compatible than MP4 with various codecs
            actual_ext = 'mkv'
            logger.info(f"API: Format {format_id} needs merge. Target ext: .{actual_ext}.")
        else:
             logger.info(f"API: Format {format_id} has audio or is audio-only. Direct download of .{actual_ext}.")

        # --- Step 2: Prepare final filename AND temporary output template ---
        safe_base_filename = sanitize_filename(os.path.splitext(filename_in)[0])
        # Construct final filename with correct extension (mkv if merged, original otherwise)
        final_filename = f"{safe_base_filename}.{actual_ext}"
        # Use a generic name template for yt-dlp output within the temp dir
        temp_output_template = os.path.join(temp_dir_path, f"download_temp.%(ext)s")

        # Define yt-dlp options for the actual download/merge
        ydl_opts_download = {
            'format': final_format_selector,           # Use determined selector (single ID or merge syntax)
            'outtmpl': temp_output_template,          # Output to temp name/path
            'noplaylist': True,
            # 'quiet': True,                          # Keep commented out to see yt-dlp/ffmpeg logs on server
            'no_warnings': True,
            'merge_output_format': actual_ext if needs_audio_merge else None, # Specify container only if merging
            'socket_timeout': 60,                     # Increased network timeout
            # 'ffmpeg_location': '/path/to/ffmpeg',   # Optional: Specify if ffmpeg isn't in system PATH
        }

        # --- Step 3: Execute Download (and Merge if needed) ---
        logger.info(f"API: Starting yt-dlp process for {final_filename}..."); logger.info(f"API: yt-dlp options: {json.dumps(ydl_opts_download)}")
        try:
            # Use context manager for yt-dlp instance
            with yt_dlp.YoutubeDL(ydl_opts_download) as ydl_down:
                ydl_down.download([video_url]) # Pass URL as a list
            logger.info(f"API: yt-dlp process finished successfully to temp dir.")
        except Exception as download_exc:
            # Log the full error from yt-dlp/ffmpeg
            logger.error(f"API: yt-dlp download/merge failed: {download_exc}")
            # Provide specific feedback if ffmpeg seems missing during merge attempt
            if needs_audio_merge and ('ffmpeg' in str(download_exc).lower() or 'ffprobe' in str(download_exc).lower()):
                 abort(500, description="Download failed: FFmpeg utility may be missing or not found on the server. It's required for merging audio/video.")
            else: # Generic failure during download/merge
                 abort(500, description=f"Download failed during server processing: {download_exc}")

        # --- Step 4: Find the actual downloaded temp file ---
        # Use glob to find the file since the extension was added dynamically by yt-dlp
        search_pattern = os.path.join(temp_dir_path, "download_temp.*")
        downloaded_files = glob.glob(search_pattern)
        if not downloaded_files:
             logger.error(f"API: Could not find downloaded file matching pattern '{search_pattern}' in temp dir: {temp_dir_path}")
             abort(500, description="Server error: Output file missing after download.")
        actual_temp_filepath = downloaded_files[0] # Assume only one file matches
        logger.info(f"API: Found temporary file: {actual_temp_filepath}")

        # --- Step 5: Rename temp file to the desired final filename ---
        final_filepath = os.path.join(temp_dir_path, final_filename)
        try:
            # Rename the file within the temporary directory
            os.rename(actual_temp_filepath, final_filepath)
            logger.info(f"API: Renamed temp file to final path: {final_filepath}")
        except OSError as rename_err:
             logger.error(f"API: Failed to rename temp file from '{actual_temp_filepath}' to '{final_filepath}': {rename_err}")
             abort(500, description=f"Server error: Failed to prepare file after download: {rename_err}")

        # --- Step 6: Prepare Response and Schedule Cleanup ---
        logger.info(f"API: Preparing to send file: {final_filename}")
        # Ensure file exists before sending
        if not os.path.exists(final_filepath):
             logger.error(f"API: Final file path does not exist before sending: {final_filepath}")
             abort(500, description="Server error: Final file path lost before sending.")

        # Prepare the file response using Flask's send_file
        response = send_file(
            final_filepath,         # Path to the renamed file in the temp directory
            as_attachment=True,     # Tell the browser to treat it as a download
            download_name=final_filename # Set the filename for the browser download prompt
        )
        # Add header to potentially help with buffering issues in proxies like Nginx
        response.headers['X-Accel-Buffering'] = 'no'

        # Use Flask's after_this_request decorator to schedule cleanup *after* the file is sent
        @after_this_request
        def cleanup(response):
            tdir = temp_dir_path # Capture path from outer scope
            try:
                # Check if the directory path exists before attempting removal
                if tdir and os.path.exists(tdir):
                    logger.info(f"API: Deferred cleanup: Removing temp dir {tdir}")
                    shutil.rmtree(tdir) # Remove the entire temporary directory and its contents
                    logger.info(f"API: Deferred cleanup: Successfully removed {tdir}")
                else:
                     logger.info(f"API: Deferred cleanup: Temp dir '{tdir}' already gone or invalid.")
            except Exception as e:
                # Log errors during cleanup but don't crash the response
                logger.error(f"API: Error during deferred cleanup of {tdir}: {e}")
            return response # Important: Must return the response object from the decorated function

        return response # Return the prepared response object; cleanup runs after it's fully sent

    except Exception as e:
        # --- General Error Handling during the download process ---
        logger.exception(f"API: Critical error during download process for {video_url}: {e}")
        # Ensure cleanup happens even if an error occurs before scheduling deferred cleanup
        if temp_dir_path and os.path.exists(temp_dir_path): # Check if var exists and path is valid
             try:
                 logger.warning(f"API: Cleaning up temp dir {temp_dir_path} due to critical error: {e}")
                 shutil.rmtree(temp_dir_path)
             except Exception as cleanup_err:
                 logger.error(f"API: Error cleaning up temp dir after critical error: {cleanup_err}")
        # Return appropriate JSON error response to the frontend
        if isinstance(e, yt_dlp.utils.DownloadError):
             return jsonify({"success": False, "error": f"Download preparation failed: {e}"}), 500
        # Check if the exception originated from abort()
        elif hasattr(e, 'description') and hasattr(e, 'code') and isinstance(e.code, int):
             return jsonify({"success": False, "error": e.description}), e.code # Pass abort code/desc
        else: # Catch-all for other unexpected errors
             return jsonify({"success": False, "error": "Server download failed unexpectedly."}), 500
# --- End of /api/download Route ---


@app.route('/api/health')
def health_check():
    """Simple health check endpoint for monitoring."""
    return jsonify({"status": "healthy"}), 200

# --- Run the Flask App ---
if __name__ == '__main__':
    # Use port defined by environment variable PORT (used by Render), default to 5000 locally
    port = int(os.environ.get('PORT', 5000))
    # Set debug=False for production deployment on Render
    # host='0.0.0.0' makes it accessible externally (needed for Render)
    app.run(debug=False, host='0.0.0.0', port=port)
