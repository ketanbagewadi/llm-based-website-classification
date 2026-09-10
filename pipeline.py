import os
import re
import html
import json
import time
import sys
import glob
import shutil
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import urlsplit
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import requests
import tldextract
import trafilatura
from jsonschema import validate as jsonschema_validate, ValidationError
from llama_cpp import Llama, LlamaGrammar

# ==================== CONFIGURATION ===========================================================================================================
# Folder where the daily uncategorized-query CSVs already live (no SSH download step anymore).
INPUT_DIR = os.getenv("INPUT_DIR", "/mnt/input")

# If True, only pick up files whose name contains yesterday's date (YYYY-MM-DD), matching the
# old SSH behaviour. If False, every .csv file in INPUT_DIR is picked up regardless of name.
FILTER_BY_DATE = os.getenv("FILTER_BY_DATE", "true").lower() == "true"

# If True, matched CSVs are moved into DOWNLOADS_DIR (acting as a "processed" archive) so the
# same file isn't picked up again tomorrow. Set to False to leave INPUT_DIR untouched.
MOVE_PROCESSED_FILES = os.getenv("MOVE_PROCESSED_FILES", "true").lower() == "true"

DEVICE = os.getenv("DEVICE", "gpu")
CRON_HOUR = int(os.getenv("CRON_HOUR", "2"))
CRON_MINUTE = int(os.getenv("CRON_MINUTE", "40"))

MAX_DOMAINS_PER_DAY = int(os.getenv("MAX_DOMAINS_PER_DAY", "100"))  

# LLM settings ===================================================================================================================================
N_CTX = int(os.getenv("N_CTX", "8192"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "256"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.1"))


N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", "0" if DEVICE.lower() == "cpu" else "-1"))

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
HTTP_RETRIES = int(os.getenv("HTTP_RETRIES", "3"))
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "120000"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Threads
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "6"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))
DB_BATCH_SIZE = int(os.getenv("DB_BATCH_SIZE", "50"))
USE_CACHE = os.getenv("USE_CACHE", "true").lower() == "true"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(SCRIPT_DIR, "downloads")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache")
DB_PATH = os.path.join(SCRIPT_DIR, "instance", "site.db")

DEFAULT_MODEL_PATHS = [
    os.path.join(SCRIPT_DIR, "models-gguf", "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"),
]
MODEL_GGUF = os.getenv("MODEL_GGUF") or DEFAULT_MODEL_PATHS[0]

CATEGORIES = [
    "Adult Content", "Advertisement", "Alcohol and Drugs", "Banking and Finance",
    "Blogs", "Business", "Chemistry", "Crypto", "Dating", "DoH Providers",
    "Education", "Entertainment", "Fortunetelling", "Gamble", "Games",
    "Global Religion", "Government", "Homestyle", "Information Technology",
    "Job Search", "Media Converter and Editor", "Military", "Music", "News",
    "Peer to Peer", "Pets", "Porn", "Restaurant", "Search Engines and Portals",
    "Shopping", "Social Network", "Sports", "Telecommunication", "Trading",
    "Transport", "Travel", "Web mail", "Wellness",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "minLength": 1},
        "category": {"type": "string", "enum": CATEGORIES},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 15, "uniqueItems": True},
        "explanation": {"type": "string", "maxLength": 400}
    },
    "required": ["domain", "category", "confidence", "tags"],
    "additionalProperties": False
}

SYSTEM_PROMPT = (
    "You are a strict website classifier. Pick exactly ONE category from the allowed list. "
    "Be precise and conservative. Output ONLY valid JSON per schema.\n\n"
    "Allowed categories:\n- " + "\n- ".join(CATEGORIES) + "\n\n"
    "Notes:\n"
    "- 'Porn' is explicit sexual content; 'Adult Content' may include suggestive/erotic but non-explicit material.\n"
    "- 'Gamble' is betting/casinos; 'Games' is video/online games.\n"
    "- 'Banking and Finance' covers banks, loans, cards, brokers; 'Trading' is active markets/trading platforms.\n"
    "- 'Information Technology' covers software/infra/dev tools.\n"
    "- 'Search Engines and Portals' covers general search and web directories/portals.\n"
    "- 'Web mail' is web-based email services.\n"
    "- 'DoH Providers' = DNS-over-HTTPS providers.\n"
    "- 'Homestyle' = home & living, decor, DIY.\n"
    "- If text is minimal but domain/hints are clear, use those confidently.\n"
    "- If truly unclear after analyzing all signals, skip classification.\n"
    "Confidence reflects both text strength and clarity. Tags must be brief, relevant keywords."
)

