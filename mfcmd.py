#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
mfcmd.py v0.82

MediaFire resumable uploader for mediafire==0.6.1.

Features:
    - MediaFire SDK authentication
    - app_id support
    - SHA-256 / MD5 calculation
    - MediaFire upload/check
    - Instant upload when MediaFire already has the file
    - Resumable uploads
    - Resume after interruption
    - MediaFire bitmap support
    - No MediaFire SubsetIO
    - Retry failed units
    - Progress bar
    - Final upload polling
    - Download URL output

Tested design target:
    mediafire 0.6.1

Example:

    python3 mfcmd.py \
        -e "your@email.com" \
        -p "your-password" \
        -f "large-file.zip"

Optional folder:

    python3 mfcmd.py \
        -e "your@email.com" \
        -p "your-password" \
        -u "mfcmd" \
        -f "large-file.zip"

Optional SHA-256:

    python3 mfcmd.py \
        -e "your@email.com" \
        -p "your-password" \
        -h "sha256..." \
        -f "large-file.zip"
"""

import getopt
import hashlib
import io
import os
import sys
import time
from urllib.parse import quote

from tqdm import tqdm
from mediafire import MediaFireApi


# ============================================================================
# Configuration
# ============================================================================

VERSION = "0.82"

# MediaFire application ID used by the Python Open SDK.
MEDIAFIRE_APP_ID = "42511"

# Retry individual upload units this many times.
UNIT_RETRIES = 5

# Seconds between upload polling requests.
POLL_INTERVAL = 5

# Hashing buffer.
HASH_BUFFER_SIZE = 1024 * 1024

# Default MediaFire folder.
DEFAULT_FOLDER = "My Files"


# ============================================================================
# Console helpers
# ============================================================================

def eprint(message=""):
    print(message, file=sys.stderr)


def human_size(value):
    value = float(value)

    units = (
        "B",
        "KiB",
        "MiB",
        "GiB",
        "TiB",
    )

    for unit in units:
        if value < 1024:
            return f"{value:.2f} {unit}"

        value /= 1024

    return f"{value:.2f} PiB"


# ============================================================================
# File hashing
# ============================================================================

def calculate_file_hashes(filepath):
    """
    Calculate MD5 and SHA-256 for the complete file.
    """

    md5 = hashlib.md5()
    sha256 = hashlib.sha256()

    total = 0

    with open(filepath, "rb") as fd:

        while True:

            data = fd.read(HASH_BUFFER_SIZE)

            if not data:
                break

            md5.update(data)
            sha256.update(data)

            total += len(data)

    return (
        md5.hexdigest().lower(),
        sha256.hexdigest().lower(),
        total,
    )


# ============================================================================
# Unit hashing
# ============================================================================

def calculate_unit_hashes(
    filepath,
    unit_size,
    number_of_units,
):
    """
    Calculate SHA-256 for every MediaFire resumable unit.

    MediaFire tells us the unit size through upload/check.
    """

    hashes = []

    eprint(
        f"Calculating SHA-256 for "
        f"{number_of_units} MediaFire units..."
    )

    with open(filepath, "rb") as fd:

        while True:

            data = fd.read(unit_size)

            if not data:
                break

            digest = hashlib.sha256(data).hexdigest().lower()

            hashes.append(digest)

    if len(hashes) != number_of_units:

        raise RuntimeError(
            "MediaFire unit count mismatch: "
            f"API returned {number_of_units}, "
            f"but local file produced {len(hashes)}."
        )

    return hashes


# ============================================================================
# MediaFire bitmap
# ============================================================================

def decode_bitmap(bitmap_node, number_of_units):
    """
    Decode MediaFire's resumable upload bitmap.

    This follows the same bitmap format used by
    mediafire.uploader in version 0.6.1.
    """

    result = {
        unit_id: False
        for unit_id in range(number_of_units)
    }

    if not bitmap_node:
        return result

    count = int(
        bitmap_node.get("count", 0)
    )

    words = bitmap_node.get(
        "words",
        []
    )

    bitmap = 0

    for token_id in range(count):

        if token_id >= len(words):
            break

        value = int(words[token_id])

        bitmap |= (
            value << (0xF * token_id)
        )

    for unit_id in range(number_of_units):

        mask = 1 << unit_id

        result[unit_id] = (
            bitmap & mask
        ) == mask

    return result


# ============================================================================
# Authentication
# ============================================================================

def authenticate(email, password):
    """
    Authenticate using MediaFireApi 0.6.1.

    IMPORTANT:
        MediaFireApi() does not take app_id in its constructor.

    Correct API:

        api = MediaFireApi()

        session = api.user_get_session_token(
            app_id="42511",
            email=email,
            password=password
        )

        api.session = session
    """

    eprint(
        "Authenticating with MediaFire SDK..."
    )

    api = MediaFireApi()

    session = api.user_get_session_token(
        app_id=MEDIAFIRE_APP_ID,
        email=email,
        password=password,
    )

    if not session:

        raise RuntimeError(
            "MediaFire returned an empty session token."
        )

    # This is required by mediafire==0.6.1.
    api.session = session

    eprint(
        "MediaFire authentication successful."
    )

    return api


# ============================================================================
# Account information
# ============================================================================

def print_account_info(api):

    try:

        response = api.user_get_info()

        user_info = response.get(
            "user_info",
            {}
        )

        display_name = (
            user_info.get("display_name")
            or user_info.get("email")
            or user_info.get("username")
        )

        if display_name:
            eprint(
                f"Account : {display_name}"
            )

    except Exception:
        pass


# ============================================================================
# Folder lookup
# ============================================================================

def get_folder_key(api, folder_name):
    """
    Return a MediaFire folder key.

    My Files is always represented by "myfiles".
    """

    if not folder_name:
        return "myfiles"

    if folder_name.lower() in (
        "my files",
        "myfiles",
    ):
        return "myfiles"

    eprint(
        f"Looking for MediaFire folder: {folder_name}"
    )

    try:

        result = api.folder_search(
            search_text=folder_name,
            folder_key="myfiles",
        )

    except Exception as exc:

        raise RuntimeError(
            f"Folder search failed: {exc}"
        )

    folder_content = result.get(
        "folder_content",
        {}
    )

    folders = folder_content.get(
        "folders",
        []
    )

    for folder in folders:

        name = (
            folder.get("name")
            or folder.get("foldername")
        )

        if name != folder_name:
            continue

        folder_key = (
            folder.get("folderkey")
            or folder.get("folder_key")
        )

        if folder_key:

            eprint(
                f"Folder found: {folder_key}"
            )

            return folder_key

    raise RuntimeError(
        f"MediaFire folder '{folder_name}' "
        "was not found."
    )


# ============================================================================
# Upload check
# ============================================================================

def mediafire_upload_check(
    api,
    filename,
    filesize,
    sha256_hash,
    folder_key,
):
    """
    Call MediaFire upload/check.

    This is preferable to manually calling upload/instant.php.
    """

    return api.upload_check(
        filename=filename,
        size=filesize,
        hash_=sha256_hash,
        folder_key=folder_key,
        resumable=True,
    )


# ============================================================================
# In-memory MediaFire unit
# ============================================================================

class UnitFile(io.BytesIO):
    """
    Small file-like object used for ONE MediaFire upload unit.

    The MediaFire 0.6.1 uploader expects the file-like object
    to have a .len attribute.

    Unlike SubsetIO, this object owns its underlying buffer and
    therefore does not depend on the parent file descriptor.
    """

    def __init__(self, data):
        super().__init__(data)

        self.len = len(data)

    def close(self):
        """
        BytesIO.close() is safe, but keeping this method explicit
        makes the ownership of the unit clear.
        """

        super().close()


# ============================================================================
# Read one file unit
# ============================================================================

def read_file_unit(
    filepath,
    offset,
    size,
):
    """
    Read one MediaFire unit.

    Only one unit is loaded into memory.
    The entire 4.24 GiB file is NEVER loaded.
    """

    with open(filepath, "rb") as fd:

        fd.seek(
            offset,
            os.SEEK_SET
        )

        data = fd.read(size)

    return data


# ============================================================================
# Upload one unit
# ============================================================================

def upload_unit(
    api,
    filepath,
    filesize,
    file_hash,
    unit_hash,
    unit_id,
    unit_size,
    folder_key,
):
    """
    Upload a single MediaFire resumable unit.

    This directly uses:

        api.upload_resumable()

    and completely bypasses:

        mediafire.subsetio.SubsetIO
    """

    offset = (
        unit_id
        * unit_size
    )

    remaining = (
        filesize
        - offset
    )

    actual_size = min(
        unit_size,
        remaining
    )

    data = read_file_unit(
        filepath,
        offset,
        actual_size
    )

    if len(data) != actual_size:

        raise IOError(
            f"Could not read unit "
            f"{unit_id + 1}: "
            f"expected {actual_size} bytes, "
            f"received {len(data)}."
        )

    # Verify unit hash locally.
    actual_hash = hashlib.sha256(
        data
    ).hexdigest().lower()

    if actual_hash != unit_hash:

        raise RuntimeError(
            f"Unit {unit_id + 1} SHA-256 mismatch."
        )

    unit_fd = UnitFile(data)

    try:

        response = api.upload_resumable(
            unit_fd,
            filesize,
            file_hash,
            unit_hash,
            unit_id,
            actual_size,
            folder_key=folder_key,
            action_on_duplicate="keep",
        )

    finally:

        unit_fd.close()

    return response


# ============================================================================
# Upload polling
# ============================================================================

def poll_upload(api, upload_key):
    """
    Poll MediaFire until the upload receives a quickkey.
    """

    eprint()
    eprint(
        f"Upload key: {upload_key}"
    )

    eprint(
        "Waiting for MediaFire to finalize the file..."
    )

    while True:

        response = api.upload_poll(
            upload_key
        )

        doupload = response.get(
            "doupload",
            {}
        )

        status = int(
            doupload.get(
                "status",
                0
            )
        )

        result_code = int(
            doupload.get(
                "result",
                0
            )
        )

        description = doupload.get(
            "description",
            ""
        )

        eprint(
            f"MediaFire status: "
            f"{status} | "
            f"result: {result_code}"
            + (
                f" | {description}"
                if description
                else ""
            )
        )

        file_error = doupload.get(
            "fileerror"
        )

        if file_error:

            raise RuntimeError(
                f"MediaFire file error: "
                f"{file_error}"
            )

        quickkey = doupload.get(
            "quickkey"
        )

        if quickkey:

            return doupload

        if result_code != 0:

            raise RuntimeError(
                "MediaFire upload polling failed: "
                f"{doupload}"
            )

        time.sleep(
            POLL_INTERVAL
        )


# ============================================================================
# URL
# ============================================================================

def make_mediafire_url(
    quickkey,
    filename,
):
    return (
        "https://www.mediafire.com/file/"
        f"{quote(str(quickkey), safe='')}/"
        f"{quote(filename, safe='')}/file"
    )


# ============================================================================
# Main
# ============================================================================

def main(argv):

    eprint(
        f"mfcmd.py v{VERSION}"
    )

    email = ""
    password = ""
    filepath = ""
    upload_folder = ""
    supplied_hash = ""

    # ------------------------------------------------------------------------
    # Parse command line
    # ------------------------------------------------------------------------

    try:

        opts, args = getopt.getopt(
            argv,
            "e:p:u:h:f:",
            [
                "email=",
                "password=",
                "upload-folder=",
                "hash=",
                "file=",
            ],
        )

    except getopt.GetoptError as exc:

        eprint(
            f"Argument error: {exc}"
        )

        return 1

    for opt, arg in opts:

        if opt in (
            "-e",
            "--email",
        ):

            email = arg

        elif opt in (
            "-p",
            "--password",
        ):

            password = arg

        elif opt in (
            "-u",
            "--upload-folder",
        ):

            upload_folder = arg

        elif opt in (
            "-h",
            "--hash",
        ):

            supplied_hash = arg.lower()

        elif opt in (
            "-f",
            "--file",
        ):

            filepath = arg

    # ------------------------------------------------------------------------
    # Validate arguments
    # ------------------------------------------------------------------------

    if not email:

        eprint(
            "ERROR: email is required."
        )

        return 1

    if not password:

        eprint(
            "ERROR: password is required."
        )

        return 1

    if not filepath:

        eprint(
            "ERROR: file is required."
        )

        return 1

    filepath = os.path.abspath(
        filepath
    )

    if not os.path.isfile(filepath):

        eprint(
            f"ERROR: File does not exist:\n"
            f"{filepath}"
        )

        return 1

    filename = os.path.basename(
        filepath
    )

    filesize = os.path.getsize(
        filepath
    )

    if filesize <= 0:

        eprint(
            "ERROR: File is empty."
        )

        return 1

    if not upload_folder:

        upload_folder = DEFAULT_FOLDER

    # ------------------------------------------------------------------------
    # File information
    # ------------------------------------------------------------------------

    eprint()
    eprint(
        f"Filepath      : {filepath}"
    )
    eprint(
        f"Filename      : {filename}"
    )
    eprint(
        f"File size     : {human_size(filesize)}"
    )
    eprint(
        f"Upload folder : {upload_folder}"
    )

    # ------------------------------------------------------------------------
    # Hash file
    # ------------------------------------------------------------------------

    eprint()

    eprint(
        "Calculating MD5 and SHA-256..."
    )

    md5_hash, calculated_sha256, calculated_size = (
        calculate_file_hashes(filepath)
    )

    if calculated_size != filesize:

        eprint(
            "ERROR: File size changed while hashing."
        )

        return 1

    if supplied_hash:

        sha256_hash = supplied_hash

        eprint(
            "Using supplied SHA-256."
        )

    else:

        sha256_hash = calculated_sha256

    eprint(
        f"MD5     : {md5_hash}"
    )

    eprint(
        f"SHA-256 : {sha256_hash}"
    )

    # ------------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------------

    try:

        api = authenticate(
            email,
            password
        )

    except Exception as exc:

        eprint()
        eprint(
            f"Login Error: {exc}"
        )

        return 1

    print_account_info(api)

    eprint(
        "MediaFire API: Connected"
    )

    # ------------------------------------------------------------------------
    # Folder
    # ------------------------------------------------------------------------

    try:

        folder_key = get_folder_key(
            api,
            upload_folder
        )

    except Exception as exc:

        eprint()
        eprint(
            f"Folder Error: {exc}"
        )

        return 1

    eprint(
        f"Folder key   : {folder_key}"
    )

    # ------------------------------------------------------------------------
    # MediaFire upload/check
    # ------------------------------------------------------------------------

    eprint()

    eprint(
        "Checking MediaFire upload state..."
    )

    try:

        check = mediafire_upload_check(
            api,
            filename,
            filesize,
            sha256_hash,
            folder_key
        )

    except Exception as exc:

        eprint()
        eprint(
            f"MediaFire upload/check failed: {exc}"
        )

        return 1

    # ------------------------------------------------------------------------
    # Existing file
    # ------------------------------------------------------------------------

    hash_exists = (
        check.get("hash_exists")
        == "yes"
    )

    in_folder = (
        check.get("in_folder")
        == "yes"
    )

    file_exists = (
        check.get("file_exists")
        == "yes"
    )

    different_hash = (
        check.get(
            "different_hash",
            "no"
        )
        == "yes"
    )

    if (
        hash_exists
        and in_folder
        and file_exists
        and not different_hash
    ):

        quickkey = check.get(
            "duplicate_quickkey"
        )

        if quickkey:

            eprint()
            eprint(
                "File already exists on MediaFire."
            )

            print(
                make_mediafire_url(
                    quickkey,
                    filename
                )
            )

            return 0

    # ------------------------------------------------------------------------
    # Instant upload if MediaFire already has identical content elsewhere
    # ------------------------------------------------------------------------

    if hash_exists and not in_folder:

        eprint()
        eprint(
            "MediaFire already has this file."
        )

        eprint(
            "Attempting instant upload..."
        )

        try:

            instant = api.upload_instant(
                filename,
                filesize,
                sha256_hash,
                folder_key=folder_key,
                action_on_duplicate="keep"
            )

            quickkey = instant.get(
                "quickkey"
            )

            if quickkey:

                eprint(
                    "Instant upload successful."
                )

                print(
                    make_mediafire_url(
                        quickkey,
                        filename
                    )
                )

                return 0

        except Exception as exc:

            eprint(
                f"Instant upload unavailable: {exc}"
            )

            eprint(
                "Continuing with resumable upload."
            )

    # ------------------------------------------------------------------------
    # Resumable information
    # ------------------------------------------------------------------------

    resumable = check.get(
        "resumable_upload"
    )

    if not resumable:

        eprint()
        eprint(
            "ERROR: MediaFire did not provide "
            "resumable upload information."
        )

        eprint(
            f"MediaFire response: {check}"
        )

        return 1

    try:

        unit_size = int(
            resumable[
                "unit_size"
            ]
        )

        number_of_units = int(
            resumable[
                "number_of_units"
            ]
        )

    except (
        KeyError,
        TypeError,
        ValueError
    ) as exc:

        eprint()
        eprint(
            "ERROR: Invalid MediaFire resumable "
            f"upload information: {exc}"
        )

        eprint(
            f"Response: {resumable}"
        )

        return 1

    eprint()
    eprint(
        "MediaFire resumable upload parameters:"
    )

    eprint(
        f"Unit size    : {human_size(unit_size)}"
    )

    eprint(
        f"Unit count   : {number_of_units}"
    )

    eprint(
        f"Expected file: {human_size(filesize)}"
    )

    # ------------------------------------------------------------------------
    # Calculate unit hashes
    # ------------------------------------------------------------------------

    try:

        unit_hashes = calculate_unit_hashes(
            filepath,
            unit_size,
            number_of_units
        )

    except Exception as exc:

        eprint()
        eprint(
            f"Unit hash calculation failed: {exc}"
        )

        return 1

    # ------------------------------------------------------------------------
    # Decode MediaFire's resume bitmap
    # ------------------------------------------------------------------------

    bitmap = decode_bitmap(
        resumable.get("bitmap"),
        number_of_units
    )

    uploaded_units = [
        unit_id
        for unit_id, uploaded
        in bitmap.items()
        if uploaded
    ]

    uploaded_bytes = 0

    for unit_id in uploaded_units:

        offset = unit_id * unit_size

        uploaded_bytes += min(
            unit_size,
            max(
                0,
                filesize - offset
            )
        )

    eprint()
    eprint(
        f"Already uploaded: "
        f"{len(uploaded_units)}/{number_of_units} units"
    )

    if uploaded_units:

        eprint(
            f"Already uploaded: "
            f"{human_size(uploaded_bytes)}"
        )

    # ------------------------------------------------------------------------
    # If all units already exist, get upload key if possible.
    # ------------------------------------------------------------------------

    all_ready = (
        resumable.get(
            "all_units_ready",
            "no"
        )
        == "yes"
    )

    upload_key = None

    # ------------------------------------------------------------------------
    # Progress bar
    # ------------------------------------------------------------------------

    progress = tqdm(
        total=filesize,
        initial=uploaded_bytes,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=f"Uploading {filename}",
        bar_format=(
            "{desc}: "
            "{percentage:3.0f}%|{bar}| "
            "{n_fmt}/{total_fmt} "
            "[{elapsed}<{remaining}, {rate_fmt}]"
        ),
        file=sys.stderr
    )

    # ------------------------------------------------------------------------
    # Upload missing units
    # ------------------------------------------------------------------------

    try:

        for unit_id in range(
            number_of_units
        ):

            if bitmap.get(
                unit_id,
                False
            ):

                continue

            offset = (
                unit_id
                * unit_size
            )

            actual_size = min(
                unit_size,
                filesize - offset
            )

            eprint()
            eprint(
                f"Uploading unit "
                f"{unit_id + 1}/"
                f"{number_of_units} "
                f"({human_size(actual_size)})"
            )

            success = False

            last_error = None

            for attempt in range(
                1,
                UNIT_RETRIES + 1
            ):

                try:

                    response = upload_unit(
                        api=api,
                        filepath=filepath,
                        filesize=filesize,
                        file_hash=sha256_hash,
                        unit_hash=unit_hashes[
                            unit_id
                        ],
                        unit_id=unit_id,
                        unit_size=unit_size,
                        folder_key=folder_key
                    )

                    # MediaFire normally returns the upload key
                    # inside doupload.
                    doupload = response.get(
                        "doupload",
                        {}
                    )

                    candidate_key = (
                        doupload.get("key")
                        or response.get("key")
                    )

                    if candidate_key:

                        upload_key = candidate_key

                    success = True

                    progress.update(
                        actual_size
                    )

                    eprint(
                        f"Unit {unit_id + 1} "
                        f"uploaded successfully."
                    )

                    break

                except KeyboardInterrupt:

                    raise

                except Exception as exc:

                    last_error = exc

                    eprint(
                        f"Unit {unit_id + 1} "
                        f"attempt "
                        f"{attempt}/{UNIT_RETRIES} "
                        f"failed: {exc}"
                    )

                    if attempt < UNIT_RETRIES:

                        time.sleep(3)

            if not success:

                raise RuntimeError(
                    f"Failed to upload unit "
                    f"{unit_id + 1} after "
                    f"{UNIT_RETRIES} attempts: "
                    f"{last_error}"
                )

    except KeyboardInterrupt:

        progress.close()

        eprint()
        eprint(
            "Upload interrupted by user."
        )

        eprint(
            "MediaFire keeps successfully uploaded "
            "units on the server."
        )

        eprint(
            "Run the same command again to resume."
        )

        return 130

    except Exception as exc:

        progress.close()

        eprint()
        eprint(
            f"Upload failed: {exc}"
        )

        return 1

    finally:

        progress.close()

    # ------------------------------------------------------------------------
    # Verify upload/check again
    # ------------------------------------------------------------------------

    eprint()
    eprint(
        "Data transfer finished."
    )

    eprint(
        "Verifying MediaFire upload bitmap..."
    )

    try:

        final_check = mediafire_upload_check(
            api,
            filename,
            filesize,
            sha256_hash,
            folder_key
        )

    except Exception as exc:

        eprint(
            f"Final upload/check failed: {exc}"
        )

        return 1

    final_resumable = final_check.get(
        "resumable_upload"
    )

    if not final_resumable:

        eprint(
            "ERROR: MediaFire returned no "
            "resumable state after upload."
        )

        return 1

    final_unit_count = int(
        final_resumable.get(
            "number_of_units",
            number_of_units
        )
    )

    final_bitmap = decode_bitmap(
        final_resumable.get(
            "bitmap"
        ),
        final_unit_count
    )

    missing_units = [
        unit_id + 1
        for unit_id, uploaded
        in final_bitmap.items()
        if not uploaded
    ]

    final_all_ready = (
        final_resumable.get(
            "all_units_ready",
            "no"
        )
        == "yes"
    )

    if not final_all_ready:

        eprint()
        eprint(
            "MediaFire does not yet consider "
            "all units uploaded."
        )

        eprint(
            f"Missing units: {missing_units}"
        )

        eprint(
            "Run the command again to resume."
        )

        return 1

    eprint(
        "MediaFire reports all units are ready."
    )

    # ------------------------------------------------------------------------
    # Upload key
    # ------------------------------------------------------------------------

    if not upload_key:

        eprint()
        eprint(
            "WARNING: No upload key was returned "
            "during unit uploads."
        )

        eprint(
            "The data is on MediaFire, but this "
            "process cannot safely poll it without "
            "the upload key."
        )

        eprint(
            "Run the command again. MediaFire should "
            "recognize the completed upload."
        )

        return 1

    # ------------------------------------------------------------------------
    # Poll
    # ------------------------------------------------------------------------

    try:

        final_result = poll_upload(
            api,
            upload_key
        )

    except KeyboardInterrupt:

        eprint()
        eprint(
            "Polling interrupted."
        )

        eprint(
            "The upload data remains on MediaFire."
        )

        return 130

    except Exception as exc:

        eprint()
        eprint(
            f"Upload polling failed: {exc}"
        )

        return 1

    # ------------------------------------------------------------------------
    # Quickkey
    # ------------------------------------------------------------------------

    quickkey = final_result.get(
        "quickkey"
    )

    if not quickkey:

        eprint()
        eprint(
            "MediaFire finalized the request but "
            "returned no quickkey."
        )

        eprint(
            f"MediaFire response: {final_result}"
        )

        return 1

    # ------------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------------

    download_url = make_mediafire_url(
        quickkey,
        filename
    )

    eprint()
    eprint(
        "============================================================"
    )
    eprint(
        "UPLOAD SUCCESSFUL"
    )
    eprint(
        "============================================================"
    )
    eprint(
        f"Filename : {filename}"
    )
    eprint(
        f"Size     : {human_size(filesize)}"
    )
    eprint(
        f"Quickkey : {quickkey}"
    )
    eprint()
    eprint(
        "Download URL:"
    )

    print(
        download_url
    )

    return 0


# ============================================================================
# Entry point
# ============================================================================

if __name__ == "__main__":

    try:

        exit_code = main(
            sys.argv[1:]
        )

    except KeyboardInterrupt:

        eprint()
        eprint(
            "Interrupted by user."
        )

        exit_code = 130

    except Exception as exc:

        eprint()
        eprint(
            f"FATAL ERROR: {exc}"
        )

        exit_code = 1

    eprint(
        "Done."
    )

    sys.exit(
        exit_code
    )
