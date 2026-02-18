import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import threading
import sys
import os
import io

# Import logic
import youtube_subtitle_trans
from utils import subtitle_formatter

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Subtitle Generator & Merger")
        self.root.geometry("750x650")

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

        ttk.Label(settings_frame, text="Model:").grid(row=1, column=0, padx=5, pady=5)
        self.model_entry = ttk.Entry(settings_frame, width=15)
        self.model_entry.insert(0, "gpt-4o")
        self.model_entry.grid(row=1, column=1, padx=5, pady=5)

        # Force Audio Checkbox
        self.force_audio_var = tk.BooleanVar(value=False)
        self.force_audio_check = ttk.Checkbutton(tab, text="Force Audio Source (Skip Manual Subs)", variable=self.force_audio_var)
        self.force_audio_check.pack(pady=2)
        
        # Engine Selection
        engine_frame = ttk.Frame(tab)
        engine_frame.pack(pady=2)
        ttk.Label(engine_frame, text="Engine:").pack(side='left', padx=5)
        self.engine_combo = ttk.Combobox(engine_frame, values=["Whisper", "Google"], width=10, state='readonly')
        self.engine_combo.set("Whisper")
        self.engine_combo.pack(side='left', padx=5)

        # VAD Checkbox
        self.use_vad_var = tk.BooleanVar(value=False)
        self.use_vad_check = ttk.Checkbutton(tab, text="Enable VAD (Filter Silence/Noise before Transcription)", variable=self.use_vad_var)
        self.use_vad_check.pack(pady=2)

        # Chunk Size (row in settings_frame)
        ttk.Label(settings_frame, text="Chunk Size:").grid(row=1, column=2, padx=5, pady=5)
        self.chunk_size_options = {"Auto (10 min)": None, "Medium (3 min)": 180, "Fine (90s)": 90}
        self.chunk_size_combo = ttk.Combobox(settings_frame, values=list(self.chunk_size_options.keys()), width=14, state='readonly')
        self.chunk_size_combo.set("Auto (10 min)")
        self.chunk_size_combo.grid(row=1, column=3, padx=5, pady=5)

        # Whisper Prompt
        prompt_frame = ttk.Frame(tab)
        prompt_frame.pack(fill='x', padx=5, pady=2)
        ttk.Label(prompt_frame, text="Whisper Prompt:").pack(side='left', padx=(0,5))
        self.whisper_prompt_entry = ttk.Entry(prompt_frame, width=60)
        self.whisper_prompt_entry.insert(0, "")
        self.whisper_prompt_entry.pack(side='left', fill='x', expand=True)

        # Button
        self.start_btn = ttk.Button(tab, text="Start Processing", command=self.start_processing)
        self.start_btn.pack(pady=10)

        # Log Area (Shared?)
        self.log_area = scrolledtext.ScrolledText(tab, height=15)
        self.log_area.pack(fill='both', expand=True, padx=5, pady=5)

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

    def start_processing(self):
        url = self.url_entry.get()
        lang = self.lang_entry.get()
        model = self.model_entry.get()
        force_audio = self.force_audio_var.get()
        source_lang_name = self.source_lang_combo.get()
        source_lang = self.source_lang_options.get(source_lang_name, "en")
        use_vad = self.use_vad_var.get()
        whisper_prompt = self.whisper_prompt_entry.get().strip() or None
        chunk_size_name = self.chunk_size_combo.get()
        max_segment_sec = self.chunk_size_options.get(chunk_size_name)
        engine = self.engine_combo.get().lower()
        
        if not url:
            self.log("Please enter a URL.")
            return
            
        self.start_btn.config(state='disabled')
        vad_status = "VAD ON" if use_vad else "VAD OFF"
        prompt_info = f', Prompt="{whisper_prompt[:30]}..."' if whisper_prompt and len(whisper_prompt) > 30 else (f', Prompt="{whisper_prompt}"' if whisper_prompt else '')
        self.log(f"Starting... (Engine: {engine}, Source: {source_lang_name}, Target: {lang}, {vad_status}, Chunk: {chunk_size_name}{prompt_info})")
        
        def run():
            try:
                youtube_subtitle_trans.process_video(
                    url, lang, model, force_audio=force_audio, 
                    source_lang=source_lang, use_vad=use_vad, 
                    whisper_prompt=whisper_prompt, max_segment_sec=max_segment_sec,
                    engine=engine, progress_callback=self.log
                )
            except Exception as e:
                self.log(f"Error: {e}")
            finally:
                self.root.after(0, lambda: self.start_btn.config(state='normal'))

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

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
