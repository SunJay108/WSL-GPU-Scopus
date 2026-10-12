#!/usr/bin/env python3
"""
Load test for the Scopus search service: response times, how many users it
can serve at the same time, and GPU use.  Standard library only.

Normally started by scripts/load_test.sh, which runs a throwaway copy of the
search service with its own empty database (real accounts and chats are never
touched).

  python3 scripts/load_test.py --url http://localhost:7870 --out loadtest_results/run1
  python3 scripts/load_test.py --compare loadtest_results/*      table of several runs

Scenarios (--scenarios, comma separated):
  baseline     one user asks questions one after another: time of every stage
  burst        N users ask at the same moment (--levels): waiting time, throughput
  threadpool   many users wait at the same time (--threadpool-users): does the
               rest of the site (login, chat list) stay responsive?
  sustained    users ask, read the answer, ask again (--users, --think, --duration)

The capacity estimate uses mean value analysis of a single server that answers
one question at a time (the search service has one language model instance).
"""

import argparse
import http.cookiejar
import json
import os
import random
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request

RESEARCH_QUESTIONS = [
    "What are recent methods for fault detection in electric motor drives?",
    "What are the main mechanisms of catalyst degradation in CO2 electrolysis?",
    "How does remote work affect employee productivity?",
    "What factors influence customer satisfaction in online retail?",
    "How is machine learning used to predict protein structure?",
    "What are the effects of microplastics on marine organisms?",
    "How does social media use affect adolescent mental health?",
    "What strategies improve supply chain resilience?",
    "How do perovskite solar cells degrade under illumination?",
    "What is the role of gut microbiota in obesity?",
    "How does corporate social responsibility affect firm performance?",
    "What are the challenges of implementing electronic health records?",
    "How do second language learners acquire vocabulary?",
    "How does climate change affect crop yields?",
    "How do lithium-ion batteries age during fast charging?",
    "What drives employee turnover in the hospitality industry?",
    "What are the neural mechanisms of working memory?",
    "How is deep learning used for medical image segmentation?",
    "What factors affect student engagement in online learning?",
    "What are the impacts of tourism on local communities?",
]
# Refused by the guard without a search: the fast path.
FAST_QUESTIONS = [
    "hello, who are you?",
    "write a poem about the sea",
    "best pizza recipe in Naples",
    "who is the president of France",
]
PASSWORD = "load-test-pass-1"
STAGE_ORDER = ["loading", "queued", "check", "search", "answer", "summary", "cite", "followups"]

# Reading the answer and typing the next question (seconds), and the mean
# response times the estimate is given for.
THINK_TIMES = [60, 120, 300]
TARGETS = [30, 60]


# =====================================================
# Helpers
# =====================================================

def pct(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def fmt(value, digits=1):
    return "-" if value is None else f"{value:.{digits}f}"


def log(text):
    print(time.strftime("%H:%M:%S"), text, flush=True)


class Client:
    """One simulated user with its own login cookie."""

    def __init__(self, base, name):
        self.base, self.name = base, name
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, timeout=120):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        with self.opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"null")

    def sign_in(self):
        try:
            self.call("POST", "/api/auth/signup", {"username": self.name, "password": PASSWORD})
        except urllib.error.HTTPError:
            self.call("POST", "/api/auth/login", {"username": self.name, "password": PASSWORD})
        return self

    def new_conversation(self):
        return self.call("POST", "/api/conversations")["id"]

    def ask(self, conversation_id, question, kind):
        """Ask one question and time the answer stream (server-sent events)."""
        r = {"user": self.name, "question": question, "kind": kind, "stages": {},
             "first_delta": None, "done": None, "status": None, "category": None,
             "error": None, "answer_chars": 0, "references": 0}
        req = urllib.request.Request(
            f"{self.base}/api/conversations/{conversation_id}/ask", method="POST",
            data=json.dumps({"question": question}).encode(),
            headers={"Content-Type": "application/json"})
        r["submitted"] = time.time()
        try:
            with self.opener.open(req, timeout=1800) as resp:
                event = None
                for raw in resp:
                    now = time.time()
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:])
                    if event == "stage":
                        r["stages"].setdefault(data["stage"], now)
                    elif event == "delta" and r["first_delta"] is None:
                        r["first_delta"] = now
                    elif event in ("done", "error"):
                        d = (data.get("message") or {}).get("data") or {}
                        r.update(done=now, status=d.get("status") or event,
                                 category=(d.get("guard") or {}).get("category"),
                                 answer_chars=len(d.get("answer") or ""),
                                 references=len(d.get("references") or []))
                        break
        except Exception as exc:
            r["error"] = f"{type(exc).__name__}: {exc}"
        if r["done"] is None:
            r["done"] = time.time()
            r["error"] = r["error"] or "stream ended without an answer"
        return r