HINTS: List[Tuple[str, str, float]] = [
    (r"\b(cart|checkout|sku|wishlist|shop now|add to cart)\b", "Shopping", 0.30),
    (r"\b(bank|savings account|checking account|loan|mortgage|credit card|net banking|brokerage|mutual fund|ETF)\b", "Banking and Finance", 0.35),
    (r"\b(trading platform|order book|leverage|derivatives|futures|spread|forex|metaTrader|MT4|MT5)\b", "Trading", 0.35),
    (r"\b(crypto|blockchain|wallet|exchange|staking|token|nft|defi)\b", "Crypto", 0.40),
    (r"\b(api|sdk|developer portal|docs|release notes|saas|cloud|devops|kubernetes|microservices|roadmap)\b", "Information Technology", 0.30),
    (r"\b(search|web search|results|index the web)\b", "Search Engines and Portals", 0.35),
    (r"\b(hotmail|gmail|yahoo mail|sign in to mail|webmail)\b", "Web mail", 0.40),
    (r"\b(advertise with us|advertising|ads manager|programmatic|ad network)\b", "Advertisement", 0.35),
    (r"\b(restaurant|menu|reservations|dining|cuisine|order online)\b", "Restaurant", 0.35),
    (r"\b(hotel|resort|rooms & suites|amenities|check-in|check-out|book now|itinerary|flight|airline)\b", "Travel", 0.30),
    (r"\b(train|bus|taxi|rideshare|airport transfer|shipping|logistics|freight)\b", "Transport", 0.30),
    (r"\b(sportsbook|odds|wager|casino|roulette|blackjack|poker)\b", "Gamble", 0.50),
    (r"\b(download mp3|youtube to mp3|convert video|video editor|audio editor|compress pdf)\b", "Media Converter and Editor", 0.45),
    (r"\b(peer[- ]?to[- ]?peer|torrent|magnet:?|bittorrent|seed|leech)\b", "Peer to Peer", 0.50),
    (r"\b(forum|community|followers|timeline|feed|comment|post|social network)\b", "Social Network", 0.30),
    (r"\b(news|breaking|opinion|world|local|investigations|subscribe)\b", "News", 0.30),
    (r"\b(education|university|college|campus|admissions|course catalog|syllabus|curriculum)\b", "Education", 0.30),
    (r"\b(hospital|clinic|doctor|patient portal|appointment|telemedicine)\b", "Wellness", 0.25),
    (r"\b(music|album|playlist|stream|artist|listen now)\b", "Music", 0.35),
    (r"\b(games|gaming|esports|play now|download game|steam|xbox|playstation)\b", "Games", 0.30),
    (r"\b(entertainment|movies|tv shows|episodes|streaming|watch now)\b", "Entertainment", 0.30),
    (r"\b(job search|careers|vacancies|hiring|apply now|recruitment)\b", "Job Search", 0.40),
    (r"\b(government|ministry|department|regulation|official gazette)\b", "Government", 0.40),
    (r"\b(military|defense|armed forces|army|navy|air force)\b", "Military", 0.45),
    (r"\b(church|temple|mosque|religion|faith|worship|sangha)\b", "Global Religion", 0.35),
    (r"\b(chemistry|chemical|periodic table|reagent|synthesis|molecule)\b", "Chemistry", 0.40),
    (r"\b(pets|veterinary|dog|cat|adoption|pet food)\b", "Pets", 0.30),
    (r"\b(blog|blogger|wordpress|medium|substack)\b", "Blogs", 0.35),
    (r"\b(dating|matchmaking|meet singles|swipe)\b", "Dating", 0.45),
    (r"\b(telecom|broadband|fiber|mobile plans|prepaid|postpaid|5g|isp)\b", "Telecommunication", 0.35),
    (r"\b(home decor|furniture|interiors|DIY|gardening|kitchenware)\b", "Homestyle", 0.30),
    (r"\b(nutrition|fitness|yoga|mental health|wellness|meditation)\b", "Wellness", 0.35),
    (r"\b(alcohol|beer|wine|spirits|liquor|cannabis|marijuana)\b", "Alcohol and Drugs", 0.45),
    (r"\b(dns over https|doh|resolver|dns privacy|dnssec)\b", "DoH Providers", 0.60),
    (r"\b(porn|xxx|adult videos|cam girls|nsfw)\b", "Porn", 0.70),
    (r"\b(shop|buy now|sale|deals)\b", "Shopping", 0.25),
    (r"\b(business solutions|enterprise|corporate|b2b|services)\b", "Business", 0.30),
]

# LOGGING =====================================================================================================================================
def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"pipeline_{datetime.now().strftime('%Y%m%d')}.log")
    logger = logging.getLogger("DomainClassifier")
    logger.setLevel(logging.INFO)
    logger.handlers = []

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

logger = setup_logging()
db_lock = Lock()
cache_lock = Lock()

# ==================== CACHE ==============================================================================================================

class SimpleCache:
    """Simple file-based cache for HTML content"""
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _get_path(self, domain: str) -> str:
        safe_name = re.sub(r'[^\w\-\.]', '_', domain)
        return os.path.join(self.cache_dir, f"{safe_name}.json")

    def get(self, domain: str) -> Optional[Dict]:
        if not USE_CACHE:
            return None
        path = self._get_path(domain)
        try:
            with cache_lock:
                if os.path.exists(path):
                    with open(path, 'r') as f:
                        return json.load(f)
        except:
            pass
        return None

    def set(self, domain: str, data: Dict):
        if not USE_CACHE:
            return
        path = self._get_path(domain)
        try:
            with cache_lock:
                with open(path, 'w') as f:
                    json.dump(data, f)
        except:
            pass

cache = SimpleCache(CACHE_DIR)

# DATABASE =======================================================================================================================================
def check_database():
    if not os.path.exists(DB_PATH):
        logger.error(f"X Database not found at: {DB_PATH}")
        logger.error("Please run the Flask app first to create the database:")
        logger.error("  python app.py")
        sys.exit(1)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cur.fetchall()}
    con.close()

    required_tables = {"dom", "unknown", "user"}
    if not required_tables.issubset(tables):
        missing = required_tables - tables
        logger.error(f"X Missing tables in database: {missing}")
        logger.error("Please run the Flask app first to create tables:")
        logger.error("  python app.py")
        sys.exit(1)

    logger.info(f"OK Using existing database: {DB_PATH}")
    logger.info(f"   Tables found: {', '.join(sorted(tables))}")

def get_processed_domains() -> set:
    try:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT domains FROM dom;")
        domains = {str(r[0]).strip().lower() for r in cur.fetchall() if r and r[0]}
        con.close()
        return domains
    except Exception as e:
        logger.error(f"Error loading from DB: {e}")
        return set()

def insert_to_db_batch(results: List[Dict]):

    with db_lock:
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()

        categorized = []
        unknown = []

        for r in results:
            # Use resolved_domain if available; fall back to original domain
            domain = r.get("resolved_domain") or r.get("domain", "")
            category = r.get("category", "")
            error = r.get("error", "")

            if error or not category:
                unknown.append((domain,))
            else:
                categorized.append((domain, category))

        try:
            if categorized:
                cur.executemany(
                    "INSERT OR IGNORE INTO dom (domains, category, git_push, verified, unknown_domains) VALUES (?, ?, 0, 0, '');",
                    categorized
                )
            if unknown:
                cur.executemany("INSERT OR IGNORE INTO unknown (unknown_domains) VALUES (?);", unknown)

            con.commit()
            logger.info(f"OK Batch inserted {len(categorized)} categorized, {len(unknown)} unknown domains")
        except Exception as e:
            logger.error(f"Error in batch insert: {e}")
        finally:
            con.close()

