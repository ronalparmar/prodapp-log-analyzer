# ProdApp Log Analyzer

A complete log-analysis application for Posten Bring Android ShopApp (TC57) log files.

## Features

- **Upload**: Accept `.txt` or `.zip` log files via web UI or REST API
- **Parse**: Extract device ID, app version, emails, scan events, and exceptions from log files
- **Search**: Search by item number, device ID, or exception type with optional filters (date range, environment, process)
- **Analytics**: Per-device stats (scan vs manual counts, top items, exception frequency)
- **Download**: Download original uploaded files
- **CLI**: Command-line tool for ingesting, searching, and reporting
- **Web UI**: Server-rendered web interface built with Jinja2 templates

---

## Prerequisites

- Python 3.10+ (for local dev)
- Docker & Docker Compose (for containerized run)

---

## Local Run (Development)

### 1. Install dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Start the server

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Then open **http://localhost:8000** in your browser.

### 3. Run tests

```bash
# From repo root
pip install -r requirements-test.txt
pytest tests/ -v
```

---

## Docker Run

### Start with Docker Compose

```bash
docker compose up --build
```

Then open **http://localhost:8000** in your browser.

Data is persisted in the `./data/` directory (SQLite database + uploaded files).

### Stop

```bash
docker compose down
```

---

## Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./data/log_analyzer.db` | SQLAlchemy database URL |
| `UPLOAD_DIR` | `./data/uploads` | Directory for storing uploaded log files |

---

## Usage

### Web UI

| Page | URL | Description |
|------|-----|-------------|
| Home | `/` | Overview |
| Upload | `/upload` | Upload a `.txt` or `.zip` log file |
| Search | `/search` | Search events by item/device/exception |
| Uploads | `/uploads` | List all uploads + download originals |
| Device | `/devices/{device_id}` | Per-device summary and stats |

### REST API

#### Upload a file

```bash
curl -X POST http://localhost:8000/api/v1/uploads \
  -F "file=@samples/shopappNorwayLog_7.txt"
```

Response:
```json
{"upload_id": 1, "log_file_ids": [1]}
```

#### Search by item number

```bash
curl "http://localhost:8000/api/v1/search?q=00370438104439901228&search_type=item"
```

#### Search by device ID

```bash
curl "http://localhost:8000/api/v1/search?q=TC5705956&search_type=device"
```

#### Search by exception

```bash
curl "http://localhost:8000/api/v1/search?q=Exception&search_type=exception"
```

#### Download original upload

```bash
curl -OJ http://localhost:8000/uploads/1/download
```

---

## CLI

The CLI tool is in `cli/shopapp_logs_cli.py`.

### Ingest a log file

```bash
python cli/shopapp_logs_cli.py ingest samples/shopappNorwayLog_7.txt
```

### Search by item number

```bash
python cli/shopapp_logs_cli.py search --item 00370438104439901228
python cli/shopapp_logs_cli.py search --item 00370438104439901228 --format json
```

### Search by device ID

```bash
python cli/shopapp_logs_cli.py search --device TC5705956
```

### Search by exception

```bash
python cli/shopapp_logs_cli.py search --exception Exception
```

### Generate a report

```bash
# JSON report for a specific device
python cli/shopapp_logs_cli.py report --device TC5705956

# CSV report for all devices
python cli/shopapp_logs_cli.py report --format csv

# Use a custom database
python cli/shopapp_logs_cli.py --db sqlite:///mydata.db report --device TC5705956
```

---

## Log Format

Log files follow this format:

```
YYYY-MM-DD HH:MM:SS.mmmm <message>
```

### Extracted metadata

| Field | Example line |
|-------|-------------|
| `deviceId` | `Device set up ---- Device Id - TC5705956` |
| `appVersion` | `App version ---- 550.26.1.1` |
| `email` | `Validated User & Authorised User - user@tcs.com, App Version : ...` |
| `packageName` | `Database path set to: /data/user/0/com.postenbring.shopapp.norwayqa/...` |
| `environment` | Derived: `QA` if package ends with `qa`, else `PROD` |

### Scan events

```
Scanned package number <ITEM> bar code format <FORMAT> in <PROCESS> process
```

- `entryMode = "manual"` if `barcodeFormat == "ManualInput"`
- `entryMode = "scan"` otherwise

### Exception detection

A line is classified as an exception if it contains:
- `Exception:` (any casing)
- `TaskSchedulerOnUnobservedTaskException`
- `Unhandled`

---

## Project Structure

```
prodapp-log-analyzer/
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI application
│   │   ├── parsing/
│   │   │   └── shopapp_parser.py  # Log parser
│   │   ├── db/
│   │   │   └── models.py          # SQLAlchemy models (SQLite)
│   │   ├── storage/
│   │   │   └── filesystem.py      # File storage
│   │   ├── services/
│   │   │   └── analytics.py       # Analytics/stats
│   │   └── templates/             # Jinja2 HTML templates
│   └── requirements.txt
├── cli/
│   └── shopapp_logs_cli.py        # CLI tool
├── tests/
│   └── test_parser.py             # Parser unit tests
├── samples/
│   └── shopappNorwayLog_7.txt     # Sample log file
├── docs/
│   └── soti-mobicontrol-api.md    # SOTI MobiControl API guide
├── Dockerfile
├── docker-compose.yml
└── README.md
```
