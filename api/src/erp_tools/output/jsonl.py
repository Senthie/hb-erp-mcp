"""JSONL 输出 + 断点续跑。

为什么用 JSONL 而不是「每条一个 JSON 文件」：
  * 18496 条记录会产生 18496 个目录，重跑时无法判断哪条已完成；
  * 追加写 + 以业务主键去重，天然支持中断续跑、失败重试；
  * 下游（pandas / duckdb / jq）直接读。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JsonlSink:
    """线程安全的追加式 JSONL 写入器，可按主键跳过已完成记录。"""

    def __init__(self, path: str | Path, *, key: str = "id", resume: bool = True) -> None:
        self.path = Path(path)
        self.key = key
        self._lock = threading.Lock()
        self._fh = None
        self._done: set[str] = set()
        if resume:
            self._done = self._scan_done()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- 续跑 ----------
    def _scan_done(self) -> set[str]:
        if not self.path.is_file():
            return set()
        done: set[str] = set()
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                val = rec.get(self.key)
                if val is not None:
                    done.add(str(val))
        return done

    @property
    def done_count(self) -> int:
        return len(self._done)

    def is_done(self, key_value: Any) -> bool:
        return str(key_value) in self._done

    # ---------- 写入 ----------
    def write(self, record: dict) -> bool:
        """写入一条；若主键已完成则跳过并返回 False。"""
        key_value = record.get(self.key)
        if key_value is not None and str(key_value) in self._done:
            return False
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            if self._fh is None:
                self._fh = self.path.open("a", encoding="utf-8")
            self._fh.write(line + "\n")
            self._fh.flush()
            if key_value is not None:
                self._done.add(str(key_value))
        return True

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> "JsonlSink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_jsonl(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
