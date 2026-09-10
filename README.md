# Listening Companion

Listening Companion は、接続された SoundCloud アカウント上のコンテンツを**読み取り専用で探索するための、単一ユーザー向けローカル Python MVP**です。

Codex プラグインとしてパッケージ化されており、外部依存関係を持たない MCP JSON-RPC stdio サーバーと、ループバック接続によるブラウザビューアーを備えています。

アプリの表示名は意図的に **Listening Companion** としています。内部パッケージ／プラグインの slug は互換性維持のため `soundcloud-recommender` のままですが、これはユーザー向けの製品名ではありません。

## 実装されている機能

- OAuth 2.1 の Authorization Code Flow を実装しています。必須の S256 PKCE、正確なリダイレクト URI に紐付けられた一度限り有効な state、10分間の state 有効期限、リプレイ攻撃対策、リフレッシュトークンのローテーションに対応しています。
- アクセストークンとリフレッシュトークンは、デフォルトでは**セッション中のみ保持**されます。実行中のプロセス内にだけ存在し、切断時または終了時に削除されます。この MVP は平文トークンファイルをサポートしておらず、トークンが MCP ツールから返されたりログに記録されたりすることもありません。
- SoundCloud API クライアントは許可リスト方式かつ GET 専用です。以下に対応しています：`/me`、最近再生したトラック、いいねしたトラック、プレイリスト、フォロー中ユーザー／フォロー中ユーザーのトラック、検索、resolve、リソース詳細、プレイリスト内トラック、関連トラック。
- ループバックビューアーでは、プロバイダーから取得したレコードを**有効期限付きのインメモリ結果キャッシュ**にのみ保持します。ビューアーの HTML はエスケープ処理され、帰属表示のため SoundCloud へのリンクが含まれます。プロバイダーのコンテンツが SQLite やアーカイブに保存されることはありません。
- 決定論的なローカル・ルールベース方式によるポッドキャスト分類およびレコメンドを実装しています。Embedding 呼び出し、モデル呼び出し、音声ダウンロード、ストリーム取得、ファインチューニングは一切行いません。
- オプトイン方式の SQLite ストアには、制限された `preference_profile` のみ保存できます。生の会話、メモリ一覧、プロバイダーペイロード、視聴履歴は保存しません。
- 認証情報やネットワーク接続なしで、エンドツーエンドテストや初回動作確認を行える合成デモモードを備えています。

## 重要：ライブ出力の境界

ライブのプロバイダー操作では、MCP の結果として返される情報は、意図的に**アプリ自身が所有するフィールドだけ**に制限されています。

```json
{
  "status": "ok",
  "result_id": "opaque-random-id",
  "count": 5,
  "viewer_url": "http://127.0.0.1:8765/view/opaque-random-id?token=opaque-capability",
  "expires_in_seconds": 600,
  "warnings": []
}
```

トラックタイトル、説明、アーティスト名、SoundCloud URL、プロバイダーのペイロードは、ライブ環境において**MCP／モデル境界を越えません**。

これらは、不透明な capability URL を開いた本人に対して、認証済みローカルビューアーからのみ表示されます。

合成デモ用 fixture については、ローカルテストを理解しやすくするため、完全なデータを返す場合があります。

ループバックビューアーでは、不透明な capability token、短時間のみ有効なインメモリ結果、`HttpOnly` / `SameSite=Strict` Cookie を使用し、デフォルトでは外部インターフェースへ bind しません。

これはローカル利用向けの簡易機構であり、本番環境向けの ID 管理システムではありません。LAN やインターネット上へ公開しないでください。

## 認証情報なしでのクイックスタート

```bash
# このファイルが存在するプラグインルートディレクトリで以下を実行してください。
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
python scripts/validate_local.py
python scripts/run_mcp.py --demo
```

デモサーバーは stdout を通じて MCP 通信を行い、stdout にログを出力しないため、そのまま MCP ホストへ接続できます。

stdio のフレーミングには、デフォルトで NDJSON（newline-delimited JSON）を使用します。

