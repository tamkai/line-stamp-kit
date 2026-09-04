#!/usr/bin/env python3
"""生成済みスタンプ（output/raw/stamp_NN.png）を機械検査し、検収ページ output/review.html を作る。

検査項目（1つでも引っかかれば NG）:
  - ファイルがある
  - RGBA（アルファチャンネルがある）
  - 透過率 10% 以上（背景が本当に抜けている）
  - 四隅が透明（背景が残っていない）
  - 被写体が縁に接していない（見切れ）
  - 他の番号と同じ画像でない（取り違え）

使い方:
  python3 check_stamps.py --plan <作業フォルダ>          # review.json / review.html を書き、NG があれば exit 1
  python3 check_stamps.py --plan <作業フォルダ> --quiet  # 一覧表示を省く

review.html はサーバー不要の静的ページ。ブラウザで開いて、作り直したい番号にチェックを入れ、
右下の文をコピーしてエージェントに貼れば、その番号だけ作り直しに回る。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stamp_common import load_plan, md5_of, output_dir, raw_path, samples_dir, stamp_by_no  # noqa: E402

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow がありません。`python3 -m pip install --user pillow` を実行してください（stamp-setup が自動で入れます）。")

TEMPLATE = Path(__file__).resolve().parent / "review_template.html"
SAMPLES_TEMPLATE = Path(__file__).resolve().parent / "samples_template.html"
ALPHA_ZERO = 16            # これ以下の alpha を「透明」とみなす
MIN_TRANSPARENT_RATIO = 0.10
EDGE_MARGIN_RATIO = 0.015  # 画像サイズに対してこの比率未満の余白は「縁に近すぎ」


def check_image(path: Path) -> tuple[list[str], list[str], dict]:
    """(NG理由, 注意, 画像情報) を返す。注意は OK 判定に影響しない。"""
    info: dict = {}
    problems: list[str] = []
    warnings: list[str] = []
    try:
        im = Image.open(path)
        im.load()
    except Exception as e:  # noqa: BLE001
        return [f"画像として開けません（{e}）"], [], info
    info.update(width=im.width, height=im.height, mode=im.mode, kb=round(path.stat().st_size / 1024))
    if im.mode != "RGBA":
        problems.append(f"透過がありません（カラーモード {im.mode}）。背景透過で作り直し")
        return problems, warnings, info
    a = im.getchannel("A")
    hist = a.histogram()
    ratio = sum(hist[: ALPHA_ZERO + 1]) / (im.width * im.height)
    info["transparent_ratio"] = round(ratio, 3)
    if ratio < MIN_TRANSPARENT_RATIO:
        problems.append(f"透過率 {ratio:.0%}。背景が抜けていない可能性")
    corners = [a.getpixel((0, 0)), a.getpixel((im.width - 1, 0)), a.getpixel((0, im.height - 1)), a.getpixel((im.width - 1, im.height - 1))]
    if any(c > ALPHA_ZERO for c in corners):
        problems.append("四隅が透明ではありません（背景が残っている）")
    # 縁に alpha=1〜3 程度のゴミ画素が乗ることがある（実測）。閾値を超えた画素だけで被写体の範囲を取る
    bbox = a.point(lambda v: 255 if v > ALPHA_ZERO else 0).getbbox()
    if bbox is None:
        problems.append("画像が空（全部透明）")
    else:
        l, t, r, b = bbox
        info["bbox"] = [l, t, r, b]
        if l == 0 or t == 0 or r == im.width or b == im.height:
            problems.append("被写体が縁に接しています（見切れ）")
        else:
            m = min(l, t, im.width - r, im.height - b) / min(im.width, im.height)
            if m < EDGE_MARGIN_RATIO:
                # 仕上げで余白は付け直すので NG にはしない。切れていないか目で確かめてもらう
                warnings.append("縁ぎりぎりです。髪や足が切れていないか目で確認")
    return problems, warnings, info


def check_all(plan: dict, workdir: Path) -> dict:
    items = []
    md5s: dict[str, list[int]] = {}
    for s in plan["stamps"]:
        p = raw_path(workdir, s["no"])
        item = {
            "no": s["no"],
            "expression": s["expression"],
            "text": s.get("text", ""),
            "file": f"raw/{p.name}",
            "exists": p.exists(),
            "problems": [],
            "warnings": [],
        }
        if p.exists():
            problems, warnings, info = check_image(p)
            item.update(info)
            item["problems"] = problems
            item["warnings"] = warnings
            item["mtime"] = int(p.stat().st_mtime)
            h = md5_of(p)
            item["md5"] = h
            md5s.setdefault(h, []).append(s["no"])
        else:
            item["problems"] = ["まだ生成されていません"]
        items.append(item)
    for h, nos in md5s.items():
        if len(nos) > 1:
            for it in items:
                if it.get("md5") == h:
                    others = [n for n in nos if n != it["no"]]
                    it["problems"].append(f"{', '.join(f'{n:02d}' for n in others)} と同じ画像です（取り違え）")
    for it in items:
        it["ok"] = it["exists"] and not it["problems"]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "title": plan.get("title", ""),
        "count": plan["count"],
        "main": plan.get("main", 1),
        "style": plan.get("style", ""),
        "items": items,
        "ok_count": sum(1 for it in items if it["ok"]),
    }


def write_review(report: dict, workdir: Path) -> tuple[Path, Path]:
    out = output_dir(workdir)
    out.mkdir(parents=True, exist_ok=True)
    jpath = out / "review.json"
    jpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html = TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("/*__REVIEW_DATA__*/null", data)
    hpath = out / "review.html"
    hpath.write_text(html, encoding="utf-8")
    return jpath, hpath


def check_samples(plan: dict, workdir: Path) -> dict:
    """output/samples/stamp_NN__<style>.png を検査し、絵柄比較ページ用のデータを作る。"""
    sdir = samples_dir(workdir)
    files = sorted(sdir.glob("stamp_*__*.png")) if sdir.exists() else []
    if not files:
        sys.exit(f"試し打ちの画像がありません: {sdir}\n先に build_prompts.py --sample-style <style> と run_codex.py --sample-style <style> を実行してください。")
    items = []
    no = None
    known = {q.stem for q in (Path(__file__).resolve().parent.parent.parent / "stamp-plan" / "styles").glob("*.txt")}
    for f in files:
        stem_no, style = f.stem.split("__", 1)
        if style not in known:
            continue   # Codex の中間ファイル（stamp_01__clay_chroma.png 等）は無視
        n = int(stem_no.split("_")[1])
        no = no or n
        problems, warnings, info = check_image(f)
        item = {"style": style, "no": n, "file": f"samples/{f.name}", "problems": problems, "warnings": warnings,
                "mtime": int(f.stat().st_mtime), "ok": not problems}
        item.update(info)
        items.append(item)
    stamp = stamp_by_no(plan, no) if no else {"expression": "", "text": ""}
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "title": plan.get("title", ""),
        "no": no,
        "expression": stamp.get("expression", ""),
        "text": stamp.get("text", ""),
        "items": items,
    }


def write_samples(report: dict, workdir: Path) -> tuple[Path, Path]:
    out = output_dir(workdir)
    jpath = out / "samples.json"
    jpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html = SAMPLES_TEMPLATE.read_text(encoding="utf-8")
    data = json.dumps(report, ensure_ascii=False).replace("</", "<\\/")
    hpath = out / "samples.html"
    hpath.write_text(html.replace("/*__SAMPLES_DATA__*/null", data), encoding="utf-8")
    return jpath, hpath


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="stamp_plan.json またはそれがある作業フォルダ")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--samples", action="store_true", help="絵柄の試し打ち（output/samples/）を検査して比較ページ samples.html を作る")
    args = ap.parse_args()

    plan, workdir, _ = load_plan(args.plan)
    if args.samples:
        report = check_samples(plan, workdir)
        _, hpath = write_samples(report, workdir)
        for it in report["items"]:
            mark = "OK" if it["ok"] else "NG"
            extra = "" if it["ok"] else "  -> " + "; ".join(it["problems"])
            print(f"[{mark}] {it['style']:<16} {it.get('width')}x{it.get('height')} {it.get('mode', '')}{extra}")
        print()
        print(f"比較ページ: {hpath}")
        print("ブラウザで開いて絵柄を選び、右下の文をコピーして伝えてください。")
        return
    report = check_all(plan, workdir)
    jpath, hpath = write_review(report, workdir)

    if not args.quiet:
        for it in report["items"]:
            mark = "OK" if it["ok"] else "NG"
            size = f"{it.get('width')}x{it.get('height')} {it.get('mode', '')}" if it.get("exists") else "-"
            extra = "" if it["ok"] else "  -> " + "; ".join(it["problems"])
            if it.get("warnings"):
                extra += "  (注意: " + "; ".join(it["warnings"]) + ")"
            print(f"[{mark}] {it['no']:02d} {size:<18} {it['text'] or '(文字なし)'}{extra}")
    ng = report["count"] - report["ok_count"]
    print()
    print(f"OK {report['ok_count']} / NG {ng}  （全 {report['count']} 個）")
    print(f"検収ページ: {hpath}")
    print("ブラウザで開いて確認し、作り直す番号があれば右下の文をコピーして伝えてください。")
    sys.exit(1 if ng else 0)


if __name__ == "__main__":
    main()
