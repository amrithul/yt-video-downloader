# YouTube Video & Audio Downloader

A simple web application built with Flask (Python) and vanilla JavaScript to download YouTube videos and audio streams, including merging separate high-quality streams.

**(Optional: Add a screenshot here later if you like)**
## Features

* Paste YouTube video URL (supports standard formats, Shorts, potentially playlists depending on backend logic).
* Fetches available video and audio formats.
* Displays format details (Quality, Extension, Size, FPS).
* Filters formats based on quality (All, HD, 4K for video; All, High for audio).
* Handles downloading of separate high-quality video and audio streams by **merging them on the server** using `yt-dlp` and `ffmpeg`.
* Provides download with the video title as the filename.
* Basic video preview in a modal.
* Copy video info button.
* Responsive UI using Tailwind CSS (via CDN).

## Technology Stack

* **Backend:** Python, Flask
* **Video Processing:** yt-dlp, FFmpeg
* **Frontend:** HTML, Tailwind CSS (via CDN), Vanilla JavaScript
* **Other:** Font Awesome (Icons via CDN), Clipboard.js (Optional, for copy button fallback)

## Prerequisites

Before you begin, ensure you have the following installed on the machine where you will run the **backend (`app.py`)**:

1.  **Python:** Version 3.9 or higher recommended (due to `yt-dlp` deprecation warnings for 3.8).
2.  **pip:** Python package installer (usually comes with Python).
3.  **Git:** For cloning the repository.
4.  **FFmpeg:** **Essential** for merging video and audio streams. `yt-dlp` relies on it. Installation varies by OS (see [ffmpeg.org](https://ffmpeg.org/download.html) or use package managers like `apt`, `dnf`, `brew`). Verify installation by running `ffmpeg -version` in your terminal.

## Setup and Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY_NAME.git
    cd YOUR_REPOSITORY_NAME
    ```
    *(Replace `YOUR_USERNAME/YOUR_REPOSITORY_NAME` with your actual GitHub details)*

2.  **Create a Virtual Environment:** (Recommended)
    ```bash
    python -m venv venv
    ```

3.  **Activate the Virtual Environment:**
    * **Windows (cmd/powershell):** `.\venv\Scripts\activate`
    * **Linux/macOS (bash/zsh):** `source venv/bin/activate`

4.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

5.  **Run the Backend Server:**
    ```bash
    python app.py
    ```
    The server should start, typically listening on `http://127.0.0.1:5000` or `http://0.0.0.0:5000`.

6.  **Access the Frontend:**
    * Open the `index.html` file directly in your web browser (e.g., by double-clicking it or using `file:///path/to/project/index.html`).

## File Structure

```text
flask-youtube-downloader/   <-- This is your main project folder (Project Root)
├── venv/                   <-- Your virtual environment (ignored by git)
├── app.py                  <-- Flask backend logic
├── index.html              <-- Frontend HTML structure
├── app.js                  <-- Frontend JavaScript logic
├── style.css               <-- Frontend CSS styles
├── requirements.txt        <-- Python dependencies
├── README.md               <-- This file
└── .gitignore              <-- Files/folders ignored by git
```


## Configuration

* **Supported Domains:** The `SUPPORTED_DOMAINS` list in `app.py` defines which YouTube domains are accepted. Modify this list if needed.
* **Backend URL:** The `BACKEND_URL` constant in `app.js` should match where your Flask server is running (default is `http://127.0.0.1:5000`).

## Usage

1.  Make sure the Flask backend server (`app.py`) is running.
2.  Open `index.html` in your browser.
3.  Paste a valid YouTube video URL into the input field.
4.  Click "Fetch".
5.  Available, downloadable formats (excluding adaptive manifests like HLS/DASH) will be displayed. Video-only formats will *not* be marked, as the backend handles merging.
6.  Use the quality filters (optional) to narrow down the list.
7.  Click on a desired format card to select it.
8.  Click the "Download Now" button.
9.  Wait for the server to process (download and merge if necessary). This might take time.
10. Your browser should eventually prompt you to save the file, or start the download automatically, with the correct filename and merged audio/video.

## Important Notes & Limitations

* **FFmpeg is Required:** The server running `app.py` *must* have `ffmpeg` installed and accessible in its PATH for downloading high-quality video formats that require merging audio and video streams. Downloads will fail without it.
* **YouTube Bot Detection / Restricted Content:** Some videos may trigger YouTube's "Sign in to confirm you're not a bot" error, especially when accessed from server IPs. This application cannot automatically bypass this. For such videos, or age-restricted/member-only content, you may need to download them manually using `yt-dlp` on your local machine with browser cookies (`--cookies-from-browser BROWSER` or `--cookies cookies.txt`). Implementing cookie handling within this web app securely is complex and not recommended for general use.
* **Rate Limiting:** Excessive use from a single IP might lead to temporary blocks or throttling by YouTube.
* **Terms of Service & Copyright:** This tool should only be used to download content you have the right to access and download. Respect YouTube's Terms of Service and copyright laws. **Intended for personal, private use only.**
* **Development Server:** The Flask application runs using Flask's built-in development server, which is not suitable for a production environment. Use a production-ready WSGI server (like Gunicorn or Waitress) behind a reverse proxy (like Nginx) for deployment.
* **Error Handling:** While basic error handling is included, more robust checks could be added.

## License

(Optional but Recommended) Choose a license (e.g., MIT). Create a `LICENSE` file in your repository and state the license here. Example:

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