従来の Content-Length フレーミングを必要とするホストでは、以下を指定できます。

```text
--framing content-length
```

ただし、これはデフォルトではありません。

`.env.example` はあくまでテンプレートです。このサービスは dotenv ファイルを自動読み込みしません。

ローカルシェルで実行する場合は、無視対象となるファイルへコピーし、プレースホルダーを書き換え、サーバーを起動する前に export / source してください。

例：

```bash
cp .env.example .env.local
# .env.local を編集した後、このシェルだけに読み込む：
set -a
. ./.env.local
set +a
python scripts/run_mcp.py
```

GUI ホストから stdio サーバーを起動する場合、そのサーバーには**ホスト起動時点の環境変数**が渡されます。

認証情報を追加または変更した場合は、`connect_account` を呼び出す前にホストを再起動してください。

ホスト側が明示的に環境変数の再読み込みをサポートしている場合は、プラグインの再読み込みでも構いません。

## ライブ環境のローカルセットアップ

1. 現在の SoundCloud 開発者向け手順に従って API アプリケーションを登録します。現在の SoundCloud ガイダンスでは、API アプリケーションの登録に Artist Pro が必要になる場合があります。クライアント ID とセキュリティコードは必ず非公開の環境変数として保持し、このプラグイン内やチャットメッセージへ記載しないでください。

2. `.env.example` を、Git 等から除外された非公開ファイルへコピーし、サービスを起動するシェル内で export / source します。SoundCloud から、このアプリで利用可能な scope が明示的に提供されていない限り、`SOUNDCLOUD_SCOPE` は空のままにしてください。このプロジェクト側で独自の scope を捏造することはありません。example ファイルは自動読み込みされません。

3. `LISTENING_COMPANION_REDIRECT_URI` に設定されている**完全に一致するループバックリダイレクト URI**を、SoundCloud アプリ側へ登録します。

4. 以下を実行します。

```bash
python scripts/run_mcp.py
```

サービスはデフォルトで `127.0.0.1:8765` 上にループバックビューアーを起動します。

`connect_account` を呼び出し、返されたローカルの `viewer_url` を開きます。ローカルページがブラウザを SoundCloud へリダイレクトし、認証後の callback を受信します。

認証情報を export する前から GUI ホストを起動していた場合は、先にそのホストを再起動してください。

5. 読み取り専用ツールを呼び出します。返されたそれぞれの `viewer_url` をローカルブラウザで開くことで、プロバイダーのメタデータおよび帰属表示用リンクを確認できます。

`connect_account` の結果として、`configuration_required` または `authorization_required` が返される場合があります。

これは意図された正直な状態です。有効なローカル認証情報がなければアカウント接続は試行されず、このリポジトリが独自にアカウントを有効化することもありません。

## MCP ツール

サーバーは、厳格なスキーマを持つ以下のツールを公開します。

`connection_status`、`connect_account`、`disconnect_account`、`get_profile`、`recent_plays`、`liked_tracks`、`liked_playlists`、`my_playlists`、`followings`、`following_tracks`、`search_tracks`、`search_playlists`、`search_users`、`resolve_resource`、`get_track`、`get_playlist`、`playlist_tracks`、`related_tracks`、`classify_podcasts`、`recommend`、`get_preferences`、`save_preferences`、`delete_preferences`、`demo_catalog`

プロバイダーのリスト取得では、上限付きページネーションを使用します。

最近再生したトラックについては、文書化されたエンドポイントを使用して取得し、現在の API の挙動ではそのエンドポイントが `access` のみ受け付けるため、最大25件に制限されています。

通常のコレクションには `limit` と `linked_partitioning` を使用します。

フォロー中ユーザーのトラックには `limit` と `offset` を使用します。

`next_href` は、API ホストが厳密な許可リスト検証を通過した場合にのみ追跡します。

`/resolve` は 302 レスポンスを返す可能性があるものとして扱い、後続リクエストを送る前にリダイレクト先を検証します。

