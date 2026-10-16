# Rawan — Local Video Downloader

A local video downloader with a Persian, right-to-left web interface. Download supported video pages, direct media URLs, HLS and DASH streams, select a quality limit, and manage downloads individually or in batches.

**Open the app: [http://127.0.0.1:8765](http://127.0.0.1:8765)**

Docker installs Python, yt-dlp, FFmpeg, FFprobe, Deno and the YouTube JavaScript solver inside the image. You do not need to install them separately on your computer.

## Quick start

Install and start Docker with Docker Compose. On Windows and macOS, use Docker Desktop. The initial build requires internet access.

Run these commands from the project directory.

### Windows

```powershell
powershell -NoProfile -File .\scripts\start.ps1
```

### Linux and macOS

```sh
sh scripts/start.sh
```

The launcher creates a local `.env` file, starts the container, and opens the app in your browser when supported.

**Website address: http://127.0.0.1:8765**

Default download folders:

| Platform | Default folder |
| --- | --- |
| Windows | The current user's Downloads folder from Windows settings, including redirected folders |
| Linux | `xdg-user-dir DOWNLOAD`, falling back to `~/Downloads` |
| macOS | `~/Downloads` |

An existing `.env` is preserved, so subsequent launches keep your selected folder. Native macOS execution has not been tested in this development environment.

### Direct Docker Compose startup

Copy `.env.example` to `.env` and replace `DOWNLOAD_DIR` with an absolute folder path on your computer. For example:

```dotenv
# Windows example; use forward slashes.
DOWNLOAD_DIR='D:/Videos'
APP_PORT=8765
```

On Linux or macOS, use a path such as `/home/yourname/Downloads` or `/Users/yourname/Downloads` instead. Then run:

```sh
docker compose up --build -d
```

Open **http://127.0.0.1:8765**. If you change `APP_PORT`, use `http://127.0.0.1:<APP_PORT>` instead. Compose requires a configured download folder; the launchers can detect it automatically.

## Downloading videos

1. Paste a supported video page URL or a direct media/playlist URL.
2. In single-link mode, inspect the link first to discover its title and available qualities. Downloading stays disabled until inspection succeeds; changing the URL requires another inspection.
3. Select an available quality and an optional destination subfolder. If the source does not report its resolution, the UI offers its available stream without inventing resolution choices.
4. Start the download and follow its status, speed and progress in the history.

The selected resolution is an upper limit. A lower quality may be selected when the requested quality is unavailable. A single-quality playlist cannot provide qualities that are not present in that playlist. Final merging or remuxing can take additional time without increasing the percentage.

Files are saved directly in your configured host folder. The completed job's download button retrieves another copy through the browser. Closing the browser tab does not stop downloads while the container remains running.

### Batch downloads

Select batch mode and enter one link per line, up to 50 lines per request. All links share the selected quality limit and destination folder.

- Duplicate links within the same batch are ignored.
- Invalid links are reported by line without preventing valid links from entering the queue.
- Two downloads run concurrently; the remaining jobs wait in the queue.
- Cancellation and retry are available separately for each job.
- The queue allows at most 100 active jobs. Request size is limited; submit smaller batches for unusually long links.

### YouTube and supported sites

You can enter a page URL such as `https://www.youtube.com/watch?v=...`; finding a raw video URL is not required for sites supported by yt-dlp. Deno and yt-dlp-ejs are installed inside Docker for YouTube support.

Some videos or connections may require account login, age verification or an anti-bot check. This version does not import browser sessions or provide account login. Such videos may fail even though their URL is supported. Expiring links and DRM-protected content are also not supported by this workflow.

A YouTube parameter such as `t=271s` does not trim the video: the full video is downloaded by default. MP4 output does not guarantee that every codec will play on every device.

## Choosing a different download folder

### Windows folder picker

```powershell
powershell -NoProfile -File .\scripts\start.ps1 -ChooseFolder
```

### Windows absolute path

```powershell
powershell -NoProfile -File .\scripts\start.ps1 -DownloadDirectory "D:\My Videos"
```

### Linux/macOS absolute path

```sh
sh scripts/start.sh --download-dir "/mnt/storage/My Videos"
```

Alternatively, edit `DOWNLOAD_DIR` in `.env` and apply the change:

```sh
docker compose up -d
```

The web form accepts relative subfolders such as `Courses/DevOps` or full paths inside the mounted download folder. Subfolders are created automatically. For example, if `DOWNLOAD_DIR` is `D:/My Videos`, `D:/My Videos/Courses` is valid.

Docker cannot access an arbitrary host folder merely because its path is entered in a web form. To use a path outside the mounted folder, first change the main folder through the launcher or `.env`.

Examples for the web form (replace the username and match the displayed default folder):

| System | Full path example |
| --- | --- |
| Windows | `C:\Users\Asus\Downloads\Courses` |
| Linux | `/home/username/Downloads/Courses` |
| macOS | `/Users/username/Downloads/Courses` |

Enter paths without quotes. The web form does not expand `~`, `$HOME` or `%USERPROFILE%`. A blank path uses the default folder; `Courses/DevOps` works as a relative subfolder on all three systems. Host path formats are covered by automated tests in the Linux container; this does not constitute native testing on macOS.

## Download history

The interface shows the latest 100 jobs, with 10 matching results per page. Filters return to the first page. Older records stay in SQLite until you clear history.

“Clear history” asks for confirmation and removes all finished, failed, cancelled and interrupted records, including those outside the latest 100. Queued jobs, running jobs and subprocesses still stopping are protected. Video files and partial files remain on disk. Cleared records can no longer be retried or accessed through the app; existing files can still be opened from their folder.

Change the mounted folder only when no downloads are running. Existing files are not moved. History is preserved, but files in the previous folder cannot be retrieved through the new mount.

## Container management

```sh
# Follow application logs
docker compose logs -f

# Stop the application
docker compose stop

# Start it again
docker compose up -d

# Remove the container and network, keeping the named data volume
docker compose down
```

History is stored in a named SQLite volume. Downloads interrupted by a container restart are marked as interrupted. Retry them to resume where supported by the source and remaining temporary files. Cancelling a job preserves temporary files for a possible retry.

## Architecture

```text
Persian web UI → FastAPI → Download queue → yt-dlp / FFmpeg → Mounted host folder
                       ↘ SQLite history
```

- **FastAPI:** API and static web interface.
- **yt-dlp:** Metadata extraction, format selection, downloading and retry.
- **PyCryptodomex:** Native AES-128 HLS downloading with intermediate progress updates.
- **Deno + yt-dlp-ejs:** JavaScript challenge support for YouTube.
- **FFmpeg / FFprobe:** Media merging, remuxing and verification.
- **SQLite + two worker threads:** Persistent history and concurrent downloads within one web process.
- **Server-Sent Events:** Live status updates.
- **Docker Compose:** Runtime dependencies, persistent data and host-folder mapping.

The `scripts/` directory contains optional platform-specific launchers. The `app/tests/` directory contains tests and is not part of normal application execution. tus is not used: it is a resumable upload protocol, whereas this app downloads media from remote sources.

## Tests

Run the test suite without installing Python on the host:

```sh
docker compose run --rm -T --no-deps downloader python -m unittest discover -s app/tests -v
```

Tests start an isolated server and database in temporary storage and do not modify your normal history or downloaded files. Coverage includes encrypted HLS downloads and intermediate progress, MP4 video/audio output, batches, cancellation and retry, path validation, symlink escapes, queue limits, request size, cross-origin requests and invalid inputs.

## Security and performance

The application is intended for personal, local use with trusted links, not public hosting or untrusted users.

- The published port binds only to `127.0.0.1`.
- Invalid Host headers, cross-site requests and non-JSON POST requests are rejected.
- Download and file-serving paths must remain inside the mounted folder.
- Titles and error messages are inserted as text rather than executable HTML.
- The container root filesystem is read-only. Download storage, SQLite data and temporary storage remain writable.
- Linux capabilities are dropped; privilege escalation, memory and process counts are restricted.
- Two downloads and two inspections can run concurrently.
- Progress writes are throttled to at most twice per second per download.
- SQLite uses WAL mode and indexes. History snapshots are cached until data changes.
- SSE sends changed data and idle heartbeats. The UI updates changed cards while preserving existing controls.

Initial URL validation rejects private and local network addresses. This is **not a complete outbound firewall**: redirects, DNS rebinding and URLs inside media manifests may fall outside that initial check. Only submit trusted links. `ALLOW_PRIVATE_SOURCES=1` disables the initial private-address restriction for explicit local use; it is not enabled in the normal Compose configuration.

URLs are stored in local history and may contain access tokens. Do not commit `.env`, databases, downloads or other personal runtime data. Python dependency advisory checks do not constitute a complete audit of FFmpeg, operating-system packages or the application.

## Updating dependencies

Python package versions are specified in `requirements.txt`. Update compatible yt-dlp and yt-dlp-ejs versions together, then rebuild:

```sh
docker compose up --build -d
```

FFmpeg is installed from Debian package repositories during image construction. Deno is copied from its versioned Docker image. To refresh cached system packages, rebuild with `docker compose build --no-cache`, then run `docker compose up -d`.
