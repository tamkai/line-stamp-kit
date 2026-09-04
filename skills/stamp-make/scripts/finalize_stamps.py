#!/usr/bin/env python3
"""検収済みの output/raw/stamp_NN.png を LINE Creators Market の規格に整え、申請用 zip を作る。

  - スタンプ: 透明部分をトリム → 余白を付けて W370×H320 以内に縮小（幅・高さは偶数）→ 01.png … NN.png
  - メイン画像: 計画の main 番号から 240×240 の main.png
  - タブ画像: 同じ番号から 96×74 の tab.png
  - 各ファイル 1MB 以下を確認し、output/submit/ に配置、output/stamps_submit.zip を作る

使い方:
  python3 finalize_stamps.py --plan <作業フォルダ>
  python3 finalize_stamps.py --plan <作業フォルダ> --force      # NG が残っていても強行（非推奨）
  python3 finalize_stamps.py --plan <作業フォルダ> --margin 12  # 余白を変える（既定 10px）

事前に check_stamps.py が全部 OK になっていることが前提。NG が残っていれば止まる。
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stamp_common import load_plan, output_dir, raw_path, submit_dir  # noqa: E402
from check_stamps import check_all  # noqa: E402

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow がありません。`python3 -m pip install --user pillow` を実行してください。")

STAMP_MAX = (370, 320)   # W, H の上限
MAIN_SIZE = (240, 240)
TAB_SIZE = (96, 74)
MAX_BYTES = 1024 * 1024
ZIP_MAX_BYTES = 60 * 1024 * 1024
ALPHA_TRIM = 16   # これ以下の alpha は透明扱い（check_stamps.ALPHA_ZERO と揃える）


def fit(img: Image.Image, box: tuple[int, int], margin: int) -> Image.Image:
    """透明トリム → box 以内に縮小（余白ぶんを差し引く）→ 幅高さを偶数に → 余白付きキャンバスに載せる。"""
    # check_stamps と同じ閾値で被写体範囲を取る（縁の alpha=1〜3 のゴミ画素を無視）
    bbox = img.getchannel("A").point(lambda v: 255 if v > ALPHA_TRIM else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    inner_w, inner_h = box[0] - margin * 2, box[1] - margin * 2
    scale = min(inner_w / img.width, inner_h / img.height, 1.0)
    w = max(2, int(img.width * scale)) // 2 * 2
    h = max(2, int(img.height * scale)) // 2 * 2
    img = img.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGBA", (w + margin * 2, h + margin * 2), (0, 0, 0, 0))
    canvas.paste(img, (margin, margin), img)
    return canvas


def fit_exact(img: Image.Image, size: tuple[int, int], margin: int) -> Image.Image:
    """メイン・タブ用: 指定サイズぴったりのキャンバス中央に載せる。"""
    inner = fit(img, size, margin)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(inner, ((size[0] - inner.width) // 2, (size[1] - inner.height) // 2), inner)
    return canvas


def save_capped(img: Image.Image, path: Path) -> int:
    img.save(path, "PNG", optimize=True)
    size = path.stat().st_size
    if size > MAX_BYTES:
        # 色数を落として再保存を試みる（透過は保つ）
        q = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
        q.save(path, "PNG", optimize=True)
        size = path.stat().st_size
    if size > MAX_BYTES:
        sys.exit(f"{path.name} が 1MB を超えました（{size // 1024}KB）。その番号を簡素な絵柄で作り直してください。")
    return size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="stamp_plan.json またはそれがある作業フォルダ")
    ap.add_argument("--margin", type=int, default=10, help="スタンプ周囲の透明余白 px（既定 10）")
    ap.add_argument("--force", action="store_true", help="検収 NG が残っていても続行する")
    args = ap.parse_args()

    plan, workdir, _ = load_plan(args.plan)
    report = check_all(plan, workdir)
    ng = [it["no"] for it in report["items"] if not it["ok"]]
    if ng and not args.force:
        sys.exit(f"検収 NG が残っています: {ng}\n作り直してから再実行してください（check_stamps.py で内容を確認できます）。")

    sub = submit_dir(workdir)
    sub.mkdir(parents=True, exist_ok=True)
    for old in sub.glob("*.png"):
        old.unlink()

    sizes: dict[str, int] = {}
    for s in plan["stamps"]:
        src = raw_path(workdir, s["no"])
        img = Image.open(src).convert("RGBA")
        out = sub / f"{s['no']:02d}.png"
        sizes[out.name] = save_capped(fit(img, STAMP_MAX, args.margin), out)

    main_no = plan.get("main", 1)
    main_src = Image.open(raw_path(workdir, main_no)).convert("RGBA")
    sizes["main.png"] = save_capped(fit_exact(main_src, MAIN_SIZE, args.margin), sub / "main.png")
    sizes["tab.png"] = save_capped(fit_exact(main_src, TAB_SIZE, 4), sub / "tab.png")

    zpath = output_dir(workdir) / "stamps_submit.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(sub.glob("*.png")):
            z.write(f, f.name)
    zsize = zpath.stat().st_size
    if zsize > ZIP_MAX_BYTES:
        sys.exit(f"zip が 60MB を超えました（{zsize // (1024 * 1024)}MB）。")

    # 最終確認
    for f in sorted(sub.glob("*.png")):
        im = Image.open(f)
        assert im.mode == "RGBA", f.name
        if f.name == "main.png":
            assert im.size == MAIN_SIZE, f"{f.name} {im.size}"
        elif f.name == "tab.png":
            assert im.size == TAB_SIZE, f"{f.name} {im.size}"
        else:
            assert im.width <= STAMP_MAX[0] and im.height <= STAMP_MAX[1], f"{f.name} {im.size}"
            assert im.width % 2 == 0 and im.height % 2 == 0, f"{f.name} 奇数サイズ {im.size}"

    print(f"完成: {sub}/  （スタンプ {plan['count']} 個 + main.png + tab.png、メインは {main_no:02d} 番）")
    print(f"zip : {zpath}  ({zsize // 1024}KB)")
    biggest = max(sizes.items(), key=lambda kv: kv[1])
    print(f"最大ファイル: {biggest[0]} {biggest[1] // 1024}KB（上限 1024KB）")
    print("次: LINE Creators Market（https://creator.line.me/）で新規登録 → 画像をアップロード")


if __name__ == "__main__":
    main()
