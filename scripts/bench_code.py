"""Coding benchmark: questions over two repos (click + flask) answered three ways.
  A = agent greps/reads the repos on disk   B = notebook of the raw source files
  D = notebook holding only notelab's research_ingest_codebase digest (structure summary)
usage: bench_code.py <A|B|D> --repos DIR [--notebook ID] [--budget USD]   (DIR contains click/ and flask/)
Appends to $BENCH_OUT (default ./bench-code-results.jsonl)."""
import argparse, json, os, shutil, subprocess, tempfile, time

QUESTIONS = [
    "I want a Flask CLI command that prompts for a password with hidden input. Which Flask and click pieces do I use, and in which files are they defined?",
    "How does the `flask` command find and load the application? Name the functions/classes involved, in call order.",
    "If a click command raises a UsageError inside a Flask CLI command, what exit code results, and which method prints the message?",
    "In which file and function does click implement hidden-input prompting, and which stdlib module does it use?",
    "What is the difference between click's Context.invoke and Context.forward?",
    "Which Flask methods handle an unhandled exception in a view and turn it into an HTTP 500, and how do they differ from the handler for HTTP exceptions?",
    "Give the exact source code of click.utils.make_str, verbatim.",
    "Where is FlaskGroup.parse_toml_config implemented, and what does it do?",
]
FMT = "Answer each concisely (max 70 words, code excepted), naming the file/function for each claim. If something does not exist, say so plainly."


def prompt(arm, nb):
    qs = "\n".join(f"{i+1}. {q}" for i, q in enumerate(QUESTIONS))
    if arm == "A":
        return f"The folders ./click and ./flask hold two Python repos. Use them to answer.\n{qs}\n{FMT}"
    cmds = "\n".join(f'nlmx notebook query {nb} "{q}"' for q in QUESTIONS)
    return f"A NotebookLM notebook holds material about click and flask. Do not browse. Run one nlmx query per question, then answer.\n{cmds}\n{FMT}"


ap = argparse.ArgumentParser()
ap.add_argument("arm"); ap.add_argument("--repos"); ap.add_argument("--notebook")
ap.add_argument("--budget", type=float, default=6)
a = ap.parse_args()
cwd = tempfile.mkdtemp(prefix=f"code-{a.arm}-")
if a.arm == "A":
    for r, sub in (("click", "src"), ("flask", "src")):
        shutil.copytree(os.path.join(a.repos, r, sub), os.path.join(cwd, r))
tools = "Read Glob Grep" if a.arm == "A" else "Bash(nlmx:*)"
t0 = time.time()
try:
    p = subprocess.run(["claude", "-p", prompt(a.arm, a.notebook), "--output-format", "json", "--allowed-tools", tools,
                        "--max-budget-usd", str(a.budget)], cwd=cwd, capture_output=True, text=True, timeout=1500,
                       stdin=subprocess.DEVNULL)
    out, rc = p.stdout, p.returncode
except subprocess.TimeoutExpired:
    out, rc = "", None
rec = {"arm": a.arm, "seconds": round(time.time() - t0, 1), "rc": rc}
text = out
try:
    j = json.loads(out); text = j.get("result", ""); u = j["usage"]
    rec.update(cost_usd=j.get("total_cost_usd"), turns=j.get("num_turns"), in_tok=u["input_tokens"],
               cache_create=u["cache_creation_input_tokens"], cache_read=u["cache_read_input_tokens"],
               out_tok=u["output_tokens"], subtype=j.get("subtype"))
except Exception as e:
    rec["parse_error"] = str(e)
rec["answer"] = text
open(os.environ.get("BENCH_OUT", "bench-code-results.jsonl"), "a").write(json.dumps(rec) + "\n")
print(json.dumps({k: v for k, v in rec.items() if k != "answer"}))
