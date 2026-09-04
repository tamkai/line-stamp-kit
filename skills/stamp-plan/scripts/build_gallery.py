#!/usr/bin/env python3
"""絵柄ギャラリーを組み立てる（キットの保守用。利用者は実行しない）。

入力: 試し打ちの出力 output/samples/stamp_NN__<style>.png（同じマスコット・同じ表情で全絵柄を作ったもの）
出力: styles/gallery/<style>.png（縮小版）と styles/gallery.html（静的・サーバー不要）

使い方:
  python3 build_gallery.py --samples <ギャラリー用作業フォルダ>/output/samples [--size 512]

styles/*.txt の先頭「## Visual Style: 〜」行を説明文として拾う。
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from PIL import Image

SKILL_DIR = Path(__file__).resolve().parent.parent   # skills/stamp-plan
STYLES_DIR = SKILL_DIR / "styles"
GALLERY_DIR = STYLES_DIR / "gallery"
ORDER = ["bold-outline", "atsunuri", "flat-vector", "line-watercolor", "yuru-tegaki", "clay"]

TEMPLATE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>絵柄ギャラリー</title>
<style>
:root{--paper:#F7F5F0;--card:#fff;--line:#E2DED6;--ink:#24211C;--soft:#625C52;--faint:#948D80;--accent:#2D6BD4;
 --font:"Hiragino Sans","Hiragino Kaku Gothic ProN","Yu Gothic","Yu Gothic UI",Meiryo,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--font);font-size:15px;line-height:1.6;padding-bottom:120px}
header{padding:28px 32px 6px}h1{font-size:22px;margin:0 0 4px}.meta{color:var(--soft);font-size:13px}
.hint{margin:6px 32px 0;color:var(--soft);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:18px;padding:20px 32px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column}
.card.selected{outline:3px solid var(--accent);outline-offset:-1px}
.thumb{aspect-ratio:1/1;display:flex;align-items:center;justify-content:center;background:#7494C0}
.thumb img{max-width:86%;max-height:86%;object-fit:contain}
.body{padding:10px 12px 12px}.name{font-weight:700;font-family:ui-monospace,Menlo,monospace;font-size:15px}
.desc{color:var(--soft);font-size:13px}.use{color:var(--faint);font-size:12px;margin-top:4px}
.toolbar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:12px 32px;display:flex;gap:14px;align-items:center;box-shadow:0 -8px 24px -18px rgba(0,0,0,.35)}
.toolbar input{flex:1;font:inherit;font-size:14px;padding:9px 10px;border:1px solid var(--line);border-radius:8px;background:var(--paper)}
button{font:inherit;font-size:14px;padding:9px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;font-weight:600;cursor:pointer}
button:disabled{opacity:.5;cursor:default}.copied{color:#2F7D4F;font-size:12px;min-width:10em}
.bg{display:flex;gap:6px;align-items:center;font-size:13px;color:var(--soft);margin-left:auto}
.bg button{background:var(--card);color:var(--ink);border-color:var(--line);font-weight:400;padding:4px 10px;font-size:12px}.bg button.on{border-color:var(--accent);color:var(--accent)}
</style></head><body>
<header><h1>絵柄ギャラリー</h1><div class="meta" id="meta"></div></header>
<div class="hint">同じマスコット・同じ表情・同じ文字を、絵柄だけ変えて作った見本です。気に入ったものをクリックして、右下の名前を伝えてください。あなたの写真で作るとき、この絵柄で描かれます。</div>
<div class="grid" id="grid"></div>
<div class="toolbar"><input id="out" readonly placeholder="絵柄をクリックすると名前が入ります"><button id="copy" disabled>この文をコピー</button><span class="copied" id="copied"></span>
<span class="bg">背景: <button data-bg="#7494C0" class="on">LINE風</button><button data-bg="#fff">白</button><button data-bg="#2B2B2B">暗</button></span></div>
<script>
const DATA = /*__GALLERY_DATA__*/null;
const $ = (s) => document.querySelector(s); let picked = null;
$("#meta").textContent = `${DATA.items.length} 種 / 作成 ${DATA.generated_at.slice(0,10)}`;
for (const it of DATA.items) {
  const c = document.createElement("div"); c.className = "card"; c.dataset.style = it.style;
  c.innerHTML = `<div class="thumb"><img src="gallery/${it.file}" alt="${it.style}"></div><div class="body"><div class="name">${it.style}</div><div class="desc">${it.desc}</div><div class="use">${it.use}</div></div>`;
  c.addEventListener("click", () => { picked = it.style; document.querySelectorAll(".card").forEach((x) => x.classList.toggle("selected", x === c)); $("#out").value = `絵柄は ${picked} にしてください。`; $("#copy").disabled = false; });
  $("#grid").appendChild(c);
}
$("#copy").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("#out").value); } catch { $("#out").select(); document.execCommand("copy"); } $("#copied").textContent = "コピーしました。"; setTimeout(() => ($("#copied").textContent = ""), 3000); });
document.querySelectorAll(".bg button").forEach((b) => b.addEventListener("click", () => { document.querySelectorAll(".bg button").forEach((x) => x.classList.toggle("on", x === b)); document.querySelectorAll(".thumb").forEach((t) => (t.style.background = b.dataset.bg)); }));
</script></body></html>
"""

USES = {
    "bold-outline": "文具ブランド風。いちばん「スタンプらしい」",
    "atsunuri": "本人・知人を元にした「映える」もの",
    "flat-vector": "モダン・デザイン寄り。仕事の連絡にも",
    "line-watercolor": "家族・季節・やわらかい雰囲気",
    "yuru-tegaki": "日常会話・ネタ",
    "clay": "マスコット・キャラもの。似顔絵の再現度が高い",
}


def style_desc(style: str) -> str:
    p = STYLES_DIR / f"{style}.txt"
    if not p.exists():
        return ""
    m = re.search(r"^## Visual Style:\s*(.+)$", p.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", required=True, help="stamp_NN__<style>.png が入ったフォルダ")
    ap.add_argument("--size", type=int, default=512, help="縮小後の一辺 px（既定 512）")
    args = ap.parse_args()

    src = Path(args.samples).expanduser().resolve()
    files = {}
    known = {q.stem for q in STYLES_DIR.glob("*.txt")}
    for f in sorted(src.glob("stamp_*__*.png")):
        style = f.stem.split("__", 1)[1]
        if style not in known:      # Codex の中間ファイル（_chroma 等）は無視
            continue
        files[style] = f
    if not files:
        raise SystemExit(f"見本画像がありません: {src}")
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for style in sorted(files, key=lambda s: ORDER.index(s) if s in ORDER else 99):
        im = Image.open(files[style]).convert("RGBA")
        im.thumbnail((args.size, args.size), Image.LANCZOS)
        out = GALLERY_DIR / f"{style}.png"
        im.save(out, "PNG", optimize=True)
        items.append({"style": style, "file": out.name, "desc": style_desc(style), "use": USES.get(style, ""),
                      "kb": out.stat().st_size // 1024})
        print(f"{style:<16} {im.size} {out.stat().st_size // 1024}KB")
    data = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "items": items}
    html = TEMPLATE.replace("/*__GALLERY_DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (STYLES_DIR / "gallery.html").write_text(html, encoding="utf-8")
    print(f"→ {STYLES_DIR / 'gallery.html'}（{len(items)} 種）")


if __name__ == "__main__":
    main()
