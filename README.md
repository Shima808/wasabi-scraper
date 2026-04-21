# wasabi-scraper

農林水産省「うちの郷土料理」（[https://www.maff.go.jp/j/keikaku/syokubunka/k_ryouri/search_menu/](https://www.maff.go.jp/j/keikaku/syokubunka/k_ryouri/search_menu/)）の全レシピデータをスクレイピングして Supabase に保存するスクリプトです。

全国47都道府県・約1,365件の郷土料理レシピ（タイトル・材料・作り方・説明・地域情報）を取得します。

---

## 取得できるデータ

| テーブル | 内容 |
|---|---|
| `recipes` | レシピ名（日本語）、作り方、説明、都道府県、地方、出典URL |
| `ingredients` | 食材名 |
| `recipe_ingredients` | レシピと食材の紐付け（分量含む） |

---

## 環境構築

### 前提
- Python 3.10 以上
- Supabase プロジェクト（`recipes` / `ingredients` / `recipe_ingredients` テーブルが作成済みであること）

### 1. 仮想環境の作成・有効化

```bash
python3 -m venv venv
source venv/bin/activate      # Mac / Linux / WSL
# venv\Scripts\activate       # Windows (cmd)
```

### 2. パッケージのインストール

```bash
pip install -r requirements.txt
```

### 3. .env の設定

プロジェクトルートに `.env` ファイルを作成し、以下を記載します：

```env
SUPABASE_URL=https://xxxxxxxxxxxxxxxxxxxx.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key
```

- `SUPABASE_URL`: Supabase プロジェクトの URL（Project Settings > API）
- `SUPABASE_SERVICE_KEY`: Service Role キー（Project Settings > API > service_role）

---

## 実行方法

### 通常スクレイピング（全レシピ取得）

```bash
python scrape_maff.py
```

- 実行済みの URL は `maff_done_urls.txt` に記録されます
- 途中で中断しても、次回実行時に続きから再開します

### 食材のみ再取得・再INSERT

既存レシピの食材データだけを再取得してDBに反映する場合：

```bash
python scrape_maff.py --ingredients-only
```

- `maff_done_urls.txt` に記録されたURLを対象に食材のみを再取得します
- 進捗は `maff_ingr_done_urls.txt` に記録され、途中再開が可能です

---

## ファイル構成

```
wasabi-scraper/
├── scrape_maff.py          # メインスクリプト
├── requirements.txt        # 依存パッケージ
├── .env                    # 環境変数（gitignore済み）
├── maff_done_urls.txt      # スクレイプ済みURL（自動生成・gitignore済み）
└── maff_ingr_done_urls.txt # 食材INSERT済みURL（自動生成・gitignore済み）
```

---
## 実装上の工夫・ハマったこと

**Supabaseの断続的502エラー対策**  
Cloudflare経由の接続が不安定で、1,365件・約2時間の処理中に
スクリプトがクラッシュする問題が発生。以下で解決：
- 全DBコールにリトライ処理（3回・5秒間隔）を追加
- 処理済みURLをチェックポイントファイルに記録し、中断再開を実装

**バックグラウンド実行の問題**  
`wsl.exe bash -c "python script.py &"` ではWSLセッション終了と同時に
プロセスが死ぬことを確認。実行方式を切り替えて解決。

## 開発メモ
中断再開の仕組みはClaude Codeと協働で実装。
プロンプト設計・データ構造・デバッグは自分で担当。
## 注意事項

- サーバー負荷を下げるため、各リクエスト間に 1.5 秒のウェイトを設けています
- 農林水産省サイトの利用規約および著作権に従ってご利用ください
