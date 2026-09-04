---
name: stamp-make
description: 計画JSON（stamp_plan.json）と参照画像から、Codex の画像生成（image_gen）で透過スタンプを量産し、検収ページ（review.html）で見て作り直しを経て、LINE Creators Market 申請用の zip（スタンプ・main.png・tab.png）に仕上げる第2段階。APIキー不要。"stamp-make", "これをスタンプにして", "スタンプを生成", "スタンプを作り直して", "申請用zip", "スタンプを仕上げて" で発動。前段は stamp-plan。
---

# stamp-make — 計画をスタンプにする（第2段階）

**ユーザーとのやり取りは必ず日本語で行う。**

stamp-plan が作った `stamp_plan.json` から、透過 PNG を生成し、検収し、LINE の規格に整えて zip にする。

## 全体の流れ

```
stamp_plan.json + reference.png
   │
   ├─ Step 2  本人の参照画像でメインを試し打ち（選んだ絵柄だけ / 全絵柄）→ 利用者が見て 1 つ選ぶ   ← ここで絵柄と似顔絵を固める
   ├─ Step 3  残りを並列で生成（メインを「お手本」に添付して画風を揃える）
   ├─ Step 4  機械検査 → review.html を開いてもらう → 作り直し番号をもらう → その番号だけ再生成
   └─ Step 5  規格変換（370×320 / 240×240 / 96×74）→ zip
```

**メインを先に試し打ちして絵柄を確定する**のは必須手順。ギャラリーで決めた 1 種だけでも、本人の顔で全絵柄を見比べてもよい。全部作ってから「違う」となると、個数ぶんの時間と使用量が無駄になる。

## Step 0: 環境チェック（初回のみ・自動）

`~/.line-stamp-kit-ready` が無ければ stamp-setup を実行してから続行。あれば中身を読み、
`stamp_make: unavailable` なら**作業を始める前に**こう伝えて止まる:

> このパソコンでは画像生成（透過PNG）が確認できていません。先に準備を直しますか？

## Step 1: 入力を確認する

- 作業フォルダに `stamp_plan.json` があるか（無ければ stamp-plan へ）
- 参照画像（`reference.png` など）が計画通りにあるか。無ければ「文章だけでキャラを作る」ことを伝えて続行
- 計画の `count` と `stamps` の個数が一致しているか（スクリプトが検証してくれる）

成果物は**作業フォルダ配下**に作る。**プラグイン配下や `~/.claude/` には絶対に書かない**（Codex がそこに書けない）。

```
<作業フォルダ>/
├── stamp_plan.json
├── reference.png
├── style.txt            （任意。置くと同梱スタイルより優先）
├── prompts/             NN.txt（生成指示書）・NN.json（添付画像と保存先）
└── output/
    ├── raw/             stamp_NN.png（生成された原画。作り直し前のものは _old/ に退避）
    ├── logs/            Codex の実行ログ
    ├── review.html      検収ページ（ブラウザで開く。サーバー不要）
    ├── review.json
    ├── submit/          01.png … NN.png / main.png / tab.png（申請規格）
    └── stamps_submit.zip
```

## 透過の仕組み（重要・非自明）

Codex 組み込みの image_gen（gpt-image-2）は **参照画像を付けると透過 PNG を返さない**（2026-09-04 実測: JPG/PNG、`-i`/view_image のどれでも RGB で返り、白や黒や市松模様の背景が描き込まれる）。
Codex 自身の imagegen スキルも「本物の透過は出せない。**フラットな単色（既定 #00ff00）背景で生成 → 同梱の `remove_chroma_key.py` で色を抜く**」を公式手順にしている。

このキットもその手順に乗る:

