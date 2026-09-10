# Listening Companion

Listening Companion は、SoundCloud アカウントに接続して、曲やプレイリストなどを**読み取り専用で探したり、おすすめを出したりするためのローカル Python アプリ**です。

現時点では、1人で使うことを前提とした MVP（最小限の実用版）です。Codex プラグインとして動作し、依存ライブラリなしの MCP JSON-RPC stdio サーバーと、ローカルブラウザで結果を見るためのビューアーを備えています。

アプリ上の表示名は **Listening Companion** です。

内部では互換性維持のため `soundcloud-recommender` というパッケージ名・プラグイン名を使っていますが、これはユーザー向けの製品名ではありません。

## 現在できること

以下の機能を実装しています。

- SoundCloud アカウントへの OAuth 2.1 認証
- 最近再生した曲の取得
- いいねした曲・プレイリストの取得
- 自分のプレイリストの取得
- フォロー中ユーザーや、そのユーザーの曲の取得
- 曲・プレイリスト・ユーザーの検索
- SoundCloud URL からリソースを取得
- 曲・プレイリストの詳細取得
- プレイリスト内の曲の取得
- 関連楽曲の取得
- ポッドキャストの簡易分類
- ローカルルールによるおすすめ生成
- 好みの保存・削除
- 認証なしで試せるデモモード

OAuth 認証では、S256 PKCE、state の有効期限、リプレイ攻撃対策、リフレッシュトークンのローテーションなどを実装しています。

アクセストークンとリフレッシュトークンは、基本的に**アプリを起動している間だけメモリ上に保持**します。

切断またはアプリ終了時に削除されます。

トークンを平文ファイルへ保存する機能はなく、MCP ツールの結果としてトークンを返したり、ログへ出力したりすることもありません。

## SoundCloud API について

SoundCloud API へのアクセスは、許可したエンドポイントだけに限定しています。

また、API 通信は GET リクエストのみです。

主に以下を利用します。

- `/me`
- 最近再生した曲
- いいね
- プレイリスト
- フォロー
- 検索
- `/resolve`
- 曲・プレイリストなどの詳細
- プレイリスト内の曲
- 関連楽曲

SoundCloud 側へ書き込みを行う機能はありません。

## ローカルビューアー

SoundCloud から取得した情報は、MCP を通してそのままモデルへ渡すのではなく、ローカルブラウザ上のビューアーで確認する仕組みになっています。

取得結果は、期限付きのインメモリキャッシュに一時的に保存されます。

SoundCloud から取得したコンテンツを SQLite やアーカイブへ保存することはありません。

ビューアーでは HTML エスケープを行い、SoundCloud へのリンクも表示して、適切に出典を確認できるようにしています。

## おすすめ機能について

おすすめやポッドキャスト分類は、完全にローカルで動作するルールベース方式です。

以下の処理は行いません。

- Embedding API の利用
- AI モデルへの問い合わせ
- 音声ファイルのダウンロード
- ストリーム音声の保存
- ファインチューニング

つまり、曲のメタデータをもとに、決められたルールで分類やランキングを行います。

## 好みの保存

必要であれば、SQLite に `preference_profile` を保存できます。

ただし、保存はユーザーが明示的に許可した場合のみです。

保存できるのは、例えば以下のような情報に限定されています。

- 好きなジャンル
- 興味のあるポッドキャストの話題
- 言語
- 好きなクリエイター
- 避けたいキーワード
- 好みの曲の長さ
- 新しい曲をどれくらい積極的に探したいか

以下は保存しません。

- 会話内容
- AI のメモリ一覧
- SoundCloud API の生レスポンス
- 再生履歴そのもの

一時的に渡した好みは、`save_preferences` を `consent: true` 付きで明示的に実行しない限り保存されません。

## 重要：SoundCloud のデータをモデルへ直接渡さない

ライブ環境では、MCP から返すデータを意図的に最小限にしています。

例えば以下のような結果だけを返します。

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

モデル側へ返されるのは、

- 処理結果
- 結果ID
- 件数
- ローカルビューアーのURL
- 有効期限
- 警告

など、Listening Companion 側で生成した情報だけです。

以下のような SoundCloud のデータは、ライブ環境ではモデル側へ直接渡しません。

- 曲名
- アーティスト名
- 曲の説明
- SoundCloud URL
- SoundCloud API の生データ

