# mfcmd

[![Python Version](https://img.shields.io/badge/python-3.6%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![MediaFire SDK](https://img.shields.io/badge/mediafire-0.6.1-orange.svg)](https://pypi.org/project/mediafire/)

`mfcmd.py` is a resilient, command-line resumable file uploader for **MediaFire**, engineered specifically for integration with the official `mediafire==0.6.1` Python Open SDK [cite: 1].

It provides high-performance, fault-tolerant transfers for large files by leveraging chunked multi-unit uploads, automatic resume on network failure or manual interruption, SHA-256 block verification, instant server-side deduplication, and low memory consumption [cite: 1].

---

## Key Features

* **Resumable Multi-Unit Uploads:** Automatically slices large files into server-specified unit sizes [cite: 1]. If an upload drops or is canceled (`Ctrl+C`), running the command again decodes MediaFire's unit bitmap and seamlessly resumes from the exact missing block [cite: 1].
* **Instant Server-Side Deduplication:** Checks file hashes with MediaFire prior to uploading [cite: 1]. If an identical file already exists on MediaFire's servers, the file transfer is bypassed and the direct download URL is returned immediately [cite: 1].
* **Memory Efficient (`UnitFile` Architecture):** Replaces `mediafire.subsetio.SubsetIO` with a custom isolated memory buffer [cite: 1]. Streams individual chunks without loading entire multi-gigabyte files into RAM [cite: 1].
* **Per-Unit Automatic Retries:** Retries failed chunk uploads up to 5 times with exponential fallback before exiting, handling unstable connection issues smoothly [cite: 1].
* **Terminal Progress Bar:** Displays real-time progress via `tqdm`, including current transfer speeds, ETA, elapsed time, percentage, and byte counters [cite: 1].
* **Automated Polling & Finalization:** Listens to MediaFire's asynchronous processing queue after byte transmission finishes and outputs the final shareable public URL [cite: 1].

---

## Architecture & How It Works

```
                        ┌───────────────────────────────┐
                        │      Initialize Session       │
                        └───────────────┬───────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │    Calculate Local Hashes     │
                        │    (MD5 & Global SHA-256)     │
                        └───────────────┬───────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │  Check MediaFire Upload State │
                        └───────────────┬───────────────┘
                                        │
         ┌──────────────────────────────┴──────────────────────────────┐
         │                                                             │
  [ Hash Exists? ]                                              [ New Upload ]
         │                                                             │
         ▼                                                             ▼
┌─────────────────┐                                            ┌───────────────┐
│ Instant Return  │                                            │ Decode Bitmap │
│ Download Link   │                                            └───────┬───────┘
└─────────────────┘                                                    │
                                                                       ▼
                                                              ┌─────────────────┐
                                                     ┌───────►│  Upload Chunk   │
                                                     │        └────────┬────────┘
                                                     │                 │
                                              [ More Chunks? ] ◄───────┘
                                                     │
                                                     ▼
                                            ┌─────────────────┐
                                            │  Poll Finalize  │
                                            └────────┬────────┘
                                                     │
                                                     ▼
                                            ┌─────────────────┐
                                            │ Output Direct   │
                                            │ Public URL      │
                                            └─────────────────┘
```

1. **Authentication:** Connects to MediaFire using application ID `42511` [cite: 1].
2. **Hash Check:** Calculates full-file SHA-256 and MD5 [cite: 1].
3. **Bitmap Decoding:** Requests `upload/check` from MediaFire to parse the 15-bit integer word array representing which chunks already reside on the server [cite: 1].
4. **Chunked Streaming:** Uploads missing chunks using `UnitFile` in-memory buffers [cite: 1].
5. **Polling:** Monitors the `upload_key` via `upload/poll` until MediaFire assigns a `quickkey` and generates the public link [cite: 1].

---

## Requirements

* **Python:** `3.6` or higher
* **Dependencies:**
  * `mediafire==0.6.1`
  * `tqdm`

---

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/bx758/mfcmd.git
   cd mfcmd
   ```

2. **Install requirements:**
   ```bash
   pip install mediafire==0.6.1 tqdm
   ```

---

## Usage

```bash
python3 mfcmd.py -e <email> -p <password> -f <filepath> [options]
```

### Command-Line Arguments

| Flag | Long Flag | Description | Required | Default |
|---|---|---|---|---|
| `-e` | `--email` | Your MediaFire account email address [cite: 1]. | **Yes** | — |
| `-p` | `--password` | Your MediaFire account password [cite: 1]. | **Yes** | — |
| `-f` | `--file` | Path to the local file to upload [cite: 1]. | **Yes** | — |
| `-u` | `--upload-folder` | Target folder name on MediaFire [cite: 1]. | No | `My Files` [cite: 1] |
| `-h` | `--hash` | Pre-calculated SHA-256 string (skips local hash calculation) [cite: 1]. | No | Computed automatically [cite: 1] |

---

## Examples

### Basic File Upload
Upload a file to the default root directory (`My Files`):
```bash
python3 mfcmd.py \
    -e "user@example.com" \
    -p "YourPassword123" \
    -f "backup.zip"
```

### Upload to a Specific Folder
Upload a file directly into a subfolder named `Backups`:
```bash
python3 mfcmd.py \
    -e "user@example.com" \
    -p "YourPassword123" \
    -u "Backups" \
    -f "database_dump.sql.gz"
```

### Skip Local Hashing (Pre-calculated SHA-256)
If you already computed the SHA-256 hash of a large file, pass it via `-h` to start uploading immediately:
```bash
python3 mfcmd.py \
    -e "user@example.com" \
    -p "YourPassword123" \
    -h "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" \
    -f "ubuntu-desktop.iso"
```

---

## Exit Status Codes

`mfcmd.py` returns standard exit codes for integration into shell scripts or CI/CD pipelines [cite: 1]:

| Code | Status | Meaning |
|---|---|---|
| `0` | **SUCCESS** | File uploaded or instant duplicate match found; URL printed to stdout [cite: 1]. |
| `1` | **ERROR** | Validation failed, login rejected, or upload retries exhausted [cite: 1]. |
| `130` | **INTERRUPTED** | Interrupted by user (`SIGINT` / `Ctrl+C`). Server retains uploaded units [cite: 1]. |

---

## License

Distributed under the [MIT License](LICENSE).
