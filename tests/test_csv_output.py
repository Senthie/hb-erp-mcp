"""CSV 输出测试。"""

import csv

from erp_tools.output.csv import CsvSink, write_csv


def test_write_csv_with_explicit_columns(tmp_path):
    path = tmp_path / "out.csv"
    rows = [{"a": 1, "b": "中文", "c": None}, {"a": 2, "b": "多行\n内容", "c": "x"}]
    n = write_csv(path, rows, ["a", "b"])

    assert n == 2
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # utf-8-sig，Excel 直接打开不乱码

    with path.open(encoding="utf-8-sig", newline="") as fh:
        got = list(csv.DictReader(fh))
    assert [r["a"] for r in got] == ["1", "2"]
    assert got[1]["b"] == "多行 内容"      # 换行被压成空格，不破坏行结构


def test_columns_taken_from_first_record(tmp_path):
    path = tmp_path / "auto.csv"
    sink = CsvSink(path)
    sink.write({"x": "1", "y": "2"})
    sink.write({"x": "3", "y": "4"})
    sink.close()

    assert sink.count == 2
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "x,y"
    assert len(lines) == 3


def test_ignores_extra_keys(tmp_path):
    path = tmp_path / "subset.csv"
    write_csv(path, [{"a": "1", "b": "2", "c": "3"}], ["a"])
    assert path.read_text(encoding="utf-8-sig").splitlines() == ["a", "1"]
