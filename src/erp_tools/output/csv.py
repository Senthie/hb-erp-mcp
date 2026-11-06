"""CSV 输出。

编码用 utf-8-sig：Excel 双击直接打开中文不乱码。
列顺序优先用显式传入的 columns；不传则取第一条记录的键顺序，后续记录按同一表头写。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence


class CsvSink:
    """流式 CSV 写入器（表头只写一次）。"""

    def __init__(self, path: str | Path, columns: Sequence[str] | None = None,
                 *, encoding: str = "utf-8-sig") -> None:
        self.path = Path(path)
        self.columns: list[str] | None = list(columns) if columns else None
        self.encoding = encoding
        self._fh = None
        self._writer: csv.DictWriter | None = None
        self.count = 0

    def write(self, record: dict) -> None:
        if self._writer is None:
            if self.columns is None:
                self.columns = list(record.keys())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # newline="" 是 csv 模块在 Windows 下的必需写法
            self._fh = self.path.open("w", encoding=self.encoding, newline="")
            self._writer = csv.DictWriter(self._fh, fieldnames=self.columns,
                                          extrasaction="ignore")
            self._writer.writeheader()
        self._writer.writerow({k: _cell(record.get(k)) for k in self.columns})
        self.count += 1

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._writer = None

    def __enter__(self) -> "CsvSink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def write_csv(path: str | Path, records: Iterable[dict],
              columns: Sequence[str] | None = None) -> int:
    """一次性写出一批记录，返回写入行数。"""
    sink = CsvSink(path, columns)
    try:
        for rec in records:
            sink.write(rec)
    finally:
        sink.close()
    return sink.count


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", " ").replace("\n", " ").strip()