# LOCAL FILE INTAKE (replaces SSH download) =====================================================================================================
def get_yesterday_date() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

def collect_input_csvs() -> List[str]:
    """
    Pick up CSV files directly from INPUT_DIR instead of pulling them over SSH.

    Behaviour mirrors the old SSH step:
      - if FILTER_BY_DATE is enabled, only files whose name contains yesterday's
        date (YYYY-MM-DD) are picked up
      - matched files are optionally copied into DOWNLOADS_DIR so the rest of the
        pipeline (and its logs) work unchanged, and so a file already processed
        isn't picked up again on the next run
    """
    yesterday = get_yesterday_date()
    logger.info(f"\n{'='*60}")
    logger.info("STEP 1: Local File Intake")
    logger.info(f"{'='*60}")
    logger.info(f"Input folder: {INPUT_DIR}")

    if not os.path.isdir(INPUT_DIR):
        logger.error(f"X Input folder does not exist: {INPUT_DIR}")
        return []

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    all_csvs = sorted(glob.glob(os.path.join(INPUT_DIR, "*.csv")))

    if FILTER_BY_DATE:
        logger.info(f"Target date: {yesterday}")
        matching_files = [f for f in all_csvs if yesterday in os.path.basename(f)]
    else:
        logger.info("Date filter disabled — picking up all CSVs in the folder")
        matching_files = all_csvs

    if not matching_files:
        logger.warning(f"! No matching CSV files found in {INPUT_DIR}")
        return []

    logger.info(f"\nFound {len(matching_files)} matching files:")
    for f in matching_files:
        logger.info(f"  - {os.path.basename(f)}")

    collected = []
    logger.info("\nCollecting files...")
    for src_path in matching_files:
        filename = os.path.basename(src_path)
        dest_path = os.path.join(DOWNLOADS_DIR, filename)
        try:
            if MOVE_PROCESSED_FILES:
                shutil.move(src_path, dest_path)
            else:
                shutil.copy2(src_path, dest_path)
            file_size = os.path.getsize(dest_path)
            logger.info(f"  {filename}... OK ({file_size:,} bytes)")
            collected.append(dest_path)
        except Exception as e:
            logger.error(f"  {filename}... X Error: {e}")

    logger.info(f"\nOK Collected {len(collected)} files")
    return collected

# MERGE & FILTER =====================================================================================================================
def clean_domain(domain: str) -> str:
    if not domain:
        return ""
    domain = domain.strip().lower()
    if domain.endswith("."):
        domain = domain[:-1]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain

def merge_and_filter(csv_files: List[str]) -> str:
    logger.info(f"\n{'='*60}")
    logger.info("STEP 2: Merge & Filter Uncategorized")
    logger.info(f"{'='*60}")

    if not csv_files:
        logger.warning("! No CSV files to process")
        return None

    all_domains = []
    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
            if "query" not in df.columns or "category" not in df.columns:
                logger.warning(f"Reading {os.path.basename(csv_file)}... X Missing columns")
                continue

            df["category"] = df["category"].astype(str).str.strip()
            uncategorized = df[df["category"].str.lower() == "uncategorized"]["query"]
            logger.info(f"Reading {os.path.basename(csv_file)}... OK Found {len(uncategorized)} uncategorized")
            all_domains.extend(uncategorized.tolist())
        except Exception as e:
            logger.error(f"Reading {os.path.basename(csv_file)}... X Error: {e}")

    if not all_domains:
        logger.warning("! No uncategorized domains found")
        return None

    logger.info(f"\nTotal uncategorized domains: {len(all_domains)}")
    logger.info("Cleaning domains...")
    cleaned = [clean_domain(d) for d in all_domains]
    cleaned = [d for d in cleaned if d and "." in d]
    logger.info(f"After cleaning: {len(cleaned)} domains")

    unique_domains = list(set(cleaned))
    logger.info(f"After deduplication: {len(unique_domains)} unique domains")

    output_file = os.path.join(OUTPUT_DIR, "to_scrape_today.csv")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pd.DataFrame({"domain": sorted(unique_domains)}).to_csv(output_file, index=False)
    logger.info(f"OK Saved to {output_file}")
    return output_file

def filter_existing(input_file: str) -> str:
    logger.info(f"\n{'='*60}")
    logger.info("STEP 3: Filter Already Processed")
    logger.info(f"{'='*60}")

    existing = get_processed_domains()
    logger.info(f"Already processed domains in DB: {len(existing)}")

    df = pd.read_csv(input_file)
    df["domain_lower"] = df["domain"].str.lower()
    before = len(df)
    df = df[~df["domain_lower"].isin(existing)]
    after = len(df)

    if before != after:
        logger.info(f"Filtered out {before - after} domains already in DB")
        logger.info(f"Remaining new domains: {after}")

    if df.empty:
        logger.info("OK All domains already processed. Nothing new to classify.")
        return None

    if len(df) > MAX_DOMAINS_PER_DAY:
        logger.info(f"! Limiting to {MAX_DOMAINS_PER_DAY} domains per day (from {len(df)} available)")
        df_limited = df[["domain"]].sample(n=MAX_DOMAINS_PER_DAY, random_state=int(time.time()))
    else:
        df_limited = df[["domain"]]

    output_file = os.path.join(OUTPUT_DIR, "new_domains.csv")
    df_limited.to_csv(output_file, index=False)
    logger.info(f"OK {len(df_limited)} new domains saved to {output_file}")
    logger.info(f"   Expected successful classifications: ~{int(len(df_limited)*0.25)}-{int(len(df_limited)*0.4)}")
    return output_file

# SUBDOMAIN FALLBACK LOGIC =====================================================================================================================

def get_main_domain(domain: str) -> str:
    """
    Extract the registrable (main) domain using tldextract.
    e.g. apps.mzstatic.com.g.aaplimg.com -> aaplimg.com
    """
    ext = tldextract.extract(domain)
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return domain

