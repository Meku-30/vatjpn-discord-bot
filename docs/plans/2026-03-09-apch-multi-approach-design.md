# APCH TYPE 複数進入方式対応 設計書

## 背景・課題

RJTTなど複数滑走路空港では、plain_dataに複数行の進入方式が含まれる:

```
RJTT (APCH) ILS X RWY34L
            HIGHWAY VISUAL RWY34R
     LDG RWY 34L/34R
     DEP RWY 05/34R
```

現在のSWIM APIパーサーは1行目（`ILS X RWY34L`）しか抽出せず、2行目（`HIGHWAY VISUAL RWY34R`）が欠落。
これにより:
- APCH TYPE通知で情報が不完全
- baseline判定で2行目の進入方式をフィルターできない

## 変更箇所

### 1. SWIM API — パーサー修正

**ファイル**: `swim-api/src/scraper/airspace.py` L492-495

現在:
```python
m = re.search(r"\(APCH\)\s+(.+?)(?:\n|$)", plain_data)
if m:
    approach_type = m.group(1).strip()
```

修正後: `(APCH)` 行の後の継続行（空白インデントで始まる進入方式行）も取得

```python
# 1行目: (APCH) 直後
m = re.search(r"\(APCH\)\s+(.+?)(?:\n|$)", plain_data)
if m:
    approach_type = m.group(1).strip()
    approach_types = [approach_type]
    # 継続行: (APCH)行より後、LDG/DEP/USING行より前のインデント行
    rest = plain_data[m.end():]
    for line in rest.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith(("LDG ", "DEP ", "USING ")):
            break
        approach_types.append(stripped)
```

### 2. SWIM API — DBモデル

**ファイル**: `swim-api/src/db/models.py` L115-136

追加フィールド:
```python
approach_types: Mapped[str | None] = mapped_column(Text, nullable=True)
# JSON文字列として格納: '["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]'
```

`approach_type` (既存) は1行目を維持し、後方互換を保つ。

### 3. SWIM API — レスポンスモデル

**ファイル**: `swim-api/src/api/models.py` L107-118

追加フィールド:
```python
approach_types: list[str] | None  # 全進入方式の配列
```

DBから取得時にJSON文字列→リストに変換するvalidator追加。

### 4. SWIM API — ルーター

変更不要。`RunwayInfoResponse.model_validate(rwy)` で自動変換。

### 5. Discord Bot — baseline判定修正

**ファイル**: `cogs/swim.py` apch_loop内

現在: `approach_type` (単一文字列) に対してbaseline部分一致
修正後: `approach_types` (配列) の**全要素**に対してbaseline部分一致

```python
# 現在
self._apch_matches_baseline(apch, bl)

# 修正後
any(self._apch_matches_baseline(a, bl) for a in approach_types)
```

### 6. Discord Bot — 通知表示変更

現在:
```
現在: ILS X RWY34L     ← 1行のみ
使用滑走路: 34L/34R     ← 別フィールド
```

修正後:
```
進入方式:
  ILS X RWY34L
  HIGHWAY VISUAL RWY34R
使用滑走路: LDG 34L/34R / DEP 05/34R
```

APCH TYPEと使用滑走路を1つのEmbedフィールドにまとめる。

## 後方互換性

| 項目 | 対応 |
|------|------|
| `approach_type` (既存フィールド) | 1行目を維持、削除しない |
| `approach_types` (新フィールド) | 新規追加。nullableで既存データに影響なし |
| Bot側フォールバック | `approach_types` がnull/空なら `approach_type` を使用 |

## DBマイグレーション

swim-apiのSQLiteに `approach_types` カラムを追加（ALTER TABLE、既存行はNULL）。
次回のスクレイピングから新データが格納される。

## テスト

### SWIM API側
- `_parse_rwy_info` テスト追加: RJTT形式の複数行plain_data → `approach_types` が2要素の配列
- 単一進入方式の空港 → `approach_types` が1要素の配列
- レスポンスモデルのJSON変換テスト

### Discord Bot側
- baseline判定: `approach_types` 配列内のいずれかに一致すればOK
- 表示: 複数進入方式が改行で表示されること
