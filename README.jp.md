# chrome-web-mcp

[![Verify](https://github.com/kotao-boop/chrome-web-mcp-windows/actions/workflows/verify.yml/badge.svg)](https://github.com/kotao-boop/chrome-web-mcp-windows/actions/workflows/verify.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English README](README.md)

> 本リポジトリは、[kuraneko1/chrome-web-mcp](https://github.com/kuraneko1/chrome-web-mcp)をもとに
> Windows向けへ再設計した独立派生版です。元プロジェクトの公式Windows版ではなく、元プロジェクトの
> 管理者による保証や承認を示すものではありません。

Google Chromeを操作し、AIツールにGoogle検索結果やWebページの本文を取得させる
MCP（Model Context Protocol。AIと外部ツールを接続するための共通規格）サーバーです。
Windows 10およびWindows 11で動作します。

主に次の3つのツールを提供します。

- `google_search`: Google検索を実行し、タイトル、URL、概要などの構造化データを返します。
- `fetch_url`: 公開Webページを取得し、余計な装飾を除いた読みやすい本文を返します。
- `health_check`: ブラウザの稼働状態、検索の待機時間、CAPTCHAの状態などを確認します。

---

## 目次

1. [重要な前提と注意事項](#重要な前提と注意事項)
2. [主な特徴](#主な特徴)
3. [クイックスタート（導入手順）](#クイックスタート導入手順)
4. [提供ツールの詳細と仕様](#提供ツールの詳細と仕様)
5. [設定とカスタマイズ](#設定とカスタマイズ)
6. [CAPTCHA（画像認証）への対処手順](#captcha画像認証への対処手順)
7. [安全性とセキュリティ設計](#安全性とセキュリティ設計)
8. [動作環境と容量の目安](#動作環境と容量の目安)
9. [更新手順](#更新手順)
10. [開発とテスト](#開発とテスト)
11. [由来と謝辞](#由来と謝辞)
12. [ライセンス](#ライセンス)

---

## 重要な前提と注意事項

> **Windows専用（Windows 10 / Windows 11）**
> このソフトウェアはWindows環境向けに設計・検証されています。対応OSはWindowsのみです。
> 追加の表示サーバーを導入する必要はなく、Windowsの標準機能でそのまま動作します。

> [!CAUTION]
> **検索の頻度とブラウザの起動について**
> 1つのMCPサーバープロセスにつき、起動するブラウザは1つです。短時間に大量の検索を
> 連続して実行せず、基本的に順番に処理します。
>
> - 通常の利用範囲ではレート制限（短時間に大量のアクセスを行わないための制限）に
>   かかりません。1分間に15回以上の検索を開始すると、`pace_warning`が返ります。
> - 警告が出ても検索自体は継続されます。ただし、過度な連続アクセスを行うとGoogleから
>   CAPTCHA（「私はロボットではありません」のような画像認証）を求められる場合が
>   あります。認証画面が出たら数分待って再試行するか、ブラウザを表示する設定にして
>   手動で解除してください。
> - 独立した複数のMCPサーバープロセスを起動すると、それぞれ別のブラウザが起動します。
>   複数のAIツールから同時に過度な検索を実行しないでください。

---

## 主な特徴

- **本物のChromeを利用**: JavaScriptで表示内容が変わるWebページでも、検索と本文取得を
  実際のChromeで行います。
- **読みやすいMarkdown整形**: `trafilatura`と`html2text`を使い、ヘッダー、フッター、
  広告などの不要な部分を省いて本文を整形します。
- **表示言語と検索地域を指定**: `hl: "ja"`、`gl: "jp"`など、目的に合わせた検索結果を
  指定できます。
- **画面表示を切り替え可能**: 通常のウィンドウ表示、画面外へ移動して非表示にする方式、
  画面を持たない環境向けのヘッドレスモードに対応しています。
- **アクセス安全機能**: 公開アドレスへの接続だけを許可し、`localhost`やプライベートIP
  アドレスへの通信を遮断します。
- **確実な終了処理**: WindowsのJob Objectを使い、MCPクライアント終了時にChromeなどの
  関連プロセスも自動的に終了させます。

---

## クイックスタート（導入手順）

このWindows版は、このGitHubリポジトリとGitHub Releasesから配布しています。最も確実な方法は、
ローカルの仮想環境にインストールした実行ファイルを使うことです。こうすると、すべてのMCP
クライアントが同じ検証済みのコピーを利用できます。

### 1. ローカルの作業コピーを準備する

リポジトリを取得し、コマンドプロンプトまたはPowerShellで実行環境を作成します。

```bat
git clone https://github.com/kotao-boop/chrome-web-mcp-windows.git
cd chrome-web-mcp-windows
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

PCにGoogle Chromeがインストールされていない場合は、次のコマンドでChrome for Testingを配置
できます。対応するGoogle ChromeまたはChromiumがすでにある場合は、この手順は不要です。

```bat
python scripts\install-chrome-for-testing.py
```

### 2. MCPクライアントを設定する

仮想環境内の実行ファイルを、絶対パスで指定します。`YOU`をWindowsのユーザー名に置き換え、
必要であれば作業フォルダーの部分も変更してください。

```json
{
  "mcpServers": {
    "chrome-web": {
      "command": "C:\\Users\\YOU\\chrome-web-mcp-windows\\.venv\\Scripts\\chrome-web-mcp.exe"
    }
  }
}
```

リポジトリには、主要なWindows向けMCPクライアント用の設定例を用意しています。既存の設定を
全部消さず、該当するサーバー項目だけを追加・統合してください。

#### Cursor / Claude Desktop

[`examples/mcp-config.windows.json`](examples/mcp-config.windows.json)を、Cursorの
`%USERPROFILE%\.cursor\mcp.json`またはClaude Desktopの
`%APPDATA%\Claude\claude_desktop_config.json`に統合します。

#### VS Code / GitHub Copilot

プロジェクト内の`.vscode/mcp.json`を作成・編集するか、コマンドパレットから
`MCP: Open User Configuration`を実行します。設定例は
[`examples/mcp-config.vscode.json`](examples/mcp-config.vscode.json)です。

#### Google Antigravity CLI

現在のGoogleのターミナルエージェントはAntigravity CLIです。Gemini CLIから移行する場合は、
Google公式の[移行ガイド](https://antigravity.google/docs/cli/gcli-migration)を参照してください。
[`examples/mcp-config.antigravity.json`](examples/mcp-config.antigravity.json)を、全体設定の
`%USERPROFILE%\.gemini\config\mcp_config.json`に統合します。プロジェクト単位で設定する場合は、
同じ項目をプロジェクト内の`.agents\mcp_config.json`に保存します。Antigravity IDEでも同じ
`mcp_config.json`形式を使います。詳しくはGoogle公式の
[Antigravity MCPガイド](https://antigravity.google/docs/cli/mcp/)を参照してください。

#### Windsurf（Cascade）

[`examples/mcp-config.windsurf.json`](examples/mcp-config.windsurf.json)を、
`%USERPROFILE%\.codeium\windsurf\mcp_config.json`に統合します。Windsurfの「Settings」→
「Cascade」→「MCP Servers」→「View Raw Config」から設定ファイルを開くこともできます。

#### OpenCode

プロジェクトの`opencode.jsonc`に、
[`examples/mcp-config.opencode.jsonc`](examples/mcp-config.opencode.jsonc)を統合します。
OpenCodeでは、実行ファイルのパスを1要素の`command`配列で指定します。

#### Codexでの設定例

Codex DesktopまたはCodex CLIでは、[`examples/mcp-config.codex.toml`](examples/mcp-config.codex.toml)の
内容を`%USERPROFILE%\.codex\config.toml`に追加します。`YOU`の部分は自分のユーザー名に置き換えてください。

```toml
[mcp_servers.chrome-web]
command = 'C:\Users\YOU\chrome-web-mcp-windows\.venv\Scripts\chrome-web-mcp.exe'
args = []
cwd = 'C:\Users\YOU\chrome-web-mcp-windows'
startup_timeout_sec = 30
tool_timeout_sec = 120
enabled = true

[mcp_servers.chrome-web.env]
# 対話的なデスクトップがない場合は "headless" に変更します。
CW_DISPLAY_MODE = "hidden"
```

設定を保存したら、MCPクライアントを再起動してください。

---

## 提供ツールの詳細と仕様

### 1. `google_search`（Google検索）

Google検索を行い、整理された結果を返します。

#### 入力パラメータ

```json
{
  "query": "検索キーワード",
  "limit": 5,
  "hl": "ja",
  "gl": "jp"
}
```

- `query`（必須）: 検索する文字列。最大512文字です。
- `limit`（省略可能）: 取得件数。1〜20件で、初期値は5件です。
- `hl`（省略可能）: Googleの表示言語。2〜8文字のコードを指定します。
- `gl`（省略可能）: 検索地域。2〜8文字のコードを指定します。

#### 成功時のレスポンス例

```json
{
  "success": true,
  "data": {
    "web": [
      {
        "title": "ページのタイトル",
        "url": "https://example.com/page",
        "description": "検索結果の説明文",
        "position": 1
      }
    ],
    "waited_ms": 1240,
    "pace_warning": null
  }
}
```

検索結果の一覧は`data.web`に入ります。`waited_ms`は検索開始までの待機時間（ミリ秒）です。
`pace_warning`は通常`null`で、短時間に検索が集中した場合は警告文が入ります。

---

### 2. `fetch_url`（Webページ取得）

公開されているWebページの内容を読み取り、指定された形式で本文を返します。

#### 入力パラメータ

```json
{
  "url": "https://example.com",
  "char_limit": 15000,
  "format": "markdown"
}
```

- `url`（必須）: 取得先のURL。`http://`または`https://`のみ、最大2048文字です。
- `char_limit`（省略可能）: 返す本文の最大文字数。100〜200000文字で、初期値は15000です。
- `format`（省略可能）: 出力形式。初期値は`"markdown"`です。
  - `"markdown"`: 本文以外の定型部分を除いた読みやすいMarkdownを返します。本文は
    `data.markdown`に入り、ページ内リンクの`data.links`（最大200件）も返します。
  - `"text"`: ページの全文をプレーンテキストで返します。本文は`data.text`に入り、
    `links`は返しません。Markdown抽出で欠落がある場合に使用します。
  - `"links"`: プレーンテキスト本文（`data.text`）と、ページ内リンクの`data.links`
    （最大200件）を返します。

#### 成功時のレスポンス例（`format: "markdown"`）

```json
{
  "success": true,
  "data": {
    "requested_url": "https://example.com",
    "final_url": "https://example.com/page",
    "redirected": true,
    "title": "ページタイトル",
    "total_chars": 8500,
    "truncated": false,
    "format": "markdown",
    "formatted": true,
    "extraction": "trafilatura",
    "markdown": "# 記事見出し\n\n本文テキスト...",
    "links": [
      {"text": "関連ページ", "url": "https://example.com/subpage"}
    ]
  }
}
```

URLは公開アドレスだけを取得できます。`localhost`、プライベートIP、クラウドのメタデータ用
ホスト、認証情報を含むURLなどは拒否されます。レスポンスには最終URL、リダイレクトの有無、
総文字数、切り詰めの有無、本文の抽出方法も含まれます。

---

### 3. `health_check`（稼働状態確認）

引数は不要です。引数を渡すとエラーになります。

#### 成功時のレスポンス例

```json
{
  "success": true,
  "data": {
    "platform": "Windows 10 (AMD64)",
    "display_mode": "native",
    "chrome_binary": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "chrome_version": "Google Chrome 150.0.0.0",
    "chrome_detection_error": null,
    "chrome_alive": true,
    "windows_job_attached": true,
    "gpu_device": null,
    "gpu_renderer": null,
    "gpu_backend": "UNKNOWN",
    "rate_limiter_queue_wait_s": 0.0,
    "rate_limit_min_delay_s": 1.0,
    "rate_limit_max_delay_s": 2.5,
    "recent_searches_60s": 2,
    "pace_warning": null,
    "last_captcha_at": null
  }
}
```

ブラウザの稼働状態、Chromeのバージョン、Job Objectへの接続状態、直近60秒間の検索回数
などを確認できます。このツールを実行してもブラウザ自体は起動しません。

---

## 設定とカスタマイズ

### 設定ファイルの作成

設定ファイル（`config.json`）を配置すると、既定の動作を変更できます。PowerShellで次の
コマンドを実行して雛形を作成します。

```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\chrome-web-mcp"
Copy-Item examples\config.json, examples\config.md "$env:APPDATA\chrome-web-mcp\"
```

配置先は`%APPDATA%\chrome-web-mcp\config.json`です。別の場所を使う場合は、`CW_CONFIG`
環境変数にJSONファイルのパスを指定してください。

```json
{
  "show_browser": true,
  "hl": "ja",
  "gl": "jp",
  "limit": 5,
  "char_limit": 15000,
  "format": "markdown",
  "min_delay": 1.0,
  "max_delay": 2.5
}
```

主な設定は次のとおりです。

- `show_browser`: `true`で通常のChromeウィンドウを表示し、`false`で画面外へ移動して
  非表示にします。対話的なデスクトップがない場合は`CW_DISPLAY_MODE=headless`を使います。
- `hl` / `gl`: Googleの表示言語と検索地域です。日本語・日本向けなら`ja` / `jp`を指定します。
- `limit`: `google_search`の既定結果数。1〜20です。
- `char_limit`: `fetch_url`の既定最大文字数。100〜200000です。
- `format`: `fetch_url`の既定形式。`markdown`、`text`、`links`から選びます。
- `min_delay` / `max_delay`: Google検索開始間隔の秒数です。範囲内でランダム化されます。

### サーバーやバックグラウンドでの実行（ヘッドレスモード）

タスクスケジューラ、CI、自動処理、リモート操作環境など、画面表示を行わない環境では、
環境変数を使ってヘッドレスモードを明示します。

```bat
set CW_DISPLAY_MODE=headless
chrome-web-mcp
```

`CW_DISPLAY_MODE`は`show_browser`より優先されます。`native`、`hidden`、`headless`の
いずれかを指定できます。

### 主な環境変数

| 環境変数 | 役割 | 既定値 |
| :--- | :--- | :--- |
| `CW_CONFIG` | 設定ファイル（JSON）のパス | `%APPDATA%\chrome-web-mcp\config.json` |
| `CW_DISPLAY_MODE` | 表示モード。`native`、`hidden`、`headless`。`show_browser`より優先 | 未指定 |
| `CW_CHROME` | 使用するChrome実行ファイルのパス | 自動検出 |
| `CW_PROFILE_DIR` | Chromeプロファイルの保存先 | `%TEMP%\chrome-web-v2-profile\{プロセスID}` |
| `CW_LOCK_PATH` | プロファイルロックファイルのパス | プロファイル内の`.instance.lock` |
| `CW_RATE_LIMIT_DB` | 検索間隔制御用SQLiteのパス | `%TEMP%\chrome-web-mcp\search-rate-limit.sqlite3` |
| `CW_MIN_DELAY` / `CW_MAX_DELAY` | 検索開始間隔（秒） | `1.0` / `2.5` |

設定ファイルの不明なキーや不正な値は、標準エラー出力へ警告を出して既定値が使われます。
設定を変更したらMCPクライアントを再起動してください。

---

## CAPTCHA（画像認証）への対処手順

このサーバーは、Googleの画像認証やパスワード認証を自動的に突破する機能を備えていません。

短時間に検索を繰り返してGoogleから認証を求められた場合、ツールは次のようなエラーを返します。

```json
{
  "success": false,
  "error": "...",
  "captcha_required": true
}
```

- **ブラウザを表示している場合（`show_browser: true`）**: 開いているChromeウィンドウを
  操作し、自分で画像認証を完了してから、同じ検索をもう一度実行してください。
- **ブラウザを非表示にしている場合（`show_browser: false`）**: 自動的には解除できないため、
  数分待って再試行するか、`show_browser: true`に変更して再起動してください。

---

## 安全性とセキュリティ設計

- **ローカル通信の遮断**: `localhost`やプライベートIPアドレスへのアクセスを遮断し、
  外部の公開Webページだけを取得します。
- **認証情報を含むURLの拒否**: パスワードなどの秘密情報が含まれるURLへのアクセスを拒否します。
- **プロセスの自動終了**: WindowsのJob ObjectにChromeを割り当て、MCPクライアント終了時に
  関連プロセスも終了させます。割り当てに失敗した場合は起動を停止します。
- **プロファイルの分離**: 普段使っているChromeの履歴や保存済みパスワードとは分離した、
  プロセスごとの一時プロファイルでブラウザを起動します。
- **孤立プロセスの回収**: 前回の異常終了で残ったChromeを、プロセスIDと起動時刻を確認して
  回収します。無関係なChromeを名前だけで終了させることはありません。

このサーバーは認証やCAPTCHAを迂回するものではなく、一般的なリモートブラウザ操作APIでも
ありません。また、任意のページコンテキストJavaScriptを実行するツールは提供しません。

---

## 動作環境と容量の目安

- **対応OS**: Windows 10またはWindows 11（64-bit）
- **Python**: 3.10以上
- **ブラウザ**: Google Chrome、Google Chrome for Testing、またはChromium
- **ディスク容量の目安**:
  - Python仮想環境（依存ライブラリを含む）: 約100MB
  - 本パッケージのソース: 1MB未満
  - Chrome for Testing（専用Chromeを配置する場合）: 約485MB
  - 合計: 約600MB

すでにGoogle Chromeをインストールしている場合は、Chrome for Testingの容量は必要ありません。
実際の容量はPythonのバージョンやChromeの更新状況によって変わります。

---

## 更新手順

### ソースコードを取得して利用している場合

ローカルの変更を確認してから、更新内容を取得します。`reset --hard`は作業内容を失う
おそれがあるため使用しません。

```powershell
git -C "C:\Users\YOU\chrome-web-mcp" fetch origin
git -C "C:\Users\YOU\chrome-web-mcp" pull --ff-only
& "C:\Users\YOU\chrome-web-mcp-windows\.venv\Scripts\python.exe" -m pip install -e "C:\Users\YOU\chrome-web-mcp-windows[test]"
```

更新後はMCPクライアントを再起動してください。`uv run`で起動している場合は、次回起動時に
依存関係が同期されます。

---

## 開発とテスト

開発者向けの環境構築、テスト、ビルド手順は[`CONTRIBUTING.md`](CONTRIBUTING.md)を参照してください。

外部への通信を行わず、毎回同じ結果が得られるテストだけを実行する場合は次を実行します。

```bat
pytest -q -m "not live"
```

`live`マーク付きのテストは実際にGoogleへ接続するため、ネットワーク状態やCAPTCHAの発生に
よって失敗することがあります。

---

## 由来と謝辞

このリポジトリは、[kuraneko1/chrome-web-mcp](https://github.com/kuraneko1/chrome-web-mcp)をもとにした
独立したWindows向けForkです。[antirez/ds4](https://github.com/antirez/ds4)に含まれていたブラウザを
使ったWeb処理を出発点にしています。元のプロジェクトのライセンス通知には、
`The ds4.c authors`と`The ggml authors`の著作権表示が含まれており、その通知は
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)に残しています。

Gitの履歴には、次の著者名と開発の経緯が記録されています。

- `kuraneko1`: `chrome-web-mcp`の初期構造とブラウザ実行基盤を整備。
- `ryu`: MCP向けの検索・本文取得機能、Markdown整形、設定、ドキュメントを拡充。
- `_ryu15_`: 対応プラットフォーム範囲を整理し、DS4のライセンス通知を追加。

現在のWindows版は、PythonとMCPの規格に合わせて全面的な再設計・再実装を行ったものです。
元のコードを単に再配布するものではなく、検索、公開URL取得、本文整形、安全な接続確認、
レート制限、WindowsのChrome起動・終了処理などを追加・再設計しています。
基礎となるアイデアを提供した先行開発者と、ds4.c・ggmlの貢献者に感謝します。

このWindows版に関する質問や不具合報告は、このリポジトリの
[Issues](https://github.com/kotao-boop/chrome-web-mcp-windows/issues)を利用してください。
Windows版固有の問い合わせを、元プロジェクトへ送らないようにしてください。

---

## ライセンス

このプロジェクトはMITライセンスのもとで公開されています。全文は[`LICENSE`](LICENSE)を
参照してください。DS4由来の通知は[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)に
分けて記載しています。

セキュリティ問題の報告方法は[`SECURITY.md`](SECURITY.md)を参照してください。公開Issueには
実際のパスワード、Cookie、秘密情報を含めないでください。
