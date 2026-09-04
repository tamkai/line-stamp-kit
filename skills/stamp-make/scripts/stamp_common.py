#!/usr/bin/env python3
"""stamp-make の各スクリプトが共有する小道具。

- 計画JSON（stamp_plan.json）の読み込みと検証
- 作業フォルダ内のパス規約（prompts/ output/raw/ output/submit/）
- md5 による重複画像の検出
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

VALID_COUNTS = (8, 16, 24, 32, 40)
REFERENCE_CANDIDATES = ("reference.png", "reference.jpg", "reference.jpeg", "reference.webp")


def load_plan(plan_arg: str) -> tuple[dict, Path, Path]:
    """--plan に渡されたパス（ファイルでもフォルダでも可）から (plan, workdir, plan_path) を返す。"""
    p = Path(plan_arg).expanduser().resolve()
    if p.is_dir():
        p = p / "stamp_plan.json"
    if not p.exists():
        sys.exit(f"計画ファイルが見つかりません: {p}\n先に stamp-plan で stamp_plan.json を作ってください。")
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"stamp_plan.json が壊れています（{e}）。JSON として正しい形に直してください。")
    errors = validate_plan(plan)
    if errors:
        sys.exit("stamp_plan.json に問題があります:\n  - " + "\n  - ".join(errors))
    return plan, p.parent, p


def validate_plan(plan: dict) -> list[str]:
    errors: list[str] = []
    count = plan.get("count")
    if count not in VALID_COUNTS:
        errors.append(f"count は {'/'.join(map(str, VALID_COUNTS))} のいずれか（現在: {count!r}）")
    stamps = plan.get("stamps")
    if not isinstance(stamps, list) or not stamps:
        errors.append("stamps が空です")
        return errors
    nos = [s.get("no") for s in stamps]
    if isinstance(count, int) and len(stamps) != count:
        errors.append(f"stamps の個数（{len(stamps)}）と count（{count}）が一致しません")
    if isinstance(count, int) and sorted(nos) != list(range(1, count + 1)):
        errors.append("stamps の no は 1 から count まで連番にしてください")
    for s in stamps:
        if not str(s.get("expression", "")).strip():
            errors.append(f"no {s.get('no')}: expression（表情・ポーズ）が空です")
        if "text" not in s:
            errors.append(f"no {s.get('no')}: text がありません（文字なしなら空文字 \"\" にする）")
    main = plan.get("main", 1)
    if not isinstance(main, int) or (isinstance(count, int) and not 1 <= main <= count):
        errors.append(f"main は 1〜{count} の番号（現在: {main!r}）")
    return errors


def stamp_by_no(plan: dict, no: int) -> dict:
    for s in plan["stamps"]:
        if s["no"] == no:
            return s
    raise KeyError(no)


def prompts_dir(workdir: Path) -> Path:
    return workdir / "prompts"


def output_dir(workdir: Path) -> Path:
    return workdir / "output"


def raw_dir(workdir: Path) -> Path:
    return output_dir(workdir) / "raw"


def raw_path(workdir: Path, no: int) -> Path:
    return raw_dir(workdir) / f"stamp_{no:02d}.png"


def submit_dir(workdir: Path) -> Path:
    return output_dir(workdir) / "submit"


def samples_dir(workdir: Path) -> Path:
    """絵柄の試し打ち（同じ表情をスタイル違いで作る）の置き場。"""
    return output_dir(workdir) / "samples"


def sample_path(workdir: Path, no: int, style: str) -> Path:
    return samples_dir(workdir) / f"stamp_{no:02d}__{style}.png"


def resolve_reference(plan: dict, workdir: Path) -> Path | None:
    """計画の reference（無ければ reference.png 等）を探す。見つからなければ None。"""
    ref = plan.get("reference")
    if ref:
        p = (workdir / ref).resolve() if not Path(ref).is_absolute() else Path(ref)
        return p if p.exists() else None
    for name in REFERENCE_CANDIDATES:
        p = workdir / name
        if p.exists():
            return p
    return None


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_duplicates(paths: list[Path]) -> dict[str, list[Path]]:
    """同じ内容のファイル群を返す（重複があるものだけ）。"""
    groups: dict[str, list[Path]] = {}
    for p in paths:
        if p.exists():
            groups.setdefault(md5_of(p), []).append(p)
    return {k: v for k, v in groups.items() if len(v) > 1}


def parse_only(values: list[str] | None) -> list[int]:
    """--only 3 5 / --only 3,5 / --only 3-6 を番号リストにする。"""
    if not values:
        return []
    nos: set[int] = set()
    for v in values:
        for part in str(v).replace("、", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                nos.update(range(int(a), int(b) + 1))
            else:
                nos.add(int(part))
    return sorted(nos)
