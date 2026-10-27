"""Create a synthetic test image with known content (text + shapes)."""
import sys

from PIL import Image, ImageDraw, ImageFont

img = Image.new("RGB", (640, 400), "white")
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("arial.ttf", 48)
except OSError:
    font = ImageFont.load_default()
d.text((40, 40), "INVOICE 4721", fill="black", font=font)
d.ellipse((60, 180, 220, 340), fill="red")
d.rectangle((380, 180, 560, 340), fill="blue")
img.save(sys.argv[1])
print("saved", sys.argv[1])
