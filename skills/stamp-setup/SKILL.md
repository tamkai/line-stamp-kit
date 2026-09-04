---
name: stamp-setup
description: LINE Stamp Kit を使う準備をする。Python / Pillow / Codex CLI の確認と、Codex の画像生成（image_gen）で「本物の透過PNG」が出るかの疎通テストを行い、結果を ~/.line-stamp-kit-ready に記録する。通常は初回に1度だけ自動で走るので、利用者が覚える必要はない。うまく動かないときの診断にも使う。"stamp-setup", "スタンプの準備", "スタンプが作れない", "画像が透過にならない", "動かない" で発動。
---

# stamp-setup — 使う準備をする

**ユーザーとのやり取りは必ず日本語で行う。**

LINE Stamp Kit が動く状態を作り、結果を `~/.line-stamp-kit-ready` に記録する。

## このスキルの立ち位置

- **通常は初回に1度だけ**、stamp-plan / stamp-make から自動で呼ばれる。**利用者は存在を知らなくてよい**
- 明示的に呼ばれるのは「**動かないとき**」。そのときは診断ツールとして働く
- **できるだけ自動で直す。** 詰まったところだけ、日本語で「何をすればいいか」を1つだけ伝える

## 実行の原則

- **黙って進める。** 途中経過を逐一報告せず、最後にまとめて1回報告する
- **失敗しても止まらない。** 全項目を試してから結果を出す（`check_env.py` がそう作られている）
- 画像生成テストは 1〜3 分かかる。**始める前に一言添える**

## 何を確認するか

このキットの画像生成は **Codex 組み込みの image_gen** を使う。OpenAI の API キーは要らないが、
**Codex CLI が入っていて、有料プラン（Plus 以上）でログインしている**必要がある。
透過は「単色背景で生成 → Codex 同梱のヘルパーで色を抜く」方式（詳細は stamp-make の「透過の仕組み」）。
最重要なのは **「画像は出るのに透過にできない」を先に見つけること**。
40 個作ってから気づくのが最悪なので、ここで 1 枚だけ本番と同じ手順で試して RGBA を機械検査する。

## 手順

### 1. スクリプトを実行する

```bash
python3 <plugin>/skills/stamp-setup/scripts/check_env.py
```

Windows は `python` または `py`:

```powershell
python <plugin>\skills\stamp-setup\scripts\check_env.py
```

スクリプトが以下を順に確認し、`~/.line-stamp-kit-ready` を書く:

| 項目 | 内容 | 直し方 |
|---|---|---|
| Python | 3.9 以上 | Windows: `winget install --id Python.Python.3.12 -e --source winget` |
| Pillow | 画像の検査と規格変換に使う。**無ければ自動で入れる** | 手動なら `python3 -m pip install --user pillow` |
| Codex CLI | `codex --version` | macOS/Linux: `curl -fsSL https://chatgpt.com/codex/install.sh \| sh`／Windows: `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 \| iex"` |
| 画像生成 | image_gen で単色背景のステッカーを 1 枚生成し、Codex 同梱の `remove_chroma_key.py` で透過化 → **RGBA・四隅が透明**かを検査 | 下記 |
| クロマキー用ヘルパー | `$CODEX_HOME/skills/.system/imagegen/scripts/remove_chroma_key.py` の有無（Codex が自動更新で配る。2026-09-04 以降の版に同梱） | Codex を更新 |

### 2. 画像生成テストが NG のとき

`~/.line-stamp-kit-ready` の `detail:` 行を見て切り分ける:

- **ログインしていない** → 利用者に `codex login` を実行してもらう（ブラウザが開く）。終わったら再実行
- **Free プラン** → 画像生成は Free では使えない。有料プラン（Plus 以上）が必要と伝える
- **画像は出るが透過でない（alpha: ng）** → 透過化ヘルパーが無いか失敗している。`detail:` を見る。`chroma_helper: absent` なら
  Codex を更新（`npm i -g @openai/codex` か再インストール）して再実行。それでも NG なら **このパソコンではスタンプを作れない**と正直に伝える
- **タイムアウト** → 混雑やレート制限。時間を置いて再実行

### 3. Codex から実行している場合の注意

Codex のサンドボックスは既定でネットワークを遮断する。Pillow の自動インストールで
承認を求められたら許可してもらう。許可できない環境では、利用者に手動で
`python3 -m pip install --user pillow` を実行してもらう。

**Claude Code から実行している場合、この制約はない。**

## 結果ファイル

`~/.line-stamp-kit-ready`（`check_env.py` が書く。**stamp-plan / stamp-make はこのファイルの有無と
`stamp_make:` の値で初回判定する**）:

```yaml
checked_at: 2026-09-04T13:20:00+09:00
os: darwin                # darwin | win32 | linux
python: 3.12.4
pillow: 12.3.0
codex: codex-cli 0.146.0
chroma_helper: ok         # Codex 同梱の remove_chroma_key.py の有無
image_gen: ok             # ok | ng | untested
alpha: ok                 # ok | ng | untested   ← 透過PNGが本当に出るか
stamp_make: available     # available | unavailable
detail: 1024x1024 RGBA 透過率 61%（95s）
```

## 最後の報告（利用者向け）

**技術用語を並べない。** できる/できないだけを伝える:

> 準備ができました。画像生成と透過の確認も通っています。
> 「LINEスタンプを作りたい」と話しかけてください。

NG のとき（例: ログイン）:

> あと1つだけ必要です。ターミナルで `codex login` を実行して、ブラウザでログインしてください。
> 終わったら「もう一度確認して」と言ってください。
