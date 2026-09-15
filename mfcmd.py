#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
mfcmd.py v0.86

MediaFire resumable uploader com Parallel Unit Uploads,
Clean Terminal UI, tratamento robusto de upload_key e suporte a Instant Upload.
"""

import argparse
import getpass
import hashlib
import io
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from tqdm import tqdm
from mediafire import MediaFireApi


# ============================================================================
# Configuration | Configuração
# ============================================================================

VERSION = "0.86"
MEDIAFIRE_APP_ID = "42511"
UNIT_RETRIES = 5
POLL_INTERVAL = 5
HASH_BUFFER_SIZE = 1024 * 1024
DEFAULT_FOLDER = "My Files"
MAX_PARALLEL_UPLOADS = 4
LOG_FILE = "upload_mfcmd.log"

auth_lock = threading.Lock()


# ============================================================================
# Logging Setup | Configuração de Registro
# ============================================================================

def setup_logger():
    logger = logging.getLogger("mfcmd")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()


def log_and_print(message="", to_console=False):
    if message:
        logger.info(message)
    if to_console:
        tqdm.write(message, file=sys.stderr)


# ============================================================================
# Console Helpers & Hashing | Auxiliares de Console e Hashing 
# ============================================================================

def human_size(value):
    value = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if value < 1024:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PiB"


def calculate_file_hashes(filepath):
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

    return md5.hexdigest().lower(), sha256.hexdigest().lower(), total


def calculate_unit_hashes(filepath, unit_size, number_of_units):
    hashes = []
    logger.info(f"Calculating SHA-256 for {number_of_units} MediaFire units...")

    with open(filepath, "rb") as fd:
        while True:
            data = fd.read(unit_size)
            if not data:
                break
            digest = hashlib.sha256(data).hexdigest().lower()
            hashes.append(digest)

    if len(hashes) != number_of_units:
        raise RuntimeError(
            f"MediaFire unit count mismatch: API returned {number_of_units}, but local file produced {len(hashes)}."
        )

    return hashes


# ============================================================================
# MediaFire Bitmap & Auth | Bitmap e Autenticação do MediaFire
# ============================================================================

def decode_bitmap(bitmap_node, number_of_units):
    result = {unit_id: False for unit_id in range(number_of_units)}
    if not bitmap_node:
        return result

    count = int(bitmap_node.get("count", 0))
    words = bitmap_node.get("words", [])
    bitmap = 0

    for token_id in range(count):
        if token_id >= len(words):
            break
        value = int(words[token_id])
        bitmap |= (value << (0xF * token_id))

    for unit_id in range(number_of_units):
        mask = 1 << unit_id
        result[unit_id] = (bitmap & mask) == mask

    return result


def authenticate(email, password):
    logger.info("Authenticating with MediaFire SDK...")
    api = MediaFireApi()
    session = api.user_get_session_token(app_id=MEDIAFIRE_APP_ID, email=email, password=password)

    if not session:
        raise RuntimeError("MediaFire returned an empty session token.")

    api.session = session
    logger.info("MediaFire authentication successful.")
    return api


def get_folder_key(api, folder_name):
    if not folder_name or folder_name.lower() in ("my files", "myfiles"):
        return "myfiles"

    logger.info(f"Searching MediaFire folder: {folder_name}")
    try:
        result = api.folder_search(search_text=folder_name, folder_key="myfiles")
    except Exception as exc:
        raise RuntimeError(f"Folder search failed: {exc}")

    folder_content = result.get("folder_content", {})
    folders = folder_content.get("folders", [])

    for folder in folders:
        name = folder.get("name") or folder.get("foldername")
        if name != folder_name:
            continue
        folder_key = folder.get("folderkey") or folder.get("folder_key")
        if folder_key:
            logger.info(f"Folder found: {folder_key}")
            return folder_key

    raise RuntimeError(f"MediaFire folder '{folder_name}' was not found.")


def mediafire_upload_check(api, filename, filesize, sha256_hash, folder_key):
    return api.upload_check(
        filename=filename,
        size=filesize,
        hash_=sha256_hash,
        folder_key=folder_key,
        resumable=True,
    )


class UnitFile(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.len = len(data)


def read_file_unit(filepath, offset, size):
    with open(filepath, "rb") as fd:
        fd.seek(offset, os.SEEK_SET)
        data = fd.read(size)
    return data


def upload_unit(api, filepath, filesize, file_hash, unit_hash, unit_id, unit_size, folder_key, email, password):
    offset = unit_id * unit_size
    remaining = filesize - offset
    actual_size = min(unit_size, remaining)

    data = read_file_unit(filepath, offset, actual_size)
    if len(data) != actual_size:
        raise IOError(f"Could not read unit {unit_id + 1}: expected {actual_size} bytes, got {len(data)}.")

    actual_hash = hashlib.sha256(data).hexdigest().lower()
    if actual_hash != unit_hash:
        raise RuntimeError(f"Unit {unit_id + 1} SHA-256 mismatch.")

    unit_fd = UnitFile(data)
    try:
        response = api.upload_resumable(
            unit_fd, filesize, file_hash, unit_hash, unit_id, actual_size,
            folder_key=folder_key, action_on_duplicate="keep"
        )
        
        if isinstance(response, dict) and response.get("result") == "ERROR":
            if int(response.get("error", 0)) == 105:
                raise PermissionError("Session expired")

    except Exception as exc:
        if "105" in str(exc) or "expired" in str(exc).lower():
            with auth_lock:
                log_and_print(f"[Sessão Expirada] Renovando token na unidade {unit_id + 1}...", to_console=True)
                session = api.user_get_session_token(app_id=MEDIAFIRE_APP_ID, email=email, password=password)
                api.session = session
            
            unit_fd.seek(0)
            response = api.upload_resumable(
                unit_fd, filesize, file_hash, unit_hash, unit_id, actual_size,
                folder_key=folder_key, action_on_duplicate="keep"
            )
        else:
            raise exc
    finally:
        unit_fd.close()

    return response


def poll_upload(api, upload_key):
    logger.info(f"Polling upload key: {upload_key}")

    while True:
        response = api.upload_poll(upload_key)
        doupload = response.get("doupload", {})
        status = int(doupload.get("status", 0))
        result_code = int(doupload.get("result", 0))
        description = doupload.get("description", "")

        logger.info(f"Polling Status: {status} | Result: {result_code} | Desc: {description}")

        file_error = doupload.get("fileerror")
        if file_error:
            raise RuntimeError(f"MediaFire file error: {file_error}")

        quickkey = doupload.get("quickkey")
        if quickkey:
            return doupload

        if result_code != 0:
            raise RuntimeError(f"MediaFire upload polling failed: {doupload}")

        time.sleep(POLL_INTERVAL)


def make_mediafire_url(quickkey, filename):
    return f"https://www.mediafire.com/file/{quote(str(quickkey), safe='')}/{quote(filename, safe='')}/file"


# ============================================================================
# Main Program | Programa Principal
# ============================================================================

def main(argv=None):
    logger.info(f"=== Starting mfcmd.py v{VERSION} ===")

    parser = argparse.ArgumentParser(description="Envio otimizado de arquivos em partes para o MediaFire.")
    parser.add_argument("-e", "--email", required=True, help="E-mail da sua conta MediaFire")
    parser.add_argument("-p", "--password", help="Senha da conta")
    parser.add_argument("-f", "--file", required=True, help="Caminho do arquivo local")
    parser.add_argument("-u", "--upload-folder", default=DEFAULT_FOLDER, help="Pasta de destino")
    parser.add_argument("-s", "--hash", dest="supplied_hash", help="Hash SHA-256 pré-calculado")
    parser.add_argument("-t", "--threads", type=int, default=MAX_PARALLEL_UPLOADS, help="Uploads simultâneos")

    args = parser.parse_args(argv)

    email = args.email
    password = args.password or os.environ.get("MEDIAFIRE_PASSWORD")

    if not password:
        try:
            password = getpass.getpass("Senha do MediaFire: ")
        except (KeyboardInterrupt, EOFError):
            return 130

    filepath = os.path.abspath(args.file)
    if not os.path.isfile(filepath):
        log_and_print(f"ERRO: O arquivo não existe: {filepath}", to_console=True)
        return 1

    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)

    if filesize <= 0:
        log_and_print("ERRO: O arquivo está vazio.", to_console=True)
        return 1

    upload_folder = args.upload_folder
    supplied_hash = (args.supplied_hash or "").lower().strip()
    parallel_threads = max(1, args.threads)

    log_and_print(f"Arquivo   : {filename}", to_console=True)
    log_and_print(f"Tamanho   : {human_size(filesize)} | Threads: {parallel_threads}", to_console=True)
    log_and_print(f"Log detalhado salvo em: {LOG_FILE}\n", to_console=True)

    logger.info("Calculando hashes MD5 e SHA-256...")
    md5_hash, calculated_sha256, calculated_size = calculate_file_hashes(filepath)

    if calculated_size != filesize:
        log_and_print("ERRO: Tamanho do arquivo alterado durante a leitura.", to_console=True)
        return 1

    sha256_hash = supplied_hash if supplied_hash else calculated_sha256
    logger.info(f"MD5: {md5_hash} | SHA-256: {sha256_hash}")

    try:
        api = authenticate(email, password)
        folder_key = get_folder_key(api, upload_folder)
        check = mediafire_upload_check(api, filename, filesize, sha256_hash, folder_key)
    except Exception as exc:
        log_and_print(f"Erro na conexão com MediaFire: {exc}", to_console=True)
        return 1

    if check.get("hash_exists") == "yes" and check.get("in_folder") == "yes":
        quickkey = check.get("duplicate_quickkey")
        if quickkey:
            log_and_print("Arquivo já existe no MediaFire.", to_console=True)
            print(make_mediafire_url(quickkey, filename))
            return 0

    if check.get("hash_exists") == "yes" and check.get("in_folder") != "yes":
        logger.info("Tentando upload instantâneo (Instant Upload)...")
        try:
            instant = api.upload_instant(filename, filesize, sha256_hash, folder_key=folder_key, action_on_duplicate="keep")
            quickkey = instant.get("quickkey")
            if quickkey:
                log_and_print("Upload instantâneo concluído com sucesso!", to_console=True)
                print(make_mediafire_url(quickkey, filename))
                return 0
        except Exception as exc:
            logger.warning(f"Upload instantâneo indisponível: {exc}")

    resumable = check.get("resumable_upload")
    if not resumable:
        log_and_print("ERRO: Informações de upload resumível indisponíveis.", to_console=True)
        return 1

    unit_size = int(resumable["unit_size"])
    number_of_units = int(resumable["number_of_units"])

    logger.info(f"Upload resumível: {number_of_units} partes de {human_size(unit_size)}")
    unit_hashes = calculate_unit_hashes(filepath, unit_size, number_of_units)

    bitmap = decode_bitmap(resumable.get("bitmap"), number_of_units)
    uploaded_units = [u for u, uploaded in bitmap.items() if uploaded]
    uploaded_bytes = sum(min(unit_size, max(0, filesize - u * unit_size)) for u in uploaded_units)

    if uploaded_units:
        log_and_print(f"Retomando envio: {len(uploaded_units)}/{number_of_units} partes já enviadas.", to_console=True)

    upload_key = resumable.get("key") or check.get("upload_key")
    lock = threading.Lock()

    progress = tqdm(
        total=filesize,
        initial=uploaded_bytes,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc="Enviando",
        bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        file=sys.stderr,
    )

    pending_units = [u for u in range(number_of_units) if not bitmap.get(u, False)]

    def worker(unit_id):
        nonlocal upload_key
        offset = unit_id * unit_size
        actual_size = min(unit_size, filesize - offset)

        for attempt in range(1, UNIT_RETRIES + 1):
            try:
                response = upload_unit(
                    api=api, filepath=filepath, filesize=filesize, file_hash=sha256_hash,
                    unit_hash=unit_hashes[unit_id], unit_id=unit_id, unit_size=unit_size,
                    folder_key=folder_key, email=email, password=password
                )

                candidate_key = (
                    response.get("doupload", {}).get("key") 
                    or response.get("key") 
                    or response.get("resumable_upload", {}).get("key")
                )
                with lock:
                    if candidate_key:
                        upload_key = candidate_key
                    progress.update(actual_size)
                return True
            except Exception as exc:
                logger.warning(f"Tentativa {attempt} para unidade {unit_id + 1} falhou: {exc}")
                if attempt < UNIT_RETRIES:
                    time.sleep(2)

        raise RuntimeError(f"Unidade {unit_id + 1} falhou após {UNIT_RETRIES} tentativas.")

    try:
        if pending_units:
            with ThreadPoolExecutor(max_workers=parallel_threads) as executor:
                futures = [executor.submit(worker, u) for u in pending_units]
                for future in as_completed(futures):
                    future.result()
    except Exception as exc:
        progress.close()
        log_and_print(f"\nFalha durante o upload: {exc}", to_console=True)
        return 1
    finally:
        progress.close()

    logger.info("Transferência concluída. Verificando estado final no MediaFire...")

    try:
        final_check = mediafire_upload_check(api, filename, filesize, sha256_hash, folder_key)
    except Exception as exc:
        if "105" in str(exc) or "expired" in str(exc).lower():
            logger.info("Renovando sessão para verificação final...")
            api.session = api.user_get_session_token(app_id=MEDIAFIRE_APP_ID, email=email, password=password)
            final_check = mediafire_upload_check(api, filename, filesize, sha256_hash, folder_key)
        else:
            log_and_print(f"Erro na verificação final: {exc}", to_console=True)
            return 1

    final_resumable = final_check.get("resumable_upload", {})
    if not upload_key:
        upload_key = final_resumable.get("key") or final_check.get("upload_key")

    if not upload_key:
        log_and_print("Erro: Nenhuma chave de upload foi retornada pela API do MediaFire.", to_console=True)
        return 1

    try:
        final_result = poll_upload(api, upload_key)
    except Exception as exc:
        log_and_print(f"Erro ao aguardar processamento final: {exc}", to_console=True)
        return 1

    quickkey = final_result.get("quickkey")
    if not quickkey:
        log_and_print("Erro: MediaFire não retornou a chave de download.", to_console=True)
        return 1

    download_url = make_mediafire_url(quickkey, filename)

    logger.info("============================================================")
    logger.info("UPLOAD CONCLUÍDO COM SUCESSO!")
    logger.info(f"Arquivo  : {filename}")
    logger.info(f"Quickkey : {quickkey}")
    logger.info(f"Link     : {download_url}")
    logger.info("============================================================")

    print("\n============================================================", file=sys.stderr)
    print("UPLOAD CONCLUÍDO COM SUCESSO!", file=sys.stderr)
    print("============================================================", file=sys.stderr)
    print(f"Arquivo  : {filename}", file=sys.stderr)
    print(f"Quickkey : {quickkey}\n", file=sys.stderr)
    print("Link de Download:", file=sys.stderr)
    print(download_url)

    return 0


if __name__ == "__main__":
    try:
        exit_code = main(sys.argv[1:])
    except KeyboardInterrupt:
        log_and_print("\nOperação interrompida pelo usuário.", to_console=True)
        exit_code = 130
    except Exception as exc:
        log_and_print(f"\nERRO FATAL: {exc}", to_console=True)
        exit_code = 1

    sys.exit(exit_code)
