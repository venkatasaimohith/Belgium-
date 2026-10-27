`RealESRGAN_x4plus.pth` is the required production Image Enhancement model.
Agent 4 loads it once through Spandrel/PyTorch and reuses it for every image.
The production pipeline does not silently fall back to Pillow enhancement.
