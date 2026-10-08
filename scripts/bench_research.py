"""Quick research benchmark: same task, two arms, several agent CLIs.
A = agent researches with its own web tools.  B = agent drives notelab's nlmx loop.
Records wall time, answer size, distinct cited URLs, and tokens/cost where the CLI reports them.
usage: bench_research.py <claude|copilot|qoder|kiro-cli|opencode|kilo|kilo-auto|codex> <A|B>
  appends a JSON line to $BENCH_OUT (default ./bench-results.jsonl). Arm B creates a notebook
  named notelab-bench-<runner> in YOUR NotebookLM account; delete it afterwards.
"""
import json, os, re, subprocess, sys, tempfile, time

Q = "the state of quantum error correction: key experimental results and open problems"
TASK = ("Write a ~200 word briefing on " + Q + ". Cite at least 5 distinct sources by full URL. "
        "Print only the briefing.")
A_PROMPT = ("Research this yourself using your web search/fetch tools: find about 8-10 sources, open and read them, "
            "then answer. " + TASK)


def b_prompt(nb):
    return ("Do NOT browse the web yourself. Run exactly these two shell commands, nothing else:\n"
            f'1) nlmx research run "{Q}" --notebook-id {nb} --mode fast\n'
            f'2) nlmx notebook query {nb} "{TASK}"\n'
            "Then print the answer from command 2 verbatim.")


def new_notebook(runner):
    out = subprocess.run(["nlm", "notebook", "create", f"notelab-bench-{runner}"], capture_output=True, text=True).stdout
    return re.search(r"ID:\s*([0-9a-f-]{36})", out).group(1)


def cmd(runner, arm, prompt):
    if runner == "claude":
        tools = "WebSearch WebFetch" if arm == "A" else "Bash(nlmx:*)"
        return ["claude", "-p", prompt, "--output-format", "json", "--allowed-tools", tools]
    if runner == "copilot":
        perm = ["--allow-all-urls"] if arm == "A" else ["--allow-tool=shell(nlmx:*)"]
        return ["copilot", "-p", prompt] + perm
    if runner == "qoder":
        tools = "WebSearch WebFetch" if arm == "A" else "Bash(nlmx:*)"
        return ["qodercli", "-p", prompt, "--allowed-tools", tools]
    if runner == "kiro-cli":
        tools = "web_search,web_fetch" if arm == "A" else "execute_bash"
        return ["kiro-cli", "chat", prompt, "--no-interactive", f"--trust-tools={tools}"]
    if runner == "opencode":
        return ["opencode", "run", prompt, "--standalone"]
    if runner == "codex":
        return ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write",
                "-c", "sandbox_workspace_write.network_access=true", prompt]
    if runner == "kilo-auto":
        return ["kilo", "run", "--auto", "-m", "kilo/kilo-auto/free", prompt]
    if runner == "kilo":
        return ["kilo", "run", "-m", "kilo/kilo-auto/free", prompt]
    raise SystemExit("unknown runner")


def main():
    runner, arm = sys.argv[1], sys.argv[2]
    nb = new_notebook(runner) if arm == "B" else None
    prompt = A_PROMPT if arm == "A" else b_prompt(nb)
    cwd = tempfile.mkdtemp(prefix=f"bench-{runner}-{arm}-")
    t0 = time.time()
    try:
        p = subprocess.run(cmd(runner, arm, prompt), cwd=cwd, capture_output=True, text=True, timeout=900,
                           stdin=subprocess.DEVNULL)
        out, rc, to = p.stdout, p.returncode, False
        err = p.stderr[-300:]
    except subprocess.TimeoutExpired as e:
        out, rc, to, err = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), None, True, ""
    dt = round(time.time() - t0, 1)
    rec = {"runner": runner, "arm": arm, "seconds": dt, "rc": rc, "timeout": to, "notebook": nb}
    text = out
    if runner == "claude":
        try:
            j = json.loads(out); text = j.get("result", "")
            u = j["usage"]
            rec.update(cost_usd=j.get("total_cost_usd"), turns=j.get("num_turns"),
                       in_tok=u["input_tokens"], cache_create=u["cache_creation_input_tokens"],
                       cache_read=u["cache_read_input_tokens"], out_tok=u["output_tokens"],
                       web_search=u.get("server_tool_use", {}).get("web_search_requests"))
        except Exception as e:
            rec["parse_error"] = str(e)
    urls = set(re.findall(r"https?://[^\s)\]>\"']+", text))
    rec.update(answer_words=len(text.split()), distinct_urls=len(urls), answer_head=text[:300], stderr=err)
    open(os.environ.get("BENCH_OUT", "bench-results.jsonl"), "a").write(json.dumps(rec) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k != "answer_head"}))


main()
