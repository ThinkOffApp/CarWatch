#!/usr/bin/env python3
"""Board-vs-board LLM benchmark tables for CarWatch (stdlib only).

Compare two or more board runs written by bench/board-bench.sh:

    python3 bench/compare.py results/vadelma-2026-10-02.json results/ventuno-2026-10-02.json

One result file prints that board's table in the README's format:

    python3 bench/compare.py --readme results/vadelma-2026-10-02.json

Two helper subcommands are used by board-bench.sh itself while it runs, so
the llama-bench parsing lives in one tested place instead of in shell:

    compare.py record META.jsonl section=rows key=value key:=<json> [--bench llama-bench.json]
    compare.py assemble META.jsonl OUT.json

Merge a run into the dash's per-device numbers (board-bench.sh
--update-registry calls this):

    compare.py update-registry RESULT.json [--registry ~/.config/carwatch/model-bench.json] [--threads 4]

Numbers are llama-bench's own mean and standard deviation over its -r
repetitions (tokens per second). CPU rows and ACCELERATED rows (Vulkan,
OpenCL, NPU) are always printed in separate tables: a GPU number in a CPU
column would make the board comparison meaningless.
"""

from __future__ import annotations

import json
import os
import sys

SCHEMA = "carwatch-board-bench/1"
THREAD_MODES = (("4", "4 threads (README baseline)"),
                ("all", "all cores (nproc)"))
STATUS_NOTES = {"SKIPPED-NOFIT": "NOFIT", "MISSING": "MISSING",
                "FAILED": "FAILED"}


# ---------------------------------------------------------------- ingest

def rows_from_llama_bench(entries: list, meta: dict) -> list:
    """llama-bench -o json emits one object per (test, thread count). Merge
    the pp and tg objects of each thread count into one row."""
    by_threads: dict = {}
    for e in entries:
        t = int(e.get("n_threads", 0))
        row = by_threads.setdefault(t, {})
        n_p, n_g = int(e.get("n_prompt", 0)), int(e.get("n_gen", 0))
        samples = e.get("samples_ts") or []
        stat = {"mean": float(e.get("avg_ts", 0.0)),
                "sd": float(e.get("stddev_ts", 0.0)),
                "reps": len(samples) if samples else None}
        if n_p > 0 and n_g == 0:
            row["pp"] = dict(stat, n=n_p)
        elif n_g > 0 and n_p == 0:
            row["tg"] = dict(stat, n=n_g)
        else:
            continue  # pg (combined) tests are not part of this table
        row.setdefault("bench_backends", e.get("backends") or e.get("backend") or "")
        row.setdefault("build_commit", e.get("build_commit", ""))
        row.setdefault("n_gpu_layers", e.get("n_gpu_layers"))
        row.setdefault("model_type", e.get("model_type", ""))
    nproc = meta.get("nproc")
    out = []
    for t in sorted(by_threads):
        modes = []
        if t == 4:
            modes.append("4")
        if nproc is not None and t == int(nproc):
            modes.append("all")
        row = dict(meta)
        row.update(by_threads[t])
        row.setdefault("backend", "CPU")
        row["threads"] = t
        row["thread_modes"] = modes
        row["status"] = "OK" if ("pp" in row and "tg" in row) else "FAILED"
        if row["status"] == "FAILED":
            row["note"] = (row.get("note", "") + " incomplete llama-bench output").strip()
        out.append(row)
    return out


def _parse_kv(args: list) -> dict:
    """key=value -> string, key:=value -> JSON, key:=@file -> JSON read
    from a file (httpie convention)."""
    d = {}
    for a in args:
        if ":=" in a and a.index(":=") + 1 == a.index("="):
            k, v = a.split(":=", 1)
            try:
                if v.startswith("@"):
                    with open(v[1:], encoding="utf-8") as f:
                        d[k] = json.load(f)
                else:
                    d[k] = json.loads(v)
            except (OSError, ValueError) as e:
                d[k] = v if not v.startswith("@") else {"error": str(e)}
        elif "=" in a:
            k, v = a.split("=", 1)
            d[k] = v
        else:
            raise SystemExit(f"record: bad argument {a!r} (want key=value)")
    return d