1. スタイル定義とプロンプトが「背景はキャンバス全面フラットな #00ff00」を要求する（`stamp_plan.json` の `key_color` で変えられる。キャラが緑なら `#ff00ff`）
2. Codex に同梱ヘルパーで透過化して保存させる（プロンプトにコマンドが書いてある）
3. Codex が透過化を忘れても、`run_codex.py` が回収時に **RGBA・四隅透明・透過率**を検査し、ダメなら**こちら側で同じヘルパーを実行**して透過化する。それでもダメなら `_rejected/` に退避して自動でやり直す

白フチ付きのイラストは被写体と背景の境界がはっきりしているので、クロマキーでもエッジは綺麗に出る。
**背景除去 AI や「白を抜く」処理は使わない**（白フチが消える）。

## 経路の判定（重要）

このスキルは **Claude Code から**でも **Codex 単体**でも動く。最初に自分がどちらかを判定する:

| 自分は | 経路 | 画像生成のやり方 |
|---|---|---|
| **image_gen ツールを持っていない**（Claude Code など） | **A** | `scripts/run_codex.py` が Codex を 1 枚ごとに使い捨てで起動する |
| **image_gen ツールを持っている**（Codex 自身） | **B** | 自分で `prompts/NN.txt` を読んで image_gen を呼ぶ |

プロンプト生成・検査・規格変換の Python スクリプトは両経路で共通。違うのは「誰が image_gen を呼ぶか」だけ。

スクリプトの場所は `<plugin>/skills/stamp-make/scripts/`。以下 `S=` と略記する。
`--plan` には作業フォルダ（または `stamp_plan.json` のパス）を渡す。

## Step 2: 絵柄の試し打ち（本人の参照画像でメインを作って、絵柄を確定する）

絵柄の候補は stamp-plan のギャラリー（`../stamp-plan/styles/gallery.html`）で見てある。ここでは **本人の参照画像でどう出るか**を確かめる。
最初にこれだけ聞く:

> 試し打ちはどちらにしますか？
>
> **A. ギャラリーで選んだ絵柄だけ**（1〜2 枚・2〜5 分）— 絵柄は決まっている。似顔絵の出方だけ確認したい
> **B. 全部の絵柄で自分を描いてみる**（6 枚並列・5〜8 分）— 自分の顔でどの絵柄が合うか見比べて決めたい
>
> 迷ったら A。使用量が気になるときも A。

```bash
# A: 計画の style（と style_alt があればそれ）だけ
python3 $S/build_prompts.py --plan <作業フォルダ> --sample-style <style>
python3 $S/run_codex.py   --plan <作業フォルダ> --sample-style <style>          # 経路 A。2 種なら並列で投げてよい

# B: 全スタイル（build も run も all で一括。run は並列に走る）
python3 $S/build_prompts.py --plan <作業フォルダ> --sample-style all
python3 $S/run_codex.py   --plan <作業フォルダ> --sample-style all

# どちらも最後に比較ページ
python3 $S/check_stamps.py --plan <作業フォルダ> --samples                       # → output/samples.html を開いてもらう
```

経路 B（Codex 単体）は「経路 B の生成手順」で `prompts/samples/<style>/NN.json` を 1 枚ずつ。

比較ページを開いてもらい、こう聞く:

> この絵柄・この似顔絵で進めていいですか？ これを「お手本」にして残りを揃えます。
> 直したい点があれば言ってください（例: メガネを太く / もっと 2 頭身に / 文字を大きく）

利用者から「絵柄は clay にしてください。」の文（または OK）が来たら:

1. `stamp_plan.json` の `style` をその名前にする（変わった場合）
2. 選ばれた画像を **メインの完成画像として採用**: `output/samples/stamp_MM__<style>.png` を `output/raw/stamp_MM.png` にコピー
3. Step 3 へ（この画像が自動で「お手本」になる）

**キャラの再現が弱い**（メガネが無い、髪色が違う等）なら `character_note` にその特徴を足して同じ絵柄で作り直す。
**絵柄ごと変えたい**なら別の名前で `--sample-style` を 1 枚だけ。OK が出るまで残りに進まない。

## Step 3: 残りを並列で生成する

