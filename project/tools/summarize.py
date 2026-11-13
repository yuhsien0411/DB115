#!/usr/bin/env python3
"""把 runs.csv／failures.csv 整理成報告用的表格（Markdown）。

  表 1  兩方案在 50／200／500 併發下的吞吐（ops/s）與 p95（3 輪取中位數）
  表 2  PG 鎖競爭代價：同一併發下 p95(hot) − p95(uniform1000)；Redis 列為對照
  表 3  鎖等待取樣：Lock:transactionid 等事件出現在多少比例的取樣中（取樣，不是時間）
  表 4  Redis 遺失視窗：強制中斷後少算的上下界，與提案判準 3 的門檻比較（D41 前為「吞吐 × T」）
  表 5  三條成功判準逐輪判定（提案第四節：200 併發，3 次都成立才算通過）

作廢（RUN VOID）、對帳不一致（COUNT MISMATCH）或缺值的列不納入；只取指定的正式期長度與回寫間隔；
同一格同一輪跑了多次時只取最後一次。有效輪數不足 3 的格會標註。
只讀結果檔，不產生任何量測值。設計見 docs/design/summarize.md。
"""
import argparse
import csv
import json
import os
import platform
import re
import statistics
import subprocess
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = [(b, s, c) for b in ("pg", "redis") for s in ("hot80", "uniform1000") for c in (50, 200, 500)]
HOT = ["hot80", "hot5"]
EVENTS = ["Lock:transactionid", "Lock:tuple"]  # 同一列 UPDATE 互等時會出現的兩種行鎖等待


def num(v):
    return float(v) if v not in ("", None) else None


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt(v, digits=1):
    if v is None:
        return "—"
    return f"{v:,.{digits}f}" if isinstance(v, float) and digits else f"{int(v):,}"


def pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def bad_reason(notes):
    for tag in ("RUN VOID", "MISMATCH", "criterion3=VOID"):
        if tag in notes:
            return tag
    return None


def load_runs(path, duration, interval):
    """回傳 (有效列, [(run_id, 排除原因)], 其他設定的列數, [(被取代的 run_id, 取代者)])。"""
    rows, dropped, other = [], [], 0
    if not os.path.exists(path):
        return rows, dropped, other, []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if duration is not None and num(row["measured_s"]) != duration:
                other += 1
            elif row["backend"] == "redis" and num(row["flush_interval_s"]) != interval:
                other += 1
            elif bad_reason(row["notes"]):
                dropped.append((row["run_id"], bad_reason(row["notes"])))
            elif num(row["success_ops_per_s"]) is None or num(row["p95_ms"]) is None:
                dropped.append((row["run_id"], "缺吞吐或 p95"))
            else:
                rows.append(row)
    # 同一格同一輪補跑過：只取最後一次（run_id 結尾是時間戳）
    latest, superseded = {}, []
    for r in sorted(rows, key=lambda r: r["run_id"].rsplit("_", 1)[-1]):
        k = (r["backend"], r["scenario"], r["concurrency"], r["round"])
        if k in latest:
            superseded.append((latest[k]["run_id"], r["run_id"]))
        latest[k] = r
    return list(latest.values()), dropped, other, superseded


def lock_share(row):
    m = re.search(r"lock_share=([\d.]+)", row["notes"])
    return float(m.group(1)) if m else None


def wait_samples(row):
    """回傳 (取樣次數, {事件: 出現的取樣次數}, {事件: 等待連線數總和})；沒有取樣檔回 None。"""
    d = os.path.join(ROOT, row["raw_log_path"])
    try:
        with open(os.path.join(d, "waits_summary.json")) as f:
            samples = json.load(f)["samples"]
        seen, backends = defaultdict(set), defaultdict(int)
        with open(os.path.join(d, "waits.csv"), newline="") as f:
            for r in csv.DictReader(f):
                k = f"{r['wait_event_type']}:{r['wait_event']}"
                seen[k].add(r["ts"])
                backends[k] += int(r["count"])
    except FileNotFoundError:
        return None
    return samples, {k: len(v) for k, v in seen.items()}, dict(backends)


def group(rows):
    g = defaultdict(list)
    for r in rows:
        g[(r["backend"], r["scenario"], int(r["concurrency"]))].append(r)
    return g


def levels(g):
    """資料裡出現過的併發數（煙霧測試可能不是 50／200／500）。"""
    return sorted({k[2] for k in g} | {c for _, _, c in REQUIRED})


