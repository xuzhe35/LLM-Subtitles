import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import threading
import sys
import os
import io

# Import logic
import youtube_subtitle_trans
from utils import subtitle_formatter

ENHANCE_AUDIO_OPTIONS = {
    "Off": (False, "off"),
    "Mild": (True, "mild"),
    "Strong FFmpeg": (True, "strong_ffmpeg"),
}

TRANSLATION_MODEL_OPTIONS = (
    "gpt-realtime-translate",
    "gpt-4o",
)
DEFAULT_TRANSLATION_MODEL = youtube_subtitle_trans.DEFAULT_TRANSLATION_MODEL
DEFAULT_POLISH_MODEL = youtube_subtitle_trans.DEFAULT_POLISH_MODEL

# Processing modes map GUI labels to pipeline names. "Fast" keeps the current
# Realtime behavior and stays the default; "High Quality" is the opt-in
# Transcribe + LLM route with two ASR passes and reusable intermediate files.
PROCESSING_MODE_OPTIONS = {
    "Fast / Realtime Translation": youtube_subtitle_trans.PIPELINE_REALTIME,
    "High Quality / Transcribe + LLM": youtube_subtitle_trans.PIPELINE_TRANSCRIBE_LLM,
    "Legacy": youtube_subtitle_trans.PIPELINE_LEGACY,
}
DEFAULT_PROCESSING_MODE = "Fast / Realtime Translation"

TIMING_MODE_OPTIONS = ("auto", "whisper-1", "gpt-4o-transcribe-diarize")
HIGH_QUALITY_TRANSLATION_MODEL_OPTIONS = ("gpt-5.6-terra", "gpt-5.6", "gpt-4o")
DEFAULT_HIGH_QUALITY_TRANSLATION_MODEL = "gpt-5.6-terra"


def resolve_enhance_audio_selection(selection):
    return ENHANCE_AUDIO_OPTIONS.get(selection, ENHANCE_AUDIO_OPTIONS["Off"])


def resolve_processing_mode_selection(selection):
    """Map the GUI mode label to a pipeline name; unknown labels stay Fast."""
    return PROCESSING_MODE_OPTIONS.get(
        selection, youtube_subtitle_trans.PIPELINE_REALTIME
    )


def mode_control_states(pipeline):
    """Which control groups apply to the selected processing mode.

    The Global Context Polish control only affects the Realtime route, so it
    must be disabled — not just ignored — on the other routes.
    """
    return {
        "polish_enabled": pipeline == youtube_subtitle_trans.PIPELINE_REALTIME,
        "high_quality_enabled": pipeline == youtube_subtitle_trans.PIPELINE_TRANSCRIBE_LLM,
        "legacy_engine_enabled": pipeline == youtube_subtitle_trans.PIPELINE_LEGACY,
        "translation_model_enabled": pipeline != youtube_subtitle_trans.PIPELINE_TRANSCRIBE_LLM,
    }


