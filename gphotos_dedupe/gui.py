from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Optional

from PIL import Image, ImageOps, ImageTk

from .browser_bot import BrowserWorker
from .hashing import find_candidate_clusters
from .models import DuplicateGroup, MediaItem, ScanConfig
from .scoring import score_duplicate_clusters
from .storage import DedupeStorage


def _app_data_dir() -> str:
    local_app = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local_app, "GooglePhotosDedupe")


def _fmt_size(b: int) -> str:
    if b <= 0:
        return "unbekannt"
    if b >= 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.0f} KB"
    return f"{b} B"


def _fmt_res(it: MediaItem) -> str:
    if it.width > 0 and it.height > 0:
        mp = (it.width * it.height) / 1_000_000.0
        return f"{it.width}×{it.height} ({mp:.1f} MP)"
    if it.megapixels > 0:
        return f"{it.megapixels:.1f} MP"
    return "unbekannt"


def _fmt_date(it: MediaItem) -> str:
    if it.taken_ts:
        s = datetime.fromtimestamp(it.taken_ts).strftime("%d.%m.%Y %H:%M")
        return s
    return it.date_display or "unbekannt"


class DedupeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Google Fotos Smart-Duplikat-Finder (Playwright)")
        self.geometry("1160x820")
        self.minsize(980, 700)

        self.data_dir = _app_data_dir()
        self.storage = DedupeStorage(os.path.join(self.data_dir, "cache"))
        self.worker = BrowserWorker(os.path.join(self.data_dir, "chrome_profile"), self.storage)

        self.stop_event = threading.Event()
        self.is_busy = False
        self.groups: list[DuplicateGroup] = []
        self.selected_group_idx: int = -1
        self._tk_img_left: Optional[ImageTk.PhotoImage] = None
        self._tk_img_right: Optional[ImageTk.PhotoImage] = None

        self._setup_styles()
        self._build_ui()
        self._refresh_from_cache_silent()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "clam", "default"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#555555")
        style.configure("CardTitle.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8))
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8))

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        # Header
        hdr = ttk.Frame(root)
        hdr.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(hdr, text="Google Fotos Duplikate aufräumen", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            hdr,
            text="Behält automatisch bessere Qualität, mehr EXIF-Infos & älteres Originaldatum · Sicher mit Batch-Bremse & Papierkorb",
            style="Sub.TLabel",
        ).pack(side=tk.LEFT, padx=(14, 0))

        # Step 1 & 2 Controls Frame
        top_grid = ttk.Frame(root)
        top_grid.pack(fill=tk.X, pady=(0, 8))
        top_grid.columnconfigure(0, weight=1)
        top_grid.columnconfigure(1, weight=2)

        # Step 1: Login Card
        step1 = ttk.LabelFrame(top_grid, text=" Schritt 1 · Google Fotos Browser ", padding=10)
        step1.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.browser_var = tk.StringVar(value="Auto (Chrome / Edge)")
        ttk.Button(
            step1,
            text="1. Browser öffnen & anmelden",
            style="Big.TButton",
            command=self._on_open_browser,
        ).pack(fill=tk.X, pady=(0, 6))

        row1b = ttk.Frame(step1)
        row1b.pack(fill=tk.X)
        ttk.Button(row1b, text="Verbindung prüfen", command=self._on_check_login).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Combobox(
            row1b,
            textvariable=self.browser_var,
            state="readonly",
            width=20,
            values=("Auto (Chrome / Edge)", "Google Chrome", "Microsoft Edge"),
        ).pack(side=tk.LEFT, padx=(8, 0))

        self.conn_status_var = tk.StringVar(value="Status: Noch nicht geöffnet")
        ttk.Label(step1, textvariable=self.conn_status_var, style="Sub.TLabel", wraplength=320).pack(anchor="w", pady=(6, 0))

        # Step 2: Scan & Batch Controls Card
        step2 = ttk.LabelFrame(top_grid, text=" Schritt 2 · Scan-Umfang & Batch-Bremse (Anti-Block) ", padding=10)
        step2.grid(row=0, column=1, sticky="nsew")

        opts = ttk.Frame(step2)
        opts.pack(fill=tk.X)

        # Scope
        ttk.Label(opts, text="Bereich:").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=2)
        self.scope_var = tk.StringVar(value="Gesamte Mediathek (empfohlen)")
        ttk.Combobox(
            opts,
            textvariable=self.scope_var,
            state="readonly",
            width=30,
            values=[
                "Gesamte Mediathek (empfohlen)",
                "Zuletzt hochgeladen (_tra_)",
                "Ab aktuell sichtbarer Browser-Position",
            ],
        ).grid(row=0, column=1, sticky="w", padx=(0, 12), pady=2)

        # Strictness
        ttk.Label(opts, text="Genauigkeit:").grid(row=0, column=2, sticky="w", padx=(0, 4), pady=2)
        self.strictness_var = tk.StringVar(value="Ausgewogen (empfohlen)")
        ttk.Combobox(
            opts,
            textvariable=self.strictness_var,
            state="readonly",
            width=24,
            values=["Streng (nur fast identisch)", "Ausgewogen (empfohlen)", "Tolerant"],
        ).grid(row=0, column=3, sticky="w", pady=2)

        # Max scan & batch sizes
        ttk.Label(opts, text="Max. neue Fotos/Lauf:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=4)
        self.max_scan_var = tk.StringVar(value="2000")
        ttk.Combobox(
            opts,
            textvariable=self.max_scan_var,
            state="readonly",
            width=12,
            values=["250", "500", "1000", "2000", "5000", "Unbegrenzt"],
        ).grid(row=1, column=1, sticky="w", padx=(0, 12), pady=4)

        batch_box = ttk.Frame(opts)
        batch_box.grid(row=1, column=2, columnspan=2, sticky="w", pady=4)
        ttk.Label(batch_box, text="Scan-Paket:").pack(side=tk.LEFT)
        self.scan_batch_var = tk.IntVar(value=40)
        ttk.Spinbox(batch_box, from_=10, to=120, increment=10, textvariable=self.scan_batch_var, width=4).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(batch_box, text="Lösch-Paket (Batch):").pack(side=tk.LEFT)
        self.trash_batch_var = tk.IntVar(value=15)
        ttk.Spinbox(batch_box, from_=5, to=50, increment=5, textvariable=self.trash_batch_var, width=4).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(batch_box, text="Pause (s):").pack(side=tk.LEFT)
        self.trash_pause_var = tk.DoubleVar(value=4.5)
        ttk.Spinbox(batch_box, from_=1.5, to=15.0, increment=0.5, textvariable=self.trash_pause_var, width=5).pack(side=tk.LEFT, padx=(4, 0))

        self.include_videos_var = tk.BooleanVar(value=True)
        btn_row = ttk.Frame(step2)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        self.btn_scan = ttk.Button(
            btn_row,
            text="2. Mediathek scannen & Duplikate finden",
            style="Big.TButton",
            command=self._on_start_scan,
        )
        self.btn_scan.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(btn_row, text="Cache neu gruppieren", command=self._on_regroup_cache).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(btn_row, text="Cache leeren", command=self._on_clear_cache).pack(side=tk.LEFT, padx=(6, 0))
        self.btn_stop = ttk.Button(btn_row, text="Stopp", command=self._on_stop, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=(6, 0))

        # Progress row
        prog_row = ttk.Frame(root)
        prog_row.pack(fill=tk.X, pady=(0, 8))
        self.progress_bar = ttk.Progressbar(prog_row, mode="determinate", maximum=100)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.status_var = tk.StringVar(value="Bereit. Tipp: Falls dein PC schon einmal gescannt hat, liegen die Bilder im lokalen Cache.")
        ttk.Label(prog_row, textvariable=self.status_var, width=62, anchor="e").pack(side=tk.RIGHT)

        # Step 3: Side-by-Side Comparison Workspace
        mid = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # Left list of duplicate groups
        left_pane = ttk.LabelFrame(mid, text=" Schritt 3 · Gefundene Duplikat-Gruppen ", padding=6)
        mid.add(left_pane, weight=1)

        cols = ("id", "sim", "keep", "drop", "state")
        self.tree = ttk.Treeview(left_pane, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("id", text="Gruppe")
        self.tree.heading("sim", text="Ähnl.")
        self.tree.heading("keep", text="Behalten")
        self.tree.heading("drop", text="Papierkorb")
        self.tree.heading("state", text="Aktion")
        self.tree.column("id", width=65, anchor="center")
        self.tree.column("sim", width=55, anchor="center")
        self.tree.column("keep", width=110)
        self.tree.column("drop", width=110)
        self.tree.column("state", width=95, anchor="center")

        scroll_y = ttk.Scrollbar(left_pane, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_group)

        # Right side-by-side detail inspector
        right_pane = ttk.LabelFrame(mid, text=" Direktvergleich (Links behalten · Rechts in Papierkorb) ", padding=8)
        mid.add(right_pane, weight=2)

        compare_row = ttk.Frame(right_pane)
        compare_row.pack(fill=tk.BOTH, expand=True)
        compare_row.columnconfigure(0, weight=1)
        compare_row.columnconfigure(1, weight=1)

        # Keeper card (left)
        self.keep_card = tk.Frame(compare_row, bd=2, relief="groove", bg="#eef8ef", padx=8, pady=8)
        self.keep_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        tk.Label(self.keep_card, text="BEHALTEN (Beste Variante)", bg="#2e7d32", fg="white", font=("Segoe UI", 9, "bold"), padx=6, pady=2).pack(fill=tk.X)
        self.keep_img_label = tk.Label(self.keep_card, bg="#dcecdc", width=230, height=210, text="Vorschau")
        self.keep_img_label.pack(pady=8)
        self.keep_meta_var = tk.StringVar(value="Wähle links eine Gruppe aus.")
        tk.Label(self.keep_card, textvariable=self.keep_meta_var, bg="#eef8ef", justify=tk.LEFT, anchor="w", wraplength=275, font=("Segoe UI", 9)).pack(fill=tk.X)

        # Duplicate card (right)
        self.drop_card = tk.Frame(compare_row, bd=2, relief="groove", bg="#fdf0f0", padx=8, pady=8)
        self.drop_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        tk.Label(self.drop_card, text="PAPIERKORB (Duplikat)", bg="#c62828", fg="white", font=("Segoe UI", 9, "bold"), padx=6, pady=2).pack(fill=tk.X)
        self.drop_img_label = tk.Label(self.drop_card, bg="#f5dada", width=230, height=210, text="Vorschau")
        self.drop_img_label.pack(pady=8)
        self.drop_meta_var = tk.StringVar(value="")
        tk.Label(self.drop_card, textvariable=self.drop_meta_var, bg="#fdf0f0", justify=tk.LEFT, anchor="w", wraplength=275, font=("Segoe UI", 9)).pack(fill=tk.X)

        # Reason + quick actions
        self.reason_var = tk.StringVar(value="Entscheidungsgrund: —")
        ttk.Label(right_pane, textvariable=self.reason_var, font=("Segoe UI", 9, "bold"), wraplength=600).pack(anchor="w", pady=(8, 6))

        card_btns = ttk.Frame(right_pane)
        card_btns.pack(fill=tk.X)
        ttk.Button(card_btns, text="Tauschen (Rechts behalten)", command=self._on_swap_keeper).pack(side=tk.LEFT)
        ttk.Button(card_btns, text="Gruppe überspringen / aktivieren", command=self._on_toggle_skip).pack(side=tk.LEFT, padx=(8, 0))

        # Step 4: Trash Execution Bar
        bottom = ttk.LabelFrame(root, text=" Schritt 4 · Sicher in den Google Fotos Papierkorb verschieben (60 Tage wiederherstellbar) ", padding=10)
        bottom.pack(fill=tk.X)

        self.summary_var = tk.StringVar(value="Noch keine Duplikate geladen.")
        ttk.Label(bottom, textvariable=self.summary_var, font=("Segoe UI", 10)).pack(side=tk.LEFT)

        self.btn_trash = ttk.Button(
            bottom,
            text="3. Markierte Duplikate in Papierkorb verschieben (0)",
            style="Danger.TButton",
            command=self._on_trash_selected,
            state=tk.DISABLED,
        )
        self.btn_trash.pack(side=tk.RIGHT)

    def _current_config(self) -> ScanConfig:
        strict_map = {
            "Streng (nur fast identisch)": "strict",
            "Ausgewogen (empfohlen)": "balanced",
            "Tolerant": "loose",
        }
        max_raw = self.max_scan_var.get()
        max_items = 0 if max_raw == "Unbegrenzt" else int(max_raw)
        browser_map = {
            "Auto (Chrome / Edge)": "auto",
            "Google Chrome": "chrome",
            "Microsoft Edge": "msedge",
        }
        return ScanConfig(
            max_scan_items=max_items,
            scan_batch_size=max(5, int(self.scan_batch_var.get())),
            scan_batch_pause_sec=1.5,
            metadata_batch_size=15,
            metadata_batch_pause_sec=2.5,
            trash_batch_size=max(3, int(self.trash_batch_var.get())),
            trash_batch_pause_sec=max(1.0, float(self.trash_pause_var.get())),
            action_delay_sec=1.1,
            strictness=strict_map.get(self.strictness_var.get(), "balanced"),
            include_videos=self.include_videos_var.get(),
            browser_choice=browser_map.get(self.browser_var.get(), "auto"),
        )

    def _set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        state_idle = tk.DISABLED if busy else tk.NORMAL
        self.btn_scan.configure(state=state_idle)
        self.btn_stop.configure(state=tk.NORMAL if busy else tk.DISABLED)
        if busy:
            self.btn_trash.configure(state=tk.DISABLED)
        else:
            self._update_summary()

    def _run_bg(self, target) -> None:
        self.stop_event.clear()
        self._set_busy(True)
        threading.Thread(target=target, daemon=True).start()

    def _on_stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopp angefordert – laufender Schritt wird sauber beendet...")

    def _on_open_browser(self) -> None:
        browser_map = {
            "Auto (Chrome / Edge)": "auto",
            "Google Chrome": "chrome",
            "Microsoft Edge": "msedge",
        }
        choice = browser_map.get(self.browser_var.get(), "auto")

        def work() -> None:
            try:
                url = self.worker.open_google_photos(choice)
                self.after(0, lambda: self.conn_status_var.set(f"Browser offen ({url[:42]}). Falls nötig anmelden, dann 'Verbindung prüfen'."))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Browser-Fehler", str(exc)))
            finally:
                self.after(0, lambda: self._set_busy(False))

        self._run_bg(work)

    def _on_check_login(self) -> None:
        def work() -> None:
            try:
                ok, msg = self.worker.is_logged_in()
                self.after(0, lambda: self.conn_status_var.set(msg))
            except Exception as exc:
                self.after(0, lambda: self.conn_status_var.set(f"Fehler: {exc}"))
            finally:
                self.after(0, lambda: self._set_busy(False))

        self._run_bg(work)

    def _on_start_scan(self) -> None:
        cfg = self._current_config()
        scope = self.scope_var.get()
        start_url = ""
        reset_top = False
        if "Gesamte" in scope:
            start_url = "https://photos.google.com/"
            reset_top = True
        elif "Zuletzt" in scope:
            start_url = "https://photos.google.com/search/_tra_"
            reset_top = True

        def progress(cur: int, total: int, msg: str) -> None:
            def ui_up() -> None:
                self.status_var.set(msg)
                if total > 0:
                    pct = min(100.0, (cur / max(1, total)) * 100.0)
                    self.progress_bar["value"] = pct

            self.after(0, ui_up)

        def work() -> None:
            try:
                ok, msg = self.worker.is_logged_in()
                if not ok:
                    self.after(0, lambda: messagebox.showwarning("Anmeldung nötig", msg))
                    return

                self.after(0, lambda: self.progress_bar.configure(mode="indeterminate"))
                self.after(0, self.progress_bar.start)
                added = self.worker.scan_grid(cfg, start_url, reset_top, self.stop_event, progress)
                self.after(0, self.progress_bar.stop)
                self.after(0, lambda: self.progress_bar.configure(mode="determinate", value=35))

                all_items = self.storage.load_active_items()
                clusters = find_candidate_clusters(all_items, cfg.strictness)
                candidate_items = [it for _, _, members in clusters for it in members if not it.details_fetched]

                if candidate_items and not self.stop_event.is_set():
                    progress(0, len(candidate_items), f"Prüfe Detail-Metadaten für {len(candidate_items)} Kandidaten...")
                    self.worker.fetch_candidate_details(candidate_items, cfg, self.stop_event, progress)

                self.after(0, self._recompute_and_display)
                self.after(0, lambda: self.progress_bar.configure(value=100))
                self.after(0, lambda: self.status_var.set(f"Scan fertig (+{added} neue Fotos). {len(self.groups)} Duplikat-Gruppen gefunden."))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Scan-Fehler", str(exc)))
            finally:
                self.after(0, self.progress_bar.stop)
                self.after(0, lambda: self._set_busy(False))

        self._run_bg(work)

    def _refresh_from_cache_silent(self) -> None:
        self._recompute_and_display()

    def _on_regroup_cache(self) -> None:
        self._recompute_and_display()
        self.status_var.set(f"Cache neu verglichen: {len(self.groups)} Duplikat-Gruppen.")

    def _on_clear_cache(self) -> None:
        if messagebox.askyesno("Cache leeren", "Gespeicherte Scan-Daten wirklich zurücksetzen? (Auf Google Fotos wird nichts gelöscht.)"):
            self.storage.clear_cache()
            self._recompute_and_display()
            self.status_var.set("Cache geleert.")

    def _recompute_and_display(self) -> None:
        cfg = self._current_config()
        all_items = self.storage.load_active_items()
        clusters = find_candidate_clusters(all_items, cfg.strictness)
        self.groups = score_duplicate_clusters(clusters)

        for row_id in self.tree.get_children():
            self.tree.delete(row_id)

        for idx, g in enumerate(self.groups):
            keep_label = g.keeper.item.filename or _fmt_res(g.keeper.item)
            drop_label = f"{len(g.duplicates)}x ({g.duplicates[0].item.filename or _fmt_res(g.duplicates[0].item)})"
            state_txt = "Übersprungen" if g.skip_group else "Papierkorb"
            self.tree.insert("", tk.END, iid=str(idx), values=(g.group_id, f"{g.similarity_pct:.0f}%", keep_label, drop_label, state_txt))

        if self.groups:
            self.tree.selection_set("0")
            self._show_group(0)
        else:
            self.selected_group_idx = -1
            self.keep_meta_var.set(f"Keine Duplikate gefunden ({len(all_items)} Fotos im Cache).")
            self.drop_meta_var.set("")
            self.reason_var.set("Entscheidungsgrund: —")
            self.keep_img_label.configure(image="", text="Keine Vorschau")
            self.drop_img_label.configure(image="", text="Keine Vorschau")
        self._update_summary()

    def _on_select_group(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self._show_group(int(sel[0]))

    def _load_preview(self, path: str) -> Optional[ImageTk.PhotoImage]:
        if not path or not os.path.exists(path):
            return None
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((230, 210), Image.Resampling.LANCZOS)
                return ImageTk.PhotoImage(im)
        except Exception:
            return None

    def _show_group(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.groups):
            return
        self.selected_group_idx = idx
        g = self.groups[idx]
        k = g.keeper.item
        d = g.duplicates[0].item

        self._tk_img_left = self._load_preview(k.thumb_path)
        self._tk_img_right = self._load_preview(d.thumb_path)
        if self._tk_img_left:
            self.keep_img_label.configure(image=self._tk_img_left, text="")
        else:
            self.keep_img_label.configure(image="", text="Kein Vorschaubild")
        if self._tk_img_right:
            self.drop_img_label.configure(image=self._tk_img_right, text="")
        else:
            self.drop_img_label.configure(image="", text="Kein Vorschaubild")

        def meta_text(it: MediaItem) -> str:
            return (
                f"Datei: {it.filename or it.media_key}\n"
                f"Auflösung: {_fmt_res(it)}\n"
                f"Größe: {_fmt_size(it.file_size_bytes)}\n"
                f"Datum: {_fmt_date(it)}\n"
                f"Kamera/EXIF: {(it.camera_info + ' ' + it.exposure_info).strip() or 'keine'}\n"
                f"Ort/GPS: {it.location_info or 'keiner'}\n"
                f"Qualität: {it.storage_quality or 'unbekannt'}"
            )

        self.keep_meta_var.set(meta_text(k))
        extra_dups = f" (+{len(g.duplicates) - 1} weitere)" if len(g.duplicates) > 1 else ""
        self.drop_meta_var.set(meta_text(d) + extra_dups)
        status_note = " [ÜBERSPRUNGEN]" if g.skip_group else ""
        self.reason_var.set(f"Grund ({g.group_id}{status_note}): {g.keeper.reason_summary}")

    def _on_swap_keeper(self) -> None:
        if self.selected_group_idx < 0 or self.selected_group_idx >= len(self.groups):
            return
        g = self.groups[self.selected_group_idx]
        if not g.duplicates:
            return
        old_keeper = g.keeper
        new_keeper = g.duplicates[0]
        old_keeper.is_keeper = False
        old_keeper.reason_summary = "Manuell zum Löschen markiert"
        new_keeper.is_keeper = True
        new_keeper.reason_summary = "Manuell als Behalten gewählt"
        g.keeper = new_keeper
        g.duplicates[0] = old_keeper

        self.tree.item(
            str(self.selected_group_idx),
            values=(
                g.group_id,
                f"{g.similarity_pct:.0f}%",
                g.keeper.item.filename or _fmt_res(g.keeper.item),
                f"{len(g.duplicates)}x ({g.duplicates[0].item.filename or _fmt_res(g.duplicates[0].item)})",
                "Übersprungen" if g.skip_group else "Papierkorb",
            ),
        )
        self._show_group(self.selected_group_idx)
        self._update_summary()

    def _on_toggle_skip(self) -> None:
        if self.selected_group_idx < 0 or self.selected_group_idx >= len(self.groups):
            return
        g = self.groups[self.selected_group_idx]
        g.skip_group = not g.skip_group
        self.tree.item(
            str(self.selected_group_idx),
            values=(
                g.group_id,
                f"{g.similarity_pct:.0f}%",
                g.keeper.item.filename or _fmt_res(g.keeper.item),
                f"{len(g.duplicates)}x ({g.duplicates[0].item.filename or _fmt_res(g.duplicates[0].item)})",
                "Übersprungen" if g.skip_group else "Papierkorb",
            ),
        )
        self._show_group(self.selected_group_idx)
        self._update_summary()

    def _pending_trash_items(self) -> tuple[list[MediaItem], dict[str, str]]:
        items: list[MediaItem] = []
        reasons: dict[str, str] = {}
        for g in self.groups:
            if g.skip_group:
                continue
            for dc in g.duplicates:
                items.append(dc.item)
                reasons[dc.item.media_key] = g.keeper.reason_summary
        return items, reasons

    def _update_summary(self) -> None:
        items, _ = self._pending_trash_items()
        active_groups = sum(1 for g in self.groups if not g.skip_group)
        self.summary_var.set(f"{active_groups} aktive Gruppen · {len(items)} Duplikate zum Verschieben in den Papierkorb")
        self.btn_trash.configure(
            text=f"3. Markierte Duplikate in Papierkorb verschieben ({len(items)})",
            state=tk.NORMAL if (len(items) > 0 and not self.is_busy) else tk.DISABLED,
        )

    def _on_trash_selected(self) -> None:
        items, reasons = self._pending_trash_items()
        if not items:
            return
        cfg = self._current_config()
        confirmed = messagebox.askyesno(
            "Sicherheitsabfrage · Papierkorb",
            f"Sollen wirklich {len(items)} unterlegene Duplikate in Paketen à {cfg.trash_batch_size} Stück "
            f"(mit je {cfg.trash_batch_pause_sec:.1f}s Pause) in den Google Fotos Papierkorb verschoben werden?\n\n"
            "• Alle markierten Originale (grün) bleiben unberührt.\n"
            "• Gelöschte Kopien bleiben 60 Tage im Papierkorb wiederherstellbar.\n"
            f"• Protokoll: {self.storage.trash_csv_path}",
        )
        if not confirmed:
            return

        def progress(cur: int, total: int, msg: str) -> None:
            def ui_up() -> None:
                self.status_var.set(msg)
                self.progress_bar["value"] = min(100.0, (cur / max(1, total)) * 100.0)

            self.after(0, ui_up)

        def work() -> None:
            try:
                ok, msg = self.worker.is_logged_in()
                if not ok:
                    self.after(0, lambda: messagebox.showwarning("Anmeldung nötig", msg))
                    return
                moved, failed = self.worker.trash_items_in_batches(items, reasons, cfg, self.stop_event, progress)
                self.after(0, self._recompute_and_display)
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Abgeschlossen",
                        f"{moved} Duplikat(e) erfolgreich in den Papierkorb verschoben"
                        + (f" ({failed} übersprungen/Fehler)" if failed else "")
                        + f".\n\nProtokoll gespeichert unter:\n{self.storage.trash_csv_path}",
                    ),
                )
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Papierkorb-Fehler", str(exc)))
            finally:
                self.after(0, lambda: self._set_busy(False))

        self._run_bg(work)

    def _on_close(self) -> None:
        self.stop_event.set()
        try:
            self.worker.shutdown()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    app = DedupeApp()
    app.mainloop()
