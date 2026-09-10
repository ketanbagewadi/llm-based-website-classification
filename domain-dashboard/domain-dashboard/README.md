# Domain Review Dashboard

Flask app for reviewing/verifying LLM-classified domains (login, dashboard with
pagination/search, add/edit/verify/discard domains) and — optionally — pushing
verified domains to a Git repo of category files.

## What changed from your original code

- **Git push is now optional.** It's controlled by a `GIT_ENABLED` env var
  (defaults to `false`). The `GitPython` import only happens if `GIT_ENABLED=true`,
  so the app runs fine without `GitPython` installed and without a Git repo set
  up at all. Clicking "Push to Git" while it's disabled just returns a clear
  "not enabled yet" message instead of crashing.
- **`GIT_REPO_PATH` is now read from an env var** (falls back to your original
  `/home/ketan/domaindb` if unset) instead of being hardcoded.
- **Passwords are now hashed** (`werkzeug.security.generate_password_hash` /
  `check_password_hash`) instead of stored and compared in plain text. See the
  note at the bottom — this means any old users in an existing `site.db` won't
  be able to log in until they re-register.
- **`dashboard.html`'s inline CSS/JS were split out** into
  `static/css/dashboard.css` and `static/js/dashboard.js`, so the template
  itself is ~320 lines instead of ~1,690. Behavior is identical — the one
  Jinja expression the JS needed (`push_to_git`'s URL) is now passed in via a
  small `window.APP_URLS` object set inline in the template.
- Everything else (routes, models, forms, categories list, dashboard logic,
  modals) is unchanged.

## Directory structure

Set the project up exactly like this — Flask expects `templates/` and
`static/` as siblings of `app.py`:

```
domain-dashboard/                  <- project root
├── app.py
├── requirements.txt
├── .env.example
├── instance/
│   └── site.db                    <- created automatically on first run
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── register.html
│   └── dashboard.html
└── static/
    ├── css/
    │   └── dashboard.css
    └── js/
        └── dashboard.js
```

## 1. Get the files into place

```bash
mkdir -p domain-dashboard/{templates,static/css,static/js,instance}
cd domain-dashboard
```

Copy each file from this response into the matching path shown above:
- `app.py` → project root
- `base.html`, `login.html`, `register.html`, `dashboard.html` → `templates/`
- `dashboard.css` → `static/css/`
- `dashboard.js` → `static/js/`
- `requirements.txt`, `.env.example` → project root

## 2. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

`GitPython` is listed but only actually required once you turn Git push on
(step 5) — installing it up front is harmless either way.

## 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` (or just `export` these in your shell) and set at least
`SECRET_KEY` to something random for anything beyond local testing. Leave
`GIT_ENABLED=false` for now — that's the point of this setup, you can turn it
on later.

If you're using something like `python-dotenv` to auto-load `.env`, add:

```bash
pip install python-dotenv
```

and at the top of `app.py`:

```python
from dotenv import load_dotenv
load_dotenv()
```

Otherwise just `export` the variables manually before running, e.g.:

```bash
export SECRET_KEY="something-random"
export GIT_ENABLED=false
```

## 4. Run it

```bash
python app.py
```

This creates `instance/site.db` automatically on first run (via
`db.create_all()`) and starts the server on `http://0.0.0.0:5003`.

Open `http://localhost:5003`, register an account, log in, and you'll land on
the dashboard. It'll be empty until domains exist in the `dom` table — that's
what your classification pipeline populates.

## 5. Enable Git push later (optional)

When you're ready:

1. Clone your domains repo locally, with a `categories/` folder inside it
   containing one plain-text file per category name (matching the
   `CATEGORIES` list in `app.py` exactly), e.g.:
   ```
   domaindb/
   └── categories/
       ├── Adult Content
       ├── Advertisement
       ├── Banking and Finance
       └── ... (one file per category)
   ```
2. Set:
   ```bash
   export GIT_ENABLED=true
   export GIT_REPO_PATH=/path/to/domaindb
   ```
3. Make sure `git` credentials/SSH keys are set up for that clone to `pull`
   and `push` without a prompt (the app calls `origin.pull()` and
   `origin.push()` non-interactively).
4. Restart the app. The "Push to Git" button on the dashboard will now work;
   until then it returns a friendly error instead of failing.

## Notes / things worth knowing

- **Password hashing changed the storage format.** If you already have a
  `site.db` with users registered under the old plain-text scheme, their
  stored password won't match the new hash check and they won't be able to
  log in. Simplest fix: delete `instance/site.db` and re-register, or write a
  one-off script to rehash existing rows with `generate_password_hash`.
- `SECRET_KEY` defaults to `"secret_key"` if unset — fine for local testing,
  but set a real random value before exposing this anywhere beyond localhost.
- The app binds to `0.0.0.0:5003` by default (`app.run(host="0.0.0.0",
  port=5003)`) — change the `port=` value in `app.py` if that conflicts with
  something else on your machine.
