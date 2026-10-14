#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GAMETECH NDS Adaptor ("セーブデータやりくり上手" / VID: 0x4670, PID: 0xE6E9)
Ubuntu Linux Unified Manager (CLI / GUI)
"""

import sys
import os
import time
import hashlib
import argparse

try:
    import usb.core
    import usb.util
    import usb.backend.libusb1
except ImportError:
    print("[ERROR] PyUSB package is required. Please install via: sudo apt install python3-pip && pip install pyusb")
    sys.exit(1)

VID = 0x4670
PID = 0xE6E9
EP_OUT = 0x02
EP_IN = 0x81

SAVE_MODES = {
    "512B": {"size": 512, "mode": 0x01, "desc": "512 Bytes (4Kbit)"},
    "8KB":  {"size": 8192, "mode": 0x02, "desc": "8 KB (64Kbit)"},
    "64KB": {"size": 65536, "mode": 0x12, "desc": "64 KB (512Kbit - DQ9等)"},
    "256KB": {"size": 262144, "mode": 0x03, "desc": "256 KB (2Mbit)"},
    "512KB": {"size": 524288, "mode": 0x13, "desc": "512 KB (4Mbit)"},
    "1MB":  {"size": 1048576, "mode": 0x23, "desc": "1 MB (8Mbit)"},
}

MODE_LOOKUP = {info["mode"]: name for name, info in SAVE_MODES.items()}
MODE_TO_SIZE = {info["mode"]: info["size"] for name, info in SAVE_MODES.items()}

_active_device = None

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

def get_device():
    global _active_device
    if _active_device is not None:
        try:
            _active_device.backend.get_device_descriptor(_active_device._dev)
            return _active_device
        except Exception:
            _active_device = None

    backend = usb.backend.libusb1.get_backend()
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
    if dev is None:
        dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        print("[ERROR] GAMETECH NDS Adaptor (VID:0x4670, PID:0xE6E9) not found!")
        print("Please check USB connection and ensure you run with sudo privileges (sudo python3 ...)")
        return None

    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except Exception:
        pass

    try:
        dev.set_configuration()
    except Exception:
        pass

    try:
        usb.util.claim_interface(dev, 0)
    except Exception:
        pass

    _active_device = dev
    return dev

def query_cartridge(dev):
    print("\n[STEP] Querying Cartridge Autodetect (0x9C 0xA5)...")
    query_cmd = bytearray([0x9C, 0xA5, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    mode_byte = 0x12 # Default 64KB

    try:
        dev.write(EP_OUT, query_cmd, timeout=500)
        time.sleep(0.01)
        resp = dev.read(EP_IN, 8, timeout=500)
        if resp and len(resp) >= 1:
            mode_byte = resp[0]
            hex_resp = " ".join(f"{b:02X}" for b in resp)
            desc = SAVE_MODES.get(MODE_LOOKUP.get(mode_byte, ""), {}).get("desc", f"Unknown Mode 0x{mode_byte:02X}")
            print(f"  * Autodetect Response (8B): [{hex_resp}] -> Detected Mode: 0x{mode_byte:02X} ({desc})")
        else:
            print("  * Autodetect Query returned empty response, using default Mode 0x12.")
    except Exception as e:
        print(f"  * Autodetect Query Note: {e} (Using default Mode 0x12)")

    return mode_byte

def dump_save(output_file, size_key="auto", dev=None, progress_callback=None):
    if dev is None:
        dev = get_device()
    if not dev:
        return False

    mode_byte = query_cartridge(dev)

    if size_key and size_key != "auto" and size_key in SAVE_MODES:
        total_bytes = SAVE_MODES[size_key]["size"]
        mode_byte = SAVE_MODES[size_key]["mode"]
    else:
        total_bytes = MODE_TO_SIZE.get(mode_byte, 65536)

    block_size = 512
    total_blocks = total_bytes // block_size

    print(f"\n[DUMP] Starting Dump: {total_bytes} Bytes ({total_blocks} Blocks of 512 Bytes, Mode 0x{mode_byte:02X})...")
    full_data = bytearray()
    start_time = time.time()

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

        dev.write(EP_OUT, cmd, timeout=500)
        time.sleep(0.002)
        chunk = dev.read(EP_IN, block_size, timeout=1000)

        if len(chunk) != block_size:
            print(f"\n[ERROR] Short read at block {b} (offset 0x{offset:04X}): Got {len(chunk)} / {block_size} B")
            return False

        full_data.extend(chunk)

        if progress_callback:
            progress_callback(len(full_data), total_bytes, start_time)

        if (b + 1) % 16 == 0 or b == total_blocks - 1:
            pct = ((b + 1) / total_blocks) * 100
            print(f"  Progress: {b+1}/{total_blocks} Blocks ({pct:5.1f}%) - {len(full_data)} / {total_bytes} Bytes")

    elapsed = max(0.001, time.time() - start_time)
    speed_kb = (len(full_data) / 1024) / elapsed
    print(f"\n[OK] Dump Complete in {elapsed:.2f}s ({speed_kb:.1f} KB/s)")

    with open(output_file, "wb") as f:
        f.write(full_data)

    print(f"  Saved File: {output_file}")

    hdr32 = bytes(full_data[:32])
    hex_str = " ".join(f"{b:02X}" for b in hdr32)
    ascii_str = "".join(chr(b) if 32 <= b <= 126 else "." for b in hdr32)
    header_title = parse_header_text(hdr32)

    print("\n=========================================================")
    print(" [FIRST 32 BYTES HEADER REPORT]")
    print("=========================================================")
    print(f"  HEX:   [{hex_str}]")
    print(f"  ASCII: [{ascii_str}]")
    if header_title:
        print(f"  TITLE: \"{header_title}\"")
    print("=========================================================")
    return True

def restore_save(input_file, size_key="auto", dev=None, progress_callback=None):
    if dev is None:
        dev = get_device()
    if not dev:
        return False

    if not os.path.exists(input_file):
        print(f"[ERROR] Input file {input_file} not found!")
        return False

    with open(input_file, "rb") as f:
        write_data = f.read()

    file_size = len(write_data)

    if size_key and size_key != "auto" and size_key in SAVE_MODES:
        expected_size = SAVE_MODES[size_key]["size"]
        mode_byte = SAVE_MODES[size_key]["mode"]
    else:
        mode_byte = query_cartridge(dev)
        expected_size = MODE_TO_SIZE.get(mode_byte, file_size)

    if file_size != expected_size:
        print(f"[WARNING] File size ({file_size} B) differs from mode size ({expected_size} B). Using file size.")
        expected_size = file_size

    print(f"\n[RESTORE] Starting Restore: {expected_size} Bytes to Cartridge (Mode 0x{mode_byte:02X})...")
    chunk_size = 256
    total_chunks = expected_size // chunk_size
    start_time = time.time()

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

        dev.write(EP_OUT, cmd, timeout=500)
        time.sleep(0.002)

        payload = write_data[offset:offset+chunk_size]
        dev.write(EP_OUT, payload, timeout=1000)
        time.sleep(0.005)

        if progress_callback:
            progress_callback((c + 1) * chunk_size, expected_size, start_time)

        if (c + 1) % 32 == 0 or c == total_chunks - 1:
            pct = ((c + 1) / total_chunks) * 100
            print(f"  Progress: {c+1}/{total_chunks} Chunks ({pct:5.1f}%)")

    elapsed = max(0.001, time.time() - start_time)
    speed_kb = (len(write_data) / 1024) / elapsed
    print(f"\n[OK] Restore Complete in {elapsed:.2f}s ({speed_kb:.1f} KB/s)")
    return True

def verify_file(filepath):
    if not os.path.exists(filepath):
        print(f"[ERROR] File {filepath} not found!")
        return

    with open(filepath, "rb") as f:
        data = f.read()

    total_bytes = len(data)
    md5_hash = hashlib.md5(data).hexdigest()
    ff_count = data.count(b'\xFF')
    zero_count = data.count(b'\x00')
    valid_bytes = total_bytes - (ff_count + zero_count)
    valid_percent = (valid_bytes / total_bytes) * 100 if total_bytes > 0 else 0

    hdr32 = data[:32]
    hex_hdr = " ".join(f"{b:02X}" for b in hdr32)
    ascii_hdr = "".join(chr(b) if 32 <= b <= 126 else "." for b in hdr32)
    header_title = parse_header_text(hdr32)

    print("\n=========================================================")
    print(" [SAVE FILE DIAGNOSTIC REPORT]")
    print("=========================================================")
    print(f" File Path:   {filepath}")
    print(f" Size:        {total_bytes} bytes ({total_bytes // 1024} KB)")
    print(f" MD5 Hash:    {md5_hash}")
    print(f" Header HEX:  [{hex_hdr}]")
    print(f" Header ASCII:[{ascii_hdr}]")
    if header_title:
        print(f" Title:       \"{header_title}\"")
    print(f" Data Ratio:  {valid_bytes} B ({valid_percent:.1f}% valid data)")
    print("=========================================================")

    if ff_count == total_bytes:
        print(" STATUS: ⚠️ File is completely empty (all 0xFF bytes)")
    elif zero_count == total_bytes:
        print(" STATUS: ⚠️ File is completely null (all 0x00 bytes)")
    else:
        print(" STATUS: ✅ Valid save structure detected")

def run_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    import threading

    class UbuntuApp:
        def __init__(self, root):
            self.root = root
            self.root.title("セーブデータやりくり上手 (Ubuntu Linux Manager)")
            self.root.geometry("680x520")

            self.dev = None
            self.setup_ui()
            self.check_conn()

        def setup_ui(self):
            status_frame = ttk.LabelFrame(self.root, text=" USB Device Status ", padding=10)
            status_frame.pack(fill="x", padx=15, pady=8)

            self.lbl_status = ttk.Label(status_frame, text="Status: OFF (Not Found)", font=("Ubuntu", 11, "bold"), foreground="red")
            self.lbl_status.pack(side="left", padx=10)

            ttk.Button(status_frame, text="Reconnect / Refresh", command=self.check_conn).pack(side="right", padx=10)

            settings_frame = ttk.LabelFrame(self.root, text=" Cartridge Settings ", padding=10)
            settings_frame.pack(fill="x", padx=15, pady=5)

            ttk.Label(settings_frame, text="Save Size:").grid(row=0, column=0, padx=5, pady=5)
            self.combo = ttk.Combobox(settings_frame, values=[v["desc"] for k, v in SAVE_MODES.items()], state="readonly", width=32)
            self.combo.set(SAVE_MODES["64KB"]["desc"])
            self.combo.grid(row=0, column=1, padx=5, pady=5)

            ttk.Button(settings_frame, text="Autodetect Cartridge", command=self.autodetect).grid(row=0, column=2, padx=10, pady=5)

            action_frame = ttk.LabelFrame(self.root, text=" Actions ", padding=10)
            action_frame.pack(fill="x", padx=15, pady=5)

            self.btn_read = tk.Button(action_frame, text="Dump Save\n(DS ➔ PC)", bg="#4CAF50", fg="white", height=3, width=18, command=self.dump_action)
            self.btn_read.pack(side="left", expand=True, padx=5)

            self.btn_write = tk.Button(action_frame, text="Restore Save\n(PC ➔ DS)", bg="#2196F3", fg="white", height=3, width=18, command=self.restore_action)
            self.btn_write.pack(side="left", expand=True, padx=5)

            self.btn_check = tk.Button(action_frame, text="Verify Save File", bg="#FF9800", fg="white", height=3, width=18, command=self.verify_action)
            self.btn_check.pack(side="left", expand=True, padx=5)

            log_frame = ttk.LabelFrame(self.root, text=" Log Output ", padding=8)
            log_frame.pack(fill="both", expand=True, padx=15, pady=5)

            self.txt_log = tk.Text(log_frame, font=("Monospace", 9), height=6)
            self.txt_log.pack(fill="both", expand=True)

            progress_frame = ttk.Frame(self.root, padding=10)
            progress_frame.pack(fill="x", side="bottom")

            self.progress = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate")
            self.progress.pack(fill="x", pady=2)

            self.lbl_info = ttk.Label(progress_frame, text="Ready", font=("Ubuntu", 9))
            self.lbl_info.pack(anchor="w")

        def log(self, msg):
            self.txt_log.insert(tk.END, msg + "\n")
            self.txt_log.see(tk.END)

        def check_conn(self):
            self.dev = get_device()
            if self.dev:
                self.lbl_status.config(text="Status: ON (GAMETECH NDS Adaptor Connected)", foreground="green")
                self.log("[OK] USB Device Found (VID:0x4670, PID:0xE6E9)")
            else:
                self.lbl_status.config(text="Status: OFF (Not Found - Run with sudo)", foreground="red")

        def autodetect(self):
            if not self.dev:
                messagebox.showwarning("Warning", "Device not connected! Run script with sudo.")
                return
            mode = query_cartridge(self.dev)
            if mode in MODE_LOOKUP:
                desc = SAVE_MODES[MODE_LOOKUP[mode]]["desc"]
                self.combo.set(desc)
                self.log(f"[Autodetect] Cartridge responded with Mode 0x{mode:02X} ({desc})")
            else:
                self.log(f"[Autodetect] Cartridge responded with Mode 0x{mode:02X}")

        def dump_action(self):
            # Linux Tkinter multi-pattern format for file dialogs
            file_types = [
                ("DS Save Files (*.sav, *.nsd)", ("*.sav", "*.nsd", "*.SAV", "*.NSD")),
                ("All Files (*)", "*")
            ]
            out = filedialog.asksaveasfilename(defaultextension=".sav", filetypes=file_types)
            if not out:
                return

            def update_progress(curr, total, start_time):
                self.progress["maximum"] = total
                self.progress["value"] = curr
                elapsed = max(0.05, time.time() - start_time)
                speed_kb = (curr / 1024) / elapsed
                self.lbl_info.config(text=f"Dumping... {curr} / {total} bytes ({speed_kb:.1f} KB/s)")

            def worker():
                self.btn_read.config(state="disabled")
                self.btn_write.config(state="disabled")
                try:
                    res = dump_save(out, "auto", dev=self.dev, progress_callback=update_progress)
                    if res:
                        messagebox.showinfo("Success", f"Save successfully dumped to:\n{out}")
                except Exception as e:
                    self.log(f"[Error] Dump failed: {e}")
                    messagebox.showerror("Error", f"Dump failed: {e}")
                finally:
                    self.btn_read.config(state="normal")
                    self.btn_write.config(state="normal")
                    self.progress["value"] = 0
                    self.lbl_info.config(text="Ready")

            threading.Thread(target=worker, daemon=True).start()

        def restore_action(self):
            file_types = [
                ("DS Save Files (*.sav, *.nsd)", ("*.sav", "*.nsd", "*.SAV", "*.NSD")),
                ("All Files (*)", "*")
            ]
            inp = filedialog.askopenfilename(filetypes=file_types)
            if not inp:
                return

            if not messagebox.askyesno("Confirm Restore", f"Overwrite cartridge with {os.path.basename(inp)}?"):
                return

            def update_progress(curr, total, start_time):
                self.progress["maximum"] = total
                self.progress["value"] = curr
                elapsed = max(0.05, time.time() - start_time)
                speed_kb = (curr / 1024) / elapsed
                self.lbl_info.config(text=f"Restoring... {curr} / {total} bytes ({speed_kb:.1f} KB/s)")

            def worker():
                self.btn_read.config(state="disabled")
                self.btn_write.config(state="disabled")
                try:
                    res = restore_save(inp, "auto", dev=self.dev, progress_callback=update_progress)
                    if res:
                        messagebox.showinfo("Success", f"Save successfully restored from:\n{inp}")
                except Exception as e:
                    self.log(f"[Error] Restore failed: {e}")
                    messagebox.showerror("Error", f"Restore failed: {e}")
                finally:
                    self.btn_read.config(state="normal")
                    self.btn_write.config(state="normal")
                    self.progress["value"] = 0
                    self.lbl_info.config(text="Ready")

            threading.Thread(target=worker, daemon=True).start()

        def verify_action(self):
            file_types = [
                ("DS Save Files (*.sav, *.nsd)", ("*.sav", "*.nsd", "*.SAV", "*.NSD")),
                ("All Files (*)", "*")
            ]
            inp = filedialog.askopenfilename(filetypes=file_types)
            if inp:
                verify_file(inp)

    root = tk.Tk()
    app = UbuntuApp(root)
    root.mainloop()

def main():
    parser = argparse.ArgumentParser(description="GAMETECH NDS Adaptor Ubuntu Manager")
    parser.add_argument("--dump", help="Dump cartridge save data to specified file")
    parser.add_argument("--restore", help="Restore specified save file to cartridge")
    parser.add_argument("--verify", help="Verify and diagnose specified save file")
    parser.add_argument("--size", default="auto", choices=["auto"] + list(SAVE_MODES.keys()), help="Save size (default: auto - autodetect from cartridge)")
    parser.add_argument("--gui", action="store_true", help="Launch GUI interface")

    args = parser.parse_args()

    if args.gui or (not args.dump and not args.restore and not args.verify):
        run_gui()
    elif args.dump:
        dump_save(args.dump, args.size)
    elif args.restore:
        restore_save(args.restore, args.size)
    elif args.verify:
        verify_file(args.verify)

if __name__ == "__main__":
    main()