def _load_llama_bench(path: str) -> list:
    """llama-bench prints the JSON array on stdout; tolerate stray log lines
    around it (some builds print backend init chatter to stdout)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("no JSON array in llama-bench output")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("llama-bench JSON is not a list")
    return data


def cmd_record(argv: list) -> int:
    if not argv:
        raise SystemExit("usage: compare.py record META.jsonl section=... [k=v ...] [--bench FILE]")
    meta_path, rest = argv[0], argv[1:]
    bench = None
    if "--bench" in rest:
        i = rest.index("--bench")
        bench = rest[i + 1]
        rest = rest[:i] + rest[i + 2:]
    kv = _parse_kv(rest)
    lines = []
    if bench is not None:
        section = kv.pop("section", "rows")
        try:
            rows = rows_from_llama_bench(_load_llama_bench(bench), kv)
        except (OSError, ValueError) as e:
            rows = []
            err = str(e)
        else:
            err = "llama-bench produced no rows"
        if not rows:
            row = dict(kv, status="FAILED", threads=None, thread_modes=[])
            row["note"] = (kv.get("note", "") + " " + err).strip()
            rows = [row]
        lines = [dict(r, section=section) for r in rows]
    else:
        lines = [kv]
    with open(meta_path, "a", encoding="utf-8") as f:
        for obj in lines:
            f.write(json.dumps(obj, sort_keys=True) + "\n")
    return 0


def assemble(meta_path: str) -> dict:
    res = {"schema": SCHEMA, "device": {}, "runtimes": {}, "settings": {},
           "brain": {}, "rows": []}
    with open(meta_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            section = obj.pop("section", "rows")
            if section == "rows":
                res["rows"].append(obj)
            elif section == "runtime":
                name = obj.pop("name", "llama.cpp")
                res["runtimes"].setdefault(name, {}).update(obj)
            elif section == "top":
                res.update(obj)
            elif section == "quality":
                res.setdefault("quality", {})[obj.get("id", "?")] = obj
            else:
                res.setdefault(section, {}).update(obj)
    return res


def cmd_assemble(argv: list) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: compare.py assemble META.jsonl OUT.json")
    res = assemble(argv[0])
    tmp = argv[1] + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, argv[1])
    return 0


# ---------------------------------------------------------------- render

def load_result(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        res = json.load(f)
    if res.get("schema") != SCHEMA:
        raise SystemExit(f"{path}: not a {SCHEMA} result (schema={res.get('schema')!r})")
    return res


def board_name(res: dict) -> str:
    return res.get("device", {}).get("hostname") or "board"


def fmt_rate(stat) -> str:
    if not stat:
        return "-"
    m, sd = stat.get("mean"), stat.get("sd")
    if m is None:
        return "-"
    return f"{m:.1f} ± {sd:.1f}" if sd is not None else f"{m:.1f}"


def fmt_size(nbytes) -> str:
    return f"{nbytes / 1e9:.1f} GB" if nbytes else "-"


def _gb(nbytes) -> str:
    try:
        return f"{int(nbytes) / 1e9:.1f} GB"
    except (TypeError, ValueError):
        return "?"


def _gib(nbytes) -> str:
    try:
        return f"{int(nbytes) / 1024 ** 3:.1f} GiB"
    except (TypeError, ValueError):
        return "?"


def device_summary(res: dict) -> str:
    d = res.get("device", {})
    cpus = d.get("cpu_models") or []
    if isinstance(cpus, str):
        cpus = [cpus]
    parts = [
        d.get("model") or "unknown board",
        f"{' + '.join(cpus) or 'cpu?'}, {d.get('cores', '?')} cores, max {d.get('max_mhz', '?')} MHz",
        f"{_gib(d.get('mem_total_bytes'))} RAM",
        f"kernel {d.get('kernel', '?')}",
        d.get("os", "?"),
        f"governor {d.get('governor', '?')}",
        f"models on {d.get('storage', '?')}",
        f"temp {d.get('temp_start_c', '?')} -> {d.get('temp_end_c', '?')} C",
    ]
    thr = d.get("throttled_start")
    if thr:
        parts.append(f"throttled {thr} -> {d.get('throttled_end', '?')}")
    b = res.get("brain", {})
    parts.append("brain stopped for run" if b.get("stopped") else "brain not stopped")
    return f"- **{board_name(res)}** ({res.get('started_utc', '?')}): " + "; ".join(str(p) for p in parts)


def _commit(res: dict, runtime: str = "llama.cpp") -> str:
    return (res.get("runtimes", {}).get(runtime, {}).get("commit") or "unknown")[:12]


def _is_accel(row: dict) -> bool:
    return bool(row.get("accel"))


def _group_key(row: dict) -> tuple:
    return (row.get("runtime", "llama.cpp"),
            row.get("backend", "") if _is_accel(row) else "CPU",
            row.get("mode") or "")


def _find(res: dict, model_id: str, group: tuple, mode: str):
    """The row for one model in one (runtime, backend) table at one thread
    mode. MISSING / NOFIT rows carry no thread count; a CPU one shows in
    every CPU table of its runtime, an accelerated one only in its own."""
    status_row = None
    want_accel = group[1] != "CPU"
    for r in res.get("rows", []):
        if r.get("id") != model_id or r.get("runtime", "llama.cpp") != group[0]:
            continue
        if r.get("status") in STATUS_NOTES and not r.get("thread_modes"):
            # MISSING is recorded once without a mode; NOFIT per mode
            mode_ok = not r.get("mode") or r.get("mode") == group[2]
            if (_is_accel(r) == want_accel and mode_ok
                    and (not want_accel or _group_key(r)[:2] == group[:2])):
                status_row = r
            continue
        if _group_key(r) == group and mode in (r.get("thread_modes") or []):
            return r
    return status_row


def _ordered_models(results: list) -> list:
    seen, out = set(), []
    for res in results:
        for r in res.get("rows", []):
            mid = r.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                out.append((mid, r.get("label") or mid))
    return out


def _groups(results: list) -> list:
    seen, out = set(), []
    for res in results:
        for r in res.get("rows", []):
            if not _is_accel(r) and r.get("status") != "OK" and not r.get("thread_modes"):
                continue
            g = _group_key(r)
            if g not in seen:
                seen.add(g)
                out.append(g)
    if not any(g[:2] == ("llama.cpp", "CPU") for g in seen):
        modes = sorted({r.get("mode") or "" for res in results for r in res.get("rows", [])})
        out[:0] = [("llama.cpp", "CPU", m) for m in modes]
    # CPU groups first, accelerated after: they get their own section.
    return sorted(out, key=lambda g: (g[1] != "CPU", g[0] != "llama.cpp", g[2]))


def _note(row) -> str:
    if row is None:
        return "not run"
    bits = []
    st = row.get("status", "OK")
    if st in STATUS_NOTES:
        bits.append(STATUS_NOTES[st])
    if _is_accel(row):
        bits.append(f"ACCELERATED ({row.get('backend', '?')}, ngl {row.get('n_gpu_layers', '?')})")
    if row.get("load_s") is not None:
        bits.append(f"load {row['load_s']:.1f}s {row.get('load_cache', '')}".strip())
    if row.get("note"):
        bits.append(str(row["note"]))
    if row.get("spec_note"):
        bits.append(str(row["spec_note"]))
    return ", ".join(bits)


def _ratio(a, b, key) -> str:
    try:
        return f"{key} x{b[key]['mean'] / a[key]['mean']:.2f}"
    except (TypeError, KeyError, ZeroDivisionError):
        return f"{key} -"


def _size(results, mid):
    for res in results:
        for r in res.get("rows", []):
            if r.get("id") == mid and r.get("size_bytes"):
                return r["size_bytes"]
    return None


def commit_line(results: list) -> str:
    commits = [(board_name(r), _commit(r)) for r in results]
    uniq = {c for _, c in commits}
    text = "llama.cpp commit(s): " + ", ".join(f"`{c}` ({b})" for b, c in commits)
    if len(uniq) > 1 or "unknown" in uniq:
        text += " **WARNING: commits differ or are unknown; the runs are not like-for-like.**"
    else:
        text += " (same commit on every board)."
    iks = [(board_name(r), _commit(r, "ik_llama.cpp")) for r in results
           if "ik_llama.cpp" in r.get("runtimes", {})]
    if iks:
        text += " ik_llama.cpp: " + ", ".join(f"`{c}` ({b})" for b, c in iks) + "."
    reps = sorted({str(r.get("settings", {}).get("repetitions", "?")) for r in results})
    pp = sorted({str(r.get("settings", {}).get("n_prompt", "?")) for r in results})
    tg = sorted({str(r.get("settings", {}).get("n_gen", "?")) for r in results})
    text += (f"\nEach cell is llama-bench's mean ± sd over N={'/'.join(reps)} repetitions"
             f" (-p {'/'.join(pp)} -n {'/'.join(tg)} -r {'/'.join(reps)}), tokens per second."
             " Ratios are each board divided by the first board.")
    return text


def compare_table(results: list) -> str:
    names = [board_name(r) for r in results]
    out = ["## Board comparison", ""]
    out += [device_summary(r) for r in results]
    out += ["", commit_line(results), ""]
    models = _ordered_models(results)
    show_q = any(r.get("quality") for r in results)
    for runtime, backend, svc in _groups(results):
        group = (runtime, backend, svc)
        accel = backend != "CPU"
        modes = THREAD_MODES if not accel else (("all", "accelerated"),)
        for mode, desc in modes:
            body = []
            for mid, label in models:
                rows = [_find(r, mid, group, mode) for r in results]
                if all(x is None for x in rows):
                    continue
                if accel and not any(x is not None and _is_accel(x) for x in rows):
                    continue
                cells = [label, fmt_size(_size(results, mid))]
                for x in rows:
                    ok = x is not None and x.get("status") == "OK"
                    cells += [fmt_rate(x.get("pp")) if ok else "-",
                              fmt_rate(x.get("tg")) if ok else "-"]
                ratios = []
                base = rows[0]
                for n, x in zip(names[1:], rows[1:]):
                    if base and x and base.get("status") == "OK" and x.get("status") == "OK":
                        ratios.append(f"{n}: {_ratio(base, x, 'pp')}, {_ratio(base, x, 'tg')}")
                cells.append("; ".join(ratios) or "-")
                cells.append(" / ".join(str(x.get("threads")) if x and x.get("threads") else "-"
                                        for x in rows))
                notes = [f"{n}: {_note(x)}" for n, x in zip(names, rows) if _note(x)]
                cells.append("; ".join(notes))
                if show_q:
                    cells.insert(-2, " / ".join(_quality(r, mid) for r in results))
                body.append("| " + " | ".join(cells) + " |")
            if not body:
                continue
            title = (f"### {runtime}, ACCELERATED ({backend}), not comparable to the CPU tables"
                     if accel else f"### {runtime}, CPU only, {desc}") + _svc_title(svc)
            head = ["model", "size"]
            for n in names:
                head += [f"{n} pp512", f"{n} tg128"]
            head += ["ratio"] + (["grounded checks passed"] if show_q else []) + ["threads", "notes"]
            out += [title, "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            out += body + [""]
    return "\n".join(out)


def _svc_title(svc: str) -> str:
    return {"carwatch": ", CarWatch services running",
            "clean": ", CarWatch services stopped (clean)"}.get(svc, f", services: {svc}" if svc else "")


def _quality(res: dict, model_id: str) -> str:
    q = (res.get("quality") or {}).get(model_id)
    if not q:
        return "-"
    r = q.get("result") or {}
    if "passed" not in r:
        return "error"
    return f"{r['passed']}/{r.get('total', '?')}"


def _call(res: dict, row: dict) -> str:
    q = _quality(res, row.get("id"))
    note = _note(row)
    return note if q == "-" else (f"grounded {q}" + (f", {note}" if note else ""))


def readme_table(res: dict) -> str:
    """One board, in the README's 'model | size | prompt | generation | call'
    shape, one table per runtime/backend and thread setting."""
    out = [device_summary(res), "", commit_line([res]), ""]
    models = _ordered_models([res])
    date = (res.get("started_utc") or "")[:10]
    for runtime, backend, svc in _groups([res]):
        group = (runtime, backend, svc)
        accel = backend != "CPU"
        modes = THREAD_MODES if not accel else (("all", "accelerated"),)
        for mode, desc in modes:
            rows = [(label, _find(res, mid, group, mode)) for mid, label in models]
            rows = [(l, r) for l, r in rows if r is not None and (not accel or _is_accel(r))]
            if not rows:
                continue
            threads = sorted({r.get("threads") for _, r in rows if r.get("threads")})
            kind = f"ACCELERATED {backend}" if accel else "CPU"
            out.append(f"{board_name(res)} {date}, {runtime} {kind}, {desc}{_svc_title(svc)}"
                       f" ({'/'.join(map(str, threads)) or '-'} threads), prompt pp512 /"
                       " generation tg128 in tokens per second:")
            out += ["", "| model | size | prompt | generation | call |", "|---|---|---|---|---|"]
            for label, r in rows:
                ok = r.get("status") == "OK"
                out.append(f"| {label} | {fmt_size(r.get('size_bytes'))} | "
                           f"{fmt_rate(r.get('pp')) if ok else '-'} | "
                           f"{fmt_rate(r.get('tg')) if ok else '-'} | {_call(res, r)} |")
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- registry

REGISTRY = os.path.expanduser("~/.config/carwatch/model-bench.json")


def registry_entries(res: dict, threads: int) -> dict:
    """Fresh numbers for the dash's model-bench.json, keyed by .gguf name.

    Only llama.cpp CPU rows at the brain's thread count go in: that is the
    configuration the car actually runs. carwatch/models.py and the dash
    (webchat benchTxt) read TOP-LEVEL pp512 / tg128, so those are written
    too, from the "carwatch" mode when measured (what the car will see),
    else from "clean"."""
    out: dict = {}
    commit = res.get("runtimes", {}).get("llama.cpp", {}).get("commit")
    date = (res.get("started_utc") or "")[:10]
    for r in res.get("rows", []):
        if (r.get("status") != "OK" or _is_accel(r) or r.get("runtime", "llama.cpp") != "llama.cpp"
                or r.get("threads") != threads or not r.get("file")):
            continue
        mode = r.get("mode") or "carwatch"
        e = out.setdefault(r["file"], {"date": date, "bench": {
            "threads": threads, "llama_cpp_commit": commit, "host": board_name(res),
            "reps": (r.get("pp") or {}).get("reps"), "source": "bench/board-bench.sh"}})
        e[mode] = {"pp512": round(r["pp"]["mean"], 1), "tg128": round(r["tg"]["mean"], 1)}
        e["bench"][mode] = {"pp512_sd": round(r["pp"]["sd"], 2), "tg128_sd": round(r["tg"]["sd"], 2)}
        q = (res.get("quality") or {}).get(r.get("id"))
        if q and "passed" in (q.get("result") or {}):
            e["bench"]["grounded"] = f"{q['result']['passed']}/{q['result']['total']}"
    for e in out.values():
        top = e.get("carwatch") or e.get("clean")
        e["pp512"], e["tg128"] = top["pp512"], top["tg128"]
    return out


def update_registry(res: dict, path: str, threads: int) -> dict:
    """Merge into the registry: other models and unknown keys are kept, an
    entry's other mode is kept when only one mode was measured."""
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
        if not isinstance(reg, dict):
            raise ValueError("registry is not an object")
    except FileNotFoundError:
        reg = {}
    fresh = registry_entries(res, threads)
    for fn, new in fresh.items():
        old = reg.get(fn) if isinstance(reg.get(fn), dict) else {}
        bench = dict(old.get("bench") or {})
        bench.update(new.pop("bench"))
        old.update(new)
        old["bench"] = bench
        top = old.get("carwatch") or old.get("clean")
        if top:
            old["pp512"], old["tg128"] = top["pp512"], top["tg128"]
        reg[fn] = old
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return fresh


def cmd_update_registry(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="compare.py update-registry")
    ap.add_argument("result")
    ap.add_argument("--registry", default=REGISTRY)
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args(argv)
    fresh = update_registry(load_result(a.result), a.registry, a.threads)
    print(f"updated {len(fresh)} model(s) in {a.registry}: {', '.join(sorted(fresh)) or 'none'}",
          file=sys.stderr)
    return 0


def main(argv: list) -> int:
    if argv and argv[0] == "update-registry":
        return cmd_update_registry(argv[1:])
    if argv and argv[0] == "record":
        return cmd_record(argv[1:])
    if argv and argv[0] == "assemble":
        return cmd_assemble(argv[1:])
    readme = "--readme" in argv
    paths = [a for a in argv if a != "--readme"]
    if not paths or any(p in ("-h", "--help") for p in paths):
        print(__doc__.strip())
        return 0 if paths else 2
    results = [load_result(p) for p in paths]
    if readme or len(results) == 1:
        print("\n\n".join(readme_table(r) for r in results))
    else:
        print(compare_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
