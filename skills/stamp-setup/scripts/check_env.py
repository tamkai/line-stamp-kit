#!/usr/bin/env python3
"""LINE Stamp Kit の環境チェック。結果を ~/.line-stamp-kit-ready に記録する。

確認すること:
  1. Python 3.9 以上
  2. Pillow（無ければ自動で入れる。--no-install で抑止）
  3. Codex CLI（codex --version）
  4. 画像生成の疎通: Codex の image_gen で小さな透過画像を1枚作らせ、
     本物の RGBA で四隅が透明かを機械検査する（--skip-image-test で省略）

使い方:
  python3 check_env.py                  # 全部（画像テスト込み。1〜3分）
  python3 check_env.py --skip-image-test
  python3 check_env.py --no-install

終了コード: 0 = スタンプ制作に使える / 1 = 使えない項目がある
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

READY_FILE = Path.home() / ".line-stamp-kit-ready"
PROBE_DIR = Path.home() / ".line-stamp-kit" / "probe"
PNG_PATH_RE = re.compile(r"((?:/|[A-Za-z]:\\|~/)[^\s\"'`<>|]+?\.png)", re.IGNORECASE)

PROBE_PROMPT = """Codex 組み込みの image_gen ツールで、テスト用の画像を1枚生成し、imagegen スキルの透過手順（クロマキー除去）で透過 PNG にしてください。
ImageMagick・SVG・HTML/CSS などの組版や、API キーが必要な CLI へのフォールバックはしないこと。

Use case: illustration-story
Asset type: sticker (transparency probe)
Primary request: 赤い丸いリンゴのシンプルなステッカー風イラスト。太い白フチ付き
Composition/framing: 正方形、被写体は中央、上下左右に十分な余白
Background: キャンバス全面を完全にフラットな単色 #00ff00（クロマキー）で塗る。グラデーション・影・質感・市松模様・白背景は不可。被写体に緑系の色を使わない
Constraints: 文字は入れない

■ 透過化と検証（必須）
- 生成画像を作業フォルダにコピーし、次のコマンドで透過 PNG にする:
  python "${{CODEX_HOME:-$HOME/.codex}}/skills/.system/imagegen/scripts/remove_chroma_key.py" --input <生成画像> --out {target} --auto-key border --soft-matte --transparent-threshold 12 --opaque-threshold 220 --despill --force
- 出来た PNG が RGBA で四隅が透明であることを確認する