def cell(rows):
    if not rows:
        return None
    ok = sum(int(num(r["successful_ops"]) or 0) for r in rows)
    bad = sum(int(num(r["error_ops"]) or 0) + int(num(r["unknown_ops"]) or 0) for r in rows)
    return {
        "n": len(rows),
        "ops": med(num(r["success_ops_per_s"]) for r in rows),
        "p95": med(num(r["p95_ms"]) for r in rows),
        "bad_share": bad / (ok + bad) if ok + bad else None,
    }


def n_mark(c, need):
    if c is None:
        return "0 ⚠"
    return f"{c['n']}" + (" ⚠" if c["n"] < need else "")


def table_main(g, need):
    out = ["## 表 1　吞吐與 p95（中位數）", "",
           "p95 只算成功的請求；「錯誤＋未知」不為 0 時，該格的 p95 可能偏低，報告要一起寫出。", "",
           "| 併發 | 流量 | PG ops/s | PG p95 ms | Redis ops/s | Redis p95 ms | 吞吐倍數 Redis÷PG | 錯誤＋未知 PG／Redis | 有效輪數 PG／Redis |",
           "|---:|---|---:|---:|---:|---:|---:|---|---|"]
    for c in levels(g):
        for s in ["hot80", "uniform1000", "hot5"]:
            a, b = cell(g.get(("pg", s, c))), cell(g.get(("redis", s, c)))
            if a is None and b is None:
                continue
            ratio = b["ops"] / a["ops"] if a and b and a["ops"] else None
            out.append(f"| {c} | {s} | {fmt(a and a['ops'])} | {fmt(a and a['p95'], 2)} | "
                       f"{fmt(b and b['ops'])} | {fmt(b and b['p95'], 2)} | {fmt(ratio, 1)} | "
                       f"{pct(a and a['bad_share'])}／{pct(b and b['bad_share'])} | "
                       f"{n_mark(a, need)}／{n_mark(b, need)} |")
    return out


def table_lock_cost(g, need):
    out = ["## 表 2　PG 鎖競爭代價：p95(熱點) − p95(uniform1000)", "",
           "同一份腳本、同一併發，只改商品分布。題目把這個差值定義為鎖競爭代價；它以行鎖等待為主（表 3 佐證），"
           "但也包含分布改變帶來的其他差異（快取命中、排隊）。Redis 沒有行鎖，同樣計算當作對照。", "",
           "| 方案 | 併發 | 熱點組 | p95 熱點 ms | p95 均勻 ms | 差值 ms | 倍數 | 吞吐 熱點／均勻 | 有效輪數 熱點／均勻 |",
           "|---|---:|---|---:|---:|---:|---:|---:|---|"]
    for backend in ["pg", "redis"]:
        for c in levels(g):
            u = cell(g.get((backend, "uniform1000", c)))
            for s in HOT:
                h = cell(g.get((backend, s, c)))
                if h is None or u is None:
                    continue
                diff = h["p95"] - u["p95"] if h["p95"] is not None and u["p95"] is not None else None
                ratio = h["p95"] / u["p95"] if diff is not None and u["p95"] else None
                tput = h["ops"] / u["ops"] if h["ops"] and u["ops"] else None
                name = "PG" if backend == "pg" else "Redis（對照）"
                out.append(f"| {name} | {c} | {s} | {fmt(h['p95'], 2)} | {fmt(u['p95'], 2)} | "
                           f"{fmt(diff, 2)} | {fmt(ratio, 2)} | {pct(tput)} | "
                           f"{n_mark(h, need)}／{n_mark(u, need)} |")
    return out