def get_subdomain_candidates(domain: str) -> List[str]:
    """
    Generate a list of candidate domains by progressively stripping the
    leftmost subdomain label, stopping before going beyond the main domain.

    Example for apps.mzstatic.com.g.aaplimg.com:
      1. apps.mzstatic.com.g.aaplimg.com  (original)
      2. mzstatic.com.g.aaplimg.com
      3. com.g.aaplimg.com
      4. g.aaplimg.com
      5. aaplimg.com  (main domain — last candidate, do not go further)
    """
    main = get_main_domain(domain)
    candidates = [domain]

    current = domain
    while True:
        # Strip the leftmost label
        parts = current.split(".")
        if len(parts) <= 1:
            break
        current = ".".join(parts[1:])

        candidates.append(current)

        # Stop after adding the main domain
        if current == main:
            break

    return candidates

def is_domain_reachable(domain: str, timeout: float = HTTP_TIMEOUT) -> Tuple[bool, str, int, str]:
    """
    Try to reach a domain over https/http. Returns (reachable, html, status_code, final_url).
    Uses a single attempt per URL variant (no retry loop — caller handles fallback).
    """
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })

    urls = [
        f"https://{domain}/",
        f"http://{domain}/",
        f"https://www.{domain}/",
        f"http://www.{domain}/",
    ]

    for url in urls:
        for attempt in range(1 + HTTP_RETRIES):
            try:
                r = sess.get(url, timeout=timeout, allow_redirects=True, verify=False)
                ct = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                txt = r.text if ("html" in ct or "text" in ct) else ""

                if r.status_code < 400 and txt:
                    return True, txt, r.status_code, r.url
            except requests.exceptions.Timeout:
                if attempt < HTTP_RETRIES:
                    time.sleep(0.5)
                    continue
                break
            except Exception:
                break  # Connection error — try next URL variant

    return False, "", 0, ""


def fetch_html_with_fallback(
    original_domain: str,
    existing_domains: Optional[set] = None,
) -> Tuple[str, str, int, str, str]:

    existing_domains = existing_domains or set()

    # Check cache for original domain first
    cached = cache.get(original_domain)
    if cached:
        return (
            cached.get("html", ""),
            cached.get("ct", ""),
            cached.get("code", 0),
            cached.get("url", ""),
            cached.get("resolved_domain", original_domain),
        )

    candidates = get_subdomain_candidates(original_domain)
    total = len(candidates)

    for idx, candidate in enumerate(candidates):
        label = f"[{idx+1}/{total}]"

        # For trimmed candidates (idx > 0): check DB before even trying to fetch.
        # If the trimmed domain is already in DB, it's a duplicate — stop here.
        if idx > 0 and candidate in existing_domains:
            logger.debug(f"  {label} Trimmed domain '{candidate}' already in DB — skipping as duplicate.")
            return "", "", 0, "", candidate  # empty html → caller discards

        # Check per-candidate cache (only for trimmed, original already checked above)
        if idx > 0:
            cached = cache.get(candidate)
            if cached:
                logger.debug(f"  {label} Cache hit for {candidate}")
                return (
                    cached.get("html", ""),
                    cached.get("ct", ""),
                    cached.get("code", 0),
                    cached.get("url", ""),
                    candidate,
                )

        logger.debug(f"  {label} Trying: {candidate}")
        reachable, html_text, code, final_url = is_domain_reachable(candidate)

        if reachable and html_text:
            # Cache both the original and the resolved candidate
            payload = {
                "html": html_text,
                "ct": "text/html",
                "code": code,
                "url": final_url,
                "resolved_domain": candidate,
            }
            cache.set(candidate, payload)
            if candidate != original_domain:
                cache.set(original_domain, payload)
                logger.info(f"  Subdomain resolved: {original_domain} → {candidate} (stripped {idx} label(s))")

            return html_text, "text/html", code, final_url, candidate

        logger.debug(f"  {label} {candidate} unreachable — stripping subdomain...")

    # All candidates exhausted
    logger.debug(f"  All candidates for {original_domain} are unreachable.")
    return "", "", 0, "", original_domain

# CLASSIFICATION =================================================================================================================================

def build_grammar() -> LlamaGrammar:
    if hasattr(LlamaGrammar, "from_json_schema"):
        return LlamaGrammar.from_json_schema(json.dumps(SCHEMA))

    gbnf = r"""
root ::= value
value ::= object | array | string | number | "true" | "false" | "null"
object ::= "{" ws ( member ( ws "," ws member )* )? ws "}"
member ::= string ws ":" ws value
array ::= "[" ws ( value ( ws "," ws value )* )? ws "]"
string ::= "\"" char* "\""
char ::= escape | %x20-21 | %x23-5B | %x5D-10FFFF
escape ::= "\\" ( "\"" | "\\" | "/" | "b" | "f" | "n" | "r" | "t" | unicode )
unicode ::= "u" hex hex hex hex
hex ::= "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "a" | "b" | "c" | "d" | "e" | "f" | "A" | "B" | "C" | "D" | "E" | "F"
number ::= int frac? exp?
int ::= "-"? ( "0" | onenine digit* )
frac ::= "." digit+
exp ::= ( "e" | "E" ) ( "+" | "-" )? digit+
digit ::= "0" | onenine
onenine ::= "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
ws ::= ( " " | "\n" | "\r" | "\t" )*
"""
    return LlamaGrammar.from_string(gbnf)