def total_s(r):
    return r["done"] - r["submitted"]


def wait_s(r):
    """Time before the server started on the question (waiting for the model)."""
    start = r["stages"].get("loading") or r["stages"].get("check")
    return (start - r["submitted"]) if start else None


def ttft_s(r):
    return (r["first_delta"] - r["submitted"]) if r["first_delta"] else None


def service_s(r):
    """Time the server spent on the question itself (from its first stage to the answer)."""
    start = r["stages"].get("check")
    return (r["done"] - start) if start else None


def stage_durations(r):
    marks = sorted(((t, name) for name, t in r["stages"].items() if name != "queued"))
    marks.append((r["done"], "done"))
    return {name: marks[i + 1][0] - t for i, (t, name) in enumerate(marks[:-1])}


class GpuSampler:
    """nvidia-smi samples every 0.5 s (skipped if nvidia-smi is not available)."""

    FIELDS = "utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu"

    def __init__(self):
        self.samples = []
        try:
            self.proc = subprocess.Popen(
                ["nvidia-smi", f"--query-gpu={self.FIELDS}", "--format=csv,noheader,nounits", "-lms", "500"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except FileNotFoundError:
            self.proc = None
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                util, used, total, power, temp = (float(x) for x in line.split(",")[:5])
            except ValueError:
                continue
            self.samples.append((time.time(), util, used, total, power, temp))

    def window(self, t0, t1):
        s = [x for x in self.samples if t0 <= x[0] <= t1]
        if not s:
            return {}
        return {"util_avg": mean([x[1] for x in s]), "util_max": max(x[1] for x in s),
                "vram_max_mib": max(x[2] for x in s), "vram_total_mib": s[0][3],
                "power_avg_w": mean([x[4] for x in s]), "temp_max_c": max(x[5] for x in s)}

    def stop(self):
        if self.proc:
            self.proc.terminate()


class Probe:
    """A logged-in user who keeps opening the chat list while others wait for answers."""

    def __init__(self, client, interval=0.5):
        self.client, self.interval = client, interval
        self.samples, self._stop = [], threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.client.call("GET", "/api/conversations", timeout=120)
                ok = True
            except Exception:
                ok = False
            self.samples.append((t0, time.time() - t0, ok))
            self._stop.wait(max(0.0, self.interval - (time.time() - t0)))

    def window(self, t0, t1):
        s = [x for x in self.samples if t0 <= x[0] <= t1]
        lat = [x[1] for x in s]
        return {"requests": len(s), "p50_s": pct(lat, 50), "p95_s": pct(lat, 95),
                "max_s": max(lat) if lat else None, "failed": sum(not x[2] for x in s),
                "slow_over_2s": sum(x > 2 for x in lat)}

    def stop(self):
        self._stop.set()


def summarize(results):
    research = [r for r in results if r["kind"] == "research" and not r["error"]]
    return {
        "questions": len(results),
        "errors": sum(bool(r["error"]) for r in results),
        "answered": sum(r["status"] == "answered" for r in results),
        "wait_p50_s": pct([wait_s(r) for r in results], 50),
        "wait_p95_s": pct([wait_s(r) for r in results], 95),
        "wait_max_s": max((wait_s(r) or 0 for r in results), default=None),
        "ttft_p50_s": pct([ttft_s(r) for r in research], 50),
        "ttft_p95_s": pct([ttft_s(r) for r in research], 95),
        "total_mean_s": mean([total_s(r) for r in results]),
        "total_p50_s": pct([total_s(r) for r in results], 50),
        "total_p95_s": pct([total_s(r) for r in results], 95),
        "total_max_s": max((total_s(r) for r in results), default=None),
        "service_mean_s": mean([service_s(r) for r in results]),
        "service_max_s": max((service_s(r) or 0 for r in results), default=None),
        "answer_chars_max": max((r["answer_chars"] for r in results), default=0),
    }


def baseline_summary(results):
    stages = {}
    for r in results:
        if r["kind"] == "research" and r["status"] == "answered":
            for name, d in stage_durations(r).items():
                stages.setdefault(name, []).append(d)
    research = [r for r in results if r["kind"] == "research" and not r["error"]]
    fast = [r for r in results if r["kind"] == "fast" and not r["error"]]
    answered = [r for r in research if r["status"] == "answered"]
    return {
        "research": summarize(research), "fast": summarize(fast),
        "service_research_s": mean([total_s(r) for r in research]),
        "service_fast_s": mean([total_s(r) for r in fast]),
        "stages_median_s": {k: statistics.median(v) for k, v in stages.items()},
        "answer_chars_mean": mean([r["answer_chars"] for r in answered]),
        # Characters of answer text the language model writes per second.
        "writing_speed_cps": mean([r["answer_chars"] / stage_durations(r)["answer"]
                                   for r in answered if stage_durations(r).get("answer")]),
    }


def finalize(data, fast_share):
    """(Re)compute every summary and the estimate from the raw results."""
    if "baseline" in data:
        data["baseline"].update(baseline_summary(data["baseline"]["results"]))
    for run in data.get("burst", []) + [data[k] for k in ("threadpool", "sustained") if k in data]:
        run["summary"] = summarize(run["results"])
    data["estimate"] = estimate(data, fast_share)


# =====================================================
# Scenarios
# =====================================================

def run_together(clients, questions, kind, gpu, probe):
    """Every client asks one question at the same moment."""
    n = len(clients)
    convs = [c.new_conversation() for c in clients]
    barrier = threading.Barrier(n)
    out = [None] * n

    def go(i):
        barrier.wait()
        out[i] = clients[i].ask(convs[i], questions[i], kind)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t1 = time.time()
    return {"users": n, "seconds": t1 - t0, "throughput_per_min": 60 * n / (t1 - t0),
            "summary": summarize(out), "gpu": gpu.window(t0, t1), "probe": probe.window(t0, t1),
            "results": out}


def scenario_baseline(clients, args, gpu, probe):
    c = clients[0]
    out = []
    t0 = time.time()
    plan = [(q, "research") for q in RESEARCH_QUESTIONS[:args.baseline]] + \
           [(q, "fast") for q in FAST_QUESTIONS[:args.baseline_fast]]
    for i, (q, kind) in enumerate(plan, 1):
        r = c.ask(c.new_conversation(), q, kind)
        out.append(r)
        log(f"  baseline {i}/{len(plan)}: {r['status'] or r['error']} in {total_s(r):.1f}s  {q[:60]}")
    t1 = time.time()
    return {"gpu": gpu.window(t0, t1), "results": out, **baseline_summary(out)}


def scenario_burst(clients, args, gpu, probe):
    runs, offset = [], 0
    for n in args.levels:
        qs = [RESEARCH_QUESTIONS[(offset + i) % len(RESEARCH_QUESTIONS)] for i in range(n)]
        offset += n
        log(f"  burst: {n} users at once")
        run = run_together(clients[:n], qs, "research", gpu, probe)
        s = run["summary"]
        log(f"    {run['seconds']:.0f}s, {run['throughput_per_min']:.1f} answers/min, wait p95 "
            f"{fmt(s['wait_p95_s'])}s, total max {fmt(s['total_max_s'])}s, errors {s['errors']}")
        runs.append(run)
    return runs


def scenario_threadpool(clients, args, gpu, probe):
    n = args.threadpool_users
    qs = [FAST_QUESTIONS[i % len(FAST_QUESTIONS)] for i in range(n)]
    log(f"  threadpool: {n} users wait at once (quickly refused questions)")
    run = run_together(clients[:n], qs, "fast", gpu, probe)
    p = run["probe"]
    log(f"    chat list while waiting: p50 {fmt(p['p50_s'], 2)}s, max {fmt(p['max_s'], 2)}s, "
        f"{p['slow_over_2s']} of {p['requests']} slower than 2 s, errors {run['summary']['errors']}")
    return run


def scenario_sustained(clients, args, gpu, probe):
    users = clients[:args.users]
    rng = random.Random(7)
    stop_at = time.time() + args.duration
    out, lock = [], threading.Lock()

    def user(c, seed):
        r_ = random.Random(seed)
        time.sleep(r_.uniform(0, min(args.think, args.duration / 2)))  # staggered start
        while time.time() < stop_at:
            r = c.ask(c.new_conversation(), r_.choice(RESEARCH_QUESTIONS), "research")
            with lock:
                out.append(r)
            pause = r_.expovariate(1 / args.think)
            if time.time() + pause >= stop_at:
                break
            time.sleep(pause)

    log(f"  sustained: {len(users)} users, think time {args.think}s, {args.duration}s")
    threads = [threading.Thread(target=user, args=(c, rng.random())) for c in users]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t1 = time.time()
    s = summarize(out)
    log(f"    {len(out)} answers, mean response {fmt(s['total_mean_s'])}s, p95 {fmt(s['total_p95_s'])}s")
    return {"users": len(users), "think_s": args.think, "seconds": t1 - t0,
            "throughput_per_min": 60 * len(out) / (t1 - t0), "summary": s,
            "gpu": gpu.window(t0, t1), "probe": probe.window(t0, t1), "results": out}


# =====================================================
# Capacity estimate
# =====================================================

def mva(service_s, think_s, users):
    """Mean response time of `users` users sharing one server (exact MVA, delay station)."""
    queue = 0.0
    response = service_s
    for n in range(1, users + 1):
        response = service_s * (1 + queue)
        throughput = n / (response + think_s)
        queue = throughput * response
    return response


def max_users(service_s, think_s, target_s):
    n = 1
    while n < 10000 and mva(service_s, think_s, n + 1) <= target_s:
        n += 1
    return n if mva(service_s, think_s, n) <= target_s else 0


def estimate(data, fast_share):
    """
    Capacity from the time per question.  Two values when the multi-user tests
    ran: the one-user baseline (fewer questions) and the time measured during
    the multi-user tests (more questions, more varied answer lengths); each
    figure is then a range [conservative, optimistic].
    """
    b = data.get("baseline") or {}
    s_r, s_f = b.get("service_research_s"), b.get("service_fast_s") or 0
    if not s_r:
        return {}
    loaded = [service_s(r) for run in data.get("burst", []) + ([data["sustained"]] if "sustained" in data else [])
              for r in run["results"] if r["kind"] == "research" and not r["error"]]
    times = {"one_user_s": (1 - fast_share) * s_r + fast_share * s_f}
    if len(loaded) >= 5:
        times["multi_user_s"] = (1 - fast_share) * mean(loaded) + fast_share * s_f
    slow, quick = max(times.values()), min(times.values())
    return {
        **times, "fast_share": fast_share,
        "answers_per_hour": [3600 / slow, 3600 / quick],
        "at_once_within_60s": [int(60 // slow), int(60 // quick)],
        "at_once_within_120s": [int(120 // slow), int(120 // quick)],
        "users": {f"think{z}_target{t}": [max_users(slow, z, t), max_users(quick, z, t)]
                  for z in THINK_TIMES for t in TARGETS},
    }


def span(values, digits=0):
    """'low–high' for a range (list), or one number when there is only one value."""
    values = [v for v in (values if isinstance(values, list) else [values]) if v is not None]
    if not values:
        return "-"
    lo, hi = fmt(min(values), digits), fmt(max(values), digits)
    return lo if lo == hi else f"{lo}–{hi}"


# =====================================================
# Report
# =====================================================

def report_md(data):
    m, lines = data["meta"], []
    add = lines.append
    add(f"# Search service load test: {m['label']}\n")
    add(f"- Date: {m['date']}  \n- GPU: {m.get('gpu', '-')}  \n- Settings: {m.get('settings') or 'from .env'}")
    add(f"- Index: {m.get('index_papers') or '-'} papers\n")

    b = data.get("baseline")
    if b:
        add("## One user (baseline)\n")
        add(f"Research question: **{fmt(b['service_research_s'])} s** on average "
            f"(first text after {fmt(b['research']['ttft_p50_s'])} s, answer about "
            f"{fmt(b['answer_chars_mean'], 0)} characters written at "
            f"{fmt(b.get('writing_speed_cps'), 0)} characters/s). Refused question: "
            f"**{fmt(b['service_fast_s'])} s**.\n")
        add("| Stage | Median (s) |\n|---|---|")
        for name in STAGE_ORDER:
            if name in b["stages_median_s"]:
                add(f"| {name} | {b['stages_median_s'][name]:.2f} |")
        g = b.get("gpu") or {}
        add(f"\nGPU while answering: {fmt(g.get('util_avg'), 0)} % average use, "
            f"{fmt(g.get('vram_max_mib'), 0)} / {fmt(g.get('vram_total_mib'), 0)} MiB memory, "
            f"{fmt(g.get('power_avg_w'), 0)} W, max {fmt(g.get('temp_max_c'), 0)} °C\n")

    if data.get("burst"):
        add("## Users asking at the same moment\n")
        add("| Users | Last answer after (s) | Answers/min | Wait p50 / p95 / max (s) | "
            "First text p50 / p95 (s) | Total p50 / p95 (s) | Errors | GPU use | Chat list max (s) |")
        add("|---|---|---|---|---|---|---|---|---|")
        for run in data["burst"]:
            s, g, p = run["summary"], run["gpu"], run["probe"]
            add(f"| {run['users']} | {run['seconds']:.0f} | {run['throughput_per_min']:.1f} | "
                f"{fmt(s['wait_p50_s'])} / {fmt(s['wait_p95_s'])} / {fmt(s['wait_max_s'])} | "
                f"{fmt(s['ttft_p50_s'])} / {fmt(s['ttft_p95_s'])} | "
                f"{fmt(s['total_p50_s'])} / {fmt(s['total_p95_s'])} | {s['errors']} | "
                f"{fmt(g.get('util_avg'), 0)} % | {fmt(p['max_s'], 2)} |")
        add("")

    t = data.get("threadpool")
    if t:
        p = t["probe"]
        add("## Many users waiting at once\n")
        add(f"{t['users']} users asked at the same moment (questions that are refused quickly). "
            f"All done after {t['seconds']:.0f} s, errors: {t['summary']['errors']}. Meanwhile the "
            f"chat list took p50 {fmt(p['p50_s'], 2)} s, max {fmt(p['max_s'], 2)} s; "
            f"{p['slow_over_2s']} of {p['requests']} requests were slower than 2 s.\n")

    s = data.get("sustained")
    if s:
        x = s["summary"]
        add("## Users asking repeatedly\n")
        add(f"{s['users']} users, each reading for {s['think_s']} s on average before the next "
            f"question, for {s['seconds']:.0f} s: {x['questions']} answers "
            f"({s['throughput_per_min']:.1f}/min), response mean {fmt(x['total_mean_s'])} s, "
            f"p95 {fmt(x['total_p95_s'])} s, max {fmt(x['total_max_s'])} s, errors {x['errors']}. "
            f"GPU use {fmt(s['gpu'].get('util_avg'), 0)} %. Longest answer: "
            f"{x['answer_chars_max']} characters, {fmt(x['service_max_s'])} s of server time.")
        e = data.get("estimate") or {}
        if e:
            preds = [mva(e[k], s["think_s"], s["users"]) for k in ("multi_user_s", "one_user_s") if k in e]
            add(f"The estimate below predicts a mean response of {span(preds, 1)} s for this load.\n")

    e = data.get("estimate")
    if e:
        times = [e[k] for k in ("multi_user_s", "one_user_s") if k in e]
        add("## Estimated capacity\n")
        add(f"One language model answers one question at a time: {span(times, 1)} s per question "
            f"({int(e['fast_share'] * 100)} % refused quickly) = about "
            f"**{span(e['answers_per_hour'])} answers per hour**.")
        if len(times) > 1:
            add("Ranges: the first value uses the time per question measured in the multi-user tests "
                "(more questions, more varied answers), the second the one-user test.")
        add(f"\n- Asking at the very same moment: {span(e['at_once_within_60s'])} users get their "
            f"answer within 60 s, {span(e['at_once_within_120s'])} within 120 s.")
        add("- Users who keep asking (reading time between questions = think time):\n")
        add("| Think time | Users, mean response ≤ 30 s | Users, mean response ≤ 60 s |")
        add("|---|---|---|")
        for z in THINK_TIMES:
            add(f"| {z} s | {span(e['users'][f'think{z}_target30'])} | {span(e['users'][f'think{z}_target60'])} |")
        add("\nLogged-in users who are only reading old chats or are idle cost nothing; only "
            "questions use the GPU.\n")
    return "\n".join(lines)


def compare(paths):
    rows = []
    for path in paths:
        f = os.path.join(path, "results.json")
        if not os.path.exists(f):
            continue
        d = json.load(open(f))
        b, e, m = d.get("baseline") or {}, d.get("estimate") or {}, d["meta"]
        rows.append((m.get("settings") or "from .env", b.get("service_research_s"),
                     (b.get("research") or {}).get("ttft_p50_s"), b.get("writing_speed_cps"),
                     (b.get("gpu") or {}).get("vram_max_mib"), e.get("answers_per_hour") or [None],
                     (e.get("users") or {}).get("think120_target60") or [None]))
    print("| Settings | Research answer (s) | First text (s) | Writing speed (chars/s) | "
          "GPU memory (MiB) | Answers/hour | Users (think 120 s, mean ≤ 60 s) |")
    print("|---|---|---|---|---|---|---|")
    for settings, s, ttft, speed, vram, per_hour, users in rows:
        print(f"| {settings} | {fmt(s)} | {fmt(ttft)} | {fmt(speed, 0)} | {fmt(vram, 0)} | "
              f"{span(per_hour)} | {span(users)} |")


# =====================================================
# Main
# =====================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://localhost:7870")
    ap.add_argument("--scenarios", default="baseline,burst,threadpool,sustained")
    ap.add_argument("--baseline", type=int, default=8, help="research questions in the baseline")
    ap.add_argument("--baseline-fast", type=int, default=4, help="refused questions in the baseline")
    ap.add_argument("--levels", default="1,2,4,8,16", help="users asking at the same moment")
    ap.add_argument("--threadpool-users", type=int, default=48)
    ap.add_argument("--users", type=int, default=10, help="users in the sustained test")
    ap.add_argument("--think", type=float, default=60, help="mean reading time between questions (s)")
    ap.add_argument("--duration", type=float, default=360, help="length of the sustained test (s)")
    ap.add_argument("--fast-share", type=float, default=0.1, help="share of refused questions in the estimate")
    ap.add_argument("--label", default="default")
    ap.add_argument("--settings", default="")
    ap.add_argument("--out", default=None)
    ap.add_argument("--compare", nargs="+", metavar="DIR", help="print a table of earlier runs and exit")
    ap.add_argument("--rebuild", nargs="+", metavar="DIR", help="recompute the reports of earlier runs and exit")
    args = ap.parse_args()

    if args.compare:
        compare(args.compare)
        return
    if args.rebuild:
        for path in args.rebuild:
            with open(os.path.join(path, "results.json")) as f:
                save(json.load(f), path, args.fast_share)
        return

    args.levels = [int(x) for x in args.levels.split(",") if x.strip()]
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    out_dir = args.out or os.path.join("loadtest_results", time.strftime("%Y%m%d_%H%M%S") + "_" + args.label)
    os.makedirs(out_dir, exist_ok=True)

    need = 2 + max([1] + (args.levels if "burst" in scenarios else [])
                   + ([args.threadpool_users] if "threadpool" in scenarios else [])
                   + ([args.users] if "sustained" in scenarios else []))
    log(f"signing in {need} test users at {args.url}")
    clients = [Client(args.url, f"loadtest{i:03d}").sign_in() for i in range(need)]
    probe_client, clients = clients[0], clients[1:]

    gpu = GpuSampler()
    meta = {"label": args.label, "settings": args.settings, "url": args.url,
            "date": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        name = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        meta["gpu"] = name
    except Exception:
        pass
    status = probe_client.call("GET", "/api/status")
    meta["index_papers"] = (status.get("index") or {}).get("papers")

    log("warm-up question (loads the models)")
    w = clients[0].ask(clients[0].new_conversation(), RESEARCH_QUESTIONS[-1], "research")
    log(f"  warm-up: {w['status'] or w['error']} in {total_s(w):.1f}s")

    probe = Probe(probe_client)
    data = {"meta": meta}
    try:
        if "baseline" in scenarios:
            log("scenario: baseline")
            data["baseline"] = scenario_baseline(clients, args, gpu, probe)
        if "burst" in scenarios:
            log("scenario: burst")
            data["burst"] = scenario_burst(clients, args, gpu, probe)
        if "threadpool" in scenarios:
            log("scenario: threadpool")
            data["threadpool"] = scenario_threadpool(clients, args, gpu, probe)
        if "sustained" in scenarios:
            log("scenario: sustained")
            data["sustained"] = scenario_sustained(clients, args, gpu, probe)
    finally:
        probe.stop()
        gpu.stop()
        save(data, out_dir, args.fast_share)
        log(f"results written to {out_dir}")


def save(data, out_dir, fast_share):
    finalize(data, fast_share)
    with open(os.path.join(out_dir, "results.json"), "w") as f:
        json.dump(data, f, indent=1)
    md = report_md(data)
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write(md + "\n")
    print("\n" + md)


if __name__ == "__main__":
    main()