def table_waits(g):
    out = ["## 表 3　鎖等待取樣（pg_stat_activity）", "",
           "> **這是取樣，不是時間。** `pg_stat_activity` 只顯示「取樣那一刻」每條連線在等什麼，沒有累計等待時間欄位。"
           "下表的比例是「在幾次取樣中看到」與「看到幾條連線在等」，不能換算成等了幾毫秒，也不能說「XX% 的時間在等鎖」。", "",
           "| 併發 | 流量 | 取樣次數 | 出現 Lock:transactionid 的取樣 | 出現 Lock:tuple 的取樣 | 每次取樣平均等 transactionid 的連線 | 等 Lock 連線 ÷ active 連線（D8） | 輪數 |",
           "|---:|---|---:|---:|---:|---:|---:|---:|"]
    any_row = False
    for c in levels(g):
        for s in ["hot80", "uniform1000", "hot5"]:
            rows = g.get(("pg", s, c), [])
            got = [w for w in map(wait_samples, rows) if w]
            if not got:
                if rows:
                    out.append(f"| {c} | {s} | 未取樣 | — | — | — | — | {len(rows)} |")
                    any_row = True
                continue
            n = sum(w[0] for w in got)
            share = {e: sum(w[1].get(e, 0) for w in got) / n for e in EVENTS}
            avg_tx = sum(w[2].get(EVENTS[0], 0) for w in got) / n
            out.append(f"| {c} | {s} | {n} | {pct(share[EVENTS[0]])} | {pct(share[EVENTS[1]])} | "
                       f"{avg_tx:.1f} | {pct(med(lock_share(r) for r in rows))} | {len(got)} |")
            any_row = True
    if not any_row:
        out.append("| — | — | 尚無取樣 | | | | | |")
    out += ["", "500 併發時連線名額不夠，取樣器自動關閉（D21），所以標為「未取樣」；p95 差值（表 2）不受影響。"]
    return out