```bash
python3 $S/build_prompts.py --plan <作業フォルダ>        # メインの完成画像を自動で「お手本」に添付
```

- **経路 A**: `python3 $S/run_codex.py --plan <作業フォルダ>`（既にある番号は飛ばす。並列 3）
- **経路 B**: 「経路 B の生成手順」で残りを順に作る

目安: 参照画像＋お手本を添付した生成は **1 枚 3〜5 分**。並列 3 で 8 個なら 10〜15 分。
始める前に所要時間を一言伝える。`try again at HH:MM` の類のエラーは Codex の 5 時間レート制限。
`--parallel 1` に落とすか、時間を置く。

`run_codex.py` は回収した画像が **RGBA でなければ同梱ヘルパーでクロマキー除去を試み**、それでも透過にならなければ `_rejected/` に退避して自動でやり直す（上記「透過の仕組み」）。
終了時には **md5 で「隣の番号と同じ絵」（取り違え）を検出**する。
警告が出た番号は Step 4 の作り直しに回す。

## Step 4: 検収と作り直し

```bash
python3 $S/check_stamps.py --plan <作業フォルダ>
```

RGBA / 透過率 / 四隅 / 見切れ / 取り違え を機械検査し、`output/review.html` を書く。
**利用者にはこのファイルをブラウザで開いてもらう**（Claude Code なら `open` / `start` で開いてよい）。

review.html でできること:
- 市松・暗・LINE 風の背景で透過の見え方を確認
- NG のカードは理由付きで最初からチェック済み
- 作り直したい番号にチェックを入れ、直したい点をメモ → 右下の文をコピー

利用者から来る文はこの形:

```
3, 5 を作り直してください。
3: 目をもっと大きく
5: 文字が読みにくい
```

受け取ったら番号ごとに:

```bash
python3 $S/build_prompts.py --plan <作業フォルダ> --only 3 --note "前回は目が小さすぎた。もっと大きく"
python3 $S/build_prompts.py --plan <作業フォルダ> --only 5 --note "前回は文字が読みにくかった。太く大きく"
python3 $S/run_codex.py --plan <作業フォルダ> --only 3 5          # 経路 A（古い画像は _old/ に退避される）
python3 $S/check_stamps.py --plan <作業フォルダ>
```

メモがない番号は `--note` なしでよい。**機械検査の NG 理由**（透過なし・見切れ等）は build_prompts の
`--note` にそのまま書くと直りやすい（例: `--note "前回は被写体が縁に接していた。もっと小さく中央に"`）。

全部 OK になるまで繰り返す。**同じ番号を 3 回作り直しても直らないときは、表情や文字を変える提案をする**
（2 頭身で崩れやすいポーズ、長すぎる文字、が原因のことが多い）。

## Step 5: 規格に整えて zip にする

```bash
python3 $S/finalize_stamps.py --plan <作業フォルダ>
```

検収 NG が残っていれば止まる。通れば `output/submit/` と `output/stamps_submit.zip` ができる。

利用者への報告:

> できました。
> - 申請用ファイル: `<作業フォルダ>/output/stamps_submit.zip`（スタンプ N 個 + main.png + tab.png）
> - 申請先: LINE Creators Market https://creator.line.me/ → 新規作成 → スタンプ → 画像をアップロード
>
> タイトル・説明文（日本語と英語）を申請画面で入力します。よければ案を出します。

## 経路 B の生成手順（Codex 単体で動いているとき）

`run_codex.py` は使わない（Codex の中から Codex を起動しない）。代わりに自分が職人になる:

1. `prompts/NN.json` を読む。`images[]` の各 `path` を **`view_image` で読み込んで会話に載せる**
   （Image 1 = 参照画像、Image 2 = お手本。順番を守る）
