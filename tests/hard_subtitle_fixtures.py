"""Small dependency-free English bitmap fixture; no network or large assets."""
from pathlib import Path
from codex_subtitles.video_frame_service import png_gray

FONT = {
'H': ['10001','10001','10001','11111','10001','10001','10001'],
'E': ['11111','10000','10000','11110','10000','10000','11111'],
'L': ['10000','10000','10000','10000','10000','10000','11111'],
'O': ['01110','10001','10001','10001','10001','10001','01110'],
'W': ['10001','10001','10001','10101','10101','10101','01010'],
'R': ['11110','10001','10001','11110','10100','10010','10001'],
'D': ['11110','10001','10001','10001','10001','10001','11110'],
' ': ['00000']*7,
}


def english_image(path, *, bright=False, text='HELLO WORLD'):
    scale, width, height = 6, 480, 96
    pixels = bytearray([220 if bright else 0] * (width*height))
    for n, char in enumerate(text):
        for y, line in enumerate(FONT[char]):
            for x, bit in enumerate(line):
                if bit == '1':
                    for dy in range(scale):
                        for dx in range(scale):
                            pixels[(24+y*scale+dy)*width + 36+n*6*scale+x*scale+dx] = 0 if bright else 255
    png_gray(path, bytes(pixels), width, height)
    return text
