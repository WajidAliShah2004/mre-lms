"""What every chat reader shares: the message types and the checked copy
(R40, R2, D-079, D-081).

A chat reader (WhatsApp, iMessage) reads an app's SQLite database from a
COPY: the file and its -wal and -shm are copied into a private directory and
checked with quick_check. SQLite never opens the original, so an app writing
beside us cannot be disturbed and nothing of his is ever written.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

COPY_ATTEMPTS = 3


class ChatDBError(RuntimeError):
    """The database is missing, torn, or not the shape the reader expects."""


@dataclass(frozen=True)
class Media:
    local_path: Path | None         # None: the file is not on the Mac
    title: str | None
    size: int | None
    duration_s: float | None        # None: not known (iMessage stores none)


@dataclass(frozen=True)
class Message:
    stanza_id: str                  # the source's own stable message id
    sent_at: datetime               # (WhatsApp ZSTANZAID, iMessage guid)
    from_me: bool
    sender_name: str
    text: str | None
    kind: str
    media: Media | None


@dataclass
class Chat:
    jid: str                        # the source's chat id (WhatsApp JID,
    name: str                       # iMessage chat_identifier)
    is_group: bool
    messages: list[Message] = field(default_factory=list)


def snapshot(src_dir: Path, tmp_dir: Path, db_name: str,
             attempts: int = COPY_ATTEMPTS,
             error: type[Exception] = ChatDBError) -> Path:
    """Copy the database and its WAL into tmp_dir; return the checked copy.

    The app may be writing while the three files are copied, so a copy can be
    torn. quick_check catches that; the copy is retried, then `error` is
    raised."""
    src = Path(src_dir) / db_name
    if not src.is_file():
        raise error(f"no {db_name} in {src_dir}")
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(tmp_dir, 0o700)
    last = ""
    for n in range(attempts):
        dest = tmp_dir / f"copy{n}"
        dest.mkdir(mode=0o700)
        for suffix in ("", "-wal", "-shm"):
            f = Path(src_dir) / (db_name + suffix)
            if f.exists():
                shutil.copyfile(f, dest / f.name)
        copy = dest / db_name
        try:
            conn = sqlite3.connect(copy)
            try:
                last = conn.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                conn.close()
        except sqlite3.DatabaseError as exc:
            last = str(exc)
        if last == "ok":
            return copy
    raise error(f"copy failed quick_check {attempts} times: {last}")