■ 保存と報告
- 最終的な透過 PNG が次の絶対パスにあること: {target}
- 最後に「保存した絶対パス」「ピクセル寸法」「カラーモード（RGBA か否か）」を1行ずつ報告すること
"""

def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout + "\n" + p.stderr).strip()
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return -1, f"timeout {timeout}s"


def check_python() -> tuple[str, bool]:
    v = platform.python_version()
    return v, sys.version_info >= (3, 9)


def check_pillow(install: bool) -> tuple[str, bool]:
    try:
        import PIL  # noqa: F401
        return PIL.__version__, True
    except ImportError:
        pass
    if not install:
        return "absent", False
    print("Pillow が無いので入れます（数十秒）...", flush=True)
    for cmd in ([sys.executable, "-m", "pip", "install", "--user", "pillow"],
                [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", "pillow"],
                [sys.executable, "-m", "pip", "install", "pillow"]):
        rc, _ = run(cmd, timeout=300)
        if rc == 0:
            break
    try:
        import importlib
        PIL = importlib.import_module("PIL")
        return PIL.__version__, True
    except ImportError:
        return "install failed", False


def check_codex() -> tuple[str, bool]:
    if not shutil.which("codex"):
        return "absent", False
    rc, out = run(["codex", "--version"])
    return (out.splitlines()[0] if out else "unknown"), rc == 0


def generated_images_dir() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return home / "generated_images"


def chroma_helper() -> Path | None:
    p = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "skills" / ".system" / "imagegen" / "scripts" / "remove_chroma_key.py"
    return p if p.exists() else None


def probe_image_gen(timeout: int) -> tuple[str, str, str]:
    """(image_gen, alpha, detail) を返す。image_gen: ok|ng、alpha: ok|ng|untested"""
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    target = PROBE_DIR / "probe.png"
    for old_file in (target, PROBE_DIR / "probe_src.png"):
        if old_file.exists():
            old_file.unlink()
    prompt = PROBE_PROMPT.format(target=target)
    cmd = ["codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "--skip-git-repo-check",
           "-C", str(PROBE_DIR), prompt]
    print("画像生成の疎通テストをします（1枚だけ。1〜3分かかります）...", flush=True)
    start = time.time()
    rc, out = run(cmd, timeout=timeout)
    (PROBE_DIR / "probe.log").write_text(out, encoding="utf-8")

    found: Path | None = target if target.exists() else None
    if found is None:
        for m in PNG_PATH_RE.findall(out):
            p = Path(m).expanduser()
            if p.exists() and p.stat().st_mtime >= start - 2:
                found = p
                break
    if found is None:
        d = generated_images_dir()
        if d.exists():
            cands = [p for p in d.rglob("*.png") if p.stat().st_mtime >= start - 2]
            if cands:
                found = max(cands, key=lambda p: p.stat().st_mtime)
    if found is None:
        tail = " / ".join(out.strip().splitlines()[-3:]) if out else ""
        if "login" in out.lower() or "auth" in out.lower() or "sign in" in out.lower():
            return "ng", "untested", "Codex にログインしていない可能性（codex login を実行）: " + tail
        return "ng", "untested", "画像が生成・保存されませんでした: " + tail
    if found != target:
        shutil.copy2(found, target)

    try:
        from PIL import Image
        im = Image.open(target)
        im.load()
        if im.mode != "RGBA":
            # Codex が透過化まで終えていない。こちら側で同じヘルパーを使って鍵抜きを試す（本番の run_codex.py と同じ網）
            helper = chroma_helper()
            if helper is None:
                return "ok", "ng", f"画像は出たが透過なし（{im.mode}）。remove_chroma_key.py も見つからない（Codex を更新）"
            src = PROBE_DIR / "probe_src.png"
            shutil.move(str(target), str(src))
            r = subprocess.run([sys.executable, str(helper), "--input", str(src), "--out", str(target), "--auto-key", "border",
                                "--soft-matte", "--transparent-threshold", "12", "--opaque-threshold", "220", "--despill", "--force"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not target.exists():
                return "ok", "ng", f"画像は出たが透過化に失敗（{im.mode}）: {(r.stderr or r.stdout).strip().splitlines()[-1:] }"
            im = Image.open(target)
            im.load()
            if im.mode != "RGBA":
                return "ok", "ng", f"ローカル透過化後も RGBA でない（{im.mode}）"
        a = im.getchannel("A")
        w, h = im.size
        corners = [a.getpixel((0, 0)), a.getpixel((w - 1, 0)), a.getpixel((0, h - 1)), a.getpixel((w - 1, h - 1))]
        ratio = sum(a.histogram()[:17]) / (w * h)
        if any(c > 16 for c in corners) or ratio < 0.10:
            return "ok", "ng", f"RGBA だが背景が残っている（四隅 alpha={corners}, 透過率 {ratio:.0%}）"
        keyed = "（ローカルで透過化）" if (PROBE_DIR / "probe_src.png").exists() else ""
        return "ok", "ok", f"{w}x{h} RGBA 透過率 {ratio:.0%}{keyed}（{time.time() - start:.0f}s）"
    except Exception as e:  # noqa: BLE001
        return "ok", "ng", f"画像の検査に失敗: {e}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-image-test", action="store_true")
    ap.add_argument("--no-install", action="store_true", help="Pillow を自動で入れない")
    ap.add_argument("--timeout", type=int, default=420, help="画像テストの制限秒数（既定 420）")
    args = ap.parse_args()

    py_v, py_ok = check_python()
    pil_v, pil_ok = check_pillow(install=not args.no_install)
    cx_v, cx_ok = check_codex()

    image_gen, alpha, detail = "untested", "untested", ""
    if cx_ok and not args.skip_image_test:
        image_gen, alpha, detail = probe_image_gen(args.timeout)
    elif not cx_ok:
        image_gen, detail = "ng", "Codex CLI が無い"

    usable = py_ok and pil_ok and cx_ok and image_gen == "ok" and alpha == "ok"

    os_name = {"Darwin": "darwin", "Windows": "win32", "Linux": "linux"}.get(platform.system(), platform.system().lower())
    lines = [
        "# LINE Stamp Kit セットアップ結果（check_env.py が書く。手で編集しなくてよい）",
        f"checked_at: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"os: {os_name}",
        f"python: {py_v}",
        f"pillow: {pil_v if pil_ok else 'ng'}",
        f"codex: {cx_v if cx_ok else 'absent'}",
        f"chroma_helper: {'ok' if chroma_helper() else 'absent'}   # Codex 同梱の remove_chroma_key.py",
        f"image_gen: {image_gen}        # ok | ng | untested",
        f"alpha: {alpha}            # ok | ng | untested  ← 透過PNGが本当に出るか",
        f"stamp_make: {'available' if usable else 'unavailable'}",
        f"detail: {detail}",
    ]
    READY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    print("=== 結果 ===")
    print(f"Python : {'OK' if py_ok else 'NG'} {py_v}")
    print(f"Pillow : {'OK' if pil_ok else 'NG'} {pil_v}")
    print(f"Codex  : {'OK' if cx_ok else 'NG'} {cx_v}")
    print(f"画像生成: {image_gen} / 透過: {alpha}  {detail}")
    print(f"記録   : {READY_FILE}")
    print()
    if usable:
        print("準備ができました。スタンプを作れます。")
    else:
        print("このままではスタンプを作れません。上の NG の項目を直してください。")
    sys.exit(0 if usable else 1)


if __name__ == "__main__":
    main()
