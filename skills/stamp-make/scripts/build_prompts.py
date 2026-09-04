#!/usr/bin/env python3
"""stamp_plan.json とスタイル定義から、スタンプ1個ごとの生成指示書を書き出す。

出力（作業フォルダ/prompts/）:
  NN.txt   image_gen に渡すプロンプト本文（Claude Code 経由でも Codex 単体でも同じものを使う）
  NN.json  添付する画像（参照画像・お手本）と保存先の絶対パス

使い方:
  python3 build_prompts.py --plan <作業フォルダ>                 # 全番号
  python3 build_prompts.py --plan <作業フォルダ> --only 3 5      # 作り直す番号だけ
  python3 build_prompts.py --plan <作業フォルダ> --only 3 --note "前回は目が小さすぎた。もっと大きく"

お手本（anchor）: 既定では「メイン番号の完成画像（output/raw/stamp_MM.png）」があれば、
それを Image 2 として添付する指示を入れる。メイン自身を作るときは付かない。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stamp_common import (  # noqa: E402
    load_plan, parse_only, prompts_dir, raw_path, resolve_references, sample_path, stamp_by_no,
)

SKILL_DIR = Path(__file__).resolve().parent.parent          # skills/stamp-make
STYLES_DIR = SKILL_DIR.parent / "stamp-plan" / "styles"      # skills/stamp-plan/styles


def find_style(plan: dict, workdir: Path, override: str | None = None) -> tuple[str, Path]:
    if override is None:
        local = workdir / "style.txt"
        if local.exists():
            return local.read_text(encoding="utf-8").strip(), local
    name = override or plan.get("style", "atsunuri")
    cand = STYLES_DIR / f"{name}.txt"
    if cand.exists():
        return cand.read_text(encoding="utf-8").strip(), cand
    available = ", ".join(sorted(p.stem for p in STYLES_DIR.glob("*.txt")))
    sys.exit(f"スタイル '{name}' が見つかりません。使えるもの: {available}\n"
             f"（作業フォルダに style.txt を置けばそれが優先されます）")


def build_prompt(plan: dict, stamp: dict, style_text: str, images: list[dict], target: Path, note: str,
                 attach: str = "cli") -> str:
    count = plan["count"]
    text = str(stamp.get("text", "")).strip()
    text_style = plan.get("text_style") or "手描きの太いマーカー風の日本語文字"
    char_note = str(plan.get("character_note", "")).strip()

    lines: list[str] = []
    if attach == "view" and images:
        lines.append("まず built-in の view_image ツールで、次のローカル画像を順に読み込んで会話コンテキストに載せてください:")
        for i, im in enumerate(images, 1):
            lines.append(f"- Image {i}: {im['path']}")
        lines.append("")
    key = plan.get("key_color", "#00ff00")
    lines.append("Codex 組み込みの image_gen ツールで、LINEスタンプ用の画像を1枚生成し、imagegen スキルの透過手順（クロマキー除去）で透過 PNG にしてください。")
    lines.append("ImageMagick・SVG・HTML/CSS などの組版や、API キーが必要な CLI へのフォールバックはしないこと。")
    lines.append("")
    if images:
        lines.append("■ 添付画像の役割")
        for i, im in enumerate(images, 1):
            lines.append(f"- Image {i}: {im['role']}")
        lines.append("")
    lines.append("■ スタイル定義（全スタンプ共通・厳守）")
    lines.append(style_text)
    lines.append("")
    lines.append("■ このスタンプの内容")
    lines.append(f"- 番号: {stamp['no']:02d} / 全 {count} 個{'（メイン画像になる）' if stamp['no'] == plan.get('main', 1) else ''}")
    if char_note:
        lines.append(f"- キャラクターの固定特徴（毎回同じにする）: {char_note}")
    lines.append(f"- 表情・ポーズ: {stamp['expression']}")
    if text:
        lines.append(f"- 文字（verbatim・一字一句この通り）: 「{text}」")
        lines.append(f"  - 書体: {text_style}。誤字脱字なく正確に描く。文字はキャラの近くに、キャラの顔や手と重ならない位置に置く")
    else:
        lines.append("- 文字: 入れない（文字・記号・ロゴを一切描かない）")
    lines.append("- 構図: 正方形。被写体は中央。上下左右に十分な余白を残し、キャラも文字も画像の縁に触れないこと")
    lines.append("")
    lines.append("■ 出力の制約")
    lines.append(f"- 背景: キャンバス全面を完全にフラットな単色 {key}（クロマキー）で塗る。グラデーション・影・質感・市松模様・白背景・床は不可")
    lines.append(f"- 被写体（キャラ・文字・白フチ）に {key} 系の色を使わない。被写体の縁はくっきりさせ、背景と混ざらせない")
    lines.append("- 画像は1枚だけ生成する。バリエーションを複数作らない")
    lines.append("")
    if note:
        lines.append("■ 前回の失敗（今回は必ず直すこと）")
        lines.append(note.strip())
        lines.append("")
    lines.append("■ 透過化と検証（必須）")
    lines.append(f"- 生成画像の背景が単色 {key} になっていなければ（市松模様・グラデ・別の色）、透過化せず同じ指示で image_gen をやり直す（最大 2 回）")
    lines.append("- 背景が単色なら、生成画像を作業フォルダにコピーし、次のコマンドで透過 PNG にする:")
    lines.append(f"  python '${{CODEX_HOME:-$HOME/.codex}}/skills/.system/imagegen/scripts/remove_chroma_key.py' --input <生成画像> --out {target} --auto-key border --soft-matte --transparent-threshold 12 --opaque-threshold 220 --despill --force")
    lines.append("- 出来た PNG が RGBA で、四隅が透明で、被写体の縁に緑のフリンジが無いことを確認する。薄いフリンジが残るなら --edge-contract 1 を足して 1 回だけやり直す")
    lines.append("")
    lines.append("■ 保存と報告")
    lines.append(f"- 最終的な透過 PNG が次の絶対パスにあること: {target}")
    lines.append("- 最後に「保存した絶対パス」「ピクセル寸法」「カラーモード（RGBA か否か）」を1行ずつ報告すること")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="stamp_plan.json またはそれがある作業フォルダ")
    ap.add_argument("--only", nargs="*", help="この番号だけ書き出す（例: 3 5 / 3,5 / 3-6）")
    ap.add_argument("--note", default="", help="作り直し時の申し送り（--only の番号すべてに付く）")
    ap.add_argument("--anchor", help="お手本画像のパスを明示する（既定: メイン番号の完成画像があればそれ）")
    ap.add_argument("--no-anchor", action="store_true", help="お手本を添付しない")
    ap.add_argument("--no-reference", action="store_true", help="参照画像を添付しない（キャラを文章だけで指定する場合）")
    ap.add_argument("--reference", metavar="PATH", help="参照画像を計画の指定以外のファイルにする")
    ap.add_argument("--attach", choices=("cli", "view"), default="cli",
                    help="参照画像の渡し方。cli: codex exec -i で添付（既定）/ view: プロンプト内で view_image に読ませる")
    ap.add_argument("--sample-style", metavar="STYLE",
                    help="絵柄の試し打ち: このスタイルで prompts/samples/<STYLE>/ に書き、保存先を output/samples/stamp_NN__<STYLE>.png にする（お手本は付けない）。all で全スタイル")
    args = ap.parse_args()

    plan, workdir, _ = load_plan(args.plan)
    if args.sample_style == "all":
        import subprocess
        names = sorted(p.stem for p in STYLES_DIR.glob("*.txt"))
        base = [a for a in sys.argv[1:] if a not in ("--sample-style", "all")]
        for name in names:
            subprocess.run([sys.executable, __file__, *base, "--sample-style", name], check=True)
        return
    style_text, style_path = find_style(plan, workdir, args.sample_style)
    only = parse_only(args.only)
    targets = [s for s in plan["stamps"] if not only or s["no"] in only]
    if only and len(targets) != len(only):
        sys.exit(f"--only に計画にない番号があります: {sorted(set(only) - {s['no'] for s in targets})}")

    if args.reference:
        reference = Path(args.reference).expanduser().resolve()
        if not reference.exists():
            sys.exit(f"--reference の画像がありません: {reference}")
        references = [reference]
    else:
        references = [] if args.no_reference else resolve_references(plan, workdir)
    if not references and not args.no_reference:
        print("注意: 参照画像（reference.png など）が見つかりません。文章だけでキャラを指定します。", file=sys.stderr)

    main_no = plan.get("main", 1)
    anchor: Path | None = None
    if not args.no_anchor and not args.sample_style:
        if args.anchor:
            anchor = Path(args.anchor).expanduser().resolve()
            if not anchor.exists():
                sys.exit(f"--anchor の画像がありません: {anchor}")
        elif raw_path(workdir, main_no).exists():
            anchor = raw_path(workdir, main_no)

    pdir = prompts_dir(workdir) / "samples" / args.sample_style if args.sample_style else prompts_dir(workdir)
    pdir.mkdir(parents=True, exist_ok=True)

    for s in targets:
        images: list[dict] = []
        for k, ref in enumerate(references, 1):
            role = "参照画像（キャラクターの見本）。顔・髪型・服・体型・配色はこの人物/キャラから取る"
            if len(references) > 1:
                role = f"参照画像 {k}/{len(references)}（同一人物の別アングル）。顔立ち・髪型・ひげ・服はこれらを総合して取る"
            images.append({"path": str(ref), "role": role})
        if anchor is not None and anchor != raw_path(workdir, s["no"]):
            images.append({
                "path": str(anchor),
                "role": "お手本スタンプ（完成済み・同じキャラ）。線の太さ・塗り・白フチの太さ・文字の書体・デフォルメの度合いをこれと完全に一致させる。ポーズと文字だけを変える",
            })
        target = sample_path(workdir, s["no"], args.sample_style) if args.sample_style else raw_path(workdir, s["no"])
        prompt = build_prompt(plan, s, style_text, images, target, args.note, attach=args.attach)
        (pdir / f"{s['no']:02d}.txt").write_text(prompt, encoding="utf-8")
        (pdir / f"{s['no']:02d}.json").write_text(json.dumps({
            "no": s["no"],
            "images": images,
            "target": str(target),
            "attach": args.attach,
            "key_color": plan.get("key_color", "#00ff00"),
            "expression": s["expression"],
            "text": s.get("text", ""),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"書き出し: {len(targets)} 件 → {pdir}")
    print(f"  スタイル: {style_path}")
    print(f"  参照画像: {', '.join(str(r) for r in references) if references else 'なし'}")
    print(f"  お手本  : {anchor if anchor else 'なし（メインを先に作ると自動で付きます）'}")


if __name__ == "__main__":
    main()
