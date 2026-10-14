import sys
import os
import time
import threading
import hashlib
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import usb.core
import usb.util
import libusb_package

VID = 0x4670
PID = 0xE6E9
EP_OUT = 0x02
EP_IN = 0x81

# XP Protocol Mode Table mapped from original application decompilation
SAVE_MODES = {
    "512 Bytes (4Kbit)": {"size": 512, "mode": 0x01},
    "8 KB (64Kbit)": {"size": 8192, "mode": 0x02},
    "64 KB (512Kbit - DQ9等)": {"size": 65536, "mode": 0x12},
    "256 KB (2Mbit)": {"size": 262144, "mode": 0x03},
    "512 KB (4Mbit)": {"size": 524288, "mode": 0x13},
    "1 MB (8Mbit)": {"size": 1048576, "mode": 0x23},
}

MODE_LOOKUP = {info["mode"]: name for name, info in SAVE_MODES.items()}
MODE_TO_SIZE = {info["mode"]: info["size"] for name, info in SAVE_MODES.items()}

FILE_TYPES = [
    ("DS Save Files (*.sav, *.nsd)", ("*.sav", "*.nsd", "*.SAV", "*.NSD")),
    ("All Files (*.*)", "*.*")
]

def parse_header_text(hdr32):
    """汎用ヘッダーテキスト抽出 (特定ゲームに限定しない)"""
    ascii_chars = []
    for b in hdr32:
        if 32 <= b <= 126:
            ascii_chars.append(chr(b))
        elif len(ascii_chars) >= 4:
            break
        elif ascii_chars:
            ascii_chars.append(" ")
    title = "".join(ascii_chars).strip()
    return title if len(title) >= 3 else None

class NDSSaveManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("セーブデータやりくり上手 64bit Manager for Windows 11")
        self.root.geometry("680x520")
        self.root.resizable(True, True)

        self.dev = None
        self.is_connected = False

        self.setup_ui()
        self.check_device_connection()

    def setup_ui(self):
        # Header Status
        status_frame = ttk.LabelFrame(self.root, text=" デバイス接続情報 ", padding=10)
        status_frame.pack(fill="x", padx=15, pady=8)

        self.lbl_status = ttk.Label(status_frame, text="接続状態 : OFF (未検出)", font=("Meiryo", 11, "bold"), foreground="red")
        self.lbl_status.pack(side="left", padx=10)

        btn_refresh = ttk.Button(status_frame, text="再接続・更新", command=self.check_device_connection)
        btn_refresh.pack(side="right", padx=10)

        # Cartridge Settings & Autodetect
        settings_frame = ttk.LabelFrame(self.root, text=" カートリッジ設定・自動判定 ", padding=10)
        settings_frame.pack(fill="x", padx=15, pady=5)

        ttk.Label(settings_frame, text="セーブ容量種別:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.combo_size = ttk.Combobox(settings_frame, values=list(SAVE_MODES.keys()), state="readonly", width=32)
        self.combo_size.set("64 KB (512Kbit - DQ9等)")
        self.combo_size.grid(row=0, column=1, padx=5, pady=5)

        btn_autodetect = ttk.Button(settings_frame, text="カートリッジ自動判定 (0x9C 0xA5)", command=self.autodetect_cartridge)
        btn_autodetect.grid(row=0, column=2, padx=10, pady=5)

        # Action Buttons
        action_frame = ttk.LabelFrame(self.root, text=" 主な操作 ", padding=10)
        action_frame.pack(fill="x", expand=True, padx=15, pady=5)

        self.btn_read = tk.Button(action_frame, text="パソコンに送る\n(DS ➔ PC 吸出し)", font=("Meiryo", 10, "bold"), bg="#4CAF50", fg="white", height=3, width=18, command=self.start_read_save)
        self.btn_read.pack(side="left", expand=True, padx=5, pady=5)

        self.btn_write = tk.Button(action_frame, text="カードに送る\n(PC ➔ DS 書き戻し)", font=("Meiryo", 10, "bold"), bg="#2196F3", fg="white", height=3, width=18, command=self.start_write_save)
        self.btn_write.pack(side="left", expand=True, padx=5, pady=5)

        self.btn_check = tk.Button(action_frame, text="SAVデータ検証\n(バイナリ診断)", font=("Meiryo", 10, "bold"), bg="#FF9800", fg="white", height=3, width=18, command=self.verify_save_file)
        self.btn_check.pack(side="left", expand=True, padx=5, pady=5)

        # Log & Header Display Frame
        log_frame = ttk.LabelFrame(self.root, text=" ヘッダー・ログレポート ", padding=8)
        log_frame.pack(fill="both", expand=True, padx=15, pady=5)

        self.txt_log = tk.Text(log_frame, font=("Consolas", 9), height=6, state="normal")
        self.txt_log.pack(fill="both", expand=True)

        # Progress bar
        progress_frame = ttk.Frame(self.root, padding=10)
        progress_frame.pack(fill="x", side="bottom")

        self.progress = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=2)

        self.lbl_info = ttk.Label(progress_frame, text="準備完了", font=("Meiryo", 9))
        self.lbl_info.pack(anchor="w")

    def log(self, text):
        self.txt_log.insert(tk.END, text + "\n")
        self.txt_log.see(tk.END)

    def check_device_connection(self):
        try:
            self.dev = usb.core.find(idVendor=VID, idProduct=PID, backend=libusb_package.get_libusb1_backend())
            if self.dev is None:
                self.dev = usb.core.find(idVendor=VID, idProduct=PID)

            if self.dev:
                self.is_connected = True
                try:
                    self.dev.set_configuration()
                except Exception:
                    pass
                try:
                    usb.util.claim_interface(self.dev, 0)
                except Exception:
                    pass
                self.lbl_status.config(text="接続状態 : ON (GAMETECH NDS Adaptor)", foreground="green")
                self.lbl_info.config(text="GAMETECH NDS Adaptor が正常に検出されています")
                self.log("[OK] USBデバイスの接続を確認しました (VID:0x4670, PID:0xE6E9)")
            else:
                self.is_connected = False
                self.lbl_status.config(text="接続状態 : OFF (未検出)", foreground="red")
                self.lbl_info.config(text="機器が見つかりません。USB接続またはZadigのドライバーをご確認ください。")
        except Exception as e:
            self.is_connected = False
            self.lbl_status.config(text="接続エラー", foreground="red")
            self.lbl_info.config(text=f"エラー: {e}")

    def autodetect_cartridge(self):
        if not self.is_connected:
            messagebox.showwarning("警告", "デバイスが接続されていません。")
            return

        query_cmd = bytearray([0x9C, 0xA5, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        try:
            self.dev.write(EP_OUT, query_cmd, timeout=500)
            time.sleep(0.01)
            resp = self.dev.read(EP_IN, 8, timeout=500)
            if resp and len(resp) >= 1:
                mode = resp[0]
                hex_resp = " ".join(f"{b:02X}" for b in resp)
                self.log(f"[クエリ応答成功] 8バイトレスポンス: [{hex_resp}] ➔ モード値: 0x{mode:02X}")
                if mode in MODE_LOOKUP:
                    detected_name = MODE_LOOKUP[mode]
                    self.combo_size.set(detected_name)
                    messagebox.showinfo("自動判定成功", f"カートリッジの自動判定に成功しました！\n\n判定モード: 0x{mode:02X}\n自動選択: {detected_name}")
                else:
                    messagebox.showinfo("自動判定完了", f"クエリ応答: 0x{mode:02X}\nコンボボックスから対応する容量を選択してください。")
            else:
                messagebox.showwarning("判定結果", "空のレスポンスが返されました。")
        except Exception as e:
            messagebox.showerror("エラー", f"自動判定クエリ送信に失敗しました:\n{e}")

    def verify_save_file(self):
        save_path = filedialog.askopenfilename(filetypes=FILE_TYPES)
        if not save_path:
            return

        try:
            with open(save_path, "rb") as f:
                data = f.read()

            total_bytes = len(data)
            if total_bytes == 0:
                messagebox.showerror("検証結果", "ファイルが空 (0バイト) です。")
                return

            md5_hash = hashlib.md5(data).hexdigest()
            ff_count = data.count(b'\xFF')
            zero_count = data.count(b'\x00')
            valid_bytes = total_bytes - (ff_count + zero_count)
            valid_percent = (valid_bytes / total_bytes) * 100

            hdr32 = data[:32]
            hex_hdr = " ".join(f"{b:02X}" for b in hdr32)
            ascii_hdr = "".join(chr(b) if 32 <= b <= 126 else "." for b in hdr32)
            header_title = parse_header_text(hdr32)

            msg = f"ファイル名: {os.path.basename(save_path)}\n"
            msg += f"サイズ: {total_bytes} bytes ({total_bytes // 1024} KB)\n"
            msg += f"MD5: {md5_hash}\n\n"
            msg += f"【先頭ヘッダー 32B】\nHEX:   [{hex_hdr}]\nASCII: [{ascii_hdr}]\n\n"
            msg += f"・有効データ領域: {valid_bytes} B ({valid_percent:.1f}%)\n"
            msg += f"・未書込(FF)領域: {ff_count} B\n"
            msg += f"・ヌル(00)領域  : {zero_count} B\n\n"

            if ff_count == total_bytes:
                msg += "【判定】⚠️ 全体が未書込(0xFF)データです。（端子の接触不良または未初期化の可能性があります）"
            elif zero_count == total_bytes:
                msg += "【判定】⚠️ 全体がヌル(0x00)データです。"
            else:
                msg += "【判定】✅ 有効なセーブデータ構造が検出されました。"
                if header_title:
                    msg += f"\n(検出タイトル/識別文字列: \"{header_title}\")"

            messagebox.showinfo("SAVデータ診断結果", msg)
        except Exception as e:
            messagebox.showerror("エラー", f"検証中にエラーが発生しました:\n{e}")

    def start_read_save(self):
        if not self.is_connected:
            messagebox.showwarning("警告", "デバイスが接続されていません。")
            return

        save_path = filedialog.asksaveasfilename(defaultextension=".sav", filetypes=FILE_TYPES)
        if not save_path:
            return

        threading.Thread(target=self._read_save_thread, args=(save_path,), daemon=True).start()

    def _read_save_thread(self, save_path):
        size_key = self.combo_size.get()
        size_info = SAVE_MODES[size_key]
        total_bytes = size_info["size"]
        mode_byte = size_info["mode"]

        self.btn_read.config(state="disabled")
        self.btn_write.config(state="disabled")
        self.progress["value"] = 0
        self.progress["maximum"] = total_bytes

        self.log(f"\n--- 吸出し開始: {size_key} (モード 0x{mode_byte:02X}) ---")
        start_time = time.time()

        try:
            save_data = bytearray()
            block_size = 512
            total_blocks = total_bytes // block_size

            for b in range(total_blocks):
                offset = b * block_size
                cmd = bytearray(8)
                cmd[0] = 0x2C
                cmd[1] = 0xA5
                cmd[2] = offset & 0xFF
                cmd[3] = (offset >> 8) & 0xFF
                cmd[4] = (offset >> 16) & 0xFF
                cmd[5] = (offset >> 24) & 0xFF
                cmd[6] = 0x00
                cmd[7] = mode_byte

                self.dev.write(EP_OUT, cmd, timeout=500)
                time.sleep(0.002)
                chunk = self.dev.read(EP_IN, block_size, timeout=1000)

                if len(chunk) != block_size:
                    raise Exception(f"ブロック {b} でショートリード発生: {len(chunk)} / {block_size} B")

                save_data.extend(chunk)

                current_progress = (b + 1) * block_size
                self.progress["value"] = current_progress
                elapsed = max(0.05, time.time() - start_time)
                speed_kb = (current_progress / 1024) / elapsed
                self.lbl_info.config(text=f"吸出し中... {current_progress} / {total_bytes} bytes ({speed_kb:.1f} KB/s)")

            with open(save_path, "wb") as f:
                f.write(save_data)

            elapsed = time.time() - start_time
            hdr32 = bytes(save_data[:32])
            hex_hdr = " ".join(f"{b:02X}" for b in hdr32)
            ascii_hdr = "".join(chr(b) if 32 <= b <= 126 else "." for b in hdr32)
            md5_hash = hashlib.md5(save_data).hexdigest()
            header_title = parse_header_text(hdr32)

            self.log(f"[OK] 吸出し完了: {total_bytes} B in {elapsed:.2f}s ({speed_kb:.1f} KB/s)")
            self.log(f"  保存ファイル: {os.path.basename(save_path)}")
            self.log(f"  MD5: {md5_hash}")
            self.log(f"  先頭HEX:   [{hex_hdr}]")
            self.log(f"  先頭ASCII: [{ascii_hdr}]")
            if header_title:
                self.log(f"  検出タイトル: \"{header_title}\"")

            messagebox.showinfo("吸出し完了", f"セーブデータの吸出しが正常に完了しました！\n\nファイル: {os.path.basename(save_path)}\nサイズ: {total_bytes} bytes\n処理時間: {elapsed:.2f}秒 ({speed_kb:.1f} KB/s)")

        except Exception as e:
            self.log(f"[ERROR] 吸出し失敗: {e}")
            messagebox.showerror("エラー", f"吸出し中にエラーが発生しました:\n{e}")
        finally:
            self.btn_read.config(state="normal")
            self.btn_write.config(state="normal")
            self.progress["value"] = 0
            self.lbl_info.config(text="準備完了")

    def start_write_save(self):
        if not self.is_connected:
            messagebox.showwarning("警告", "デバイスが接続されていません。")
            return

        save_path = filedialog.askopenfilename(filetypes=FILE_TYPES)
        if not save_path:
            return

        if not messagebox.askyesno("書き戻し確認", "⚠️ カートリッジのセーブデータを上書きします。\nよろしいですか？"):
            return

        threading.Thread(target=self._write_save_thread, args=(save_path,), daemon=True).start()

    def _write_save_thread(self, save_path):
        size_key = self.combo_size.get()
        size_info = SAVE_MODES[size_key]
        expected_size = size_info["size"]
        mode_byte = size_info["mode"]

        self.btn_read.config(state="disabled")
        self.btn_write.config(state="disabled")
        self.progress["value"] = 0

        self.log(f"\n--- 書き戻し開始: {os.path.basename(save_path)} (モード 0x{mode_byte:02X}) ---")

        try:
            with open(save_path, "rb") as f:
                write_data = f.read()

            if len(write_data) != expected_size:
                raise Exception(f"ファイルサイズ不一致: 選択された容量={expected_size} B, ファイル={len(write_data)} B")

            self.progress["maximum"] = expected_size
            start_time = time.time()
            chunk_size = 256
            total_chunks = expected_size // chunk_size

            for c in range(total_chunks):
                offset = c * chunk_size
                cmd = bytearray(8)
                cmd[0] = 0x7B
                cmd[1] = 0xA5
                cmd[2] = offset & 0xFF
                cmd[3] = (offset >> 8) & 0xFF
                cmd[4] = (offset >> 16) & 0xFF
                cmd[5] = (offset >> 24) & 0xFF
                cmd[6] = 0x00
                cmd[7] = mode_byte

                # 1. Send Write Command Packet
                self.dev.write(EP_OUT, cmd, timeout=500)
                time.sleep(0.002)

                # 2. Send Payload Data
                payload = write_data[offset:offset+chunk_size]
                self.dev.write(EP_OUT, payload, timeout=1000)
                time.sleep(0.005)

                current_progress = (c + 1) * chunk_size
                self.progress["value"] = current_progress
                elapsed = max(0.05, time.time() - start_time)
                speed_kb = (current_progress / 1024) / elapsed
                self.lbl_info.config(text=f"書き戻し中... {current_progress} / {expected_size} bytes ({speed_kb:.1f} KB/s)")

            elapsed = time.time() - start_time
            self.log(f"[OK] 書き戻し完了: {expected_size} B in {elapsed:.2f}s ({speed_kb:.1f} KB/s)")
            messagebox.showinfo("書き戻し完了", f"カートリッジへの書き戻しが正常に完了しました！\n\nファイル: {os.path.basename(save_path)}\n処理時間: {elapsed:.2f}秒 ({speed_kb:.1f} KB/s)")

        except Exception as e:
            self.log(f"[ERROR] 書き戻し失敗: {e}")
            messagebox.showerror("エラー", f"書き戻し中にエラーが発生しました:\n{e}")
        finally:
            self.btn_read.config(state="normal")
            self.btn_write.config(state="normal")
            self.progress["value"] = 0
            self.lbl_info.config(text="準備完了")

if __name__ == "__main__":
    root = tk.Tk()
    app = NDSSaveManagerApp(root)
    root.mainloop()