def initialize_llm():
    if not os.path.exists(MODEL_GGUF):
        logger.error(f"ERROR: Model file not found at: {MODEL_GGUF}")
        raise FileNotFoundError(f"Model file not found: {MODEL_GGUF}")

    grammar = build_grammar()

    # Determine CUDA availability and log clearly
    cuda_available = False
    try:
        import ctypes
        ctypes.CDLL("libcuda.so.1")
        cuda_available = True
    except Exception:
        pass

    device_info = DEVICE.upper()
    if DEVICE.lower() != "cpu":
        if cuda_available:
            device_info += " (CUDA)"
        else:
            device_info += " (CUDA not found — may fall back to CPU)"

    logger.info(f"Initializing LLM on {device_info}...")
    logger.info(f"  n_gpu_layers = {N_GPU_LAYERS} ({'all layers on GPU' if N_GPU_LAYERS == -1 else 'CPU only' if N_GPU_LAYERS == 0 else f'{N_GPU_LAYERS} layers on GPU'})")

    llm = Llama(
        model_path=MODEL_GGUF,
        n_ctx=N_CTX,
        n_gpu_layers=N_GPU_LAYERS,   # -1 = all layers on CUDA GPU
        logits_all=False,
        use_mmap=True,
        use_mlock=False,
        n_threads=4,
        verbose=False,
    )
    logger.info("OK LLM initialized successfully")
    return llm, grammar


def normalize_domain(d: str) -> str:
    d = (d or "").strip()
    if not d:
        return ""
    netloc = urlsplit(d if d.startswith("http") else f"http://{d}").netloc
    return netloc.lower()


def extract_text(html_text: str, url: str = "") -> str:
    if not html_text.strip():
        return ""

    try:
        out = trafilatura.extract(
            html_text,
            include_comments=False,
            include_tables=False,
            include_links=False,
            url=url or None,
        )
        if out and out.strip():
            return out
    except:
        pass

    # Fallback: manual extraction
    rough = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text)
    rough = re.sub(r"(?s)<[^>]+>", " ", rough)
    rough = html.unescape(rough)
    return re.sub(r"\s{2,}", " ", rough).strip()


def truncate(s: str, n: int = MAX_TEXT_CHARS) -> str:
    if len(s) <= n:
        return s
    half = n // 2
    return s[:half] + "\n...[TRUNCATED]...\n" + s[-half:]


def domain_hints(domain: str) -> List[str]:
    ex = tldextract.extract(domain or "")
    hints = []

    if ex.suffix in ("gov", "gov.in", "gov.uk"):
        hints.append("Government")
    if ex.suffix in ("edu", "ac.in", "ac.uk"):
        hints.append("Education")

    brand = ex.domain.lower()
    if brand in {"gmail", "outlook", "hotmail", "yahoo"}:
        hints.append("Web mail")
    if brand in {"google", "bing", "yahoo", "baidu", "duckduckgo"}:
        hints.append("Search Engines and Portals")
    if brand in {"binance", "coinbase", "kraken", "bybit", "okx"}:
        hints.append("Crypto")
    if brand in {"facebook", "instagram", "tiktok", "twitter", "x", "linkedin", "reddit"}:
        hints.append("Social Network")

    return hints


def apply_hints(text: str) -> Dict[str, float]:
    scores = {c: 0.0 for c in CATEGORIES}
    low = (text or "").lower()

    for rx, cat, boost in HINTS:
        if re.search(rx, low):
            scores[cat] = max(scores[cat], boost)

    return scores


def llm_classify(llm, grammar, domain: str, text: str, heur_scores: Dict[str, float], dhints: List[str]) -> Dict[str, Any]:
    context = truncate(text, MAX_TEXT_CHARS)

    heur_items = []
    for cat, score in heur_scores.items():
        if score > 0:
            heur_items.append(f"{cat}: {round(score, 3)}")
    heur_str = "{" + ", ".join(heur_items) + "}"

    text_note = f"(Text length: {len(context)} chars)"
    if len(context) < 200:
        text_note += " - Minimal text, rely heavily on domain/hints!"

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Domain: {domain}\n"
        f"Domain_hints: {dhints}\n"
        f"Heuristic_scores: {heur_str}\n"
        f"{text_note}\n"
        "Homepage text between <site> tags:\n<site>\n"
        f"{context}\n</site>\n"
        "Return JSON only."
    )

    t0 = time.time()
    try:
        out = llm.create_completion(
            prompt=prompt,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            grammar=grammar,
        )

        gen = out["choices"][0]["text"]
        obj = json.loads(gen)
        jsonschema_validate(instance=obj, schema=SCHEMA)

        obj["domain"] = obj.get("domain") or domain
        pred = obj.get("category", CATEGORIES[-1])

        agree = heur_scores.get(pred, 0.0) + (0.1 if pred in dhints else 0.0)
        obj["confidence"] = float(min(1.0, max(0.0, obj.get("confidence", 0.5) * (1 + agree))))

        obj["tags"] = obj.get("tags") or []
        for cat, score in sorted(heur_scores.items(), key=lambda x: x[1], reverse=True)[:4]:
            if score > 0 and cat not in obj["tags"]:
                obj["tags"].append(cat)

        for hint in dhints:
            if hint not in obj["tags"]:
                obj["tags"].append(hint)

        obj["tags"] = list(dict.fromkeys(obj["tags"]))[:12]
        obj["latency_ms"] = round((time.time() - t0) * 1000.0, 1)
        obj["final_url"] = ""
        return obj

    except Exception as e:
        if dhints or any(v > 0.35 for v in heur_scores.values()):
            best_cat = max(heur_scores.items(), key=lambda x: x[1])[0] if any(heur_scores.values()) else (dhints[0] if dhints else "")
            if best_cat:
                return {
                    "domain": domain,
                    "category": best_cat,
                    "confidence": 0.45,
                    "tags": dhints[:5] if dhints else [best_cat],
                    "explanation": "Classified using hints (LLM failed)",
                    "latency_ms": round((time.time() - t0) * 1000.0, 1),
                    "final_url": ""
                }
        raise


def fetch_and_extract(domain: str, existing_domains: Optional[set] = None) -> Dict[str, Any]:

    dom = normalize_domain(domain)
    if not dom:
        return {"domain": domain, "text": "", "error": "invalid_domain", "final_url": "", "resolved_domain": domain}

    html_text, ct, code, final_url, resolved_domain = fetch_html_with_fallback(dom, existing_domains=existing_domains)

    if not html_text:
        return {
            "domain": dom,
            "text": "",
            "error": "",
            "code": code,
            "final_url": final_url,
            "resolved_domain": resolved_domain,
        }

    text = extract_text(html_text, final_url)
    return {
        "domain": dom,
        "text": text,
        "error": "",
        "code": code,
        "final_url": final_url,
        "resolved_domain": resolved_domain,
    }