これらは、ローカルビューアーを開いたユーザー本人だけが確認できます。

なお、デモモードでは仕組みを理解しやすくするため、架空のテストデータをそのまま返す場合があります。

## セキュリティ上の注意

ローカルビューアーでは、

- ランダムな capability token
- 短時間だけ有効なインメモリデータ
- `HttpOnly` Cookie
- `SameSite=Strict` Cookie

などを使用しています。

デフォルトでは `127.0.0.1` のみに接続します。

このビューアーはあくまで**ローカル利用向けの簡易的な仕組み**です。

本番環境向けの認証システムではありません。

そのため、LAN やインターネット上へ公開しないでください。

## 認証情報なしで試す

まずはデモモードで動作確認できます。

この README があるプラグインのルートディレクトリで実行してください。

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
python scripts/validate_local.py
python scripts/run_mcp.py --demo
```

デモサーバーは stdout を使って MCP 通信を行います。

stdout へログを出さないため、そのまま MCP ホストへ接続できます。

通常は NDJSON（1行ごとに JSON を送る形式）を使用します。

Content-Length 形式が必要な MCP ホストでは、以下を指定してください。

```bash
--framing content-length
```

## 環境変数の設定

`.env.example` は設定例です。

Listening Companion 自体は `.env` ファイルを自動では読み込みません。

ローカルで実行する場合は、例えば次のようにします。

```bash
cp .env.example .env.local
```

`.env.local` の内容を編集したら、そのシェルへ読み込みます。

```bash
set -a
. ./.env.local
set +a
python scripts/run_mcp.py
```

`.env.local` は Git などへコミットしないようにしてください。

GUI アプリから MCP サーバーを起動する場合、そのアプリを起動した時点の環境変数が使われます。

そのため、認証情報を追加・変更した場合は、MCP ホストを再起動してください。

ホスト側が環境変数の再読み込みに対応している場合は、プラグインの再読み込みでも構いません。

## 実際の SoundCloud アカウントへ接続する

### 1. SoundCloud API アプリを登録する

現在の SoundCloud の開発者向け手順に従って、API アプリケーションを登録します。

現在の SoundCloud の案内では、API アプリ登録に Artist Pro が必要になる場合があります。

クライアントIDやセキュリティコードは必ず秘密にしてください。

プラグインのコードやチャットへ貼り付けないでください。

### 2. 環境変数を設定する

`.env.example` をコピーし、認証情報を設定します。

`SOUNDCLOUD_SCOPE` は、SoundCloud から利用可能な scope が明示されていない限り空のままにしてください。

Listening Companion 側で独自の scope を勝手に指定することはありません。

### 3. リダイレクトURIを登録する

`LISTENING_COMPANION_REDIRECT_URI` に指定した URI を、SoundCloud 側にも**完全に同じ文字列で**登録してください。

### 4. MCP サーバーを起動する

```bash
python scripts/run_mcp.py
```

デフォルトでは、

```text
127.0.0.1:8765
```

でローカルビューアーが起動します。

その後、`connect_account` を実行します。

返された `viewer_url` をブラウザで開くと SoundCloud の認証画面へ移動し、認証完了後にローカルページへ戻ります。

### 5. MCP ツールを使う

接続後は、各種読み取り専用ツールを利用できます。

SoundCloud のデータを確認したい場合は、ツールの結果として返された `viewer_url` をブラウザで開いてください。

`connect_account` の結果が、

```text
configuration_required
```

または

```text
authorization_required
```

になることがあります。

これはエラーをごまかしているわけではありません。

有効な認証情報や認証操作がなければ、Listening Companion が勝手に SoundCloud アカウントへ接続することはありません。

## 利用できる MCP ツール

現在、以下のツールを提供しています。

```text
connection_status
connect_account
disconnect_account
get_profile

recent_plays
liked_tracks
liked_playlists
my_playlists

followings
following_tracks

search_tracks
search_playlists
search_users

resolve_resource
get_track
get_playlist
playlist_tracks
related_tracks

classify_podcasts
recommend

get_preferences
save_preferences
delete_preferences

