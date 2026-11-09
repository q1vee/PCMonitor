"""Модульные тесты: python -m unittest discover -s tests -v"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pcmonitor.collectors import collect_cpu, collect_disks, collect_memory, collect_uptime  # noqa: E402
from pcmonitor.report import build_report, format_report  # noqa: E402
from pcmonitor.storage import MetricsLogger, read_log  # noqa: E402
from pcmonitor.thresholds import CRITICAL, WARNING, Thresholds, check  # noqa: E402


def make_snapshot(ts="2026-11-09T10:00:00", cpu=20.0, ram=40.0, temp=60.0, free_pct=50.0, health="OK"):
    return {
        "timestamp": ts, "host": "test", "os": "Test",
        "cpu": {"percent": cpu, "freq_mhz": 3000},
        "memory": {"percent": ram, "used_gb": 4.0, "swap_percent": 1.0},
        "temperature": {"available": True, "sensors": [{"name": "CPU", "celsius": temp}], "max": temp},
        "disks": [{"mount": "C:\\", "percent": 100 - free_pct, "free_gb": 100, "total_gb": 200, "fstype": "NTFS"}],
        "smart": {"available": True, "drives": [{"model": "SSD", "health": health}]},
        "uptime": {"seconds": 3600},
    }


class ThresholdTests(unittest.TestCase):
    def test_normal_values_no_alerts(self):
        self.assertEqual(check(make_snapshot(), Thresholds()), [])

    def test_temperature_warning_and_critical(self):
        a = check(make_snapshot(temp=88), Thresholds())
        self.assertEqual([x.level for x in a], [WARNING])
        a = check(make_snapshot(temp=97), Thresholds())
        self.assertEqual([x.level for x in a], [CRITICAL])

    def test_low_disk_cpu_ram_smart(self):
        a = check(make_snapshot(cpu=99, ram=95, free_pct=5, health="FAIL"), Thresholds())
        self.assertEqual({x.metric for x in a}, {"cpu", "ram", "disk", "smart"})

    def test_load_thresholds_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text(json.dumps({"temp_warning": 70, "unknown": 1}), encoding="utf-8")
            t = Thresholds.load(p)
            self.assertEqual(t.temp_warning, 70)
            self.assertEqual(len(check(make_snapshot(temp=75), t)), 1)


class LogAndReportTests(unittest.TestCase):
    def _roundtrip(self, suffix):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / f"log{suffix}"
            lg = MetricsLogger(path)
            th = Thresholds()
            for i, (cpu, temp) in enumerate([(10, 50), (30, 90), (50, 70)]):
                s = make_snapshot(ts=f"2026-11-09T10:0{i}:00", cpu=cpu, temp=temp)
                lg.write(s, check(s, th))
            rows = read_log(path)
            self.assertEqual(len(rows), 3)
            rep = build_report(rows)
            self.assertEqual(rep["cpu_percent"], {"min": 10.0, "avg": 30.0, "max": 50.0})
            self.assertEqual(rep["samples_with_alerts"], 1)
            self.assertEqual(rep["duration"], "0:02:00")
            self.assertIn("ИТОГОВЫЙ ОТЧЁТ", format_report(rep))

    def test_csv(self):
        self._roundtrip(".csv")

    def test_jsonl(self):
        self._roundtrip(".jsonl")

    def test_broken_json_line_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "log.jsonl"
            p.write_text(json.dumps(make_snapshot()) + "\n{broken\n", encoding="utf-8")
            self.assertEqual(len(read_log(p)), 1)

    def test_empty_log_raises(self):
        with self.assertRaises(ValueError):
            build_report([])

    def test_missing_log_raises(self):
        with self.assertRaises(FileNotFoundError):
            read_log("нет_такого_файла.csv")


class CollectorTests(unittest.TestCase):
    """Проверка на реальной машине: сборщики возвращают осмысленные значения."""

    def test_cpu(self):
        c = collect_cpu(0.1)
        self.assertTrue(0 <= c["percent"] <= 100)

    def test_memory(self):
        m = collect_memory()
        self.assertGreater(m["total_gb"], 0)

    def test_disks(self):
        self.assertTrue(any("free_gb" in d for d in collect_disks()))

    def test_uptime(self):
        self.assertGreater(collect_uptime()["seconds"], 0)


if __name__ == "__main__":
    unittest.main()
