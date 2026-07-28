# ermia_ayame Step 2 実装メモ (依存トランザクションのみの待機)

## 目的 (Ayame 最適化ポイント2)
現行のコミット通知条件は commitLSN <= min(全ワーカのflushedLSN) で、
自分と無関係なワーカのflushまで待つ(レイテンシがスレッド数に比例する原因)。
これを「自分の依存先のみの待機」に置き換える:

  通知条件 = 自ワーカのflushedLSN >= 自分のcommitLSN
           AND 各依存先 (worker w, lsn l) について confirmedLSN_w >= l
  (confirmedLSN_w = ワーカwが通知済みの最大コミットログLSN。依存先の
   「ログ永続化」ではなく「通知済み」を待つことで、依存先自身の依存の充足が
   再帰的に保証され、推移閉包が成立する。依存グラフはcstamp順で非巡回なので
   デッドロックしない)

## 依存の定義と収集 (Step 1のcstamp=LSN統合が効く)
- 統合により「バージョンのcstamp = 書き手のコミットログLSN」なので、
  読んだ/上書きしたバージョンのcstampがそのまま「待つべきLSN」になる。
- 依存 = RAW(実際に読んだ全バージョンの書き手。上書き済みバージョンの読みは
  read_set_に入らないため、read_internal内で読んだ時点で収集する) +
  WAW(上書きした「最新のcommitted版」の書き手。物理的なprev_はaborted版を
  指しうるため、コミット時のnext_committed走査で収集する)。
  anti-dependency(自分が読まれた側)は永続化待ち不要。
- 待ち先ワーカを特定するため、Version に writer_thid_ (uint8_t) を追加し、
  バージョンをインストールする時点(書き手のスレッドが自分のthidを知っている)で設定する。
- dep lsn = ver->cstamp_ の値そのまま(parallel commitはシフトせず格納する。
  シフト格納するのは未使用のserial版ssn_commitのみ)。
  cstamp==0(初期ロードのバージョン)は依存なしとしてスキップ。
- 同一ワーカへの複数依存はmaxを取って1件にまとめる(高々 read+write set サイズの小さい配列)。

## 変更内容 (cc/ermia_ayame のみ)
1. include/version.hh: `uint8_t writer_thid_ = 0;` を追加。init時0。
2. transaction.cc: バージョンをインストールする全箇所で ver->writer_thid_ = thid_ を設定。
3. include/pwal.hh:
   - struct Dep { uint8_t thid; uint64_t lsn; };
   - CommitEntry に std::vector<Dep> deps を追加。
   - pushCommit(cstamp, commit_lsn, start, deps)。
   - controlNotification: 全ワーカmin走査をやめ、
     「自ワーカflushedLSN >= commit_lsn かつ 全depsが満たされる」間、先頭からpop。
     (キューはFIFOのまま。先頭が未充足なら後続は見ない=シンプル優先。
      自ワーカflushedLSNが全エントリ共通の支配的条件なので逆転はまれ)
4. transaction.cc loggingセクション: read_set_/write_set_から依存を収集して
   pushCommit に渡す。
5. drainPending / アイドルワーカ対策(空バッファflushでのflushedLSN前進)は変更なし
   (依存待ちでも「相手ワーカのflushedLSNが進む」ことに依存するため引き続き必要)。

## 正しさの根拠
- 通知条件が「依存先txの通知済み(confirmedLSN)」を要求するため、帰納法により
  「通知済みtxの祖先(依存の推移閉包)はすべてログ永続化済みかつ通知済み」が成立する。
  したがって自分がreplyした時点で、自分が(推移的に)読んだ値を書いた全txの
  ログは永続であり、recoverabilityを満たす。
  (初期案の「依存先のflushedLSN待ち」は推移閉包が成立しない欠陥があり、
   監査指摘を受けて confirmedLSN 待ちに修正した)
- read-onlyトランザクションの即時通知は ermia_pwal と同一の単純化を維持(公平性)。

## 期待される効果
- 自ワーカのflush直後に通知可能になる(依存は通常古いLSNで既に永続化済み)。
  レイテンシのスレッド数比例成分が消えるはず。
- 通知判定の全ワーカ走査 O(n) も消える。

## 注意(記録)
- リカバリ時の連鎖無効化: 「T1のコミットログは永続だがT1の依存先T0のログが未永続」という
  クラッシュウィンドウがあり得る(T1は未replyなので対クライアントには安全)。リカバリで
  正しくT1を無効化するには、コミットログに依存先リストを含めて連鎖判定する必要がある。
  本実装はリカバリ未実装のためログへの依存リスト書き込みは行わない(既知の単純化。
  論文ではリカバリプロトコルの設計として記述する)。
- deps用のvector確保がコミットごとに発生する(mimallocで軽量だが、レイテンシノイズ源に
  なりうる。効果が見えたら後で最適化を検討、今はシンプル優先)。
- Step 1 の状態は results/2026-07-26_intermediate/ermia_ayame_step1_snapshot/ に保存
  (最適化1単体のablation用)。
