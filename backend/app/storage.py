"""文件存储：data/files 下按 sha256 存证，同指纹秒传去重。"""
import hashlib
import shutil
from pathlib import Path

import aiofiles

from .config import settings


def files_root() -> Path:
    root = Path(settings.files_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def save_upload(data: bytes, original_name: str) -> tuple[str, str, int]:
    """保存上传内容，返回 (sha256, stored_path, size)。

    内容寻址：data/files/<sha256前2位>/<sha256>__<安全文件名>；
    同指纹文件已存在则跳过写入（保留原名文件）。
    """
    digest = hashlib.sha256(data).hexdigest()
    safe_name = Path(original_name).name.replace("\\", "_") or "unnamed"
    rel_dir = digest[:2]
    target_dir = files_root() / rel_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_path = str(target_dir / f"{digest}__{safe_name}")
    if not Path(stored_path).exists():
        async with aiofiles.open(stored_path, "wb") as f:
            await f.write(data)
    return digest, stored_path, len(data)


def read_bytes(stored_path: str) -> bytes:
    return Path(stored_path).read_bytes()


def remove_file(stored_path: str) -> None:
    Path(stored_path).unlink(missing_ok=True)


def spare_copy(stored_path: str) -> None:
    """占位：避免误删共享内容寻址文件的语义提醒——同一 sha256 可能被多个 Document 引用。"""
    _ = stored_path


def delete_upload_checked(stored_path: str, refs: int) -> None:
    """仅当没有其他 Document 引用同一 stored_path 时才物理删除。"""
    if refs <= 0:
        remove_file(stored_path)


_ = (shutil, spare_copy)
