#!/usr/bin/env python3
"""prompts/NN.txt を `codex exec --ephemeral` で並列実行し、output/raw/stamp_NN.png に回収する。

Claude Code から呼ぶ経路（経路A）の中核。Codex は1枚ごとに使い捨て（ステートレス）で起動する。
`--ephemeral` を付けないと画像が Base64 でセッションログに残り、数百MB〜GB に膨らむ（実測 2.3GB）。

使い方:
  python3 run_codex.py --plan <作業フォルダ>                    # prompts/ にある全番号（既に raw があるものは飛ばす）
  python3 run_codex.py --plan <作業フォルダ> --only 1           # メインだけ先に作る
  python3 run_codex.py --plan <作業フォルダ> --only 3 5         # 作り直し（古い画像は output/raw/_old/ に退避）
  python3 run_codex.py --plan <作業フォルダ> --parallel 2       # 並列数（既定 3。レート制限に当たるなら下げる）

回収の順序: (1) Codex が指定パスにコピーしてくれたか → (2) 出力ログに書かれた PNG パス →
(3) $CODEX_HOME/generated_images/ に実行中に増えた PNG。全部外れたら失敗として報告する。
終わったら md5 で「隣の番号と同じ絵」（取り違え）を検出して警告する。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stamp_common import (  # noqa: E402
    load_plan, md5_duplicates, output_dir, parse_only, prompts_dir, raw_dir, raw_path, sample_path,
)

PNG_PATH_RE = re.compile(r"((?:/|[A-Za-z]:\\|~/)[^\s\"'`<>|]+?\.png)", re.IGNORECASE)


def codex_bin() -> str:
    b = shutil.which("codex")
    if not b:
        sys.exit("codex コマンドが見つかりません。stamp-setup を実行して Codex CLI を入れてください。")
    return b


def generated_images_dir() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return home / "generated_images"


def newest_generated(since: float, claimed: set[str]) -> Path | None:
    d = generated_images_dir()
    if not d.exists():
        return None
    cands = [p for p in d.rglob("*.png") if p.stat().st_mtime >= since - 2 and str(p) not in claimed]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def png_mode(path: Path) -> str:
    """PNG のカラーモード。Pillow が無ければ IHDR から判定する。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.mode
    except ImportError:
        with open(path, "rb") as f:
            head = f.read(29)
        color_type = head[25] if len(head) >= 26 else -1
        return {6: "RGBA", 4: "LA", 2: "RGB", 0: "L", 3: "P"}.get(color_type, "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def target_dir(workdir: Path, args) -> Path:
    return sample_path(workdir, 1, args.sample_style).parent if args.sample_style else raw_dir(workdir)


def chroma_helper() -> Path | None:
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    p = home / "skills" / ".system" / "imagegen" / "scripts" / "remove_chroma_key.py"
    return p if p.exists() else None


def alpha_ok(path: Path) -> tuple[bool, str]:
    """RGBA で、四隅が透明で、透過率が 10% 以上か。"""
    try:
        from PIL import Image
    except ImportError:
        return png_mode(path) == "RGBA", "Pillow なし（mode のみ確認）"
    with Image.open(path) as im:
        if im.mode != "RGBA":
            return False, f"mode={im.mode}"
        a = im.getchannel("A")
        w, h = im.size
        corners = [a.getpixel((0, 0)), a.getpixel((w - 1, 0)), a.getpixel((0, h - 1)), a.getpixel((w - 1, h - 1))]
        ratio = sum(a.histogram()[:17]) / (w * h)
        if any(c > 16 for c in corners):
            return False, f"四隅が不透明 {corners}"
        if ratio < 0.10:
            return False, f"透過率 {ratio:.0%}"
        return True, f"透過率 {ratio:.0%}"


def key_out_locally(src: Path, dst: Path) -> tuple[bool, str]:
    """Codex が透過化し忘れた場合に、同じヘルパーでこちら側でクロマキー除去する。"""
    helper = chroma_helper()
    if helper is None:
        return False, "remove_chroma_key.py が見つからない（Codex の imagegen スキル未導入）"
    cmd = [sys.executable, str(helper), "--input", str(src), "--out", str(dst), "--auto-key", "border",
           "--soft-matte", "--transparent-threshold", "12", "--opaque-threshold", "220", "--despill", "--force"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        return False, f"helper 実行失敗: {e}"
    if r.returncode != 0 or not dst.exists():
        return False, "helper エラー: " + (r.stderr or r.stdout).strip().splitlines()[-1:][0] if (r.stderr or r.stdout).strip() else "helper エラー"
    ok, why = alpha_ok(dst)
    return ok, why


def backup_old(target: Path) -> None:
    if not target.exists():
        return
    old = target.parent / "_old"
    old.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(str(target), str(old / f"{target.stem}_{stamp}.png"))


def run_one(no: int, workdir: Path, args, codex: str, claimed: set[str], lock: threading.Lock) -> dict:
    pdir = prompts_dir(workdir) / "samples" / args.sample_style if args.sample_style else prompts_dir(workdir)
    txt = pdir / f"{no:02d}.txt"
    meta_path = pdir / f"{no:02d}.json"
    if not txt.exists() or not meta_path.exists():
        return {"no": no, "ok": False, "error": f"{txt} がありません。build_prompts.py を先に実行してください"}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    prompt = txt.read_text(encoding="utf-8")
    target = Path(meta["target"])   # build_prompts が決めた保存先（通常 raw/、試し打ちなら samples/）
    target.parent.mkdir(parents=True, exist_ok=True)

    cmd = [codex, "exec", "--ephemeral", "--sandbox", "workspace-write", "--skip-git-repo-check", "-C", str(workdir)]
    if meta.get("attach", "cli") == "cli":
        for im in meta.get("images", []):
            p = Path(im["path"])
            if p.exists():
                cmd += ["-i", str(p)]
    # プロンプトは stdin で渡す。`-i` は可変長オプションなので、引数末尾に置いたプロンプトを
    # 画像パスとして飲み込んでしまう（実測: 'No prompt provided via stdin' で即失敗）

    logs = output_dir(workdir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / (f"sample_{args.sample_style}_{no:02d}.log" if args.sample_style else f"stamp_{no:02d}.log")

    start = time.time()
    print(f"[{no:02d}] 生成開始（{len(meta.get('images', []))} 枚添付）", flush=True)
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=args.timeout,
        )
        stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = f"timeout after {args.timeout}s"
        rc = -1
    elapsed = time.time() - start
    log.write_text(f"$ {' '.join(cmd)}  < prompts/{no:02d}.txt\n\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n--- rc={rc} {elapsed:.0f}s ---\n",
                   encoding="utf-8")

    source = None
    if target.exists() and target.stat().st_mtime >= start - 2:
        source = "target"
    if source is None:
        for m in PNG_PATH_RE.findall(stdout):
            p = Path(m).expanduser()
            if p.exists() and p.stat().st_mtime >= start - 2 and p.resolve() != target.resolve():
                shutil.copy2(p, target)
                source = f"stdout:{p}"
                break
    if source is None:
        with lock:
            p = newest_generated(start, claimed)
            if p is not None:
                claimed.add(str(p))
                shutil.copy2(p, target)
                source = f"generated_images:{p}"

    ok = target.exists() and target.stat().st_mtime >= start - 2
    alpha_note = ""
    if ok:
        good, why = alpha_ok(target)
        if not good:
            # Codex が透過化まで終えていない（単色背景のまま、または市松模様の擬似透過）。
            # まずこちら側で同じヘルパーを使ってクロマキー除去を試み、ダメなら不採用にしてやり直す
            src = target.parent / f"{target.stem}_src.png"
            shutil.move(str(target), str(src))
            keyed, why2 = key_out_locally(src, target)
            if keyed:
                alpha_note = f"Codex 出力（{why}）をローカルでクロマキー透過化 → {why2}"
                src.unlink(missing_ok=True)
            else:
                rejected = target.parent / "_rejected"
                rejected.mkdir(exist_ok=True)
                shutil.move(str(src), str(rejected / f"{target.stem}_{datetime.now().strftime('%H%M%S')}.png"))
                target.unlink(missing_ok=True)
                ok = False
                alpha_note = f"透過にできず不採用（{why} / {why2}）→ _rejected/ に退避"
    status = "OK" if ok else "失敗"
    print(f"[{no:02d}] {status} {elapsed:.0f}s" + (f"  ← {source}" if source and source != "target" else "") + (f"  {alpha_note}" if alpha_note else ""), flush=True)
    result = {"no": no, "ok": ok, "seconds": round(elapsed), "rc": rc, "source": source, "log": str(log)}
    if not ok:
        if alpha_note:
            result["error"] = alpha_note
        else:
            tail = (stderr or stdout).strip().splitlines()[-3:]
            result["error"] = " / ".join(tail) if tail else "画像が回収できませんでした"
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="stamp_plan.json またはそれがある作業フォルダ")
    ap.add_argument("--only", nargs="*", help="この番号だけ生成（既存の画像は _old/ に退避して作り直す）")
    ap.add_argument("--parallel", type=int, default=3, help="同時に走らせる Codex の数（既定 3）")
    ap.add_argument("--timeout", type=int, default=900, help="1枚あたりの制限秒数（既定 900）")
    ap.add_argument("--retries", type=int, default=1, help="失敗時に自動でやり直す回数（既定 1）")
    ap.add_argument("--force", action="store_true", help="--only なしでも既存画像を作り直す")
    ap.add_argument("--sample-style", metavar="STYLE", help="絵柄の試し打ち: prompts/samples/<STYLE>/ を実行（既定はメイン番号だけ）。all で全スタイルを並列に")
    args = ap.parse_args()

    plan, workdir, _ = load_plan(args.plan)
    if args.sample_style == "all":
        styles_dir = Path(__file__).resolve().parent.parent.parent / "stamp-plan" / "styles"
        names = sorted(p.stem for p in styles_dir.glob("*.txt"))
        names = [n for n in names if (prompts_dir(workdir) / "samples" / n).exists()]
        if not names:
            sys.exit("prompts/samples/ がありません。build_prompts.py --sample-style all を先に実行してください。")
        base = [a for a in sys.argv[1:] if a not in ("--sample-style", "all")]
        print(f"全スタイルを並列で試し打ち: {names}（1 枚 2〜5 分。全部で 5〜8 分が目安）")
        procs = [subprocess.Popen([sys.executable, __file__, *base, "--sample-style", n]) for n in names]
        rcs = [p.wait() for p in procs]
        print("次: python3 check_stamps.py --plan <作業フォルダ> --samples  で絵柄の比較ページを作る")
        sys.exit(1 if any(rcs) else 0)
    codex = codex_bin()
    only = parse_only(args.only)
    raw_dir(workdir).mkdir(parents=True, exist_ok=True)

    if args.sample_style:
        nos = only or [plan.get("main", 1)]
        for no in nos:
            backup_old(sample_path(workdir, no, args.sample_style))
    elif only:
        nos = only
        for no in nos:
            backup_old(raw_path(workdir, no))
    else:
        nos = [s["no"] for s in plan["stamps"]]
        if args.force:
            for no in nos:
                backup_old(raw_path(workdir, no))
        else:
            skipped = [no for no in nos if raw_path(workdir, no).exists()]
            nos = [no for no in nos if no not in skipped]
            if skipped:
                print(f"既にある番号は飛ばします: {skipped}（作り直すなら --only か --force）")
    if not nos:
        print("生成する番号がありません。")
        return

    print(f"対象: {nos}  並列: {args.parallel}  作業フォルダ: {workdir}")
    claimed: set[str] = set()
    lock = threading.Lock()
    results: dict[int, dict] = {}
    pending = list(nos)
    attempt = 0
    while pending and attempt <= args.retries:
        if attempt:
            print(f"--- やり直し {attempt} 回目: {pending}")
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
            futs = {ex.submit(run_one, no, workdir, args, codex, claimed, lock): no for no in pending}
            for f in as_completed(futs):
                r = f.result()
                results[r["no"]] = r
        pending = [no for no in pending if not results[no]["ok"]]
        attempt += 1

    dups = {} if args.sample_style else md5_duplicates([raw_path(workdir, s["no"]) for s in plan["stamps"]])
    dup_nos: list[list[int]] = []
    for paths in dups.values():
        group = sorted(int(p.stem.split("_")[1]) for p in paths)
        dup_nos.append(group)

    summary = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "targets": nos,
        "results": [results[no] for no in nos],
        "duplicates": dup_nos,
    }
    (output_dir(workdir) / (f"run_log_sample_{args.sample_style}.json" if args.sample_style else "run_log.json")).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [no for no in nos if not results[no]["ok"]]
    print()
    print(f"完了: {len(nos) - len(failed)}/{len(nos)} 枚  → {target_dir(workdir, args)}")
    for no in failed:
        print(f"  失敗 [{no:02d}]: {results[no].get('error', '')}  （ログ: {results[no].get('log', '')}）")
    for group in dup_nos:
        print(f"  取り違え疑い: {group} が同じ画像です。番号を指定して作り直してください（--only {' '.join(map(str, group[1:]))}）")
    if args.sample_style:
        print("次: python3 check_stamps.py --plan <作業フォルダ> --samples  で絵柄の比較ページを作る")
    else:
        print("次: python3 check_stamps.py --plan <作業フォルダ>  で検収ページを作る")
    sys.exit(1 if failed or dup_nos else 0)


if __name__ == "__main__":
    main()
