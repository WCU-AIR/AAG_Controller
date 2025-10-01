#!/usr/bin/env python3
"""
control_code.py  v1.4
────────────────────────────────────────────────────────────
• Collects student repo name from CLI
• Reads *all* source files under ~/logs/studentcode  (language-agnostic)
• Detects perfect autograder scores
• Retrieves last 3 teacher-reviewed comments for the repo
• Sends a retrieval-augmented prompt to Ollama (“ux1” model)
• Writes markdown feedback to ~/logs/feedback.md
• Persists rows into:

    submissions      (legacy `code` column kept)
    code_files       (one row per file)
    autograder_outputs
    feedback         (repo_name + reviewed flag)

SQLite path defaults to $HOME/agllmdatabase.db (overridable with $AGLLM_DB).
"""

import os, sys, sqlite3, subprocess, shutil, re, json, requests
from pathlib import Path
from datetime import datetime

# ─────────────────────────── config ─────────────────────────────
DB_PATH         = os.getenv("AGLLM_DB",
                             os.path.join(os.getenv("HOME"), "agllmdatabase.db"))
LOGS_DIR        = Path(os.getenv("HOME") or ".").joinpath("logs")
STUDENT_CODE_DIR= LOGS_DIR / "studentcode"
AUTO_FILE       = LOGS_DIR / "autograder_output.txt"
README_FILE     = LOGS_DIR / "README.md"
FEEDBACK_MD     = LOGS_DIR / "feedback.md"
ASSIGNMENT_ID   = 101
TEST_ID         = 1001          # reserved for future use
OLLAMA_MODEL    = "llama3.2:3b"
OLLAMA_HOST     = os.getenv("OLLAMA_HOST", "http://ollama:11434")

# ─────────────────────────── helpers ────────────────────────────
def err(msg: str):
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)

def read_file(path: Path) -> str:
    for enc in ("utf-8", "ISO-8859-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    print(f"Warning: could not decode {path.name}")
    return ""

def run_ollama(prompt: str) -> str:
    """Call the Ollama REST API instead of the CLI."""
    url = f"{OLLAMA_HOST.rstrip('/')}/api/generate"
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False          # single JSON response
    }
    try:
        r = requests.post(url, json=payload, timeout=600)
        r.raise_for_status()
        return r.json()["response"]
    except Exception as e:
        err(f"Ollama API error ⇒ {e}")

def is_perfect_score(text: str) -> bool:
    """True if autograder gave full marks."""
    if "All tests passed" in text:
        return True
    m = re.search(r"Points\s+(\d+)\s*/\s*(\d+)", text, re.I)
    return bool(m and m.group(1) == m.group(2))

# ─────────────────────────── main flow ──────────────────────────
def main() -> None:
    # 0️⃣ repo name
    if len(sys.argv) < 2:
        err("Usage: control_code.py <repo_name>")
    repo_name = sys.argv[1]

    # 1️⃣ gather every file in studentcode/
    if not STUDENT_CODE_DIR.is_dir():
        err(f"{STUDENT_CODE_DIR} not found")
    code_files = sorted(
        p for p in STUDENT_CODE_DIR.rglob("*")
        if p.is_file() and not p.name.startswith(".")
    )
    if not code_files:
        err(f"No files found in {STUDENT_CODE_DIR}")

    student_code_blob = ""
    for p in code_files:
        student_code_blob += f"File: {p.relative_to(STUDENT_CODE_DIR)}\n"
        student_code_blob += read_file(p) + "\n\n"

    autograder_out   = read_file(AUTO_FILE)   if AUTO_FILE.exists() else ""
    professor_instr  = read_file(README_FILE) if README_FILE.exists() else ""

    perfect          = is_perfect_score(autograder_out)

    # 2️⃣  DB connection (for history + later inserts)
    ts   = datetime.utcnow().isoformat() + "Z"
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # pull last 3 reviewed teacher comments
    past_fb = cur.execute(
        """
        SELECT teacher_comments
          FROM feedback
         WHERE repo_name = ? AND reviewed = 1
     ORDER BY reviewed_at DESC
         LIMIT 3
        """,
        (repo_name,),
    ).fetchall()
    prior_feedback = "\n\n".join(row[0] for row in past_fb if row[0]) or "None so far."

    # 3️⃣  build prompt
    if perfect:
        system_note = (
            "The autograder awarded a perfect score. Congratulate the student "
            "briefly. THEN examine Professor Instructions: ask guiding questions "
            "only if the code violates a requirement (e.g. banned libraries, "
            "time complexity). Otherwise add no further guidance."
        )
    else:
        system_note = (
            "Provide question-based guided feedback; do not supply final answers."
        )
    
    prompt = f"""{system_note}
   
print("=== SYSTEM PROMPT USED ===")
print(system_prompt)
print("==========================")

**Student Code**
{student_code_blob}

**Autograder Output**
{autograder_out}

**Professor Instructions**
{professor_instr}

**Recent Teacher Feedback (for context)**
{prior_feedback}
"""
    # 4️⃣ call LLM
    feedback_text = run_ollama(prompt)

    # 5️⃣ write markdown (for GitHub commit)
    FEEDBACK_MD.write_text(f"# Feedback for {repo_name}\n\n{feedback_text}",
                           encoding="utf-8")
    print(f"📄  Feedback saved → {FEEDBACK_MD}")

    # 6️⃣  insert DB rows
    try:
        # submissions row (legacy full blob)
        cur.execute(
            """INSERT INTO submissions
                 (student_repo, assignment_id, code, submitted_at)
               VALUES (?,?,?,?)""",
            (repo_name, ASSIGNMENT_ID, student_code_blob, ts)
        )
        submission_id = cur.lastrowid

        # code_files
        for p in code_files:
            cur.execute(
                "INSERT INTO code_files(submission_id, filename, code) VALUES (?,?,?)",
                (submission_id, str(p.relative_to(STUDENT_CODE_DIR)), read_file(p))
            )

        # autograder output
        cur.execute(
            "INSERT INTO autograder_outputs(submission_id, output, generated_at) "
            "VALUES (?,?,?)",
            (submission_id, autograder_out, ts)
        )

        # feedback (reviewed = 0)
        cur.execute(
            """INSERT INTO feedback
                   (submission_id, repo_name, feedback_text, generated_at)
               VALUES (?,?,?,?)""",
            (submission_id, repo_name, feedback_text, ts)
        )

        conn.commit()
        print("✅ Data inserted into agllmdatabase.db")
    except sqlite3.Error as e:
        conn.rollback()
        err(f"SQLite error → {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