def process_single_domain(domain_data: Dict, llm, grammar) -> Dict[str, Any]:
    """Process a single domain with pre-fetched data"""
    domain = domain_data["domain"]
    text = domain_data.get("text", "")
    error = domain_data.get("error", "")
    final_url = domain_data.get("final_url", "")
    resolved_domain = domain_data.get("resolved_domain", domain)

    if error:
        return {
            "domain": domain,
            "category": "",
            "confidence": 0,
            "tags": "",
            "explanation": "",
            "latency_ms": 0,
            "error": error,
            "final_url": final_url,
            "resolved_domain": resolved_domain,
        }

    heur = apply_hints(text)
    dh = domain_hints(resolved_domain)

    try:
        classified = llm_classify(llm, grammar, resolved_domain, text, heur, dh)
        classified["final_url"] = final_url

        result = {
            "domain": domain,                            # original domain (for DB storage)
            "resolved_domain": resolved_domain,          # which subdomain level responded
            "category": classified.get("category", ""),
            "confidence": classified.get("confidence", ""),
            "tags": "|".join(classified.get("tags", [])),
            "explanation": classified.get("explanation", ""),
            "latency_ms": classified.get("latency_ms", ""),
            "error": "",
            "final_url": final_url,
        }
        return result

    except Exception as e:
        logger.debug(f"Classification failed for {domain}: {str(e)}")
        return {
            "domain": domain,
            "resolved_domain": resolved_domain,
            "category": "",
            "confidence": 0,
            "tags": "",
            "explanation": "",
            "latency_ms": 0,
            "error": str(e)[:200],
            "final_url": final_url,
        }


def classify_domains(input_file: str) -> List[Dict]:
    logger.info(f"\n{'='*60}")
    logger.info("STEP 4: Domain Classification (SUBDOMAIN FALLBACK + CUDA GPU)")
    logger.info(f"{'='*60}")
    logger.info(f"Configuration:")
    logger.info(f"  Fetch Workers:   {MAX_WORKERS}")
    logger.info(f"  Batch Size:      {BATCH_SIZE}")
    logger.info(f"  HTTP Timeout:    {HTTP_TIMEOUT}s")
    logger.info(f"  HTTP Retries:    {HTTP_RETRIES}")
    logger.info(f"  Cache Enabled:   {USE_CACHE}")
    logger.info(f"  Context Size:    {N_CTX} tokens")
    logger.info(f"  GPU Layers:      {N_GPU_LAYERS} ({'all on GPU' if N_GPU_LAYERS == -1 else 'CPU' if N_GPU_LAYERS == 0 else 'partial GPU'})")
    logger.info(f"  Subdomain Fallback: ENABLED (strips left labels down to main domain)")
    logger.info(f"  DB Duplicate Check: ENABLED (checks trimmed domains against DB)")

    llm, grammar = initialize_llm()

    df = pd.read_csv(input_file)
    domains = df["domain"].tolist()
    total = len(domains)


    existing_domains: set = get_processed_domains()
    logger.info(f"  DB domains loaded for duplicate check: {len(existing_domains)}")

    logger.info(f"\nClassifying {total} domains...\n")

    all_results = []
    start_time = time.time()
    processed = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total)
        batch_domains = domains[batch_start:batch_end]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"\n{'='*60}")
        logger.info(f"Processing Batch {batch_num}/{total_batches} ({len(batch_domains)} domains)")
        logger.info(f"{'='*60}")

        logger.info("Step 1: Parallel fetching (with subdomain fallback + DB duplicate check)...")
        fetch_start = time.time()
        fetched_data = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_domain = {
                executor.submit(fetch_and_extract, domain, existing_domains): domain
                for domain in batch_domains
            }

            for future in as_completed(future_to_domain):
                try:
                    result = future.result()
                    orig = result["domain"]
                    resolved = result.get("resolved_domain", orig)

                    # If the resolved domain is already in existing_domains, discard it
                    if resolved and resolved in existing_domains and resolved != orig:
                        logger.info(f"  Skipped duplicate: {orig} resolved to '{resolved}' (already in DB)")
                        continue  # don't add to fetched_data

                    fetched_data.append(result)

                    if resolved and resolved != orig:
                        logger.info(f"  Subdomain resolved: {orig} → {resolved}")
                except Exception as e:
                    domain = future_to_domain[future]
                    fetched_data.append({
                        "domain": domain,
                        "text": "",
                        "error": str(e),
                        "code": 0,
                        "final_url": "",
                        "resolved_domain": domain,
                    })

        fetch_time = time.time() - fetch_start
        fetched_count = sum(1 for d in fetched_data if not d.get("error") and d.get("text"))
        logger.info(f"  Fetched {fetched_count}/{len(fetched_data)} domains successfully in {fetch_time:.1f}s")

        logger.info("Step 2: LLM classification...")
        classify_start = time.time()
        batch_results = []

        for idx, domain_data in enumerate(fetched_data, 1):
            result = process_single_domain(domain_data, llm, grammar)
            batch_results.append(result)
            processed += 1

            if idx % 5 == 0 or idx == len(fetched_data):
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (total - processed) / rate if rate > 0 else 0

                success = sum(1 for r in batch_results if not r.get("error") and r.get("category"))
                logger.info(
                    f"  [{idx}/{len(fetched_data)}] "
                    f"Overall: {processed}/{total} ({processed*100//total}%) | "
                    f"Success: {success}/{idx} ({success*100//idx if idx > 0 else 0}%) | "
                    f"Rate: {rate:.1f}/s | "
                    f"ETA: {eta/60:.1f}m"
                )

        classify_time = time.time() - classify_start
        logger.info(f"  Classified {len(batch_results)} domains in {classify_time:.1f}s")

        if len(batch_results) >= DB_BATCH_SIZE or batch_end == total:
            logger.info("Step 3: Batch database insertion...")
            insert_to_db_batch(batch_results)

            for r in batch_results:
                rd = r.get("resolved_domain") or r.get("domain", "")
                if rd:
                    existing_domains.add(rd.lower())
            all_results.extend(batch_results)
            batch_results = []
        else:
            all_results.extend(batch_results)

    if batch_results:
        insert_to_db_batch(batch_results)
        for r in batch_results:
            rd = r.get("resolved_domain") or r.get("domain", "")
            if rd:
                existing_domains.add(rd.lower())

    elapsed = time.time() - start_time
    successful = sum(1 for r in all_results if not r.get("error") and r.get("category"))
    failed = sum(1 for r in all_results if r.get("error") or not r.get("category"))
    resolved_count = sum(1 for r in all_results if r.get("resolved_domain") and r.get("resolved_domain") != r.get("domain"))

    logger.info(f"\n{'='*60}")
    logger.info("OK Classification Complete")
    logger.info(f"{'='*60}")
    logger.info(f"  Total Processed:      {len(all_results)}")
    logger.info(f"  Successful:           {successful}")
    logger.info(f"  Failed/Skipped:       {failed}")
    logger.info(f"  Subdomain Resolved:   {resolved_count} (fell back to shorter domain)")
    logger.info(f"  Success Rate:         {successful*100//len(all_results) if all_results else 0}%")
    logger.info(f"  Time:                 {elapsed/60:.1f} minutes ({elapsed:.1f} seconds)")
    logger.info(f"  Average:              {elapsed/total:.2f} sec/domain")
    logger.info(f"{'='*60}")

    return all_results

