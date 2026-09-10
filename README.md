# Domain Classification Pipeline

Local LLM-based website/domain classifier. Reads daily "uncategorized query" CSVs
straight from a local folder, merges/dedupes them, fetches each domain's homepage
(with subdomain fallback), classifies it with a local Llama 3.1 8B GGUF model, and
writes results into a SQLite DB.

> **Note:** the previous SSH download step has been removed. The pipeline now reads
> CSVs directly from a local folder (`INPUT_DIR`) — point that folder at wherever
> your CSVs already land (a mounted network share, a sync job, another script's
> output directory, etc.).

---

## 1. Prerequisites

- Python 3.10+
- An NVIDIA GPU + CUDA drivers if you want GPU inference (CPU also works, just slower)
- A folder that already receives the daily CSV exports (the ones with `query` and
  `category` columns)
- A `site.db` SQLite database created by the companion Flask app (`app.py`), with
  `dom`, `unknown`, and `user` tables

## 2. Get the code

```bash
git clone <your-repo-url>
cd <your-repo-folder>
```

Place `pipeline.py` at the root of your IT repo (or wherever you keep pipeline
scripts).

## 3. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install pandas requests tldextract trafilatura jsonschema llama-cpp-python python-crontab
```

If you're on GPU and want CUDA-accelerated inference, install `llama-cpp-python`
with CUDA support instead of the plain wheel:

```bash
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
```

## 4. Download the model

Download `Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` and place it at:

```
<repo-folder>/models-gguf/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
```

Or point `MODEL_GGUF` at wherever you've stored it (see step 6).

## 5. Set up the database

The pipeline expects `instance/site.db` to already exist with the required
tables. If you don't have it yet, run your Flask app once to create it:

```bash
python app.py
```

## 6. Configure environment variables

All configuration is via environment variables (all optional — sane defaults are
built in). The important one for this setup is `INPUT_DIR`:

| Variable | Default | Description |
|---|---|---|
| `INPUT_DIR` | `/mnt/input` | Folder to read daily CSVs from. **Set this to wherever your CSVs land.** |
| `FILTER_BY_DATE` | `true` | If `true`, only picks up files whose name contains yesterday's date (`YYYY-MM-DD`). Set to `false` to pick up every `.csv` in the folder regardless of name. |
| `MOVE_PROCESSED_FILES` | `true` | If `true`, matched CSVs are moved out of `INPUT_DIR` into `downloads/` after being read, so they aren't reprocessed tomorrow. Set to `false` to leave the source folder untouched (files are copied instead). |
| `DEVICE` | `gpu` | `gpu` or `cpu` |
| `N_GPU_LAYERS` | `-1` (all layers on GPU) | Set `0` to force CPU-only inference |
| `MODEL_GGUF` | `models-gguf/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf` | Path to the GGUF model file |
| `MAX_DOMAINS_PER_DAY` | `100` | Cap on how many new domains are classified per run |
| `MAX_WORKERS` | `6` | Parallel HTTP fetch threads |
| `BATCH_SIZE` | `20` | Domains processed per batch |
| `HTTP_TIMEOUT` | `15` | Seconds before an HTTP request times out |
| `HTTP_RETRIES` | `3` | Retries per URL variant on timeout |
| `USE_CACHE` | `true` | Cache fetched HTML to avoid re-downloading on reruns |
| `CRON_HOUR` / `CRON_MINUTE` | `2` / `40` | Daily cron schedule (used by `install`) |

Example:

```bash
export INPUT_DIR=/data/exports/uncategorized
export DEVICE=gpu
export MAX_DOMAINS_PER_DAY=150
```

(On Windows PowerShell: `$env:INPUT_DIR = "D:\exports\uncategorized"`)

## 7. Run it

```bash
python pipeline.py run
```

What happens, step by step:

1. **Local File Intake** — scans `INPUT_DIR` for CSVs (filtered by yesterday's date
   unless `FILTER_BY_DATE=false`), and moves/copies matches into `downloads/`.
2. **Merge & Filter** — combines all matched CSVs, keeps only rows where
   `category == "uncategorized"`, cleans and deduplicates the domains, and writes
   `output/to_scrape_today.csv`.
3. **Filter Already Processed** — drops domains already present in the `dom` table,
   caps the remainder at `MAX_DOMAINS_PER_DAY`, and writes `output/new_domains.csv`.
4. **Classification** — for each domain: fetches the homepage (falling back through
   parent subdomains if the exact host doesn't respond), extracts readable text,
   runs it through the local LLM with a strict JSON-schema grammar plus a
   keyword-heuristic pass, and inserts the result into `site.db` (`dom` table on
   success, `unknown` table on failure).

Logs are written to `logs/pipeline_YYYYMMDD.log` and echoed to the console.

## 8. Automate it (optional)

Install a daily cron job (Linux/macOS):

```bash
python pipeline.py install     # installs at CRON_HOUR:CRON_MINUTE (default 02:40)
python pipeline.py list        # view installed jobs
python pipeline.py uninstall   # remove it
```

On Windows, use Task Scheduler to run `python pipeline.py run` on your preferred
schedule instead — `install`/`uninstall`/`list` rely on `python-crontab`, which is
Linux/macOS-only.

## 9. Check status anytime

```bash
python pipeline.py status
```

Shows current config, folder/model/DB health, domains tracked so far, and any
installed cron job.

## 10. Folder layout after first run

```
<repo-folder>/
├── pipeline.py
├── models-gguf/
│   └── Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf
├── instance/
│   └── site.db
├── downloads/        # CSVs pulled in from INPUT_DIR
├── output/           # to_scrape_today.csv, new_domains.csv
├── logs/             # daily run logs
└── cache/            # cached homepage fetches (if USE_CACHE=true)
```

## Troubleshooting

- **"Database not found"** — run your Flask app (`python app.py`) once first to
  create `instance/site.db`.
- **"Model file not found"** — check `MODEL_GGUF` points to the actual `.gguf`
  path, or place the model in `models-gguf/`.
- **"Input folder does not exist"** — check `INPUT_DIR` is set and reachable
  (e.g. a network share is actually mounted).
- **No matching CSV files found** — if `FILTER_BY_DATE=true`, confirm the CSV
  filenames actually contain yesterday's date in `YYYY-MM-DD` format; otherwise
  set `FILTER_BY_DATE=false` to pick up all CSVs regardless of name.
- **CUDA not found** warning — either install a CUDA-enabled `llama-cpp-python`
  build, or set `DEVICE=cpu` / `N_GPU_LAYERS=0` to run on CPU.
