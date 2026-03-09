# APCH baseline RWY条件追加

## 問題

approach_typesに滑走路名が含まれないケース（`VISUAL`, `VOR A`, `VISUAL APCH`等）では、
baseline部分一致だけでは使用滑走路を区別できない。

例: RJFT `VISUAL` — RWY07(通常)とRWY25(レア)を区別したい。

## 解決策

`/apch set` に省略可能な `rwy` パラメータを追加。
`runway_in_use` フィールドとの厳密一致で滑走路条件を判定する。

## 変更

### DB: apch_watches

`rwy TEXT` カラム追加（NULLable）。既存レコードはNULL = rwyチェックなし。

### コマンド

```
/apch set RJFT VISUAL rwy:07        # rwy条件あり
/apch set RJFT VISUAL               # rwy条件なし（既存動作）
/apch remove RJFT VISUAL rwy:07     # rwy条件付き削除
```

### マッチングロジック

`_baseline_matches_approaches` 拡張:

1. approach_types部分一致（既存）
2. rwy条件あり → runway_in_useを "/" で分割、個別RWYと完全一致
3. 両方一致で正常判定

runway_in_use例:
- `RWY 07` → `["07"]`
- `RWY 16L/16R` → `["16L", "16R"]`
- `RWY 34L/34R` → `["34L", "34R"]`

### 表示

- `/apch list`: `"VISUAL" (RWY 07)`
- 通知embed基準欄: `VISUAL (RWY 07)`