# PIPELINE ===========================================================================================================================

def run_pipeline():
    logger.info("\n" + "=" * 60)
    logger.info("DOMAIN CLASSIFICATION PIPELINE")
    logger.info("=" * 60)
    logger.info(f"Device:           {DEVICE.upper()}")
    logger.info(f"GPU Layers:       {N_GPU_LAYERS} ({'all on CUDA GPU' if N_GPU_LAYERS == -1 else 'CPU only' if N_GPU_LAYERS == 0 else 'partial GPU'})")
    logger.info(f"Daily Limit:      {MAX_DOMAINS_PER_DAY} domains")
    logger.info(f"Expected Success: ~{int(MAX_DOMAINS_PER_DAY*0.25)}-{int(MAX_DOMAINS_PER_DAY*0.4)} classifications/day")
    logger.info(f"HTTP Timeout:     {HTTP_TIMEOUT}s")
    logger.info(f"HTTP Retries:     {HTTP_RETRIES}")
    logger.info(f"Subdomain Fallback: ENABLED")
    logger.info(f"Input Folder:     {INPUT_DIR}")
    logger.info(f"Date:             {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    check_database()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    csv_files = collect_input_csvs()
    if not csv_files:
        logger.warning("\nX No input files found. Exiting.")
        return

    merged_file = merge_and_filter(csv_files)
    if not merged_file:
        logger.warning("\nX No uncategorized domains found. Exiting.")
        return

    final_file = filter_existing(merged_file)
    if not final_file:
        logger.warning("\nX No new domains to classify. Exiting.")
        return

    results = classify_domains(final_file)

    logger.info(f"\n{'='*60}")
    logger.info("OK PIPELINE COMPLETE")
    logger.info(f"{'='*60}\n")

# CRON ==========================================================================================================================
def install_cron():
    try:
        from crontab import CronTab
        cron = CronTab(user=True)

        existing_jobs = list(cron.find_comment("domain_classifier_daily"))
        for job in existing_jobs:
            cron.remove(job)
            logger.info("Removed existing cron job")

        script_path = os.path.abspath(__file__)
        python_exec = sys.executable
        command = f"cd {SCRIPT_DIR} && {python_exec} {script_path} run >> {LOG_DIR}/cron.log 2>&1"

        job = cron.new(command=command, comment="domain_classifier_daily")
        job.hour.on(CRON_HOUR)
        job.minute.on(CRON_MINUTE)
        cron.write()

        logger.info("\n" + "=" * 60)
        logger.info("OK CRON JOB INSTALLED SUCCESSFULLY")
        logger.info("=" * 60)
        logger.info(f"  Schedule: Daily at {CRON_HOUR:02d}:{CRON_MINUTE:02d}")
        logger.info(f"  Command: {command}")
        logger.info(f"  Logs: {LOG_DIR}/cron.log")
        logger.info(f"  Daily Limit: {MAX_DOMAINS_PER_DAY} domains")
        logger.info(f"  Expected: ~{int(MAX_DOMAINS_PER_DAY*0.25)}-{int(MAX_DOMAINS_PER_DAY*0.4)} successful/day")
        logger.info("=" * 60)
        logger.info("\nView jobs with: crontab -l")
        logger.info("Edit jobs with: crontab -e")
        return True
    except ImportError:
        logger.error("\nX python-crontab module not installed.")
        logger.info("Install with: pip install python-crontab")
        return False
    except Exception as e:
        logger.error(f"\nX Error installing cron job: {e}")
        return False

def uninstall_cron():
    try:
        from crontab import CronTab
        cron = CronTab(user=True)
        existing_jobs = list(cron.find_comment("domain_classifier_daily"))

        if existing_jobs:
            for job in existing_jobs:
                cron.remove(job)
            cron.write()
            logger.info(f"OK Removed {len(existing_jobs)} cron job(s)")
        else:
            logger.info("No cron jobs found to remove.")
    except Exception as e:
        logger.error(f"X Error removing cron: {e}")

def list_cron():
    try:
        from crontab import CronTab
        cron = CronTab(user=True)
        existing_jobs = list(cron.find_comment("domain_classifier_daily"))

        if existing_jobs:
            logger.info(f"\nFound {len(existing_jobs)} cron job(s):")
            for idx, job in enumerate(existing_jobs, 1):
                logger.info(f"\n{idx}. Schedule: {job.slices}")
                logger.info(f"   Command: {job.command}")
                logger.info(f"   Enabled: {job.is_enabled()}")
        else:
            logger.info("No cron jobs found for this script.")
    except Exception as e:
        logger.error(f"Error listing cron jobs: {e}")

def status():
    logger.info("\n" + "=" * 60)
    logger.info("DOMAIN CLASSIFICATION PIPELINE STATUS")
    logger.info("=" * 60)
    logger.info("\nConfiguration:")
    logger.info(f"  Device:           {DEVICE.upper()}")
    logger.info(f"  GPU Layers:       {N_GPU_LAYERS} ({'all on CUDA GPU' if N_GPU_LAYERS == -1 else 'CPU only' if N_GPU_LAYERS == 0 else 'partial GPU'})")
    logger.info(f"  Daily Limit:      {MAX_DOMAINS_PER_DAY} domains")
    logger.info(f"  Expected Success: ~{int(MAX_DOMAINS_PER_DAY*0.25)}-{int(MAX_DOMAINS_PER_DAY*0.4)}/day")
    logger.info(f"  Workers:          {MAX_WORKERS}")
    logger.info(f"  Batch Size:       {BATCH_SIZE}")
    logger.info(f"  HTTP Timeout:     {HTTP_TIMEOUT}s")
    logger.info(f"  HTTP Retries:     {HTTP_RETRIES}")
    logger.info(f"  Cache Enabled:    {USE_CACHE}")
    logger.info(f"  Cron schedule:    {CRON_HOUR:02d}:{CRON_MINUTE:02d} daily")
    logger.info(f"  Subdomain Fallback: ENABLED")
    logger.info(f"  Input Folder:     {INPUT_DIR} {'OK' if os.path.isdir(INPUT_DIR) else '(not found)'}")
    logger.info(f"  Date Filter:      {'ENABLED (yesterday only)' if FILTER_BY_DATE else 'DISABLED (all CSVs)'}")
    logger.info(f"  Move Processed:   {MOVE_PROCESSED_FILES}")
    logger.info("\nDirectories:")
    logger.info(f"  Script:    {SCRIPT_DIR}")
    logger.info(f"  Downloads: {DOWNLOADS_DIR} {'OK' if os.path.exists(DOWNLOADS_DIR) else '(not created)'}")
    logger.info(f"  Output:    {OUTPUT_DIR} {'OK' if os.path.exists(OUTPUT_DIR) else '(not created)'}")
    logger.info(f"  Logs:      {LOG_DIR} {'OK' if os.path.exists(LOG_DIR) else '(not created)'}")
    logger.info(f"  Cache:     {CACHE_DIR} {'OK' if os.path.exists(CACHE_DIR) else '(not created)'}")
    logger.info("\nModel:")
    logger.info(f"  Path:    {MODEL_GGUF}")
    logger.info(f"  Status:  {'OK Found' if os.path.exists(MODEL_GGUF) else 'X Not found'}")
    logger.info(f"  Context: {N_CTX} tokens")
    logger.info("\nDatabase:")
    logger.info(f"  Path:   {DB_PATH}")
    logger.info(f"  Status: {'OK Found' if os.path.exists(DB_PATH) else 'X Not created yet'}")

    if os.path.exists(DB_PATH):
        processed = get_processed_domains()
        logger.info(f"  Domains tracked: {len(processed)}")

    logger.info("\nCron Job:")
    list_cron()
    logger.info("\n" + "=" * 60)

# MAIN ============================================================================================================================================

def print_usage():
    print("\n" + "=" * 60)
    print("DOMAIN CLASSIFICATION PIPELINE")
    print("=" * 60)
    print("\nUsage: python pipeline.py <command>")
    print("\nCommands:")
    print("  run       - Run the pipeline once")
    print("  install   - Install daily cron job")
    print("  uninstall - Remove cron job")
    print("  list      - List installed cron jobs")
    print("  status    - Show pipeline status")
    print("  help      - Show this help message")
    print("\nExamples:")
    print("  python pipeline.py run")
    print("  python pipeline.py install")
    print(f"\nSETTINGS:")
    print(f"  Input Folder:     {INPUT_DIR}")
    print(f"  Daily Limit:      {MAX_DOMAINS_PER_DAY} domains/day")
    print(f"  Expected Success: ~{int(MAX_DOMAINS_PER_DAY*0.25)}-{int(MAX_DOMAINS_PER_DAY*0.4)} classifications/day")
    print(f"  HTTP Timeout:     {HTTP_TIMEOUT}s")
    print(f"  HTTP Retries:     {HTTP_RETRIES}")
    print(f"  Context:          {N_CTX} tokens")
    print(f"  GPU Layers:       {N_GPU_LAYERS} (set N_GPU_LAYERS=0 for CPU, -1 for full CUDA)")
    print(f"  Subdomain Fallback: ENABLED")
    print(f"\nTo force CPU: DEVICE=cpu python pipeline.py run")
    print(f"To force GPU: DEVICE=gpu python pipeline.py run")
    print(f"Override GPU layers: N_GPU_LAYERS=32 python pipeline.py run")
    print(f"Change daily limit:  MAX_DOMAINS_PER_DAY=50 python pipeline.py run")
    print(f"Change input folder: INPUT_DIR=/path/to/csvs python pipeline.py run")
    print("=" * 60 + "\n")


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    if len(sys.argv) < 2:
        print("No command specified. Running pipeline...")
        try:
            run_pipeline()
        except KeyboardInterrupt:
            logger.warning("\n\n! Pipeline interrupted by user")
            sys.exit(1)
        except Exception as e:
            logger.error(f"\n\nX Pipeline failed with error: {e}", exc_info=True)
            sys.exit(1)
        return

    command = sys.argv[1].lower()

    if command == "run":
        try:
            run_pipeline()
        except KeyboardInterrupt:
            logger.warning("\n\n! Pipeline interrupted by user")
            sys.exit(1)
        except Exception as e:
            logger.error(f"\n\nX Pipeline failed with error: {e}", exc_info=True)
            sys.exit(1)
    elif command == "install":
        success = install_cron()
        sys.exit(0 if success else 1)
    elif command == "uninstall":
        uninstall_cron()
    elif command == "list":
        list_cron()
    elif command == "status":
        status()
    elif command in ("help", "-h", "--help"):
        print_usage()
    else:
        print(f"Unknown command: {command}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
