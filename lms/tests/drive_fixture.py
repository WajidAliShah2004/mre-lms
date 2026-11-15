"""An in-memory drive for engine and job tests (D-083)."""

from pathlib import Path

from core.adapters.drive import DriveError, DriveFile, FetchError, Listing

SID = "storage:test"


class FakeDrive:
    """Lists what was put in it; fetch copies the bytes and records the key."""

    def __init__(self, source_id: str = SID) -> None:
        self.source_id = source_id
        self.files: dict[str, DriveFile] = {}
        self.data: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.full = True
        self.cursor = "c1"
        self.fail_list: str | None = None
        self.fail_fetch: set[str] = set()
        self.fetched: list[str] = []

    def put(self, key: str, body: bytes = b"x", *, modified: str, size: int | None = None,
            area: str = "own", native: bool = False, revision: str | None = None) -> DriveFile:
        name = key.rsplit("/", 1)[-1]
        f = DriveFile(self.source_id, key, f"Test/{key}", name,
                      len(body) if size is None else size, modified,
                      revision or f"{modified}:{len(body)}", area, None, native)
        self.files[key] = f
        self.data[key] = body
        return f

    def list(self, cursor):
        if self.fail_list:
            raise DriveError(self.fail_list)
        return Listing(tuple(self.files.values()), tuple(self.deleted), self.full, self.cursor)

    def fetch(self, f, dest_dir):
        if f.file_key in self.fail_fetch:
            raise FetchError(f"{f.path}: no bytes")
        self.fetched.append(f.file_key)
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        out = Path(dest_dir) / f.name
        out.write_bytes(self.data[f.file_key])
        return out
