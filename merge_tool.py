import argparse
import os
from utils import subtitle_formatter

def main():
    parser = argparse.ArgumentParser(description="Subtitle Merger")
    parser.add_argument("file1", help="Primary Subtitle File (SRT/VTT)")
    parser.add_argument("file2", help="Secondary Subtitle File (SRT/VTT)")
    parser.add_argument("output", help="Output File Path (SRT)")
    args = parser.parse_args()

    print(f"Merging {args.file1} and {args.file2}...")

    # Helper to parse based on extension
    def load_segments(path):
        if path.endswith('.vtt'):
            return subtitle_formatter.parse_vtt(path)
        elif path.endswith('.srt'):
            return subtitle_formatter.parse_srt(path)
        else:
            print(f"Unsupported format: {path}")
            return []

    seg1 = load_segments(args.file1)
    seg2 = load_segments(args.file2)

    if not seg1 or not seg2:
        print("Error: Could not parse input files.")
        return

    # Use generate_bilingual_srt logic (which merges them)
    # File 1 is treated as "Secondary" (Bottom) usually? 
    # generate_bilingual_srt(original, translated, path) puts:
    # Translated (top/first arg)
    # Original (bottom/second arg)
    
    # If user wants merging, presumably File 1 is one language, File 2 is another.
    # We'll treat File 2 as "Primary/Top" and File 1 as "Secondary/Bottom" arbitrarily
    # unless we add flags.
    # Let's assume File 1 = Bottom (Original), File 2 = Top (Translation).
    
    subtitle_formatter.generate_bilingual_srt(seg1, seg2, args.output)
    print(f"Merged subtitle saved to {args.output}")

if __name__ == "__main__":
    main()
