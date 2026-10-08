"""Corpus benchmark: N papers already on disk (arm A) / already in a NotebookLM notebook (arm B).
Both arms answer the same specific questions. Arm B does NOT run research; the corpus is a one-time setup.
usage: bench_corpus.py <A|B> <runner: claude|muse> --papers DIR --notebook ID [--budget USD]
Appends one JSON line to $BENCH_OUT (default ./bench-corpus-results.jsonl)."""
import argparse, json, os, re, shutil, subprocess, tempfile, time

QUESTIONS = [
    "Which papers report experimental error-correction demonstrations on superconducting qubits, and what error rates do they report?",
    "Which decoding approaches are used across the papers, and which paper uses each?",
    "Which papers use trapped ions or photonics, and which code did each implement?",
    "What is the largest code distance or physical-qubit count reported, and in which paper?",
    "What open problems or limitations do the papers themselves name?",
]
FMT = "Answer all questions concisely (max 80 words each), naming the source paper(s) for every claim. "


def prompt(arm, nb):
    qs = "\n".join(f"{i+1}. {q}" for i, q in enumerate(QUESTIONS))
    if arm == "A":
        return ("The folder ./papers holds PDFs of research papers. Read what you need from them to answer these "
                "questions.\n" + qs + "\n" + FMT)
    cmds = "\n".join(f'nlmx notebook query {nb} "{q}"' for q in QUESTIONS)
    return ("A NotebookLM notebook already contains the papers. Do not research or browse. Run one nlmx query per "
            f"question, then write the answers.\n{cmds}\n" + FMT)


def cmd(runner, arm, p, budget):
    if runner == "claude":
        tools = "Read Glob Grep Bash(ls:*) Bash(pdftotext:*)" if arm == "A" else "Bash(nlmx:*)"
        return ["claude", "-p", p, "--output-format", "json", "--allowed-tools", tools,
                "--max-budget-usd", str(budget)]
    if runner == "muse":
        return ["muse", "exec", p]
    raise SystemExit("runner: claude|muse")


ap = argparse.ArgumentParser()
ap.add_argument("arm"); ap.add_argument("runner")
ap.add_argument("--papers"); ap.add_argument("--notebook"); ap.add_argument("--budget", type=float, default=6)
a = ap.parse_args()
cwd = tempfile.mkdtemp(prefix=f"corpus-{a.runner}-{a.arm}-")
if a.arm == "A":
    shutil.copytree(a.papers, os.path.join(cwd, "papers"))
t0 = time.time()
try:
    r = subprocess.run(cmd(a.runner, a.arm, prompt(a.arm, a.notebook), a.budget), cwd=cwd, capture_output=True,
                       text=True, timeout=1500, stdin=subprocess.DEVNULL)
    out, rc, to = r.stdout, r.returncode, False
except subprocess.TimeoutExpired as e:
    out, rc, to = "", None, True
rec = {"arm": a.arm, "runner": a.runner, "seconds": round(time.time() - t0, 1), "rc": rc, "timeout": to}
text = out
if a.runner == "claude":
    try:
        j = json.loads(out); text = j.get("result", ""); u = j["usage"]
        rec.update(cost_usd=j.get("total_cost_usd"), turns=j.get("num_turns"), in_tok=u["input_tokens"],
                   cache_create=u["cache_creation_input_tokens"], cache_read=u["cache_read_input_tokens"],
                   out_tok=u["output_tokens"], subtype=j.get("subtype"))
    except Exception as e:
        rec["parse_error"] = str(e)
rec.update(answer_words=len(text.split()), answer=text)
open(os.environ.get("BENCH_OUT", "bench-corpus-results.jsonl"), "a").write(json.dumps(rec) + "\n")
print(json.dumps({k: v for k, v in rec.items() if k != "answer"}))