2. `prompts/NN.txt` の全文をそのまま **image_gen** のプロンプトにする（単色 #00ff00 背景の指定が入っている）
3. 生成画像の背景が単色になっているか見る。市松模様や別の色なら同じ指示でやり直す（最大 2 回）
4. プロンプト末尾のコマンドどおり `remove_chroma_key.py` で透過化し、`prompts/NN.json` の `target`（絶対パス）に保存する。RGBA・四隅透明を確認。**`$CODEX_HOME/generated_images/` に置いたままにしない**
5. 1 枚終わるごとに次へ。**まとめて `n` 枚を 1 回で作らない**（別々の呼び出しにする）
6. 全部終わったら `python3 $S/check_stamps.py --plan <作業フォルダ>` 以降は経路 A と同じ

注意: Codex はセッションログに画像を Base64 で保存するため、**1 セッションで作る枚数が多いとログが数百 MB に膨らむ**。
16 個以上作るときは、8 個ごとに新しいセッションで再開すると軽い（`stamp_plan.json` と `output/raw/` が状態を持っているので再開できる）。

## 使用量とレート制限（Plus の実測・2026-09-04）

Codex の枠は「5 時間枠」と「週次枠」の 2 軸で、Codex・Work・ワークスペースエージェント等で共有される。
**画像 1 枚 ≒ Plus の 5 時間枠の約 2%**（46 分で約 30 枚生成して 63% 消費）。目安:

| やること | 枚数 | 5 時間枠の消費（Plus） |
|---|---|---|
| 疎通テスト | 1 | 2% |
| 試し打ち A（選んだ絵柄だけ） | 1〜2 | 2〜4% |
| 試し打ち B（全 6 絵柄で自分を描く） | 6 | 12〜13% |
| 8 個セット（試し打ち A 込み・作り直し 2 枚） | 約 11 | 約 25% |
| 16 個セット | 約 20 | 約 45% |
| 24 個以上 | 30〜 | **1 つの 5 時間枠に収まらない** |

- 24 個以上は **枠のリセットをまたいで分割**する（`run_codex.py` は既にある番号を飛ばすので、途中で止めて再開できる）。または Pro プランを使う
- `try again at HH:MM` の類のエラーは 5 時間枠の頭打ち。`--parallel 1` に落とすより、リセット時刻まで待つほうが確実
- **試し打ちはギャラリー（stamp-plan）で絵柄を決めてから A で済ませる**のが、いちばん効く節約
- 残量は ChatGPT の「設定 → プランの利用上限」で 5 時間枠・週次枠を個別に確認できる。**始める前に見てもらう**（残り 30% 未満なら 8 個は始めない）

## Windows での注意

- `python3` が無ければ `python` または `py`
- `run_codex.py` は `codex.cmd` を自動で見つける。見つからなければ stamp-setup で Codex を入れる
- 画像生成が「保存されない・パスが返らない」不具合の報告がある。**stamp-setup の疎通テストが通っていれば大丈夫**。
  通っていない環境では、まず stamp-setup を実行する

## バンドル資源

| ファイル | 用途 |
|---|---|
| `scripts/build_prompts.py` | 計画 + スタイル → `prompts/NN.txt` / `NN.json`。お手本の自動添付、`--note` で作り直しの申し送り、`--sample-style` で絵柄の試し打ち |
| `scripts/run_codex.py` | 経路 A: `codex exec --ephemeral` を並列実行して回収。失敗の自動リトライ、md5 取り違え検出 |
| `scripts/check_stamps.py` | RGBA / 透過率 / 四隅 / 見切れ / 取り違えの検査 → `review.json` + `review.html`。`--samples` で絵柄比較ページ `samples.html` |
| `scripts/samples_template.html` | 絵柄比較ページの雛形 |
| `scripts/finalize_stamps.py` | 規格変換（トリム・余白 10px・偶数・1MB）→ `submit/` + zip |
| `scripts/review_template.html` | 検収ページの雛形（静的、サーバー不要） |
| `scripts/stamp_common.py` | 共通: 計画の読み込み・検証、パス規約、md5 |
| `../stamp-plan/styles/*.txt` | 絵柄の定義。作業フォルダの `style.txt` が優先 |