def parse_keywords_entry(raw_text):
    """Comma-separated GUI keywords -> list (empty -> None)."""
    keywords = [part.strip() for part in str(raw_text or "").split(",") if part.strip()]
    return keywords or None


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Subtitle Generator & Merger")
        self.root.geometry("780x700")

        self.notebook = ttk.Notebook(root)
        self.notebook.pack(expand=True, fill='both', padx=10, pady=10)

        self.init_trans_tab()

        self.init_merge_tab()
        
        self.check_ffmpeg()

    def check_ffmpeg(self):
        import shutil
        if not shutil.which("ffmpeg"):
            self.log("WARNING: 'ffmpeg' not found in PATH. Audio extraction and splitting will fail.")
            self.log("Please install ffmpeg and add it to your system PATH.")

    def log(self, message):
        def _log():
            self.log_area.insert(tk.END, message + "\n")
            self.log_area.see(tk.END)
        self.root.after(0, _log)

    def init_trans_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Downloader & Translator")

        # URL Input
        ttk.Label(tab, text="YouTube URL:").pack(pady=5)
        self.url_entry = ttk.Entry(tab, width=50)
        self.url_entry.pack(pady=5)

        # Processing Mode
        mode_frame = ttk.Frame(tab)
        mode_frame.pack(pady=2)
        ttk.Label(mode_frame, text="Processing Mode:").pack(side='left', padx=5)
        self.processing_mode_combo = ttk.Combobox(
            mode_frame,
            values=list(PROCESSING_MODE_OPTIONS.keys()),
            width=32,
            state='readonly',
        )
        self.processing_mode_combo.set(DEFAULT_PROCESSING_MODE)
        self.processing_mode_combo.pack(side='left', padx=5)
        self.processing_mode_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.update_mode_controls()
        )

        # Settings
        settings_frame = ttk.Frame(tab)
        settings_frame.pack(pady=5)
        
        ttk.Label(settings_frame, text="Source Language:").grid(row=0, column=0, padx=5)
        self.source_lang_options = {"Auto (Mixed)": None, "English": "en", "Japanese": "ja", "Thai": "th"}
        self.source_lang_combo = ttk.Combobox(settings_frame, values=list(self.source_lang_options.keys()), width=12, state='readonly')
        self.source_lang_combo.set("English")
        self.source_lang_combo.grid(row=0, column=1, padx=5)

        ttk.Label(settings_frame, text="Target Language:").grid(row=0, column=2, padx=5)
        self.lang_entry = ttk.Entry(settings_frame, width=20)
        self.lang_entry.insert(0, "Simplified Chinese")
        self.lang_entry.grid(row=0, column=3, padx=5)

        ttk.Label(settings_frame, text="Translation Model:").grid(row=1, column=0, padx=5, pady=5)
        self.model_entry = ttk.Combobox(
            settings_frame,
            values=TRANSLATION_MODEL_OPTIONS,
            width=24,
            state="normal",
        )
        self.model_entry.set(DEFAULT_TRANSLATION_MODEL)
        self.model_entry.grid(row=1, column=1, padx=5, pady=5)

        # Force Audio Checkbox
        self.force_audio_var = tk.BooleanVar(value=False)
        self.force_audio_check = ttk.Checkbutton(tab, text="Force Audio Source (Skip Manual Subs)", variable=self.force_audio_var)
        self.force_audio_check.pack(pady=2)
        
        # Engine Selection
        engine_frame = ttk.Frame(tab)
        engine_frame.pack(pady=2)
        ttk.Label(engine_frame, text="Engine:").pack(side='left', padx=5)
        self.engine_combo = ttk.Combobox(
            engine_frame,
            values=["Whisper", "Google", "gpt-4o-transcribe-diarize"],
            width=28,
            state='readonly'
        )
        self.engine_combo.set("Whisper")
        self.engine_combo.pack(side='left', padx=5)

        # VAD Checkbox
        self.use_vad_var = tk.BooleanVar(value=False)
        self.use_vad_check = ttk.Checkbutton(tab, text="Enable VAD (Filter Silence/Noise before Transcription)", variable=self.use_vad_var)
        self.use_vad_check.pack(pady=2)

        # Audio enhancement
        enhance_frame = ttk.Frame(tab)
        enhance_frame.pack(pady=2)
        ttk.Label(enhance_frame, text="Audio Enhance:").pack(side='left', padx=5)
        self.enhance_audio_combo = ttk.Combobox(
            enhance_frame,
            values=list(ENHANCE_AUDIO_OPTIONS.keys()),
            width=16,
            state='readonly'
        )
        self.enhance_audio_combo.set("Off")
        self.enhance_audio_combo.pack(side='left', padx=5)

        # Chunk Size (row in settings_frame)
        ttk.Label(settings_frame, text="Chunk Size:").grid(row=1, column=2, padx=5, pady=5)
        self.chunk_size_options = {"Auto (10 min)": None, "Medium (3 min)": 180, "Fine (90s)": 90}
        self.chunk_size_combo = ttk.Combobox(settings_frame, values=list(self.chunk_size_options.keys()), width=14, state='readonly')
        self.chunk_size_combo.set("Auto (10 min)")
        self.chunk_size_combo.grid(row=1, column=3, padx=5, pady=5)

        # Realtime JSON global context polish
        polish_frame = ttk.Frame(tab)
        polish_frame.pack(pady=2)
        self.polish_realtime_var = tk.BooleanVar(value=True)
        self.polish_realtime_check = ttk.Checkbutton(
            polish_frame,
            text="Global Context Polish",
            variable=self.polish_realtime_var,
        )
        self.polish_realtime_check.pack(side='left', padx=5)
        ttk.Label(polish_frame, text="Polish Model:").pack(side='left', padx=(12, 5))
        self.polish_model_entry = ttk.Combobox(
            polish_frame,
            values=("gpt-5.6", "gpt-5.6-terra", "gpt-4o"),
            width=18,
            state="normal",
        )
        self.polish_model_entry.set(DEFAULT_POLISH_MODEL)
        self.polish_model_entry.pack(side='left', padx=5)

        # High Quality (Transcribe + LLM) settings. This route runs two ASR
        # passes (semantic + timing) and creates reusable intermediate files.
        hq_frame = ttk.LabelFrame(tab, text="High Quality (Transcribe + LLM) settings")
        hq_frame.pack(fill='x', padx=5, pady=2)

        hq_row1 = ttk.Frame(hq_frame)
        hq_row1.pack(fill='x', pady=2)
        ttk.Label(hq_row1, text="Source Languages:").pack(side='left', padx=5)
        self.hq_source_langs_entry = ttk.Entry(hq_row1, width=12)
        self.hq_source_langs_entry.insert(0, "")
        self.hq_source_langs_entry.pack(side='left', padx=5)
        ttk.Label(hq_row1, text="Timing:").pack(side='left', padx=(12, 5))
        self.hq_timing_combo = ttk.Combobox(
            hq_row1, values=TIMING_MODE_OPTIONS, width=24, state='readonly'
        )
        self.hq_timing_combo.set("auto")
        self.hq_timing_combo.pack(side='left', padx=5)

        hq_row2 = ttk.Frame(hq_frame)
        hq_row2.pack(fill='x', pady=2)
        ttk.Label(hq_row2, text="Transcription Prompt:").pack(side='left', padx=5)
        self.hq_prompt_entry = ttk.Entry(hq_row2, width=40)
        self.hq_prompt_entry.pack(side='left', fill='x', expand=True, padx=5)

        hq_row3 = ttk.Frame(hq_frame)
        hq_row3.pack(fill='x', pady=2)
        ttk.Label(hq_row3, text="Keywords (comma-sep):").pack(side='left', padx=5)
        self.hq_keywords_entry = ttk.Entry(hq_row3, width=40)
        self.hq_keywords_entry.pack(side='left', fill='x', expand=True, padx=5)

        hq_row4 = ttk.Frame(hq_frame)
        hq_row4.pack(fill='x', pady=2)
        ttk.Label(hq_row4, text="Translation Model:").pack(side='left', padx=5)
        self.hq_translation_model_combo = ttk.Combobox(
            hq_row4,
            values=HIGH_QUALITY_TRANSLATION_MODEL_OPTIONS,
            width=16,
            state='normal',
        )
        self.hq_translation_model_combo.set(DEFAULT_HIGH_QUALITY_TRANSLATION_MODEL)
        self.hq_translation_model_combo.pack(side='left', padx=5)
        self.hq_escalation_var = tk.BooleanVar(value=True)
        self.hq_escalation_check = ttk.Checkbutton(
            hq_row4,
            text="Selective Sol escalation",
            variable=self.hq_escalation_var,
        )
        self.hq_escalation_check.pack(side='left', padx=(12, 5))

        self.hq_widgets = [
            self.hq_source_langs_entry,
            self.hq_timing_combo,
            self.hq_prompt_entry,
            self.hq_keywords_entry,
            self.hq_translation_model_combo,
            self.hq_escalation_check,
        ]

        # Whisper Prompt
        prompt_frame = ttk.Frame(tab)
        prompt_frame.pack(fill='x', padx=5, pady=2)
        ttk.Label(prompt_frame, text="Whisper Prompt:").pack(side='left', padx=(0,5))
        self.whisper_prompt_entry = ttk.Entry(prompt_frame, width=60)
        self.whisper_prompt_entry.insert(0, "")
        self.whisper_prompt_entry.pack(side='left', fill='x', expand=True)

        # Output Directory
        output_frame = ttk.Frame(tab)
        output_frame.pack(fill='x', padx=5, pady=2)
        ttk.Label(output_frame, text="Output Dir:").pack(side='left', padx=(0,5))
        self.output_dir_entry = ttk.Entry(output_frame, width=50)
        self.output_dir_entry.insert(0, youtube_subtitle_trans.DEFAULT_OUTPUT_DIR)
        self.output_dir_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(output_frame, text="Browse", command=self.browse_output_dir).pack(side='right', padx=(5, 0))

        # Button
        self.start_btn = ttk.Button(tab, text="Start Processing", command=self.start_processing)
        self.start_btn.pack(pady=10)

        # Progress Bar
        self.progress_frame = ttk.Frame(tab)
        self.progress_frame.pack(fill='x', padx=10, pady=5)
        self.progress_label = ttk.Label(self.progress_frame, text="Download Progress:")
        self.progress_label.pack(side='left', padx=(0, 5))
        self.progress_bar = ttk.Progressbar(self.progress_frame, orient='horizontal', mode='determinate', length=400)
        self.progress_bar.pack(side='left', fill='x', expand=True)
        self.progress_bar['value'] = 0
        self.progress_frame.pack_forget() # Hide initially

        # Log Area (Shared?)
        self.log_area = scrolledtext.ScrolledText(tab, height=15)
        self.log_area.pack(fill='both', expand=True, padx=5, pady=5)

        self.update_mode_controls()

    def update_mode_controls(self):
        """Enable only the controls that apply to the selected mode."""
        pipeline = resolve_processing_mode_selection(self.processing_mode_combo.get())
        states = mode_control_states(pipeline)

        polish_state = 'normal' if states["polish_enabled"] else 'disabled'
        self.polish_realtime_check.config(state=polish_state)
        self.polish_model_entry.config(
            state='normal' if states["polish_enabled"] else 'disabled'
        )

        for widget in self.hq_widgets:
            if not states["high_quality_enabled"]:
                widget.config(state='disabled')
            elif widget is self.hq_timing_combo:
                widget.config(state='readonly')
            else:
                widget.config(state='normal')

        engine_state = 'readonly' if states["legacy_engine_enabled"] else 'disabled'
        self.engine_combo.config(state=engine_state)
        self.use_vad_check.config(
            state='normal' if states["legacy_engine_enabled"] else 'disabled'
        )
        self.model_entry.config(
            state='normal' if states["translation_model_enabled"] else 'disabled'
        )

    def init_merge_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Merge Subtitles")

        # File 1 (Original / Bottom)
        f1_frame = ttk.Frame(tab)
        f1_frame.pack(pady=5, fill='x', padx=10)
        ttk.Label(f1_frame, text="File 1 (Bottom/Original):").pack(anchor='w')
        self.f1_entry = ttk.Entry(f1_frame)
        self.f1_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(f1_frame, text="Browse", command=lambda: self.browse_file(self.f1_entry)).pack(side='right')

        # File 2 (Translated / Top)
        f2_frame = ttk.Frame(tab)
        f2_frame.pack(pady=5, fill='x', padx=10)
        ttk.Label(f2_frame, text="File 2 (Top/Translated):").pack(anchor='w')
        self.f2_entry = ttk.Entry(f2_frame)
        self.f2_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(f2_frame, text="Browse", command=lambda: self.browse_file(self.f2_entry)).pack(side='right')

        # Output
        # We can just auto-generate output name or ask
        
        ttk.Button(tab, text="Merge Subtitles", command=self.merge_subtitles).pack(pady=20)
        
        self.merge_status = ttk.Label(tab, text="")
        self.merge_status.pack()

    def browse_file(self, entry_widget):
        filename = filedialog.askopenfilename(filetypes=[("Subtitle files", "*.srt;*.vtt")])
        if filename:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, filename)

    def browse_output_dir(self):
        current = self.output_dir_entry.get() or youtube_subtitle_trans.DEFAULT_OUTPUT_DIR
        dirname = filedialog.askdirectory(initialdir=current)
        if dirname:
            self.output_dir_entry.delete(0, tk.END)
            self.output_dir_entry.insert(0, dirname)

    def update_progress_bar(self, percent_str):
        # percent_str is something like "45.0%"
        try:
            val = float(percent_str.strip('%'))
            def _update():
                if val > 0 and not self.progress_frame.winfo_ismapped():
                    self.progress_frame.pack(fill='x', padx=10, pady=5, before=self.log_area)
                self.progress_bar['value'] = val
                if val >= 100:
                    self.root.after(2000, lambda: self.progress_frame.pack_forget())
            self.root.after(0, _update)
        except ValueError:
            pass

    def start_processing(self):
        url = self.url_entry.get()
        lang = self.lang_entry.get()
        model = self.model_entry.get()
        force_audio = self.force_audio_var.get()
        source_lang_name = self.source_lang_combo.get()
        source_lang = self.source_lang_options.get(source_lang_name, "en")
        use_vad = self.use_vad_var.get()
        enhance_audio_name = self.enhance_audio_combo.get()
        enhance_audio, enhance_mode = resolve_enhance_audio_selection(enhance_audio_name)
        whisper_prompt = self.whisper_prompt_entry.get().strip() or None
        chunk_size_name = self.chunk_size_combo.get()
        max_segment_sec = self.chunk_size_options.get(chunk_size_name)
        engine = self.engine_combo.get().lower()
        output_dir = self.output_dir_entry.get().strip() or None
        polish_realtime = self.polish_realtime_var.get()
        polish_model = self.polish_model_entry.get().strip() or DEFAULT_POLISH_MODEL

        pipeline = resolve_processing_mode_selection(self.processing_mode_combo.get())
        high_quality_overrides = {
            "timing_model": self.hq_timing_combo.get().strip() or None,
            "source_languages": self.hq_source_langs_entry.get().strip() or None,
            "prompt": self.hq_prompt_entry.get().strip() or None,
            "keywords": parse_keywords_entry(self.hq_keywords_entry.get()),
            "translation_model": (
                self.hq_translation_model_combo.get().strip() or None
            ),
            "enable_selective_escalation": self.hq_escalation_var.get(),
        }

        if not url:
            self.log("Please enter a URL.")
            return

        self.start_btn.config(state='disabled')
        vad_status = "VAD ON" if use_vad else "VAD OFF"
        enhance_status = f"Enhance: {enhance_audio_name}"
        prompt_info = f', Prompt="{whisper_prompt[:30]}..."' if whisper_prompt and len(whisper_prompt) > 30 else (f', Prompt="{whisper_prompt}"' if whisper_prompt else '')
        polish_status = f"Global Polish: {polish_model}" if polish_realtime else "Global Polish: OFF"
        self.log(f"Starting... (Mode: {pipeline}, Engine: {engine}, Source: {source_lang_name}, Target: {lang}, {vad_status}, {enhance_status}, {polish_status}, Chunk: {chunk_size_name}{prompt_info})")
        if pipeline == youtube_subtitle_trans.PIPELINE_TRANSCRIBE_LLM:
            self.log(
                "High Quality mode: runs two ASR passes (semantic + timing) and "
                "creates reusable intermediate files; a second target language "
                "reuses transcription and alignment."
            )

        def run():
            try:
                youtube_subtitle_trans.process_video(
                    url, lang, model, force_audio=force_audio,
                    source_lang=source_lang, use_vad=use_vad,
                    whisper_prompt=whisper_prompt, max_segment_sec=max_segment_sec,
                    engine=engine, progress_callback=self.log,
                    download_progress_callback=self.update_progress_bar,
                    output_dir=output_dir,
                    enhance_audio=enhance_audio,
                    enhance_mode=enhance_mode,
                    polish_realtime=polish_realtime,
                    polish_model=polish_model,
                    pipeline=pipeline,
                    high_quality_overrides=high_quality_overrides,
                )
            except Exception as e:
                self.log(f"Error: {e}")
            finally:
                self.root.after(0, lambda: self.start_btn.config(state='normal'))
                self.root.after(0, lambda: self.progress_frame.pack_forget())

        threading.Thread(target=run, daemon=True).start()

    def merge_subtitles(self):
        f1 = self.f1_entry.get()
        f2 = self.f2_entry.get()
        
        if not f1 or not f2:
            self.merge_status.config(text="Please select both files.")
            return
            
        if not os.path.exists(f1) or not os.path.exists(f2):
             self.merge_status.config(text="File(s) not found.")
             return
             
        # Generate output path
        dir_name = os.path.dirname(f1)
        base_name = os.path.basename(f1).rsplit('.', 1)[0]
        output_path = os.path.join(dir_name, f"{base_name}_merged.srt")
        
        try:
            # Load
            def load(p):
                if p.endswith('.vtt'): return subtitle_formatter.parse_vtt(p)
                return subtitle_formatter.parse_srt(p)
            
            s1 = load(f1)
            s2 = load(f2)
            
            # Merge (S2 Top, S1 Bottom)
            subtitle_formatter.generate_bilingual_srt(s1, s2, output_path)
            self.merge_status.config(text=f"Saved to: {output_path}")
        except Exception as e:
            self.merge_status.config(text=f"Error: {e}")

def launch_app():
    """Create the Tk application and make its first window visible on macOS."""
    try:
        root = tk.Tk()
        app = App(root)

        # A Tk window started from Terminal can be created behind the active
        # application on macOS. Complete layout first, then briefly raise it.
        root.update_idletasks()
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)

        def release_topmost():
            root.attributes("-topmost", False)
            root.lift()
            root.focus_force()

        root.after(250, release_topmost)
        root.mainloop()
        return app
    except tk.TclError as exc:
        print(f"Unable to start the desktop UI: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    launch_app()