関連トラックには以下を使用します。

```text
/tracks/{urn}/related
```

`recommend` は各ソースを独立して取得し、利用できないソースが存在した場合は**部分的ソース利用に関する警告**を返します。

プロフィールに含められるのは、以下のような上限付きフィールドのみです。

- ジャンル
- ポッドキャストのトピック
- 言語
- 好みのクリエイター
- 除外したい用語
- 希望する再生時間
- discovery level

リクエスト内のプロフィールは一時的なものです。

ユーザーが `consent: true` を指定して明示的に `save_preferences` を呼び出した場合にのみ保存されます。

## テストと検証

```bash
python -m pytest
python scripts/validate_local.py
python <path-to-plugin-creator>/scripts/validate_plugin.py .
```

テストではモック HTTP transport とローカルの一時ストアを使用します。

ネットワーク通信は行いません。

以下について検証します。

- OAuth のリプレイ攻撃対策
- OAuth state の有効期限
- PKCE
- リフレッシュトークンのローテーション
- 許可リスト
- リダイレクト処理
- ペイロードの正規化
- キャッシュの有効期限
- preference 保存時の同意
- 決定論的ランキング
- ポッドキャスト分類
- MCP フレーミング／スキーマエラー
- ライブ出力境界

## 利用規約、権利、スコープ上の制限

これは**実装のための雛形（implementation scaffold）**であり、法的な認証、あるいはライブ環境で統合機能を運用してよいという承認を意味するものではありません。

現在の SoundCloud API Terms of Use では、API レスポンスは User Content として扱われています。

また、現在の規約では以下のような制約が記載されています。

- User Content を AI 技術への入力として利用することへの制限
- User Content の永続的なキャッシュの禁止
- 帰属表示およびバックリンクの要求
- 個人データを扱う場合のプライバシーポリシー要求
- アプリ名として SoundCloud Marks を使用することの禁止

このため、このコードでは以下の設計を採用しています。

- ローカルのルールベース処理のみを行う
- ライブのプロバイダーデータはセッション中だけのメモリキャッシュへ保持する
- モデルへ返すのは、アプリ自身が所有する不透明なローカルビューアー receipt のみとする
- プロバイダーのメタデータとプロバイダーへのリンクは、認証済みローカルビューアー内だけに留める
- 表示名として Listening Companion を使用する

実際にライブ運用を行う前には、適切な権利処理と、**最新の SoundCloud 利用規約の確認**が依然として必要です。

ローカル MCP のパッケージ形式は意図的に保守的な構成になっています。

`.mcp.json` では、ローカル stdio MCP サーバーをサポートするホスト向けに、command / args / cwd / env のエントリと `env_vars` を使用しています。

以下については、このリポジトリでは動作保証・認証していません。

- ホスト固有のインストール手順
- Python パスの処理
- リモート HTTPS MCP 認証

これは ChatGPT Web へ直接接続するリモート統合ではなく、ホストされた Web サイトを作成するものでもありません。

本番環境でリモート MCP としてデプロイする場合は、別途以下が必要になります。

- ホスティング
- 認証
- データ保護に関するレビュー
- 権利処理

このプロジェクト自体は何もデプロイしません。

## ファイル構成

```text
src/soundcloud_recommender/
  cache.py          プロバイダー／結果／カーソル用のインメモリ TTL
  classifier.py     メタデータのみを使用するポッドキャスト判定ヒューリスティック
  cli.py            ローカルコマンドのエントリーポイント
  config.py         環境設定と各種制限
  http.py           許可リスト方式 HTTP transport の抽象化
  mcp_stdio.py      MCP JSON-RPC stdio サーバー本体
  models.py         上限付きドメインモデルおよび公開結果モデル
  oauth.py          PKCE、callback フロー、トークンライフサイクル
  preferences.py    オプトイン preference_profile SQLite ストア
  recommender.py    決定論的なローカルランキング
  service.py        操作処理およびライブ出力境界
  viewer.py         ループバック認証済み結果ビューアー
```
