# 📄 NDS Adapter+ (ndsplus.cpp) ソースコード・プロトコル解析仕様書

---

## 1. 概要

本ドキュメントは、2012年に Jaron Vietor 氏によって開発された NDS Adapter+ 用オープンソース C++ プログラム（`ndsplus.cpp` / GPL v3）のソースコード解析およびプロトコル比較仕様書です。

### 対象ソースファイル
- `original_setup/ndsplus/ndsplus.cpp`

---

## 2. デバイス識別と対応関係

| 項目 | ndsplus.cpp の仕様 | セーブデータやりくり上手 (本プロジェクト) |
| :--- | :--- | :--- |
| **対象製品名** | NDS Adapter Plus | GAMETECH セーブデータやりくり上手 |
| **Vendor ID (VID)** | `0x4670` (EMS Production Ltd.) | `0x4670` (EMS Production Ltd.) |
| **Product ID (PID)** | **`0x9394`** (NDS Adapter+) | **`0xE6E9`** (GT Inter-Link / やりくり上手) |
| **転送方式** | Bulk Transfer (`EP_OUT` / `EP_IN`) | Bulk Transfer (`EP_OUT` / `EP_IN`) |
| **パケット長** | 10 Bytes パケット | 8 Bytes パケット |
| **基本プロトコル** | **`0xA5` マジックバイト構造** | **`0xA5` マジックバイト構造** |

> [!NOTE]
> `PID: 0x9394` (Adapter+) と `PID: 0xE6E9` (やりくり上手) は製品型番が異なりますが、内部で同一の **`0xA5` シリーズプロトコル** を共有していることが検証されました。

---

## 3. ステータス・容量判定プロトコル (`get_status()`)

### コマンド送信 (10 Bytes)
```
[0x9C, 0xA5, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00]
```

### ステータス返却値 (8 Bytes) 構造
`EP_IN` から受信した 8 バイトのステータスレスポンスの構造解析：

```
+----------+----------+----------+----------+----------+----------+----------+----------+
| Byte 0   | Byte 1   | Byte 2   | Byte 3   | Byte 4   | Byte 5   | Byte 6   | Byte 7   |
+----------+----------+----------+----------+----------+----------+----------+----------+
| Mode/Type| Busy Flag| Reserved | Reserved |Size (1<<X| 0xAA     | FW Ver Low| FW Ver Hi|
+----------+----------+----------+----------+----------+----------+----------+----------+
```

| バイト位置 | 説明 |
| :--- | :--- |
| **`status[0]`** | **チップ種別・モードバイト**<br>- `0x01`: 512 Bytes (0.5 KiB EEPROM)<br>- `0x02`: 8,192 Bytes (8 KiB EEPROM)<br>- **`0x12`**: **65,536 Bytes (64 KiB EEPROM / DQ9等)**<br>- その他: FLASH型チップ（容量判定は `status[4]` を参照） |
| **`status[1]`** | EEPROM Busy ビット（`bit 0 == 1` で書き込みビジー状態） |
| **`status[4]`** | FLASHセーブ容量の累乗値（`1 << status[4]` KiB） |
| **`status[5]`** | 固定マーカー（常に `0xAA`） |
| **`status[6..7]`** | アダプターのファームウェアバージョン (Little-Endian)<br>例: `0x3001` (3.01) ➔ `status[7] * 256 + status[6]` |

---

## 4. セーブデータ読み出しプロトコル (`get_save()`)

512 バイト単位のセクター読み出しシーケンス：

### コマンド送信 (10 Bytes)
```
[0x2C, 0xA5, Addr0, Addr1, Addr2, Addr3, 0x02, Mode, 0x00, 0x00]
```
- `Byte 0..1`: **`0x2C 0xA5`** (Read Opcode)
- `Byte 2..5`: 32ビット アドレス (Little-Endian)
- `Byte 6`: `0x02`
- `Byte 7`: `status[0]` で得られたモードバイト (例: `0x12`)
- **インターバル**: `usleep(1000)` (1ms 睡眠待機)
- **レスポンス**: `EP_IN` より **512 Bytes** の生セーブデータを受信。

---

## 5. セーブデータ書き込み＆イレースプロトコル (`put_save()`)

256 バイト単位のセクター書き込みシーケンス：

### 特殊Flashチップ用消去（Erase）前処理
特定Flashチップ（モード `0x93`, `0x53`, `0xA3`）の場合、書き込み前に消去コマンドを送信：
- `status[0] == 0x93` の場合 ➔ 消去コマンド **`0x5B 0xA5`** を送信
- `status[0] == 0x53` または `0xA3` の場合 ➔ 消去コマンド **`0x5E 0xA5`** を送信

### 通常書き込みコマンド送信 (10 Bytes)
```
[0x7B, 0xA5, Addr0, Addr1, Addr2, Addr3, 0x02, Mode, 0x00, 0x00]
```
- `Byte 0..1`: **`0x7B 0xA5`** (Write Opcode)
- `Byte 2..5`: 32ビット アドレス (Little-Endian)
- `Byte 7`: `status[0]` モードバイト
- コマンド送信直後に `EP_OUT` へ **256 Bytes** のペイロードデータを送信。
- インターバル: `usleep(1000)` (1ms 睡眠待機)。

---

## 6. ROMヘッダー情報取得プロトコル (`prepare_card()`, `get_header()`)

ゲームカートリッジ本体の ROM ヘッダー情報（タイトル・ROM ID・容量）を取得するシーケンス：

1. `[0x9F, 0xA5, 0x9F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]` を送信
2. `[0x90, 0xA5, 0x90, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]` を送信
3. `EP_IN` より 4 バイト応答を受信
4. `[0x00, 0xA5, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]` を送信
5. `EP_IN` より **512 Bytes** の ROM ヘッダー（カードタイトル文字列・カードID・ROMサイズ）を受信。