def table_crash(path):
    out = ["## 表 4　Redis 遺失視窗（壓測中 SIGKILL Redis）", ""]
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
    if not rows:
        return out + ["尚無故障實驗資料。"]
    out += ["少算量：已確認成功（N）卻沒進 PG 的次數，因為在途請求可能已被回寫，只能給上下界（D13）。", "",
            "| T 秒 | 第幾次 | 中斷相位 | 實際曝光 s | 已確認 N | 少算下界 | 少算上界 | 門檻 | 上界 ÷ 門檻 | 判準 3 |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    by_t, unusable = defaultdict(list), []
    for r in rows:
        notes = r["notes"]
        m = re.search(r"acked_lost in \[\s*(\d+)\s*,\s*(\d+)\s*\]", notes)
        lo, hi = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        m = re.search(r"bound=(throughput\*T|acks_after_last_snapshot)=([\d.]+)", notes)
        bound = float(m.group(2)) if m else None
        kind = ("快照後確認數" if m.group(1).startswith("acks") else "吞吐×T（舊）") if m else ""
        m = re.search(r"phase=([\d.]+)", notes)
        phase = m.group(1) if m else "—"
        m = re.search(r"criterion3=(\w+)", notes)
        verdict = m.group(1) if m else "—"
        n = int(r["acknowledged_N"]) if r["acknowledged_N"] else None
        t = r["flush_interval_s"]
        why = bad_reason(notes) or (None if lo is not None and bound is not None else "notes 無法解析")
        if why:
            unusable.append(f"{r['run_id']}（{why}）")
            verdict += f" ⚠ 不計入：{why}"
        else:
            by_t[t].append((lo, hi, verdict))
        out.append(f"| {t} | {r['repeat']} | {phase} | {r['actual_exposure_s']} | {fmt(n)} | {fmt(lo)} | "
                   f"{fmt(hi)} | {fmt(bound, 0)} {kind} | {pct(hi / bound if hi is not None and bound else None)} | {verdict} |")
    out += ["", "門檻依提案判準 3 是「最後一批成功寫入 PG 的快照取得之後，才確認成功的請求數」，由負載器逐筆記錄的回應時間計算（D41）。"
            "標「吞吐×T（舊）」的列是 D41 之前的跑法，用全程平均吞吐估計，不是提案的門檻。"]
    if unusable:
        out += ["", f"下列故障實驗不計入彙整：{', '.join(unusable)}"]
    out += ["", "| T 秒 | 有效次數 | 少算下界中位數 | 少算上界中位數 | 判定 |", "|---:|---:|---:|---:|---|"]
    for t, xs in sorted(by_t.items(), key=lambda kv: float(kv[0])):
        verdicts = ", ".join(f"{v}×{sum(1 for x in xs if x[2] == v)}" for v in sorted({x[2] for x in xs}))
        out.append(f"| {t} | {len(xs)} | {fmt(med(x[0] for x in xs), 0)} | {fmt(med(x[1] for x in xs), 0)} | {verdicts} |")
    return out


def table_criteria(runs_path, crash_path, duration, interval, conc=200, need=3):
    """提案第四節：200 併發、每條重複 3 次，3 次都成立才算通過（逐輪判定，不用中位數）。"""
    out = ["## 表 5　成功判準逐輪判定（提案第四節）", "",
           f"共同條件：{conc} 併發；每條重複 {need} 次，**{need} 次都成立才算通過**。"
           "這張表逐輪判定，不用表 1～3 的中位數。判準 1 也讀對帳不一致的列。", ""]
    rows = []
    if os.path.exists(runs_path):
        with open(runs_path, newline="") as f:
            for r in csv.DictReader(f):
                if int(r["concurrency"]) != conc or r["scenario"] not in ("hot80", "uniform1000"):
                    continue
                if duration is not None and num(r["measured_s"]) != duration:
                    continue
                if r["backend"] == "redis" and num(r["flush_interval_s"]) != interval:
                    continue
                rows.append(r)
    latest = {}
    for r in sorted(rows, key=lambda r: r["run_id"].rsplit("_", 1)[-1]):
        latest[(r["backend"], r["scenario"], r["round"])] = r
    rounds = sorted({k[2] for k in latest}, key=int)
    ok = lambda b: "✓" if b else "✗"

    def share(r):
        return lock_share(r) if r else None

    def every(r):
        m = re.search(r"sample_every=([\d.]+)s", r["notes"]) if r else None
        return float(m.group(1)) if m else None

    out += ["| 輪 | 判準 1 PG／Redis | PG 熱點鎖等待 > 50% | PG 打散鎖等待 < 5% | PG p95 熱點÷打散 ≥ 3 | Redis p95 熱點÷打散 < 1.5 | 取樣間隔 | 判準 2 |",
            "|---:|---|---:|---:|---:|---:|---|---|"]
    c1_ok, c2_ok, c2_conform = [], [], True
    for rnd in rounds:
        ph, pu = latest.get(("pg", "hot80", rnd)), latest.get(("pg", "uniform1000", rnd))
        rh, ru = latest.get(("redis", "hot80", rnd)), latest.get(("redis", "uniform1000", rnd))
        c1 = [bool(r) and not bad_reason(r["notes"]) for r in (ph, rh)]
        c1_ok.append(all(c1))
        lh, lu = share(ph), share(pu)
        valid = lambda r: r and "RUN VOID" not in r["notes"] and num(r["p95_ms"])
        px = num(ph["p95_ms"]) / num(pu["p95_ms"]) if valid(ph) and valid(pu) else None
        rx = num(rh["p95_ms"]) / num(ru["p95_ms"]) if valid(rh) and valid(ru) else None
        items = [lh is not None and lh > 0.5, lu is not None and lu < 0.05,
                 px is not None and px >= 3, rx is not None and rx < 1.5]
        ev = {every(ph), every(pu)}
        conform = ev == {0.1}
        c2_conform &= conform
        c2_ok.append(all(items) and conform)
        show = lambda v, good, f: "—" if v is None else f(v) + ("" if good else " ✗")
        out.append(f"| {rnd} | {ok(c1[0])}／{ok(c1[1])} | {show(lh, items[0], pct)} | {show(lu, items[1], pct)} | "
                   f"{show(px, items[2], lambda v: f'{v:.2f}')} | {show(rx, items[3], lambda v: f'{v:.2f}')} | "
                   f"{'、'.join('未記錄（舊版 1 s）' if e is None else f'{e:g} s' for e in sorted(ev, key=lambda e: e or 0))}"
                   f"{'' if conform else ' ✗'} | {ok(c2_ok[-1])} |")
    if not rounds:
        out.append("| — | 尚無 200 併發的輪次 | | | | | | |")
    enough = len(rounds) >= need
    out += ["", f"- **判準 1**：{'通過' if enough and all(c1_ok) else '不通過'}"
            f"（{sum(c1_ok)}／{len(rounds)} 輪成立）",
            f"- **判準 2**：{'通過' if enough and all(c2_ok) else '不通過'}（{sum(c2_ok)}／{len(rounds)} 輪成立）"
            + ("" if c2_conform else "；有輪次的取樣間隔不是提案規定的 100 ms，不能照提案判定")]

    crash = []
    if os.path.exists(crash_path):
        with open(crash_path, newline="") as f:
            crash = list(csv.DictReader(f))
    parts, c3 = [], True
    for t in (1.0, 5.0):
        xs = [r for r in crash if num(r["flush_interval_s"]) == t]
        v = [(re.search(r"criterion3=(\w+)", r["notes"]) or [None, "?"])[1] for r in xs]
        proposal = all("acks_after_last_snapshot" in r["notes"] for r in xs)
        good = len(xs) >= need and proposal and all(x == "PASS" for x in v)
        c3 &= good
        parts.append(f"T={t:g}：{len(xs)} 次（{', '.join(v) or '無'}）"
                     + ("" if proposal or not xs else "，**用的是舊門檻（吞吐×T），不是提案的門檻**"))
    out.append(f"- **判準 3**：{'通過' if c3 else '不通過或無法判定'}（{'；'.join(parts)}）")
    return out


def machine():
    def sh(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return "?"
    mem = sh(["awk", "/MemTotal/ {printf \"%.1f GB\", $2/1048576}", "/proc/meminfo"])
    pg = sh(["psql", "-X", "-At", "-d", "q01", "-c", "SHOW server_version"])
    redis = sh(["redis-cli", "INFO", "server"])
    m = re.search(r"redis_version:(\S+)", redis)
    return (f"{platform.system()} {platform.release()}，{os.cpu_count()} 核，{mem}；"
            f"PostgreSQL {pg}；Redis {m.group(1) if m else '?'}")


def latest_batch():
    """results/ 底下名稱為 8 位數日期的資料夾中最新的一個；沒有就回 None。"""
    base = os.path.join(ROOT, "results")
    names = sorted(n for n in os.listdir(base) if len(n) == 8 and n.isdigit()
                   and os.path.isfile(os.path.join(base, n, "runs.csv")))  # 只算已有結果的批次
    return os.path.join(base, names[-1]) if names else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", help="批次資料夾（如 results/20261106）；預設 results/ 底下日期最新的那一批（D39）")
    ap.add_argument("--runs", help="預設 <dir>/runs.csv")
    ap.add_argument("--failures", help="預設 <dir>/failures.csv")
    ap.add_argument("--out", help="輸出 Markdown；預設印到 stdout")
    ap.add_argument("--rounds", type=int, default=3, help="每格應有的有效輪數，不足會標 ⚠")
    ap.add_argument("--duration", default="60", help="只取這個正式期長度（秒）；any＝不篩選（煙霧測試用）")
    ap.add_argument("--interval", type=float, default=5, help="Redis 主矩陣的回寫間隔 T（秒）")
    args = ap.parse_args()
    if not (args.runs and args.failures):
        d = args.dir or latest_batch()
        if not d:
            ap.error("results/ 底下沒有日期批次資料夾（results/<YYYYMMDD>/），請用 --dir 或 --runs／--failures 指定")
        args.runs = args.runs or os.path.join(d, "runs.csv")
        args.failures = args.failures or os.path.join(d, "failures.csv")
    duration = None if args.duration == "any" else float(args.duration)

    valid, dropped, other, superseded = load_runs(args.runs, duration, args.interval)
    g = group(valid)
    lines = ["# 題01 量測摘要", "",
             f"- 產生時間：{time.strftime('%Y-%m-%d %H:%M:%S')}（由 `tools/summarize.py` 從結果檔計算）",
             f"- 來源：`{os.path.relpath(args.runs, ROOT)}`、`{os.path.relpath(args.failures, ROOT)}`",
             f"- 機器：{machine()}",
             f"- 篩選：正式期 {args.duration} 秒、Redis 回寫間隔 T={args.interval:g} 秒；其他設定的 {other} 列不納入",
             f"- 有效輪數：{len(valid)}；排除：{len(dropped)}"
             + (f"（{', '.join(f'{i}：{why}' for i, why in dropped)}）" if dropped else ""),
             f"- 同一輪補跑、只取最後一次：{len(superseded)}"
             + (f"（{', '.join(f'{a} → {b}' for a, b in superseded)}）" if superseded else ""),
             "- 每格取有效輪的中位數；⚠ 表示有效輪數少於 "
             f"{args.rounds}。p95 為 nearest-rank，單位 ms。", ""]
    durations = sorted({r["measured_s"] for r in valid})
    if len(durations) > 1:
        lines += [f"> ⚠ 正式期長度不一致：{', '.join(durations)} 秒，不同長度的輪次混在同一格。", ""]
    missing = [f"{b}/{s}/c{c}" for b, s, c in REQUIRED if len(g.get((b, s, c), [])) < args.rounds]
    if missing:
        lines += [f"> ⚠ 必要格有效輪數不足 {args.rounds}：{', '.join(missing)}", ""]
    if "pgl" in {r["backend"] for r in valid}:
        lines += ["> 另有 `pgl`（PG＋統一負載器）的交叉驗證輪次，不在表 1、2 中。", ""]
    for part in (table_main(g, args.rounds), table_lock_cost(g, args.rounds), table_waits(g),
                 table_crash(args.failures),
                 table_criteria(args.runs, args.failures, duration, args.interval, need=args.rounds)):
        lines += part + [""]
    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
