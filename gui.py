#!/usr/bin/env python3
"""Modern Tkinter GUI for batch invoice PDF renaming."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from invoice_renamer import AppConfig, load_config, rename_pdfs


def app_base_dir() -> Path:
    """Return app directory for source and frozen executable modes."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ensure_default_config(config_path: Path) -> None:
    """Create a default config.toml when missing."""
    if config_path.exists():
        return

    default_contents = (
        "[extract]\n"
        "invoice_number_regex = '发票号码[\\s\\S]{0,2000}?(?P<invoice_number>\\d{20})'\n\n"
        "[rename]\n"
        "filename_template = '{{invoice_number}}'\n"
        "preserve_pdf_extension = true\n\n"
        "[scan]\n"
        "recursive = false\n"
    )
    config_path.write_text(default_contents, encoding="utf-8")


def toml_literal(value: str) -> str:
    """Serialize user text as a TOML literal string safely."""
    return "'" + value.replace("'", "''") + "'"


class InvoiceRenamerGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PDF Invoice Renamer")
        self.root.geometry("620x620")
        self.root.minsize(620, 560)

        self._apply_styles()

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.is_running = False

        self.config_path = app_base_dir() / "config.toml"
        ensure_default_config(self.config_path)
        self.folder_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=True)
        self.include_subfolders_var = tk.BooleanVar(value=True)

        self.cfg_regex_var = tk.StringVar()
        self.cfg_template_var = tk.StringVar()
        self.cfg_preserve_ext_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self._schedule_log_poll()

    def _apply_styles(self) -> None:
        style = ttk.Style(self.root)
        for theme in ("vista", "clam", "default"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break

        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Card.TLabelframe", padding=12)
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self.root, padding=16)
        root_frame.pack(fill="both", expand=True)
        root_frame.columnconfigure(0, weight=1)
        root_frame.rowconfigure(2, weight=1)

        header = ttk.Frame(root_frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(header, text="PDF Invoice Renamer", style="Title.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            header,
            text="Batch rename invoice PDFs from regex extraction with live log output.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        top_cards = ttk.Frame(root_frame)
        top_cards.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        top_cards.columnconfigure(0, weight=1)

        process_card = ttk.LabelFrame(top_cards, text="Process Settings", style="Card.TLabelframe")
        process_card.grid(row=0, column=0, sticky="nsew")
        process_card.columnconfigure(1, weight=1)

        ttk.Label(process_card, text="Folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(process_card, textvariable=self.folder_var).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Button(process_card, text="Browse...", command=self._choose_folder).grid(
            row=0, column=2, sticky="e"
        )

        toggles = ttk.Frame(process_card)
        toggles.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(toggles, text="Dry run", variable=self.dry_run_var).pack(
            side="left"
        )
        ttk.Checkbutton(
            toggles,
            text="Include subfolders",
            variable=self.include_subfolders_var,
        ).pack(side="left", padx=(16, 0))
        body = ttk.Frame(root_frame)
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)

        editor_card = ttk.LabelFrame(body, text="Rename Rules", style="Card.TLabelframe")
        editor_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        editor_card.columnconfigure(0, weight=1)
        editor_card.columnconfigure(1, weight=3)

        ttk.Label(editor_card, text="Invoice regex").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        ttk.Entry(editor_card, textvariable=self.cfg_regex_var).grid(
            row=0, column=1, sticky="ew", padx=(10, 0), pady=(0, 8)
        )

        ttk.Label(editor_card, text="Filename template").grid(
            row=1, column=0, sticky="w", pady=(0, 8)
        )
        ttk.Entry(editor_card, textvariable=self.cfg_template_var).grid(
            row=1, column=1, sticky="ew", padx=(10, 0), pady=(0, 8)
        )

        ttk.Checkbutton(
            editor_card,
            text="Keep .pdf extension",
            variable=self.cfg_preserve_ext_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w")

        hint = (
            "Tips:\n"
            "- Regex runs on full PDF text.\n"
            "- Use a named group: (?P<invoice_number>...)\n"
            "- Template must contain {{invoice_number}}."
        )
        ttk.Label(editor_card, text=hint, justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

        log_card = ttk.LabelFrame(body, text="Live Output", style="Card.TLabelframe")
        log_card.grid(row=1, column=0, sticky="nsew")
        log_card.rowconfigure(0, weight=1)
        log_card.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_card,
            wrap="word",
            font=("Consolas", 10),
            relief="flat",
            borderwidth=0,
            background="#111827",
            foreground="#E5E7EB",
            insertbackground="#E5E7EB",
            padx=10,
            pady=10,
        )
        log_scroll = ttk.Scrollbar(log_card, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set, state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")

        footer = ttk.Frame(root_frame)
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.run_button = ttk.Button(
            footer, text="Run Rename", style="Primary.TButton", command=self._start_run
        )
        self.run_button.pack(side="left")
        ttk.Button(footer, text="Clear Log", command=self._clear_log).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(footer, textvariable=self.status_var).pack(side="right")

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _schedule_log_poll(self) -> None:
        self._drain_log_queue()
        self.root.after(100, self._schedule_log_poll)

    def _drain_log_queue(self) -> None:
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"{line}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(title="Choose Folder to Process")
        if path:
            self.folder_var.set(path)

    def _load_config_to_form(self) -> None:
        try:
            cfg = load_config(self.config_path)
        except Exception:
            # Silent fallback to current defaults if settings are not ready yet.
            return

        self.cfg_regex_var.set(cfg.invoice_number_regex)
        self.cfg_template_var.set(cfg.filename_template)
        self.include_subfolders_var.set(cfg.recursive)
        self.cfg_preserve_ext_var.set(cfg.preserve_pdf_extension)
        self.status_var.set("Ready")

    def _save_config_from_form(self) -> bool:
        regex = self.cfg_regex_var.get().strip()
        template = self.cfg_template_var.get().strip()
        recursive = self.include_subfolders_var.get()
        preserve = self.cfg_preserve_ext_var.get()

        if not regex:
            messagebox.showerror("Validation Error", "invoice_number_regex is required.")
            return False
        if not template:
            messagebox.showerror("Validation Error", "filename_template is required.")
            return False
        if "{{invoice_number}}" not in template:
            messagebox.showerror(
                "Validation Error",
                "filename_template must contain {{invoice_number}}.",
            )
            return False

        contents = (
            "[extract]\n"
            f"invoice_number_regex = {toml_literal(regex)}\n\n"
            "[rename]\n"
            f"filename_template = {toml_literal(template)}\n"
            f"preserve_pdf_extension = {'true' if preserve else 'false'}\n\n"
            "[scan]\n"
            f"recursive = {'true' if recursive else 'false'}\n"
        )
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(contents, encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Write Error", str(exc))
            return False

        self.status_var.set("Settings saved")
        return True

    def _start_run(self) -> None:
        if self.is_running:
            return

        raw_folder = self.folder_var.get().strip()
        if not raw_folder:
            messagebox.showwarning("Invalid Folder", "Folder path cannot be empty.")
            return

        selected_folder = Path(raw_folder).expanduser()
        try:
            selected_folder = selected_folder.resolve()
        except Exception:
            messagebox.showwarning(
                "Invalid Folder",
                "Folder path is invalid. Please choose a valid folder.",
            )
            return

        if not selected_folder.exists() or not selected_folder.is_dir():
            messagebox.showwarning("Invalid Folder", "Choose a valid folder to process.")
            return

        if self.include_subfolders_var.get():
            try:
                has_subfolders = any(
                    child.is_dir() for child in selected_folder.iterdir()
                )
            except Exception as exc:
                messagebox.showerror(
                    "Folder Access Error",
                    f"Could not inspect folder contents:\n{exc}",
                )
                return

            if has_subfolders:
                confirm = messagebox.askyesno(
                    "Confirm Subfolder Processing",
                    "Subfolders were found in the selected folder.\n\n"
                    "Include subfolders is enabled, so all nested folders will also "
                    "be processed.\n\nDo you want to continue?",
                )
                if not confirm:
                    self.status_var.set("Cancelled")
                    self._log("Run cancelled by user at subfolder confirmation.")
                    return

        # Always persist current settings before run.
        if not self._save_config_from_form():
            return

        config = AppConfig(
            invoice_number_regex=self.cfg_regex_var.get().strip(),
            filename_template=self.cfg_template_var.get().strip(),
            recursive=self.include_subfolders_var.get(),
            preserve_pdf_extension=self.cfg_preserve_ext_var.get(),
        )

        self.is_running = True
        self.run_button.state(["disabled"])
        self.status_var.set("Running...")
        self._log("=" * 72)
        self._log(
            "Starting run for folder "
            f"{selected_folder}. dry_run={self.dry_run_var.get()}, "
            f"include_subfolders={self.include_subfolders_var.get()}"
        )

        thread = threading.Thread(
            target=self._run_worker,
            args=(selected_folder, config, self.dry_run_var.get()),
            daemon=True,
        )
        thread.start()

    def _run_worker(self, folder: Path, config: AppConfig, dry_run: bool) -> None:
        result = rename_pdfs(folder, config, dry_run=dry_run, logger=self._log)
        self._log("\n" + "-" * 72)
        self._log(
            "Summary: "
            f"processed={result.processed}, "
            f"renamed={result.renamed}, "
            f"skipped={result.skipped}, "
            f"errors={result.errors}"
        )
        self.root.after(0, self._run_finished)

    def _run_finished(self) -> None:
        self.is_running = False
        self.run_button.state(["!disabled"])
        self.status_var.set("Done")


def main() -> None:
    root = tk.Tk()
    app = InvoiceRenamerGUI(root)
    app._load_config_to_form()
    root.mainloop()


if __name__ == "__main__":
    main()
