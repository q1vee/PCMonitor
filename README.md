# PC Monitor — мониторинг состояния ПК

Консольное приложение на Python. Оно собирает и показывает:

- загрузку CPU (общую и по ядрам) и частоту;
- занятость ОЗУ и файла подкачки;
- температуру (где доступно);
- свободное место на дисках;
- состояние SMART-атрибутов накопителей;
- время работы системы (uptime).

Показатели пишутся в лог (CSV или JSON Lines) с заданной периодичностью и проверяются по пороговым значениям. По логу формируется итоговый отчёт за период наблюдения.

## Требования

- Python 3.9+ (проверено на 3.12, Windows 10)
- ОС: Windows 10/11 (основная), Linux (CPU/ОЗУ/диски/температура через psutil, SMART через smartctl)

### Зависимости

| Пакет / программа | Обязательно | Назначение |
|---|---|---|
| [psutil](https://pypi.org/project/psutil/) | да | CPU, ОЗУ, диски, uptime, температура в Linux |
| PowerShell 5.1+ (входит в Windows) | для Windows | SMART через `Get-PhysicalDisk`, температура через WMI |
| [smartmontools](https://www.smartmontools.org/) (`smartctl`) | нет | подробные SMART-атрибуты (переназначенные сектора, наработка, износ) |
| [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) | нет | температура CPU/GPU/дисков в Windows, если запущен |

## Установка и запуск

```bash
cd Задание2_PCMonitor
python -m pip install -r requirements.txt
```

### Однократный снимок

```bash
python monitor.py snapshot
python monitor.py snapshot --json        # вывод в JSON
```

### Периодический мониторинг с логированием

```bash
# каждые 10 с, пока не нажат Ctrl+C, лог в logs/metrics_<дата>.csv
python monitor.py monitor

# каждые 5 с в течение 10 минут, JSON-лог, отчёт в файл по завершении
python monitor.py monitor -i 5 -d 600 -o logs/day.jsonl --report reports/day.txt

# фоновый режим: только предупреждения, без перерисовки экрана
python monitor.py monitor -i 30 --quiet
```

Параметры `monitor`:

| Ключ | По умолчанию | Описание |
|---|---|---|
| `-i, --interval` | 10 | период замеров, с |
| `-d, --duration` | 0 | длительность наблюдения, с (0 — до Ctrl+C) |
| `-o, --output` | `logs/metrics_<дата>.csv` | файл лога; формат по расширению: `.csv` или `.jsonl` |
| `--smart-every` | 300 | как часто обновлять SMART, с (опрос медленный) |
| `--quiet` | — | печатать только предупреждения |
| `--report FILE` | — | сохранить итоговый отчёт (`.txt` или `.json`) |

### Отчёт по готовому логу

```bash
python monitor.py report logs/demo.csv
python monitor.py report logs/demo.csv -o reports/report.json
```

### Общие ключи

`--thresholds thresholds.json` задаёт свои пороги, `--no-color` отключает цвет, `-v` включает отладочные сообщения.

> **Совет.** Чтобы получить температуру и счётчики надёжности дисков в Windows, запускайте консоль **от имени администратора** или держите запущенным LibreHardwareMonitor. Без этого программа работает, но помечает показатели как недоступные.

## Пороговые значения

По умолчанию пороги такие (их можно переопределить в `thresholds.json`):

| Параметр | Порог | Уровень |
|---|---|---|
| `cpu_percent` | > 90 % | WARNING |
| `ram_percent` | > 90 % | WARNING |
| `temp_warning` / `temp_critical` | > 85 °C / > 95 °C | WARNING / CRITICAL |
| `disk_free_percent` | свободно < 10 % | WARNING |
| `drive_temp` | накопитель > 55 °C | WARNING |
| `reallocated_sectors` | > 0 | WARNING |
| SMART health | Warning / Unhealthy | WARNING / CRITICAL |

Коды завершения: `0` — успех, `1` — ошибка (нет файла, неверные параметры), `2` — `snapshot` обнаружил CRITICAL.

## Архитектура

```
monitor.py                точка входа
pcmonitor/
  cli.py                  разбор аргументов (argparse), команды snapshot / monitor / report
  collectors.py           сбор показателей: psutil + PowerShell/WMI + smartctl
  thresholds.py           пороги (dataclass, загрузка из JSON) и проверка → список Alert
  storage.py              запись лога CSV/JSONL, чтение лога в плоские записи
  report.py               статистика за период (мин/ср/макс, частые предупреждения)
  display.py              форматированный цветной вывод в консоль
tests/test_pcmonitor.py   модульные тесты (unittest)
thresholds.json           пример файла порогов
logs/, reports/           демонстрационные лог и отчёт с реальной машины
```

Поток данных: `collectors.collect_all()` → снимок (dict) → `thresholds.check()` → предупреждения → `display.render()` (экран) и `storage.MetricsLogger.write()` (файл) → `report.build_report(read_log())` (итог).

**Обработка ошибок.** Сборщики не бросают исключения наружу. Сбой одного источника (нет прав, нет датчика, таймаут PowerShell) не останавливает мониторинг: в данных появляется поле `error` или `available: false` с пояснением. Повреждённые строки JSON-лога пропускаются, ошибки записи лога выводятся без остановки цикла, Ctrl+C корректно завершает мониторинг и строит отчёт.

**Источники температуры** (по порядку): `psutil.sensors_temperatures()` (Linux) → WMI-пространство LibreHardwareMonitor/OpenHardwareMonitor → `MSAcpi_ThermalZoneTemperature` (нужны права администратора).

**Источники SMART:** `smartctl -a -j` (если установлен) → `Get-PhysicalDisk` + `Get-StorageReliabilityCounter` (Windows).

## Тесты

```bash
python -m unittest discover -s tests -v
```

13 тестов проверяют пороги, запись и чтение CSV/JSONL, построение отчёта, обработку ошибок и работу сборщиков на текущей машине.