demo_catalog
```

## API取得時の細かい仕様

一覧データには件数制限付きのページネーションを使用します。

最近再生した曲については、現在の SoundCloud API の仕様に合わせて最大25件まで取得します。

一般的なコレクションでは、

```text
limit
linked_partitioning
```

を使用します。

フォロー中ユーザーの楽曲取得では、

```text
limit
offset
```

を使用します。

SoundCloud API から返される `next_href` をそのまま信用することはありません。

接続先が許可された SoundCloud API ホストであることを検証した場合のみ、次のページへアクセスします。

`/resolve` が HTTP 302 リダイレクトを返した場合も、リダイレクト先を検証してからアクセスします。

関連楽曲は以下の API を使用します。

```text
/tracks/{urn}/related
```

`recommend` では、複数の情報源をそれぞれ独立して取得します。

一部の情報源を取得できなかった場合でも、取得できた情報だけで結果を返し、警告を表示します。

## テスト

以下のコマンドでテストできます。

```bash
python -m pytest
python scripts/validate_local.py
python <path-to-plugin-creator>/scripts/validate_plugin.py .
```

テストでは実際の SoundCloud API にはアクセスしません。

モック HTTP 通信と一時的なローカルストレージを使います。

主に以下を確認しています。

- OAuth state の再利用防止
- OAuth state の有効期限
- PKCE
- リフレッシュトークンのローテーション
- 接続先の許可リスト
- リダイレクト先の検証
- API データの正規化
- キャッシュの有効期限
- 好み保存時の同意確認
- おすすめランキングの再現性
- ポッドキャスト分類
- MCP のフレーミング
- MCP スキーマエラー
- SoundCloud データがモデル側へ漏れないこと

## SoundCloud の利用規約について

このプロジェクトは、あくまで実装のための試作・雛形です。

このコードが存在するからといって、SoundCloud との正式な連携サービスとして運用する許可が得られるわけではありません。

現在の SoundCloud API Terms of Use では、API から取得したデータは User Content として扱われます。

また、以下のような制限があります。

- User Content を AI 技術への入力として使用することへの制限
- User Content を永続的にキャッシュすることへの制限
- SoundCloud への帰属表示やリンクの要求
- 個人情報を扱う場合のプライバシーポリシー要求
- SoundCloud の商標をアプリ名として使うことの禁止

そのため Listening Companion では、できるだけ規約に配慮して以下の設計にしています。

- おすすめ処理はローカルのルールベース方式
- SoundCloud のデータは一時的なメモリ上だけに保持
- SoundCloud のメタデータをモデルへ直接渡さない
- SoundCloud の曲名やリンクはローカルビューアーでのみ表示
- アプリ名には SoundCloud の名称を使わず `Listening Companion` とする

ただし、これだけで法的・契約上の問題がすべて解決するわけではありません。

実際に公開・運用する前には、最新の SoundCloud 利用規約を確認し、必要な権利処理を行う必要があります。

## MCP プラグインとしての位置づけ

ローカル MCP の設定は、なるべく一般的で保守的な構成にしています。

`.mcp.json` では主に、

```text
command
args
cwd
env
env_vars
```

を使用します。

ただし、以下についてはこのプロジェクトでは正式な動作保証をしていません。

- MCP ホストごとのインストール方法
- Python のパス設定
- リモート HTTPS MCP の認証

また、このプロジェクトは ChatGPT Web へ直接接続するリモート MCP サービスではありません。

Web サイトをホスティングする機能もありません。

インターネット上で利用できる本番用 MCP サーバーにする場合は、別途以下が必要です。

- サーバーのホスティング
- ユーザー認証
- データ保護・プライバシーの確認
- SoundCloud 関連の権利確認

このリポジトリ単体では、外部へのデプロイは行いません。

## ファイル構成

```text
src/soundcloud_recommender/
  cache.py
    SoundCloudの取得結果やカーソルを一時保存するTTLキャッシュ

  classifier.py
    メタデータだけを使ったポッドキャスト判定

  cli.py
    ローカルCLIのエントリーポイント

  config.py
    環境変数や各種制限の設定

  http.py
    接続先を制限したHTTP通信処理

  mcp_stdio.py
    MCP JSON-RPC stdioサーバー本体

  models.py
    内部データモデルと公開結果モデル

  oauth.py
    PKCE、OAuth callback、トークン管理

  preferences.py
    ユーザーが許可した好みをSQLiteへ保存

  recommender.py
    ローカルで動作するおすすめランキング

  service.py
    各操作の実装と、モデルへ渡す情報の境界管理

  viewer.py
    ローカルブラウザで結果を見るためのビューアー
```
